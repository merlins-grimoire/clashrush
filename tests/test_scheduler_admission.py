from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2
from clash_rush_rebuild.lifecycle_v2 import SchemaV2LifecycleSupervisor
from clash_rush_rebuild.scheduler_admission import StartedAdmission, admit_and_start_oldest
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    VisitGeneration,
)
from clash_rush_rebuild.scheduler_store import (
    _CREATE_JOBS_SQL,
    SQLiteJobStore,
    SchedulerStoreError,
)


CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
ACCOUNT_ORDER = (ACCOUNT_A, ACCOUNT_B)
NONCE = "d" * 32
BINDING = PlayerBinding(
    ProcessIdentity(101, 202),
    ProcessIdentity(102, 203),
    303,
    404,
    1600,
    900,
    "e" * 32,
)


def job(account: AccountKey, *, due_at: float, state: QueueState = QueueState.ELIGIBLE) -> QueueJob:
    return QueueJob(
        account_key=account,
        configuration_generation=CONFIGURATION,
        job_generation=JobGeneration(1),
        availability_event=AvailabilityEvent(("a" if account == ACCOUNT_A else "b") * 32),
        available_at=50.0,
        due_at=due_at,
        pending_lane=Lane.HOME,
        state=state,
    )


def inert_lifecycle() -> SchemaV2LifecycleSupervisor:
    return object.__new__(SchemaV2LifecycleSupervisor)


def test_atomic_admission_ignores_cursor_for_non_equal_deadlines_and_binds_generated_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    calls: list[tuple[AccountKey, ConfigurationGeneration, str]] = []
    monkeypatch.setattr("clash_rush_rebuild.scheduler_store.secrets.token_hex", lambda size: NONCE)

    def start(
        self: SchemaV2LifecycleSupervisor,
        account_key: AccountKey,
        generation: ConfigurationGeneration,
        visit_nonce: str,
    ) -> PlayerBinding:
        calls.append((account_key, generation, visit_nonce))
        return BINDING

    monkeypatch.setattr(SchemaV2LifecycleSupervisor, "start_admitted", start)
    ready = ReadyV2(CONFIGURATION, ACCOUNT_B)

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))
        store.insert(job(ACCOUNT_B, due_at=90.0))

        result = admit_and_start_oldest(
            store,
            inert_lifecycle(),
            ready=ready,
            now=100.0,
            account_order=ACCOUNT_ORDER,
        )

        assert result == StartedAdmission(result.admitted_visit, BINDING)
        assert result.admitted_visit.admission.account_key == ACCOUNT_B
        assert result.admitted_visit.visit_generation == VisitGeneration(1)
        assert result.admitted_visit.visit_nonce == NONCE
        assert calls == [(ACCOUNT_B, CONFIGURATION, NONCE)]
        assert store.load_admitted_visits() == (result.admitted_visit,)
        records = store.load_admission_records()
        assert records[0].admitted_visit == result.admitted_visit
        assert records[0].slot_index == 1
        assert records[0].account_order == ACCOUNT_ORDER
        persisted = {item.account_key: item for item in store.load(CONFIGURATION)}
        assert persisted[ACCOUNT_B].state is QueueState.ADMITTED
        assert persisted[ACCOUNT_A].state is QueueState.ELIGIBLE


def test_duplicate_admission_is_impossible_across_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr("clash_rush_rebuild.scheduler_store.secrets.token_hex", lambda size: NONCE)

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))
        store.insert(job(ACCOUNT_B, due_at=100.0))
        first = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        )
        assert first is not None

    with SQLiteJobStore(path) as restarted:
        with pytest.raises(SchedulerStoreError, match="open admission"):
            restarted.admit_oldest(
                ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
                now=100.0,
                account_order=ACCOUNT_ORDER,
            )
        assert restarted.load_admitted_visits() == (first,)


def test_cursor_cannot_make_future_or_noneligible_job_admissible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    generated: list[int] = []
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: generated.append(size) or NONCE,
    )

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=101.0))
        store.insert(job(ACCOUNT_B, due_at=90.0, state=QueueState.WAITING))
        assert store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        ) is None
        assert generated == []
        assert all(item.state is not QueueState.ADMITTED for item in store.load(CONFIGURATION))
        assert store.load_admitted_visits() == ()


def test_nonce_failure_rolls_back_job_and_visit_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr("clash_rush_rebuild.scheduler_store.secrets.token_hex", lambda size: "A" * 32)

    with SQLiteJobStore(path) as store:
        original = job(ACCOUNT_A, due_at=100.0)
        store.insert(original)
        with pytest.raises(SchedulerStoreError, match="admission failed"):
            store.admit_oldest(
                ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
                now=100.0,
                account_order=(ACCOUNT_A,),
            )
        assert store.load(CONFIGURATION) == (original,)
        assert store.load_admitted_visits() == ()


def test_lifecycle_failure_leaves_one_durable_admission_for_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr("clash_rush_rebuild.scheduler_store.secrets.token_hex", lambda size: NONCE)

    def fail_start(*args: object) -> PlayerBinding:
        raise RuntimeError("synthetic lifecycle failure")

    monkeypatch.setattr(SchemaV2LifecycleSupervisor, "start_admitted", fail_start)

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))
        with pytest.raises(RuntimeError, match="synthetic lifecycle failure"):
            admit_and_start_oldest(
                store,
                inert_lifecycle(),
                ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
                now=100.0,
                account_order=(ACCOUNT_A,),
            )
        admitted = store.load_admitted_visits()
        assert len(admitted) == 1
        assert admitted[0].visit_nonce == NONCE
        assert store.load(CONFIGURATION)[0].state is QueueState.ADMITTED


def test_known_version_one_database_migrates_to_current_without_changing_jobs(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"{_CREATE_JOBS_SQL}; PRAGMA user_version = 1;"
    )
    connection.close()

    with SQLiteJobStore(path) as store:
        assert store.load_all() == ()
        assert store.load_admitted_visits() == ()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (4,)
    connection.close()


def test_public_job_mutations_cannot_create_an_admitted_job_without_a_visit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    eligible = job(ACCOUNT_A, due_at=100.0)
    orphan = QueueJob(
        account_key=eligible.account_key,
        configuration_generation=eligible.configuration_generation,
        job_generation=eligible.job_generation,
        availability_event=eligible.availability_event,
        available_at=eligible.available_at,
        due_at=eligible.due_at,
        pending_lane=eligible.pending_lane,
        state=QueueState.ADMITTED,
    )

    with SQLiteJobStore(path) as store:
        with pytest.raises(SchedulerStoreError, match="atomic admission"):
            store.insert(orphan)
        store.insert(eligible)
        with pytest.raises(SchedulerStoreError, match="atomic admission"):
            store.replace(eligible, orphan)
        assert store.load_all() == (eligible,)
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        with pytest.raises(SchedulerStoreError, match="atomic finalization"):
            store.replace(admitted.job, eligible)
        assert store.load_all() == (admitted.job,)


def test_database_constraint_rejects_a_second_open_visit_row(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None

    connection = sqlite3.connect(path)
    values = (
        2,
        "e" * 32,
        ACCOUNT_B.value,
        CONFIGURATION.value,
        1,
        1,
        '["account-a","account-b"]',
        "b" * 32,
        50.0,
        100.0,
        Lane.HOME.value,
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO scheduler_visits (
                   visit_generation, visit_nonce, account_key,
                   configuration_generation, job_generation, slot_index,
                   account_order, availability_event, available_at, due_at,
                   pending_lane
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    connection.close()


def test_version_one_admitted_orphan_blocks_migration_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(f"{_CREATE_JOBS_SQL}; PRAGMA user_version = 1;")
    admitted = job(ACCOUNT_A, due_at=100.0, state=QueueState.ADMITTED)
    connection.execute(
        """INSERT INTO scheduler_jobs (
               account_key, configuration_generation, job_generation,
               availability_event, available_at, due_at, pending_lane, state,
               reconcile_at, yield_set, prior_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            admitted.account_key.value,
            admitted.configuration_generation.value,
            admitted.job_generation.value,
            admitted.availability_event.value,
            admitted.available_at,
            admitted.due_at,
            admitted.pending_lane.value,
            admitted.state.value,
            None,
            "[]",
            None,
        ),
    )
    connection.commit()
    before = connection.execute(
        "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="admitted job"):
        SQLiteJobStore(path)

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    assert connection.execute(
        "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall() == before
    connection.close()


def test_version_two_admitted_orphan_blocks_reopen(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))

    connection = sqlite3.connect(path)
    connection.execute("UPDATE scheduler_jobs SET state = 'ADMITTED'")
    connection.commit()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="admission integrity"):
        SQLiteJobStore(path)


def test_valid_range_slot_index_tampering_blocks_reopen(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=100.0))
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        )
        assert admitted is not None

    connection = sqlite3.connect(path)
    connection.execute("UPDATE scheduler_visits SET slot_index = 1")
    connection.commit()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="admission integrity"):
        SQLiteJobStore(path)


def test_instance_shadowed_store_helpers_cannot_bypass_atomic_admission(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    eligible = job(ACCOUNT_A, due_at=100.0)

    with SQLiteJobStore(path) as store:
        original_values = SQLiteJobStore._values(eligible)
        poisoned = original_values[:7] + (QueueState.ADMITTED.value,) + original_values[8:]
        store._values = lambda value: poisoned  # type: ignore[method-assign]
        store.insert(eligible)
        del store._values
        assert store.load_all() == (eligible,)
