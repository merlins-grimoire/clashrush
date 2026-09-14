"""Crash-durable SQLite persistence for scheduler queue jobs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Self

from .configuration_v2 import AccountKey, ConfigurationGeneration
from .scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    YieldToken,
)


_SCHEMA_VERSION = 1
_CREATE_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    account_key TEXT NOT NULL,
    configuration_generation TEXT NOT NULL,
    job_generation INTEGER NOT NULL CHECK(job_generation > 0),
    availability_event TEXT NOT NULL,
    available_at REAL NOT NULL,
    due_at REAL NOT NULL CHECK(due_at >= available_at),
    pending_lane TEXT NOT NULL CHECK(pending_lane IN ('HOME', 'BUILDER')),
    state TEXT NOT NULL CHECK(state IN (
        'WAITING', 'ELIGIBLE', 'DEFERRED', 'QUARANTINED', 'ADMITTED'
    )),
    reconcile_at REAL,
    yield_set TEXT NOT NULL,
    prior_state TEXT CHECK(prior_state IN ('WAITING', 'ELIGIBLE', 'DEFERRED')),
    PRIMARY KEY(account_key, configuration_generation, job_generation),
    UNIQUE(account_key, configuration_generation)
) WITHOUT ROWID
"""


class SchedulerStoreError(RuntimeError):
    """The scheduler database or a requested mutation failed closed."""


class SQLiteJobStore:
    """Own the single current pending job for each account/configuration pair."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise SchedulerStoreError("scheduler database path must be a Path")
        try:
            parent = path.parent.resolve(strict=True)
        except OSError as exc:
            raise SchedulerStoreError("scheduler database parent must exist") from exc
        if not parent.is_dir() or parent != path.parent:
            raise SchedulerStoreError("scheduler database path is not canonical")
        if path.exists() and (path.is_symlink() or path.resolve(strict=True) != path):
            raise SchedulerStoreError("scheduler database path must not redirect")

        self._path = path
        self._connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path, isolation_level=None)
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self._initialize_schema()
        except BaseException as exc:
            self.close()
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler database could not be opened") from exc

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _require_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SchedulerStoreError("scheduler database is closed")
        return self._connection

    def _initialize_schema(self) -> None:
        connection = self._require_open()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if type(version) is not int or version not in (0, _SCHEMA_VERSION):
            raise SchedulerStoreError("scheduler database schema version is unsupported")
        existing = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_jobs'"
        ).fetchone()
        if version == 0 and existing is not None:
            raise SchedulerStoreError("unversioned scheduler database schema already exists")
        if version == _SCHEMA_VERSION and existing is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        initial_manifest = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        if version == 0 and initial_manifest:
            raise SchedulerStoreError("unversioned scheduler database schema is not empty")

        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        if str(journal_mode).lower() != "wal":
            raise SchedulerStoreError("scheduler database WAL mode is unavailable")
        connection.executescript(
            f"""
            BEGIN IMMEDIATE;
            {_CREATE_JOBS_SQL};
            PRAGMA user_version = 1;
            COMMIT;
            """
        )
        stored = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_jobs'"
        ).fetchone()
        if stored is None or type(stored[0]) is not str:
            raise SchedulerStoreError("scheduler database schema is missing")
        def normalize(sql: str) -> str:
            return (
                " ".join(sql.rstrip(";").split())
                .replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)
            )

        if normalize(stored[0]) != normalize(_CREATE_JOBS_SQL):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        manifest = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        if [tuple(row) for row in manifest] != [
            ("table", "scheduler_jobs", "scheduler_jobs")
        ]:
            raise SchedulerStoreError("scheduler database schema has unexpected objects")

    @staticmethod
    def _require_job(value: object) -> QueueJob:
        if type(value) is not QueueJob:
            raise SchedulerStoreError("scheduler store requires an exact QueueJob")
        return value

    @staticmethod
    def _encode_yield_set(job: QueueJob) -> str:
        return json.dumps(
            [
                [token.account_key.value, token.job_generation.value]
                for token in job.yield_set
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @classmethod
    def _values(cls, job: QueueJob) -> tuple[object, ...]:
        return (
            job.account_key.value,
            job.configuration_generation.value,
            job.job_generation.value,
            job.availability_event.value,
            job.available_at,
            job.due_at,
            job.pending_lane.value,
            job.state.value,
            job.reconcile_at,
            cls._encode_yield_set(job),
            None if job.prior_state is None else job.prior_state.value,
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> QueueJob:
        try:
            raw_yield_set = json.loads(row["yield_set"])
            if type(raw_yield_set) is not list:
                raise ValueError("yield set is not a list")
            tokens: list[YieldToken] = []
            for raw in raw_yield_set:
                if (
                    type(raw) is not list
                    or len(raw) != 2
                    or type(raw[0]) is not str
                    or type(raw[1]) is not int
                ):
                    raise ValueError("yield token is malformed")
                tokens.append(YieldToken(AccountKey(raw[0]), JobGeneration(raw[1])))
            prior = row["prior_state"]
            return QueueJob(
                account_key=AccountKey(row["account_key"]),
                configuration_generation=ConfigurationGeneration(
                    row["configuration_generation"]
                ),
                job_generation=JobGeneration(row["job_generation"]),
                availability_event=AvailabilityEvent(row["availability_event"]),
                available_at=row["available_at"],
                due_at=row["due_at"],
                pending_lane=Lane(row["pending_lane"]),
                state=QueueState(row["state"]),
                reconcile_at=row["reconcile_at"],
                yield_set=tuple(tokens),
                prior_state=None if prior is None else QueueState(prior),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SchedulerStoreError("invalid persisted job") from exc

    def insert(self, job: QueueJob) -> QueueJob:
        job = self._require_job(job)
        connection = self._require_open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exact = connection.execute(
                """SELECT 1 FROM scheduler_jobs
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ?""",
                (
                    job.account_key.value,
                    job.configuration_generation.value,
                    job.job_generation.value,
                ),
            ).fetchone()
            if exact is not None:
                raise SchedulerStoreError("scheduler job identity already exists")
            current = connection.execute(
                """SELECT 1 FROM scheduler_jobs
                   WHERE account_key = ? AND configuration_generation = ?""",
                (job.account_key.value, job.configuration_generation.value),
            ).fetchone()
            if current is not None:
                raise SchedulerStoreError("account configuration already has a current job")
            connection.execute(
                """INSERT INTO scheduler_jobs (
                       account_key, configuration_generation, job_generation,
                       availability_event, available_at, due_at, pending_lane, state,
                       reconcile_at, yield_set, prior_state
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._values(job),
            )
            connection.execute("COMMIT")
            return job
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler job insert failed") from exc

    def replace(self, expected: QueueJob, replacement: QueueJob) -> QueueJob:
        expected = self._require_job(expected)
        replacement = self._require_job(replacement)
        if (
            replacement.account_key != expected.account_key
            or replacement.configuration_generation != expected.configuration_generation
        ):
            raise SchedulerStoreError("replacement changed account or configuration identity")
        if replacement.job_generation.value < expected.job_generation.value:
            raise SchedulerStoreError("replacement job generation regressed")

        connection = self._require_open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM scheduler_jobs
                   WHERE account_key = ? AND configuration_generation = ?""",
                (expected.account_key.value, expected.configuration_generation.value),
            ).fetchone()
            if row is None or self._decode(row) != expected:
                raise SchedulerStoreError("stale scheduler job replacement")
            connection.execute(
                """UPDATE scheduler_jobs SET
                       job_generation = ?, availability_event = ?, available_at = ?,
                       due_at = ?, pending_lane = ?, state = ?, reconcile_at = ?,
                       yield_set = ?, prior_state = ?
                   WHERE account_key = ? AND configuration_generation = ?""",
                (
                    replacement.job_generation.value,
                    replacement.availability_event.value,
                    replacement.available_at,
                    replacement.due_at,
                    replacement.pending_lane.value,
                    replacement.state.value,
                    replacement.reconcile_at,
                    self._encode_yield_set(replacement),
                    None
                    if replacement.prior_state is None
                    else replacement.prior_state.value,
                    expected.account_key.value,
                    expected.configuration_generation.value,
                ),
            )
            connection.execute("COMMIT")
            return replacement
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler job replacement failed") from exc

    def load(self, configuration_generation: ConfigurationGeneration) -> tuple[QueueJob, ...]:
        if type(configuration_generation) is not ConfigurationGeneration:
            raise SchedulerStoreError("configuration generation must be exact")
        return self._load_where(
            "WHERE configuration_generation = ?",
            (configuration_generation.value,),
        )

    def load_all(self) -> tuple[QueueJob, ...]:
        return self._load_where("", ())

    def _load_where(self, clause: str, parameters: tuple[object, ...]) -> tuple[QueueJob, ...]:
        connection = self._require_open()
        try:
            rows = connection.execute(
                f"""SELECT * FROM scheduler_jobs {clause}
                    ORDER BY configuration_generation, account_key""",
                parameters,
            ).fetchall()
            return tuple(self._decode(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler jobs could not be loaded") from exc
