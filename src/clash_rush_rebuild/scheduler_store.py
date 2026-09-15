"""Crash-durable SQLite persistence for scheduler queue jobs."""

from __future__ import annotations

import hashlib
import json
import math
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
    complete_visit,
    mark_eligible,
    quarantine,
    release_quarantine,
    select_oldest_eligible,
)
from .scheduler_journal import (
    REPLAYABLE_ACTION_STATES,
    TERMINAL_ACTION_STATES,
    ActionState,
    JournalAction,
    SchedulerJournalError,
    require_pristine_action,
    transition_action,
)
from .scheduler_successor import SuccessorKind, SuccessorPlan, SuccessorPlanError


_SCHEMA_VERSION = 5
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
_CREATE_VISITS_V4_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_visits (
    visit_generation INTEGER PRIMARY KEY CHECK(visit_generation > 0),
    open_singleton INTEGER DEFAULT 1 UNIQUE CHECK(open_singleton IS NULL OR open_singleton = 1),
    finalized INTEGER NOT NULL DEFAULT 0 CHECK(finalized IN (0, 1)),
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
    CHECK(
        (open_singleton IS 1 AND finalized = 0)
        OR (open_singleton IS NULL AND finalized = 1)
    ),
    UNIQUE(account_key, configuration_generation, job_generation)
)
"""
_CREATE_ACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_actions (
    action_generation INTEGER PRIMARY KEY CHECK(action_generation > 0),
    action_nonce TEXT NOT NULL UNIQUE CHECK(
        length(action_nonce) = 32 AND action_nonce NOT GLOB '*[^0-9a-f]*'
    ),
    intent_fingerprint TEXT NOT NULL UNIQUE CHECK(
        length(intent_fingerprint) = 64
        AND intent_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    visit_generation INTEGER NOT NULL CHECK(visit_generation > 0),
    visit_nonce TEXT NOT NULL CHECK(
        length(visit_nonce) = 32 AND visit_nonce NOT GLOB '*[^0-9a-f]*'
    ),
    lane TEXT NOT NULL CHECK(lane IN ('HOME', 'BUILDER')),
    lane_snapshot TEXT NOT NULL CHECK(
        lane_snapshot IN ('HOME', 'BUILDER') AND lane_snapshot = lane
    ),
    binding_seal TEXT NOT NULL CHECK(
        length(binding_seal) = 64 AND binding_seal NOT GLOB '*[^0-9a-f]*'
    ),
    open_singleton INTEGER UNIQUE CHECK(open_singleton IS NULL OR open_singleton = 1),
    input_started INTEGER NOT NULL CHECK(input_started IN (0, 1)),
    input_completed INTEGER NOT NULL CHECK(input_completed IN (0, 1)),
    state TEXT NOT NULL CHECK(state IN (
        'PLANNED', 'INTENT_RECORDED', 'INPUT_STARTED', 'INPUT_COMPLETED',
        'CONFIRMED', 'FAILED', 'UNCERTAIN'
    )),
    CHECK(
        (state IN ('PLANNED', 'INTENT_RECORDED')
            AND open_singleton = 1 AND input_started = 0 AND input_completed = 0)
        OR (state = 'INPUT_STARTED'
            AND open_singleton = 1 AND input_started = 1 AND input_completed = 0)
        OR (state = 'INPUT_COMPLETED'
            AND open_singleton = 1 AND input_started = 1 AND input_completed = 1)
        OR (state IN ('CONFIRMED', 'FAILED', 'UNCERTAIN')
            AND open_singleton IS NULL AND input_started = 1)
    )
)
"""

_CREATE_SUCCESSOR_PLANS_V4_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_successor_plans (
    plan_nonce TEXT PRIMARY KEY CHECK(
        length(plan_nonce) = 32 AND plan_nonce NOT GLOB '*[^0-9a-f]*'
    ),
    visit_generation INTEGER NOT NULL UNIQUE CHECK(visit_generation > 0),
    visit_nonce TEXT NOT NULL CHECK(
        length(visit_nonce) = 32 AND visit_nonce NOT GLOB '*[^0-9a-f]*'
    ),
    kind TEXT NOT NULL CHECK(kind IN (
        'IMMEDIATE_FREE', 'FUTURE_COMPLETION', 'RECONCILE_UNKNOWN'
    )),
    offset_seconds INTEGER NOT NULL CHECK(offset_seconds >= 0 AND offset_seconds <= 3600),
    account_key TEXT NOT NULL,
    configuration_generation TEXT NOT NULL,
    job_generation INTEGER NOT NULL CHECK(job_generation > 0),
    availability_event TEXT NOT NULL CHECK(
        length(availability_event) = 32
        AND availability_event NOT GLOB '*[^0-9a-f]*'
    ),
    available_at REAL NOT NULL,
    due_at REAL NOT NULL CHECK(due_at >= available_at),
    pending_lane TEXT NOT NULL CHECK(pending_lane IN ('HOME', 'BUILDER')),
    state TEXT NOT NULL CHECK(state IN ('WAITING', 'DEFERRED')),
    reconcile_at REAL,
    yield_set TEXT NOT NULL CHECK(yield_set = '[]'),
    prior_state TEXT CHECK(prior_state IS NULL),
    binding_seal TEXT NOT NULL CHECK(
        length(binding_seal) = 64 AND binding_seal NOT GLOB '*[^0-9a-f]*'
    ),
    FOREIGN KEY(visit_generation) REFERENCES scheduler_visits(visit_generation)
)
"""

_CREATE_SUCCESSOR_PLANS_SQL = _CREATE_SUCCESSOR_PLANS_V4_SQL.replace(
    "yield_set TEXT NOT NULL CHECK(yield_set = '[]')",
    "yield_set TEXT NOT NULL",
)


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
        if type(version) is not int or version not in (0, 1, 2, 3, 4, _SCHEMA_VERSION):
            raise SchedulerStoreError("scheduler database schema version is unsupported")
        jobs_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_jobs'"
        ).fetchone()
        visits_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_visits'"
        ).fetchone()
        actions_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_actions'"
        ).fetchone()
        successor_plans_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_successor_plans'"
        ).fetchone()
        if version == 0 and any(
            schema is not None
            for schema in (jobs_schema, visits_schema, actions_schema, successor_plans_schema)
        ):
            raise SchedulerStoreError("unversioned scheduler database schema already exists")
        if version in (1, 2, 3, 4, _SCHEMA_VERSION) and jobs_schema is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        if version == 1 and (visits_schema is not None or actions_schema is not None):
            raise SchedulerStoreError("version one scheduler database schema is noncanonical")
        if version in (2, 3, 4, _SCHEMA_VERSION) and visits_schema is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        if version == 2 and actions_schema is not None:
            raise SchedulerStoreError("version two scheduler database schema is noncanonical")
        if version in (3, 4, _SCHEMA_VERSION) and actions_schema is None:
            raise SchedulerStoreError("versioned scheduler database schema is missing")
        if version in (1, 2, 3) and successor_plans_schema is not None:
            raise SchedulerStoreError("older scheduler database schema is noncanonical")
        if version in (4, _SCHEMA_VERSION) and successor_plans_schema is None:
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

        if version in (1, 2, 3, 4, _SCHEMA_VERSION):
            if type(jobs_schema[0]) is not str or normalize(jobs_schema[0]) != normalize(
                _CREATE_JOBS_SQL
            ):
                raise SchedulerStoreError("scheduler database schema is noncanonical")
            expected_manifest = [("table", "scheduler_jobs", "scheduler_jobs")]
            if version in (2, 3, 4, _SCHEMA_VERSION):
                expected_visits_sql = (
                    _CREATE_VISITS_V4_SQL
                    if version in (4, _SCHEMA_VERSION)
                    else _CREATE_VISITS_SQL
                )
                if type(visits_schema[0]) is not str or normalize(
                    visits_schema[0]
                ) != normalize(expected_visits_sql):
                    raise SchedulerStoreError("scheduler database schema is noncanonical")
                expected_manifest.append(
                    ("table", "scheduler_visits", "scheduler_visits")
                )
            if version in (3, 4, _SCHEMA_VERSION):
                if type(actions_schema[0]) is not str or normalize(
                    actions_schema[0]
                ) != normalize(_CREATE_ACTIONS_SQL):
                    raise SchedulerStoreError("scheduler database schema is noncanonical")
                expected_manifest.insert(
                    0, ("table", "scheduler_actions", "scheduler_actions")
                )
            if version in (4, _SCHEMA_VERSION):
                expected_successor_sql = (
                    _CREATE_SUCCESSOR_PLANS_V4_SQL
                    if version == 4
                    else _CREATE_SUCCESSOR_PLANS_SQL
                )
                if type(successor_plans_schema[0]) is not str or normalize(
                    successor_plans_schema[0]
                ) != normalize(expected_successor_sql):
                    raise SchedulerStoreError("scheduler database schema is noncanonical")
                expected_manifest.append(
                    ("table", "scheduler_successor_plans", "scheduler_successor_plans")
                )
            expected_manifest.sort()
            if [tuple(row) for row in initial_manifest] != expected_manifest:
                raise SchedulerStoreError("scheduler database schema has unexpected objects")
            if version == 1 and connection.execute(
                "SELECT 1 FROM scheduler_jobs WHERE state = 'ADMITTED' LIMIT 1"
            ).fetchone() is not None:
                raise SchedulerStoreError(
                    "version one admitted job has no durable admission record"
                )
            if version == 2:
                SQLiteJobStore._validate_admission_integrity(self)
            if version == 3:
                SQLiteJobStore._validate_admission_integrity(self)
                SQLiteJobStore._validate_journal_integrity(self)
            if version == 4:
                SQLiteJobStore._validate_admission_integrity(self)
                SQLiteJobStore._validate_journal_integrity(self)
                SQLiteJobStore._validate_successor_integrity(self)

        if version in (2, 3):
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                ALTER TABLE scheduler_visits RENAME TO scheduler_visits_v3;
                {_CREATE_VISITS_V4_SQL};
                INSERT INTO scheduler_visits (
                    visit_generation, open_singleton, finalized, visit_nonce,
                    account_key, configuration_generation, job_generation,
                    slot_index, account_order, availability_event, available_at,
                    due_at, pending_lane
                )
                SELECT visit_generation, open_singleton, 0, visit_nonce,
                    account_key, configuration_generation, job_generation,
                    slot_index, account_order, availability_event, available_at,
                    due_at, pending_lane
                FROM scheduler_visits_v3;
                DROP TABLE scheduler_visits_v3;
                COMMIT;
                """
            )

        if version == 4:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                ALTER TABLE scheduler_successor_plans
                    RENAME TO scheduler_successor_plans_v4;
                {_CREATE_SUCCESSOR_PLANS_SQL};
                INSERT INTO scheduler_successor_plans
                SELECT * FROM scheduler_successor_plans_v4;
                DROP TABLE scheduler_successor_plans_v4;
                COMMIT;
                """
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
            {_CREATE_VISITS_V4_SQL};
            {_CREATE_ACTIONS_SQL};
            {_CREATE_SUCCESSOR_PLANS_SQL};
            PRAGMA user_version = 5;
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
            or normalize(stored_visits[0]) != normalize(_CREATE_VISITS_V4_SQL)
        ):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        stored_actions = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_actions'"
        ).fetchone()
        if (
            stored_actions is None
            or type(stored_actions[0]) is not str
            or normalize(stored_actions[0]) != normalize(_CREATE_ACTIONS_SQL)
        ):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        stored_successor_plans = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_successor_plans'"
        ).fetchone()
        if (
            stored_successor_plans is None
            or type(stored_successor_plans[0]) is not str
            or normalize(stored_successor_plans[0])
            != normalize(_CREATE_SUCCESSOR_PLANS_SQL)
        ):
            raise SchedulerStoreError("scheduler database schema is noncanonical")
        manifest = connection.execute(
            """SELECT type, name, tbl_name FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()
        if [tuple(row) for row in manifest] != [
            ("table", "scheduler_actions", "scheduler_actions"),
            ("table", "scheduler_jobs", "scheduler_jobs"),
            ("table", "scheduler_successor_plans", "scheduler_successor_plans"),
            ("table", "scheduler_visits", "scheduler_visits"),
        ]:
            raise SchedulerStoreError("scheduler database schema has unexpected objects")
        SQLiteJobStore._validate_admission_integrity(self)
        SQLiteJobStore._validate_journal_integrity(self)
        SQLiteJobStore._validate_successor_integrity(self)

    def _validate_admission_integrity(self) -> None:
        connection = SQLiteJobStore._require_open(self)
        try:
            job_rows = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE state = 'ADMITTED'"
            ).fetchall()
            visit_rows = connection.execute(
                "SELECT * FROM scheduler_visits WHERE open_singleton = 1"
            ).fetchall()
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

    def _validate_journal_integrity(self) -> None:
        connection = SQLiteJobStore._require_open(self)
        try:
            visit_rows = connection.execute("SELECT * FROM scheduler_visits").fetchall()
            visits_by_generation = {
                row["visit_generation"]: row for row in visit_rows
            }
            action_rows = connection.execute(
                "SELECT * FROM scheduler_actions ORDER BY action_generation"
            ).fetchall()
            for row in action_rows:
                action = SQLiteJobStore._decode_action(row)
                terminal = action.state in TERMINAL_ACTION_STATES
                if (
                    terminal != (row["open_singleton"] is None)
                    or row["lane_snapshot"] != action.lane.value
                    or row["binding_seal"]
                    != SQLiteJobStore._action_binding_seal(action)
                ):
                    raise SchedulerStoreError("scheduler journal integrity is invalid")
                bound_visit = visits_by_generation.get(action.visit_generation.value)
                if bound_visit is None or action.visit_nonce != bound_visit["visit_nonce"]:
                    raise SchedulerStoreError("scheduler journal integrity is invalid")
        except SchedulerStoreError as exc:
            if str(exc) == "scheduler journal integrity is invalid":
                raise
            raise SchedulerStoreError("scheduler journal integrity is invalid") from exc
        except BaseException as exc:
            raise SchedulerStoreError("scheduler journal integrity is invalid") from exc

    def _validate_successor_integrity(self) -> None:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                "SELECT * FROM scheduler_successor_plans ORDER BY visit_generation"
            ).fetchall()
            for row in rows:
                plan = SQLiteJobStore._decode_successor_plan(self, row)
                if row["binding_seal"] != SQLiteJobStore._successor_binding_seal(plan):
                    raise SchedulerStoreError("scheduler successor integrity is invalid")
        except SchedulerStoreError as exc:
            if str(exc) == "scheduler successor integrity is invalid":
                raise
            raise SchedulerStoreError("scheduler successor integrity is invalid") from exc
        except BaseException as exc:
            raise SchedulerStoreError("scheduler successor integrity is invalid") from exc

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
                "SELECT 1 FROM scheduler_visits WHERE open_singleton = 1 LIMIT 1"
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

    @staticmethod
    def _require_action(value: object) -> JournalAction:
        try:
            return require_pristine_action(value)
        except SchedulerJournalError as exc:
            raise SchedulerStoreError("scheduler journal requires an exact action") from exc

    @staticmethod
    def _decode_action(row: sqlite3.Row) -> JournalAction:
        try:
            if (
                type(row["input_started"]) is not int
                or row["input_started"] not in (0, 1)
                or type(row["input_completed"]) is not int
                or row["input_completed"] not in (0, 1)
            ):
                raise ValueError("action input markers are malformed")
            return JournalAction(
                action_generation=row["action_generation"],
                action_nonce=row["action_nonce"],
                intent_fingerprint=row["intent_fingerprint"],
                visit_generation=VisitGeneration(row["visit_generation"]),
                visit_nonce=row["visit_nonce"],
                lane=Lane(row["lane"]),
                input_started=bool(row["input_started"]),
                input_completed=bool(row["input_completed"]),
                state=ActionState(row["state"]),
            )
        except (KeyError, TypeError, ValueError, SchedulerJournalError) as exc:
            raise SchedulerStoreError("invalid persisted journal action") from exc

    @staticmethod
    def _action_values(action: JournalAction) -> tuple[object, ...]:
        return (
            action.action_generation,
            action.action_nonce,
            action.intent_fingerprint,
            action.visit_generation.value,
            action.visit_nonce,
            action.lane.value,
            action.lane.value,
            SQLiteJobStore._action_binding_seal(action),
            None if action.state in TERMINAL_ACTION_STATES else 1,
            int(action.input_started),
            int(action.input_completed),
            action.state.value,
        )

    @staticmethod
    def _action_binding_seal(action: JournalAction) -> str:
        action = require_pristine_action(action)
        material = "\0".join(
            (
                "scheduler-action-v1",
                str(action.action_generation),
                action.action_nonce,
                action.intent_fingerprint,
                str(action.visit_generation.value),
                action.visit_nonce,
                action.lane.value,
                "1" if action.input_started else "0",
                "1" if action.input_completed else "0",
                action.state.value,
            )
        ).encode("ascii")
        return hashlib.sha256(material).hexdigest()

    def plan_action(
        self,
        admitted_visit: AdmittedVisit,
        *,
        lane: Lane,
        intent_fingerprint: str,
    ) -> JournalAction:
        """Append one durable PLANNED action bound to the open admitted visit."""
        if type(admitted_visit) is not AdmittedVisit:
            raise SchedulerStoreError("action planning requires an exact admitted visit")
        if type(lane) is not Lane:
            raise SchedulerStoreError("action planning requires an exact lane")
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            visit_row = connection.execute(
                """SELECT * FROM scheduler_visits
                   WHERE visit_generation = ? AND open_singleton = 1""",
                (admitted_visit.visit_generation.value,),
            ).fetchone()
            if (
                visit_row is None
                or SQLiteJobStore._decode_admitted_visit(visit_row) != admitted_visit
            ):
                raise SchedulerStoreError("action visit is not the open admission")
            if connection.execute(
                """SELECT 1 FROM scheduler_successor_plans
                   WHERE visit_generation = ?""",
                (admitted_visit.visit_generation.value,),
            ).fetchone() is not None:
                raise SchedulerStoreError("a successor plan already exists")
            if connection.execute(
                "SELECT 1 FROM scheduler_actions WHERE intent_fingerprint = ?",
                (intent_fingerprint,),
            ).fetchone() is not None:
                raise SchedulerStoreError("action intent is already journaled")
            if connection.execute(
                "SELECT 1 FROM scheduler_actions WHERE open_singleton = 1"
            ).fetchone() is not None:
                raise SchedulerStoreError("a nonterminal action already exists")
            raw_generation = connection.execute(
                "SELECT COALESCE(MAX(action_generation), 0) + 1 FROM scheduler_actions"
            ).fetchone()[0]
            if type(raw_generation) is not int or not 1 <= raw_generation <= 2**63 - 1:
                raise SchedulerStoreError("action generation is exhausted")
            action = JournalAction(
                action_generation=raw_generation,
                action_nonce=secrets.token_hex(16),
                intent_fingerprint=intent_fingerprint,
                visit_generation=admitted_visit.visit_generation,
                visit_nonce=admitted_visit.visit_nonce,
                lane=lane,
                input_started=False,
                input_completed=False,
                state=ActionState.PLANNED,
            )
            connection.execute(
                """INSERT INTO scheduler_actions (
                       action_generation, action_nonce, intent_fingerprint,
                       visit_generation, visit_nonce, lane, lane_snapshot,
                       binding_seal, open_singleton, input_started,
                       input_completed, state
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                SQLiteJobStore._action_values(action),
            )
            connection.execute("COMMIT")
            return action
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler action planning failed") from exc

    def _transition_action(
        self,
        expected: JournalAction,
        *,
        required_state: ActionState,
        next_state: ActionState,
    ) -> JournalAction:
        expected = SQLiteJobStore._require_action(expected)
        try:
            updated_action = transition_action(
                expected,
                next_state=next_state,
            )
        except SchedulerJournalError as exc:
            raise SchedulerStoreError(str(exc)) from exc
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM scheduler_actions WHERE action_generation = ?",
                (expected.action_generation,),
            ).fetchone()
            if row is None or SQLiteJobStore._decode_action(row) != expected:
                raise SchedulerStoreError("stale scheduler action transition")
            visit = connection.execute(
                """SELECT 1 FROM scheduler_visits
                   WHERE visit_generation = ? AND visit_nonce = ?
                     AND open_singleton = 1""",
                (expected.visit_generation.value, expected.visit_nonce),
            ).fetchone()
            if visit is None:
                raise SchedulerStoreError("scheduler action visit is not open")
            changed = connection.execute(
                """UPDATE scheduler_actions
                   SET state = ?, input_started = ?, input_completed = ?,
                       binding_seal = ?
                   WHERE action_generation = ? AND state = ?""",
                (
                    next_state.value,
                    int(updated_action.input_started),
                    int(updated_action.input_completed),
                    SQLiteJobStore._action_binding_seal(updated_action),
                    expected.action_generation,
                    required_state.value,
                ),
            )
            if changed.rowcount != 1:
                raise SchedulerStoreError("stale scheduler action transition")
            connection.execute("COMMIT")
            return updated_action
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler action transition failed") from exc

    def record_action_intent(self, action: JournalAction) -> JournalAction:
        return SQLiteJobStore._transition_action(
            self,
            action,
            required_state=ActionState.PLANNED,
            next_state=ActionState.INTENT_RECORDED,
        )

    def mark_input_started(self, action: JournalAction) -> JournalAction:
        return SQLiteJobStore._transition_action(
            self,
            action,
            required_state=ActionState.INTENT_RECORDED,
            next_state=ActionState.INPUT_STARTED,
        )

    def mark_input_completed(self, action: JournalAction) -> JournalAction:
        return SQLiteJobStore._transition_action(
            self,
            action,
            required_state=ActionState.INPUT_STARTED,
            next_state=ActionState.INPUT_COMPLETED,
        )

    def finalize_action(
        self,
        action: JournalAction,
        *,
        outcome: ActionState,
        pending_lane: Lane,
    ) -> JournalAction:
        """Commit a terminal outcome and the visit's pending lane atomically."""
        action = SQLiteJobStore._require_action(action)
        if type(outcome) is not ActionState or outcome not in TERMINAL_ACTION_STATES:
            raise SchedulerStoreError("action outcome must be terminal")
        if type(pending_lane) is not Lane:
            raise SchedulerStoreError("terminal action pending lane is invalid")
        if action.state not in (ActionState.INPUT_STARTED, ActionState.INPUT_COMPLETED):
            raise SchedulerStoreError(
                f"action is {action.state.value}, expected INPUT_STARTED or INPUT_COMPLETED"
            )
        try:
            finalized = transition_action(
                action,
                next_state=outcome,
            )
        except SchedulerJournalError as exc:
            raise SchedulerStoreError(str(exc)) from exc

        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM scheduler_actions WHERE action_generation = ?",
                (action.action_generation,),
            ).fetchone()
            if row is None or SQLiteJobStore._decode_action(row) != action:
                raise SchedulerStoreError("stale scheduler action finalization")
            visit = connection.execute(
                """SELECT * FROM scheduler_visits
                   WHERE visit_generation = ? AND open_singleton = 1""",
                (action.visit_generation.value,),
            ).fetchone()
            if visit is None or visit["visit_nonce"] != action.visit_nonce:
                raise SchedulerStoreError("terminal action visit is not open")
            changed_action = connection.execute(
                """UPDATE scheduler_actions
                   SET state = ?, open_singleton = NULL,
                       input_started = ?, input_completed = ?, binding_seal = ?
                   WHERE action_generation = ? AND state = ?""",
                (
                    outcome.value,
                    int(finalized.input_started),
                    int(finalized.input_completed),
                    SQLiteJobStore._action_binding_seal(finalized),
                    action.action_generation,
                    action.state.value,
                ),
            )
            changed_visit = connection.execute(
                """UPDATE scheduler_visits SET pending_lane = ?
                   WHERE visit_generation = ? AND visit_nonce = ?""",
                (pending_lane.value, action.visit_generation.value, action.visit_nonce),
            )
            changed_job = connection.execute(
                """UPDATE scheduler_jobs SET pending_lane = ?
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ? AND state = 'ADMITTED'""",
                (
                    pending_lane.value,
                    visit["account_key"],
                    visit["configuration_generation"],
                    visit["job_generation"],
                ),
            )
            if (
                changed_action.rowcount != 1
                or changed_visit.rowcount != 1
                or changed_job.rowcount != 1
            ):
                raise SchedulerStoreError("terminal action atomic update failed")
            connection.execute("COMMIT")
            return finalized
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler action finalization failed") from exc

    def load_actions(self) -> tuple[JournalAction, ...]:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                "SELECT * FROM scheduler_actions ORDER BY action_generation"
            ).fetchall()
            return tuple(SQLiteJobStore._decode_action(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler actions could not be loaded") from exc

    def load_replayable_actions(self) -> tuple[JournalAction, ...]:
        return tuple(
            action
            for action in SQLiteJobStore.load_actions(self)
            if action.state in REPLAYABLE_ACTION_STATES
        )

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
                """SELECT * FROM scheduler_visits
                   WHERE open_singleton = 1 ORDER BY visit_generation"""
            ).fetchall()
            return tuple(SQLiteJobStore._decode_admitted_visit(row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler admissions could not be loaded") from exc

    def release_unstarted_admission(self, admitted_visit: AdmittedVisit) -> QueueJob:
        """Atomically return a pre-ACTIVE admission to its unchanged eligible job."""
        if type(admitted_visit) is not AdmittedVisit:
            raise SchedulerStoreError("unstarted release requires an exact admitted visit")
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            visit_row = connection.execute(
                """SELECT * FROM scheduler_visits
                   WHERE visit_generation = ? AND open_singleton = 1""",
                (admitted_visit.visit_generation.value,),
            ).fetchone()
            if (
                visit_row is None
                or SQLiteJobStore._decode_admitted_visit(visit_row) != admitted_visit
            ):
                raise SchedulerStoreError("unstarted admission is stale")
            if connection.execute(
                """SELECT 1 FROM scheduler_actions WHERE visit_generation = ?
                   UNION ALL
                   SELECT 1 FROM scheduler_successor_plans WHERE visit_generation = ?""",
                (
                    admitted_visit.visit_generation.value,
                    admitted_visit.visit_generation.value,
                ),
            ).fetchone() is not None:
                raise SchedulerStoreError("started visit cannot be released")
            changed_job = connection.execute(
                """UPDATE scheduler_jobs SET state = 'ELIGIBLE'
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ? AND state = 'ADMITTED'""",
                (
                    admitted_visit.job.account_key.value,
                    admitted_visit.job.configuration_generation.value,
                    admitted_visit.job.job_generation.value,
                ),
            )
            deleted_visit = connection.execute(
                """DELETE FROM scheduler_visits
                   WHERE visit_generation = ? AND visit_nonce = ?
                     AND open_singleton = 1 AND finalized = 0""",
                (
                    admitted_visit.visit_generation.value,
                    admitted_visit.visit_nonce,
                ),
            )
            if changed_job.rowcount != 1 or deleted_visit.rowcount != 1:
                raise SchedulerStoreError("unstarted admission release was not atomic")
            connection.execute("COMMIT")
            job = admitted_visit.job
            return QueueJob(
                account_key=job.account_key,
                configuration_generation=job.configuration_generation,
                job_generation=job.job_generation,
                availability_event=job.availability_event,
                available_at=job.available_at,
                due_at=job.due_at,
                pending_lane=job.pending_lane,
                state=QueueState.ELIGIBLE,
            )
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("unstarted admission release failed") from exc

    @staticmethod
    def _require_successor_plan(value: object) -> SuccessorPlan:
        if type(value) is not SuccessorPlan:
            raise SchedulerStoreError("scheduler store requires an exact successor plan")
        try:
            return SuccessorPlan(
                value.admitted_visit,
                value.plan_nonce,
                value.kind,
                value.offset_seconds,
                value.successor,
            )
        except SuccessorPlanError as exc:
            raise SchedulerStoreError("scheduler successor plan is invalid") from exc

    @staticmethod
    def _successor_binding_seal(plan: SuccessorPlan) -> str:
        plan = SQLiteJobStore._require_successor_plan(plan)
        successor = plan.successor
        material = "\0".join(
            (
                "scheduler-successor-v1",
                plan.plan_nonce,
                str(plan.admitted_visit.visit_generation.value),
                plan.admitted_visit.visit_nonce,
                plan.kind.value,
                str(plan.offset_seconds),
                successor.account_key.value,
                successor.configuration_generation.value,
                str(successor.job_generation.value),
                successor.availability_event.value,
                repr(float(successor.available_at)),
                repr(float(successor.due_at)),
                successor.pending_lane.value,
                successor.state.value,
                "" if successor.reconcile_at is None else repr(float(successor.reconcile_at)),
                *(
                    (SQLiteJobStore._encode_yield_set(successor),)
                    if successor.yield_set
                    else ()
                ),
            )
        ).encode("ascii")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _successor_values(plan: SuccessorPlan) -> tuple[object, ...]:
        plan = SQLiteJobStore._require_successor_plan(plan)
        successor = plan.successor
        return (
            plan.plan_nonce,
            plan.admitted_visit.visit_generation.value,
            plan.admitted_visit.visit_nonce,
            plan.kind.value,
            plan.offset_seconds,
            successor.account_key.value,
            successor.configuration_generation.value,
            successor.job_generation.value,
            successor.availability_event.value,
            successor.available_at,
            successor.due_at,
            successor.pending_lane.value,
            successor.state.value,
            successor.reconcile_at,
            SQLiteJobStore._encode_yield_set(successor),
            None,
            SQLiteJobStore._successor_binding_seal(plan),
        )

    @staticmethod
    def _decode_successor_plan(store: SQLiteJobStore, row: sqlite3.Row) -> SuccessorPlan:
        try:
            connection = SQLiteJobStore._require_open(store)
            visit_row = connection.execute(
                "SELECT * FROM scheduler_visits WHERE visit_generation = ?",
                (row["visit_generation"],),
            ).fetchone()
            if visit_row is None or visit_row["visit_nonce"] != row["visit_nonce"]:
                raise ValueError("successor visit binding is invalid")
            admitted = SQLiteJobStore._decode_admitted_visit(visit_row)
            successor = SQLiteJobStore._decode(row)
            return SuccessorPlan(
                admitted_visit=admitted,
                plan_nonce=row["plan_nonce"],
                kind=SuccessorKind(row["kind"]),
                offset_seconds=row["offset_seconds"],
                successor=successor,
            )
        except (KeyError, TypeError, ValueError, SuccessorPlanError) as exc:
            raise SchedulerStoreError("invalid persisted successor plan") from exc

    @staticmethod
    def _draw_offset(ceiling: int) -> int:
        raw = secrets.randbelow(ceiling)
        if type(raw) is not int or not 0 <= raw < ceiling:
            raise SchedulerStoreError("successor random offset is invalid")
        return raw

    def plan_successor(
        self,
        admitted_visit: AdmittedVisit,
        *,
        kind: SuccessorKind,
        observed_at: float,
        pending_lane: Lane,
        future_completion_at: float | None = None,
    ) -> SuccessorPlan:
        """Persist exactly one successor decision for the open visit."""
        if type(admitted_visit) is not AdmittedVisit:
            raise SchedulerStoreError("successor planning requires an exact admitted visit")
        if type(kind) is not SuccessorKind or type(pending_lane) is not Lane:
            raise SchedulerStoreError("successor planning request is invalid")
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            raise SchedulerStoreError("successor planning request is invalid")
        if future_completion_at is not None and (
            type(future_completion_at) not in (int, float)
            or not math.isfinite(future_completion_at)
        ):
            raise SchedulerStoreError("successor planning request is invalid")
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            visit_row = connection.execute(
                """SELECT * FROM scheduler_visits
                   WHERE visit_generation = ? AND open_singleton = 1""",
                (admitted_visit.visit_generation.value,),
            ).fetchone()
            if (
                visit_row is None
                or SQLiteJobStore._decode_admitted_visit(visit_row) != admitted_visit
            ):
                raise SchedulerStoreError("successor visit is not the open admission")
            if connection.execute(
                "SELECT 1 FROM scheduler_actions WHERE open_singleton = 1"
            ).fetchone() is not None:
                raise SchedulerStoreError("nonterminal action blocks successor planning")
            if connection.execute(
                """SELECT 1 FROM scheduler_successor_plans
                   WHERE visit_generation = ?""",
                (admitted_visit.visit_generation.value,),
            ).fetchone() is not None:
                raise SchedulerStoreError("a successor plan already exists")

            observed = float(observed_at)
            if kind is SuccessorKind.IMMEDIATE_FREE:
                if future_completion_at is not None:
                    raise ValueError("immediate successor cannot have a completion time")
                available_at = observed
                offset_seconds = 0
                due_at = observed
            elif kind is SuccessorKind.FUTURE_COMPLETION:
                if future_completion_at is None:
                    raise ValueError("future successor requires a completion time")
                available_at = float(future_completion_at)
                if available_at <= observed:
                    raise ValueError("future completion must follow observation")
                offset_seconds = SQLiteJobStore._draw_offset(3601)
                due_at = available_at + offset_seconds
            else:
                if future_completion_at is not None:
                    raise ValueError("unknown successor cannot have a completion time")
                available_at = observed
                offset_seconds = 300 + SQLiteJobStore._draw_offset(3301)
                due_at = observed
            yield_set = ()
            if kind is SuccessorKind.RECONCILE_UNKNOWN:
                peer_rows = connection.execute(
                    """SELECT * FROM scheduler_jobs
                       WHERE configuration_generation = ? AND state = 'ELIGIBLE'
                         AND due_at <= ?
                       ORDER BY account_key""",
                    (admitted_visit.configuration_generation.value, observed),
                ).fetchall()
                yield_set = tuple(
                    YieldToken(peer.account_key, peer.job_generation)
                    for peer in (
                        SQLiteJobStore._decode(peer_row) for peer_row in peer_rows
                    )
                    if peer.account_key != admitted_visit.job.account_key
                )
            next_state = (
                QueueState.DEFERRED
                if kind is SuccessorKind.RECONCILE_UNKNOWN
                else QueueState.WAITING
            )
            successor = complete_visit(
                admitted_visit,
                next_generation=JobGeneration(admitted_visit.job_generation.value + 1),
                availability_event=AvailabilityEvent(secrets.token_hex(16)),
                available_at=available_at,
                due_at=due_at,
                pending_lane=pending_lane,
                next_state=next_state,
                reconcile_at=(
                    available_at + offset_seconds
                    if kind is SuccessorKind.RECONCILE_UNKNOWN
                    else None
                ),
                yield_set=yield_set,
            )
            plan = SuccessorPlan(
                admitted_visit=admitted_visit,
                plan_nonce=secrets.token_hex(16),
                kind=kind,
                offset_seconds=offset_seconds,
                successor=successor,
            )
            connection.execute(
                """INSERT INTO scheduler_successor_plans (
                       plan_nonce, visit_generation, visit_nonce, kind,
                       offset_seconds, account_key, configuration_generation,
                       job_generation, availability_event, available_at, due_at,
                       pending_lane, state, reconcile_at, yield_set, prior_state,
                       binding_seal
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                SQLiteJobStore._successor_values(plan),
            )
            connection.execute("COMMIT")
            return plan
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            if isinstance(exc, (TypeError, ValueError, SuccessorPlanError)):
                raise SchedulerStoreError("successor planning request is invalid") from exc
            raise SchedulerStoreError("scheduler successor planning failed") from exc

    def finalize_successor(self, plan: SuccessorPlan) -> QueueJob:
        """Atomically consume one admitted generation from its persisted plan."""
        plan = SQLiteJobStore._require_successor_plan(plan)
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan_row = connection.execute(
                """SELECT * FROM scheduler_successor_plans
                   WHERE visit_generation = ?""",
                (plan.admitted_visit.visit_generation.value,),
            ).fetchone()
            if (
                plan_row is None
                or SQLiteJobStore._decode_successor_plan(self, plan_row) != plan
                or plan_row["binding_seal"] != SQLiteJobStore._successor_binding_seal(plan)
            ):
                raise SchedulerStoreError("stale scheduler successor finalization")
            visit_row = connection.execute(
                "SELECT * FROM scheduler_visits WHERE visit_generation = ?",
                (plan.admitted_visit.visit_generation.value,),
            ).fetchone()
            current_row = connection.execute(
                """SELECT * FROM scheduler_jobs
                   WHERE account_key = ? AND configuration_generation = ?""",
                (
                    plan.successor.account_key.value,
                    plan.successor.configuration_generation.value,
                ),
            ).fetchone()
            if visit_row is None or current_row is None:
                raise SchedulerStoreError("stale scheduler successor finalization")
            current = SQLiteJobStore._decode(current_row)
            if visit_row["finalized"] == 1 and visit_row["open_singleton"] is None:
                successor = plan.successor
                if current.job_generation.value < successor.job_generation.value or (
                    current.job_generation == successor.job_generation
                    and (
                        current.account_key != successor.account_key
                        or current.configuration_generation
                        != successor.configuration_generation
                        or current.availability_event != successor.availability_event
                        or current.available_at != successor.available_at
                        or current.due_at != successor.due_at
                        or current.pending_lane is not successor.pending_lane
                    )
                ):
                    raise SchedulerStoreError("stale scheduler successor finalization")
                connection.execute("COMMIT")
                return plan.successor
            if (
                visit_row["finalized"] != 0
                or visit_row["open_singleton"] != 1
                or SQLiteJobStore._decode_admitted_visit(visit_row)
                != plan.admitted_visit
                or current != plan.admitted_visit.job
            ):
                raise SchedulerStoreError("stale scheduler successor finalization")
            if connection.execute(
                "SELECT 1 FROM scheduler_actions WHERE open_singleton = 1"
            ).fetchone() is not None:
                raise SchedulerStoreError("nonterminal action blocks successor finalization")

            successor = plan.successor
            changed_job = connection.execute(
                """UPDATE scheduler_jobs SET
                       job_generation = ?, availability_event = ?, available_at = ?,
                       due_at = ?, pending_lane = ?, state = ?, reconcile_at = ?,
                       yield_set = ?, prior_state = ?
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ? AND state = 'ADMITTED'""",
                (
                    successor.job_generation.value,
                    successor.availability_event.value,
                    successor.available_at,
                    successor.due_at,
                    successor.pending_lane.value,
                    successor.state.value,
                    successor.reconcile_at,
                    SQLiteJobStore._encode_yield_set(successor),
                    None,
                    successor.account_key.value,
                    successor.configuration_generation.value,
                    plan.admitted_visit.job_generation.value,
                ),
            )
            changed_visit = connection.execute(
                """UPDATE scheduler_visits
                   SET open_singleton = NULL, finalized = 1
                   WHERE visit_generation = ? AND visit_nonce = ?
                     AND open_singleton = 1 AND finalized = 0""",
                (
                    plan.admitted_visit.visit_generation.value,
                    plan.admitted_visit.visit_nonce,
                ),
            )
            if changed_job.rowcount != 1 or changed_visit.rowcount != 1:
                raise SchedulerStoreError("scheduler successor atomic finalization failed")
            connection.execute("COMMIT")
            return successor
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler successor finalization failed") from exc

    def load_successor_plans(self) -> tuple[SuccessorPlan, ...]:
        connection = SQLiteJobStore._require_open(self)
        try:
            rows = connection.execute(
                "SELECT * FROM scheduler_successor_plans ORDER BY visit_generation"
            ).fetchall()
            return tuple(SQLiteJobStore._decode_successor_plan(self, row) for row in rows)
        except SchedulerStoreError:
            raise
        except BaseException as exc:
            raise SchedulerStoreError("scheduler successor plans could not be loaded") from exc

    def load_successor_plan(self, admitted_visit: AdmittedVisit) -> SuccessorPlan | None:
        if type(admitted_visit) is not AdmittedVisit:
            raise SchedulerStoreError("successor lookup requires an exact admitted visit")
        connection = SQLiteJobStore._require_open(self)
        row = connection.execute(
            "SELECT * FROM scheduler_successor_plans WHERE visit_generation = ?",
            (admitted_visit.visit_generation.value,),
        ).fetchone()
        if row is None:
            return None
        plan = SQLiteJobStore._decode_successor_plan(self, row)
        if plan.admitted_visit != admitted_visit:
            raise SchedulerStoreError("successor plan visit does not match")
        return plan

    def quarantine_current(self, expected: QueueJob) -> QueueJob:
        """Persist one explicit account quarantine without changing its due work."""
        expected = SQLiteJobStore._require_job(expected)
        try:
            replacement = quarantine(expected)
        except ValueError as exc:
            raise SchedulerStoreError("scheduler job cannot be quarantined") from exc
        return SQLiteJobStore.replace(self, expected, replacement)

    def release_quarantined(self, expected: QueueJob) -> QueueJob:
        """Restore exactly the state preserved by one durable quarantine."""
        expected = SQLiteJobStore._require_job(expected)
        try:
            replacement = release_quarantine(expected)
        except ValueError as exc:
            raise SchedulerStoreError("scheduler quarantine cannot be released") from exc
        return SQLiteJobStore.replace(self, expected, replacement)

    def refresh_due(
        self,
        configuration_generation: ConfigurationGeneration,
        *,
        now: float,
    ) -> tuple[QueueJob, ...]:
        """Atomically promote timed work whose persisted fairness barrier is clear."""
        if type(configuration_generation) is not ConfigurationGeneration:
            raise SchedulerStoreError("configuration generation must be exact")
        if type(now) not in (int, float) or not math.isfinite(now):
            raise SchedulerStoreError("scheduler refresh time must be finite")
        connection = SQLiteJobStore._require_open(self)
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM scheduler_jobs
                   WHERE configuration_generation = ? ORDER BY account_key""",
                (configuration_generation.value,),
            ).fetchall()
            jobs = tuple(SQLiteJobStore._decode(row) for row in rows)
            current_by_account = {item.account_key: item for item in jobs}
            finalized = {
                (AccountKey(row["account_key"]), JobGeneration(row["job_generation"]))
                for row in connection.execute(
                    """SELECT account_key, job_generation FROM scheduler_visits
                       WHERE configuration_generation = ? AND finalized = 1""",
                    (configuration_generation.value,),
                ).fetchall()
            }
            promoted: list[tuple[QueueJob, QueueJob]] = []
            for item in jobs:
                if item.state is QueueState.WAITING and now >= item.due_at:
                    promoted.append((item, mark_eligible(item, now=now)))
                    continue
                if item.state is not QueueState.DEFERRED or now < item.reconcile_at:
                    continue
                unresolved = []
                for token in item.yield_set:
                    peer = current_by_account.get(token.account_key)
                    terminal_visit = (token.account_key, token.job_generation) in finalized
                    independently_inactive = (
                        peer is not None
                        and peer.job_generation == token.job_generation
                        and peer.state in (QueueState.WAITING, QueueState.QUARANTINED)
                    )
                    if not terminal_visit and not independently_inactive:
                        unresolved.append(token)
                if not unresolved:
                    promoted.append((item, mark_eligible(item, now=now)))

            for expected, replacement in promoted:
                changed = connection.execute(
                    """UPDATE scheduler_jobs SET state = ?, reconcile_at = NULL,
                           yield_set = '[]', prior_state = NULL
                       WHERE account_key = ? AND configuration_generation = ?
                         AND job_generation = ? AND state = ?""",
                    (
                        replacement.state.value,
                        expected.account_key.value,
                        expected.configuration_generation.value,
                        expected.job_generation.value,
                        expected.state.value,
                    ),
                )
                if changed.rowcount != 1:
                    raise SchedulerStoreError("scheduler due refresh changed concurrently")
            connection.execute("COMMIT")
            return tuple(replacement for _, replacement in promoted)
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError("scheduler due refresh failed") from exc

    def next_wake_at(
        self,
        configuration_generation: ConfigurationGeneration,
        *,
        now: float,
    ) -> float | None:
        """Return the next persisted timed boundary; quarantines never wake a run."""
        if type(configuration_generation) is not ConfigurationGeneration:
            raise SchedulerStoreError("configuration generation must be exact")
        if type(now) not in (int, float) or not math.isfinite(now):
            raise SchedulerStoreError("scheduler wake time must be finite")
        jobs = SQLiteJobStore.load(self, configuration_generation)
        if any(
            item.state is QueueState.DEFERRED
            and item.reconcile_at is not None
            and item.reconcile_at <= now
            for item in jobs
        ):
            raise SchedulerStoreError(
                "overdue deferred fairness barrier requires intervention"
            )
        candidates = tuple(
            item.due_at
            if item.state is QueueState.WAITING
            else item.reconcile_at
            for item in jobs
            if item.state in (QueueState.WAITING, QueueState.DEFERRED)
            and (
                (item.state is QueueState.WAITING and item.due_at > now)
                or (
                    item.state is QueueState.DEFERRED
                    and item.reconcile_at is not None
                    and item.reconcile_at > now
                )
            )
        )
        return None if not candidates else min(candidates)

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
