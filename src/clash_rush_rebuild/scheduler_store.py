"""Crash-durable SQLite persistence for scheduler queue jobs."""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .configuration_v2 import AccountKey, ConfigurationGeneration
from .lifecycle_state_v2 import ReadyV2
from .scheduler_contract import (
    AdmittedVisit,
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    VisitAdmission,
    VisitGeneration,
    YieldToken,
    admit,
    select_oldest_eligible,
)


_SCHEMA_VERSION = 2
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
_CREATE_VISITS_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_visits (
    visit_generation INTEGER PRIMARY KEY CHECK(visit_generation > 0),
    open_singleton INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK(open_singleton = 1),
    visit_nonce TEXT NOT NULL UNIQUE CHECK(
        length(visit_nonce) = 32 AND visit_nonce NOT GLOB '*[^0-9a-f]*'
    ),
    account_key TEXT NOT NULL,
    configuration_generation TEXT NOT NULL,
    job_generation INTEGER NOT NULL CHECK(job_generation > 0),
    slot_index INTEGER NOT NULL CHECK(slot_index >= 0 AND slot_index < 10),
    account_order TEXT NOT NULL,
    availability_event TEXT NOT NULL,
    available_at REAL NOT NULL,
    due_at REAL NOT NULL CHECK(due_at >= available_at),
    pending_lane TEXT NOT NULL CHECK(pending_lane IN ('HOME', 'BUILDER')),
    UNIQUE(account_key, configuration_generation, job_generation),
    FOREIGN KEY(account_key, configuration_generation, job_generation)
        REFERENCES scheduler_jobs(account_key, configuration_generation, job_generation)
        DEFERRABLE INITIALLY DEFERRED
)
"""


class SchedulerStoreError(RuntimeError):
    """The scheduler database or a requested mutation failed closed."""


@dataclass(frozen=True, slots=True)
class PersistedAdmission:
    admitted_visit: AdmittedVisit
    slot_index: int
    account_order: tuple[AccountKey, ...]

    def __post_init__(self) -> None:
        if type(self.admitted_visit) is not AdmittedVisit:
            raise SchedulerStoreError("persisted admission visit is invalid")
        if type(self.slot_index) is not int or not 0 <= self.slot_index < 10:
            raise SchedulerStoreError("persisted admission slot is invalid")
        if (
            type(self.account_order) is not tuple
            or not 1 <= len(self.account_order) <= 10
            or any(type(key) is not AccountKey for key in self.account_order)
            or len(set(self.account_order)) != len(self.account_order)
            or self.slot_index >= len(self.account_order)
            or self.account_order[self.slot_index]
            != self.admitted_visit.admission.account_key
        ):
            raise SchedulerStoreError("persisted admission account order is invalid")


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
        SQLiteJobStore._require_open(self)
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
        connection = SQLiteJobStore._require_open(self)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if type(version) is not int or version not in (0, 1, _SCHEMA_VERSION):
            raise SchedulerStoreError("scheduler database schema version is unsupported")
        jobs_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_jobs'"
        ).fetchone()
        visits_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_visits'"
        ).fetchone()
        if version == 0 and (jobs_schema is not None or visits_schema is not None):
            raise SchedulerStoreError("unversioned scheduler database schema already exists")
        if version in (1, _SCHEMA_VERSION) and jobs_schema is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        if version == 1 and visits_schema is not None:
            raise SchedulerStoreError("version one scheduler database schema is noncanonical")
        if version == _SCHEMA_VERSION and visits_schema is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        initial_manifest = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        if version == 0 and initial_manifest:
            raise SchedulerStoreError("unversioned scheduler database schema is not empty")

        def normalize(sql: str) -> str:
            return (
                " ".join(sql.rstrip(";").split())
                .replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)
            )

        if version in (1, _SCHEMA_VERSION):
            if type(jobs_schema[0]) is not str or normalize(jobs_schema[0]) != normalize(
                _CREATE_JOBS_SQL
            ):
                raise SchedulerStoreError("scheduler database schema is noncanonical")
            expected_manifest = [("table", "scheduler_jobs", "scheduler_jobs")]
            if version == _SCHEMA_VERSION:
                if type(visits_schema[0]) is not str or normalize(
                    visits_schema[0]
                ) != normalize(_CREATE_VISITS_SQL):
                    raise SchedulerStoreError("scheduler database schema is noncanonical")
                expected_manifest.append(
                    ("table", "scheduler_visits", "scheduler_visits")
                )
            if [tuple(row) for row in initial_manifest] != expected_manifest:
                raise SchedulerStoreError("scheduler database schema has unexpected objects")
            if version == 1 and connection.execute(
                "SELECT 1 FROM scheduler_jobs WHERE state = 'ADMITTED' LIMIT 1"
            ).fetchone() is not None:
                raise SchedulerStoreError(
                    "version one admitted job has no durable admission record"
                )

        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        if str(journal_mode).lower() != "wal":
            raise SchedulerStoreError("scheduler database WAL mode is unavailable")
        connection.executescript(
            f"""
            BEGIN IMMEDIATE;
            {_CREATE_JOBS_SQL};
            {_CREATE_VISITS_SQL};
            PRAGMA user_version = 2;
            COMMIT;
            """
        )
        stored = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_jobs'"
        ).fetchone()
        if stored is None or type(stored[0]) is not str:
            raise SchedulerStoreError("scheduler database schema is missing")
        if normalize(stored[0]) != normalize(_CREATE_JOBS_SQL):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        stored_visits = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_visits'"
        ).fetchone()
        if (
            stored_visits is None
            or type(stored_visits[0]) is not str
            or normalize(stored_visits[0]) != normalize(_CREATE_VISITS_SQL)
        ):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        manifest = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        if [tuple(row) for row in manifest] != [
            ("table", "scheduler_jobs", "scheduler_jobs"),
            ("table", "scheduler_visits", "scheduler_visits"),
        ]:
            raise SchedulerStoreError("scheduler database schema has unexpected objects")
        SQLiteJobStore._validate_admission_integrity(self)

    def _validate_admission_integrity(self) -> None:
        connection = SQLiteJobStore._require_open(self)
        try:
            job_rows = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE state = 'ADMITTED'"
            ).fetchall()
            visit_rows = connection.execute("SELECT * FROM scheduler_visits").fetchall()
            if len(job_rows) != len(visit_rows) or len(job_rows) > 1:
                raise SchedulerStoreError("scheduler admission integrity is invalid")
            if job_rows:
                job = SQLiteJobStore._decode(job_rows[0])
                record = SQLiteJobStore._decode_admission_record(visit_rows[0])
                if job != record.admitted_visit.job:
                    raise SchedulerStoreError("scheduler admission integrity is invalid")
        except SchedulerStoreError as exc:
            if str(exc) == "scheduler admission integrity is invalid":
                raise
            raise SchedulerStoreError("scheduler admission integrity is invalid") from exc
        except BaseException as exc:
            raise SchedulerStoreError("scheduler admission integrity is invalid") from exc

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

    @staticmethod
    def _encode_account_order(account_order: tuple[AccountKey, ...]) -> str:
        return json.dumps(
            [key.value for key in account_order],
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
            SQLiteJobStore._encode_yield_set(job),
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
        job = SQLiteJobStore._require_job(job)
        if job.state is QueueState.ADMITTED:
            raise SchedulerStoreError("ADMITTED jobs require atomic admission")
        connection = SQLiteJobStore._require_open(self)
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
                SQLiteJobStore._values(job),
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
        expected = SQLiteJobStore._require_job(expected)
        replacement = SQLiteJobStore._require_job(replacement)
        if replacement.state is QueueState.ADMITTED:
            raise SchedulerStoreError("ADMITTED jobs require atomic admission")
        if expected.state is QueueState.ADMITTED:
            raise SchedulerStoreError("ADMITTED jobs require atomic finalization")
        if (
            replacement.account_key != expected.account_key
            or replacement.configuration_generation != expected.configuration_generation
        ):
            raise SchedulerStoreError("replacement changed account or configuration identity")
        if replacement.job_generation.value < expected.job_generation.value:
            raise SchedulerStoreError("replacement job generation regressed")

        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM scheduler_jobs
                   WHERE account_key = ? AND configuration_generation = ?""",
                (expected.account_key.value, expected.configuration_generation.value),
            ).fetchone()
            if row is None or SQLiteJobStore._decode(row) != expected:
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
                    SQLiteJobStore._encode_yield_set(replacement),
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

    def admit_oldest(
        self,
        *,
        ready: ReadyV2,
        now: float,
        account_order: tuple[AccountKey, ...],
    ) -> AdmittedVisit | None:
        """Atomically select and persist exactly one currently eligible visit."""
        if type(ready) is not ReadyV2:
            raise SchedulerStoreError("admission requires an exact schema-v2 READY state")
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM scheduler_visits LIMIT 1"
            ).fetchone() is not None:
                raise SchedulerStoreError("an open admission already exists")
            rows = connection.execute(
                """SELECT * FROM scheduler_jobs
                   WHERE configuration_generation = ?
                   ORDER BY account_key""",
                (ready.configuration_generation.value,),
            ).fetchall()
            selected = select_oldest_eligible(
                tuple(SQLiteJobStore._decode(row) for row in rows),
                now=now,
                account_order=account_order,
                tie_after=ready.tie_after_account_key,
            )
            if selected is None:
                connection.execute("COMMIT")
                return None

            raw_generation = connection.execute(
                "SELECT COALESCE(MAX(visit_generation), 0) + 1 FROM scheduler_visits"
            ).fetchone()[0]
            if type(raw_generation) is not int or not 1 <= raw_generation <= 2**63 - 1:
                raise SchedulerStoreError("visit generation is exhausted")
            visit = admit(
                selected,
                visit_generation=VisitGeneration(raw_generation),
                visit_nonce=secrets.token_hex(16),
                now=now,
            )
            slot_index = account_order.index(selected.account_key)
            connection.execute(
                """INSERT INTO scheduler_visits (
                       visit_generation, visit_nonce, account_key,
                       configuration_generation, job_generation, slot_index,
                       account_order, availability_event, available_at, due_at,
                       pending_lane
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    visit.visit_generation.value,
                    visit.visit_nonce,
                    selected.account_key.value,
                    selected.configuration_generation.value,
                    selected.job_generation.value,
                    slot_index,
                    SQLiteJobStore._encode_account_order(account_order),
                    selected.availability_event.value,
                    selected.available_at,
                    selected.due_at,
                    selected.pending_lane.value,
                ),
            )
            updated = connection.execute(
                """UPDATE scheduler_jobs SET state = 'ADMITTED'
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ? AND state = 'ELIGIBLE'""",
                (
                    selected.account_key.value,
                    selected.configuration_generation.value,
                    selected.job_generation.value,
                ),
            )
            if updated.rowcount != 1:
                raise SchedulerStoreError("selected scheduler job changed during admission")
            connection.execute("COMMIT")
            return visit
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler admission failed") from exc

    @staticmethod
    def _decode_admission_record(row: sqlite3.Row) -> PersistedAdmission:
        try:
            raw_order = json.loads(row["account_order"])
            if type(raw_order) is not list or any(
                type(value) is not str for value in raw_order
            ):
                raise ValueError("account order is malformed")
            account_order = tuple(AccountKey(value) for value in raw_order)
            if SQLiteJobStore._encode_account_order(account_order) != row["account_order"]:
                raise ValueError("account order is noncanonical")
            job = QueueJob(
                account_key=AccountKey(row["account_key"]),
                configuration_generation=ConfigurationGeneration(
                    row["configuration_generation"]
                ),
                job_generation=JobGeneration(row["job_generation"]),
                availability_event=AvailabilityEvent(row["availability_event"]),
                available_at=row["available_at"],
                due_at=row["due_at"],
                pending_lane=Lane(row["pending_lane"]),
                state=QueueState.ADMITTED,
            )
            admission = VisitAdmission(
                job.configuration_generation,
                job.account_key,
                job.job_generation,
                VisitGeneration(row["visit_generation"]),
                row["visit_nonce"],
            )
            return PersistedAdmission(
                AdmittedVisit(job, admission),
                row["slot_index"],
                account_order,
            )
        except (KeyError, TypeError, ValueError, SchedulerStoreError) as exc:
            raise SchedulerStoreError("invalid persisted admission") from exc

    @staticmethod
    def _decode_admitted_visit(row: sqlite3.Row) -> AdmittedVisit:
        return SQLiteJobStore._decode_admission_record(row).admitted_visit

    def load_admission_records(self) -> tuple[PersistedAdmission, ...]:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                "SELECT * FROM scheduler_visits ORDER BY visit_generation"
            ).fetchall()
            return tuple(SQLiteJobStore._decode_admission_record(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler admissions could not be loaded") from exc

    def load_admitted_visits(self) -> tuple[AdmittedVisit, ...]:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                "SELECT * FROM scheduler_visits ORDER BY visit_generation"
            ).fetchall()
            return tuple(SQLiteJobStore._decode_admitted_visit(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler admissions could not be loaded") from exc

    def load(self, configuration_generation: ConfigurationGeneration) -> tuple[QueueJob, ...]:
        if type(configuration_generation) is not ConfigurationGeneration:
            raise SchedulerStoreError("configuration generation must be exact")
        return SQLiteJobStore._load_where(
            self,
            "WHERE configuration_generation = ?",
            (configuration_generation.value,),
        )

    def load_all(self) -> tuple[QueueJob, ...]:
        return SQLiteJobStore._load_where(self, "", ())

    def _load_where(self, clause: str, parameters: tuple[object, ...]) -> tuple[QueueJob, ...]:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                f"""SELECT * FROM scheduler_jobs {clause}
                    ORDER BY configuration_generation, account_key""",
                parameters,
            ).fetchall()
            return tuple(SQLiteJobStore._decode(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler jobs could not be loaded") from exc
