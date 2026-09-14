from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    YieldToken,
    defer,
    quarantine,
)
from clash_rush_rebuild.scheduler_store import (
    _CREATE_JOBS_SQL,
    SQLiteJobStore,
    SchedulerStoreError,
)


CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")


def job(
    account: AccountKey = ACCOUNT_A,
    *,
    generation: int = 1,
    due_at: float = 100.0,
    lane: Lane = Lane.HOME,
    state: QueueState = QueueState.WAITING,
) -> QueueJob:
    return QueueJob(
        account_key=account,
        configuration_generation=CONFIGURATION,
        job_generation=JobGeneration(generation),
        availability_event=AvailabilityEvent("a" * 32),
        available_at=50.0,
        due_at=due_at,
        pending_lane=lane,
        state=state,
    )


def reopen(path: Path) -> SQLiteJobStore:
    return SQLiteJobStore(path)


def test_restart_preserves_deadline_pending_lane_and_quarantine_state(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    quarantined = quarantine(
        defer(
            replace(job(lane=Lane.BUILDER), state=QueueState.ELIGIBLE),
            now=100.0,
            reconcile_at=400.0,
            yield_set=(YieldToken(ACCOUNT_B, JobGeneration(7)),),
        )
    )

    with SQLiteJobStore(path) as store:
        store.insert(quarantined)

    with reopen(path) as store:
        assert store.load_all() == (quarantined,)


def test_store_enforces_one_current_job_and_unique_job_identity(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    original = job()
    with SQLiteJobStore(path) as store:
        store.insert(original)
        with pytest.raises(SchedulerStoreError, match="already exists"):
            store.insert(original)
        with pytest.raises(SchedulerStoreError, match="current job"):
            store.insert(job(generation=2))
        assert store.load_all() == (original,)


def test_replace_is_atomic_generation_advance_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    original = job(lane=Lane.HOME)
    successor = job(generation=2, due_at=250.0, lane=Lane.BUILDER)

    with SQLiteJobStore(path) as store:
        store.insert(original)
        assert store.replace(original, successor) == successor

    with reopen(path) as store:
        assert store.load_all() == (successor,)
        with pytest.raises(SchedulerStoreError, match="stale"):
            store.replace(original, job(generation=3))
        assert store.load_all() == (successor,)


def test_replace_supports_same_identity_state_transition(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    original = job()
    quarantined = quarantine(original)

    with SQLiteJobStore(path) as store:
        store.insert(original)
        store.replace(original, quarantined)
        assert store.load_all() == (quarantined,)


def test_load_filters_exact_configuration_generation(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    other_configuration = ConfigurationGeneration("f" * 32)
    other = replace(job(ACCOUNT_B), configuration_generation=other_configuration)

    with SQLiteJobStore(path) as store:
        store.insert(job())
        store.insert(other)
        assert store.load(CONFIGURATION) == (job(),)
        assert store.load(other_configuration) == (other,)


def test_store_rejects_non_exact_jobs_without_touching_database(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    fake = type("FakeQueueJob", (), {})()

    with SQLiteJobStore(path) as store:
        with pytest.raises(SchedulerStoreError, match="exact QueueJob"):
            store.insert(fake)  # type: ignore[arg-type]
        assert store.load_all() == ()


def test_corrupt_persisted_enum_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        store.insert(job())

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute("UPDATE scheduler_jobs SET pending_lane = 'UNKNOWN'")
    connection.commit()
    connection.close()

    with reopen(path) as store:
        with pytest.raises(SchedulerStoreError, match="invalid persisted job"):
            store.load_all()


@pytest.mark.parametrize("version", [0, 1])
def test_existing_noncanonical_schema_is_never_adopted(tmp_path: Path, version: int) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
        CREATE TABLE scheduler_jobs (
            account_key TEXT,
            configuration_generation TEXT,
            job_generation INTEGER,
            availability_event TEXT,
            available_at REAL,
            due_at REAL,
            pending_lane TEXT,
            state TEXT,
            reconcile_at REAL,
            yield_set TEXT,
            prior_state TEXT
        );
        PRAGMA user_version = {version};
        """
    )
    connection.close()

    with pytest.raises(SchedulerStoreError, match="schema"):
        SQLiteJobStore(path)


def test_rejected_foreign_version_zero_schema_is_not_mutated(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    before_version = connection.execute("PRAGMA user_version").fetchone()
    before_schema = connection.execute(
        """SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%'
           ORDER BY type, name, tbl_name"""
    ).fetchall()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="schema"):
        SQLiteJobStore(path)

    connection = sqlite3.connect(path)
    after_version = connection.execute("PRAGMA user_version").fetchone()
    after_schema = connection.execute(
        """SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%'
           ORDER BY type, name, tbl_name"""
    ).fetchall()
    connection.close()
    assert after_version == before_version
    assert after_schema == before_schema


def test_schema_validation_preserves_case_sensitive_check_literals(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    malformed = _CREATE_JOBS_SQL.replace("'HOME', 'BUILDER'", "'home', 'builder'")
    connection = sqlite3.connect(path)
    connection.executescript(f"{malformed}; PRAGMA user_version = 1;")
    connection.close()

    with pytest.raises(SchedulerStoreError, match="schema"):
        SQLiteJobStore(path)


def test_versioned_database_cannot_recreate_a_missing_jobs_table(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    with pytest.raises(SchedulerStoreError, match="schema"):
        SQLiteJobStore(path)


def test_schema_rejects_triggers_that_can_undo_durable_writes(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
        {_CREATE_JOBS_SQL};
        CREATE TRIGGER erase_scheduler_job
        AFTER INSERT ON scheduler_jobs
        BEGIN
            DELETE FROM scheduler_jobs;
        END;
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    with pytest.raises(SchedulerStoreError, match="schema"):
        SQLiteJobStore(path)
