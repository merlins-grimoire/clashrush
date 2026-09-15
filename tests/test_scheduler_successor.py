from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
)
from clash_rush_rebuild.scheduler_journal import ActionState
from clash_rush_rebuild.scheduler_store import (
    _CREATE_ACTIONS_SQL,
    _CREATE_JOBS_SQL,
    _CREATE_SUCCESSOR_PLANS_V4_SQL,
    _CREATE_VISITS_SQL,
    _CREATE_VISITS_V4_SQL,
    SQLiteJobStore,
    SchedulerStoreError,
)
from clash_rush_rebuild.scheduler_successor import SuccessorKind


CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
ACCOUNT_ORDER = (ACCOUNT_A, ACCOUNT_B)
VISIT_NONCE = "d" * 32
PLAN_NONCE = "e" * 32
EVENT_NONCE = "f" * 32


def job(account: AccountKey = ACCOUNT_A, *, due_at: float = 100.0) -> QueueJob:
    return QueueJob(
        account_key=account,
        configuration_generation=CONFIGURATION,
        job_generation=JobGeneration(1),
        availability_event=AvailabilityEvent(("a" if account == ACCOUNT_A else "b") * 32),
        available_at=50.0,
        due_at=due_at,
        pending_lane=Lane.HOME,
        state=QueueState.ELIGIBLE,
    )


def admit(store: SQLiteJobStore) -> object:
    admitted = store.admit_oldest(
        ready=ReadyV2(CONFIGURATION, ACCOUNT_B),
        now=100.0,
        account_order=ACCOUNT_ORDER,
    )
    assert admitted is not None
    return admitted


def test_immediate_free_builder_plan_is_one_durable_fair_tail(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    generated = iter((VISIT_NONCE, PLAN_NONCE, EVENT_NONCE, "1" * 32))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(generated),
    )

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_B, due_at=90.0))
        store.insert(job(ACCOUNT_A, due_at=100.0))
        admitted = admit(store)
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.IMMEDIATE_FREE,
            observed_at=200.0,
            pending_lane=Lane.HOME,
        )
        assert plan.kind is SuccessorKind.IMMEDIATE_FREE
        assert plan.offset_seconds == 0
        assert plan.successor.available_at == 200.0
        assert plan.successor.due_at == 200.0
        assert plan.successor.state is QueueState.WAITING
        assert plan.successor.job_generation == JobGeneration(2)

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_successor_plans() == (plan,)
        persisted = restarted.load_successor_plan(admitted)
        assert persisted == plan
        assert persisted.successor.due_at > job(ACCOUNT_A, due_at=100.0).due_at
        restarted.finalize_successor(plan)
        eligible_successor = QueueJob(
            account_key=plan.successor.account_key,
            configuration_generation=plan.successor.configuration_generation,
            job_generation=plan.successor.job_generation,
            availability_event=plan.successor.availability_event,
            available_at=plan.successor.available_at,
            due_at=plan.successor.due_at,
            pending_lane=plan.successor.pending_lane,
            state=QueueState.ELIGIBLE,
        )
        restarted.replace(plan.successor, eligible_successor)
        selected = restarted.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_B),
            now=200.0,
            account_order=ACCOUNT_ORDER,
        )
        assert selected is not None
        assert selected.admission.account_key == ACCOUNT_A


def test_future_completion_draws_one_inclusive_offset_and_never_redraws(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    generated = iter((VISIT_NONCE, PLAN_NONCE, EVENT_NONCE, "1" * 32, "2" * 32))
    draws: list[int] = []
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(generated),
    )
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.randbelow",
        lambda ceiling: draws.append(ceiling) or 3600,
    )

    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.FUTURE_COMPLETION,
            observed_at=200.0,
            future_completion_at=500.0,
            pending_lane=Lane.BUILDER,
        )
        assert plan.offset_seconds == 3600
        assert plan.successor.available_at == 500.0
        assert plan.successor.due_at == 4100.0
        assert draws == [3601]

        with pytest.raises(SchedulerStoreError, match="already exists"):
            store.plan_successor(
                admitted,
                kind=SuccessorKind.FUTURE_COMPLETION,
                observed_at=201.0,
                future_completion_at=600.0,
                pending_lane=Lane.BUILDER,
            )
        assert draws == [3601]
        assert store.load_successor_plan(admitted) == plan


def test_successor_planning_rejects_non_exact_time_before_persisting(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: VISIT_NONCE,
    )
    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        with pytest.raises(SchedulerStoreError, match="request is invalid"):
            store.plan_successor(
                admitted,
                kind=SuccessorKind.IMMEDIATE_FREE,
                observed_at=True,  # type: ignore[arg-type]
                pending_lane=Lane.HOME,
            )
        assert store.load_successor_plans() == ()


def test_unknown_builder_state_persists_one_bounded_reconciliation_deadline(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    generated = iter((VISIT_NONCE, PLAN_NONCE, EVENT_NONCE))
    draws: list[int] = []
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(generated),
    )
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.randbelow",
        lambda ceiling: draws.append(ceiling) or 3300,
    )

    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.RECONCILE_UNKNOWN,
            observed_at=200.0,
            pending_lane=Lane.BUILDER,
        )
        assert plan.offset_seconds == 3600
        assert plan.successor.state is QueueState.DEFERRED
        assert plan.successor.available_at == 200.0
        assert plan.successor.due_at == 200.0
        assert plan.successor.reconcile_at == 3800.0
        assert draws == [3301]

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_successor_plan(admitted) == plan


def test_unknown_reconciliation_rejects_non_exact_random_draw_without_a_plan(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: VISIT_NONCE,
    )
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.randbelow",
        lambda ceiling: True,
    )
    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        with pytest.raises(SchedulerStoreError, match="random offset"):
            store.plan_successor(
                admitted,
                kind=SuccessorKind.RECONCILE_UNKNOWN,
                observed_at=200.0,
                pending_lane=Lane.HOME,
            )
        assert store.load_successor_plans() == ()


def test_successor_finalization_is_atomic_idempotent_and_releases_next_admission(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    generated = iter((VISIT_NONCE, PLAN_NONCE, EVENT_NONCE, "1" * 32))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(generated),
    )

    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.IMMEDIATE_FREE,
            observed_at=200.0,
            pending_lane=Lane.HOME,
        )
        connection = store._require_open()
        connection.execute(
            """CREATE TEMP TRIGGER fail_successor_update
               BEFORE UPDATE OF state ON scheduler_jobs
               BEGIN SELECT RAISE(ABORT, 'synthetic finalization failure'); END"""
        )
        with pytest.raises(SchedulerStoreError, match="finalization failed"):
            store.finalize_successor(plan)
        assert store.load_all() == (admitted.job,)
        assert store.load_admitted_visits() == (admitted,)
        assert store.load_successor_plan(admitted) == plan

        connection.execute("DROP TRIGGER fail_successor_update")
        assert store.finalize_successor(plan) == plan.successor
        assert store.finalize_successor(plan) == plan.successor
        assert store.load_all() == (plan.successor,)
        assert store.load_admitted_visits() == ()

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_all() == (plan.successor,)
        assert restarted.load_successor_plans() == (plan,)
        eligible = QueueJob(
            account_key=plan.successor.account_key,
            configuration_generation=plan.successor.configuration_generation,
            job_generation=plan.successor.job_generation,
            availability_event=plan.successor.availability_event,
            available_at=plan.successor.available_at,
            due_at=plan.successor.due_at,
            pending_lane=plan.successor.pending_lane,
            state=QueueState.ELIGIBLE,
        )
        restarted.replace(plan.successor, eligible)
        next_visit = restarted.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=200.0,
            account_order=(ACCOUNT_A,),
        )
        assert next_visit is not None
        assert next_visit.visit_generation.value == 2
        assert restarted.finalize_successor(plan) == plan.successor
        assert restarted.load_admitted_visits() == (next_visit,)


def test_nonterminal_action_blocks_successor_but_terminal_history_survives(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: VISIT_NONCE,
    )
    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        action = store.plan_action(
            admitted,
            lane=Lane.HOME,
            intent_fingerprint="9" * 64,
        )
        action = store.record_action_intent(action)
        action = store.mark_input_started(action)
        with pytest.raises(SchedulerStoreError, match="nonterminal action"):
            store.plan_successor(
                admitted,
                kind=SuccessorKind.IMMEDIATE_FREE,
                observed_at=200.0,
                pending_lane=Lane.BUILDER,
            )
        assert store.load_successor_plans() == ()
        terminal = store.finalize_action(
            action,
            outcome=ActionState.UNCERTAIN,
            pending_lane=Lane.BUILDER,
        )
        admitted = store.load_admitted_visits()[0]
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.IMMEDIATE_FREE,
            observed_at=200.0,
            pending_lane=Lane.BUILDER,
        )
        store.finalize_successor(plan)

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_actions() == (terminal,)
        assert restarted.load_successor_plans() == (plan,)
        assert restarted.load_admitted_visits() == ()


def test_persisted_successor_plan_closes_the_visit_to_new_actions(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: VISIT_NONCE,
    )
    with SQLiteJobStore(path) as store:
        store.insert(job())
        admitted = store.admit_oldest(
            ready=ReadyV2(CONFIGURATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
        )
        assert admitted is not None
        plan = store.plan_successor(
            admitted,
            kind=SuccessorKind.IMMEDIATE_FREE,
            observed_at=200.0,
            pending_lane=Lane.HOME,
        )
        with pytest.raises(SchedulerStoreError, match="successor plan already exists"):
            store.plan_action(
                admitted,
                lane=Lane.HOME,
                intent_fingerprint="8" * 64,
            )
        assert store.load_successor_plan(admitted) == plan
        assert store.load_actions() == ()


def test_canonical_version_three_store_migrates_without_synthesizing_a_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""{_CREATE_JOBS_SQL};
            {_CREATE_VISITS_SQL};
            {_CREATE_ACTIONS_SQL};
            PRAGMA user_version = 3;"""
    )
    connection.close()

    with SQLiteJobStore(path) as store:
        assert store.load_all() == ()
        assert store.load_admission_records() == ()
        assert store.load_actions() == ()
        assert store.load_successor_plans() == ()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    connection.close()


def test_canonical_version_four_store_migrates_without_changing_empty_plans(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""{_CREATE_JOBS_SQL};
            {_CREATE_VISITS_V4_SQL};
            {_CREATE_ACTIONS_SQL};
            {_CREATE_SUCCESSOR_PLANS_V4_SQL};
            PRAGMA user_version = 4;"""
    )
    connection.close()

    with SQLiteJobStore(path) as store:
        assert store.load_all() == ()
        assert store.load_successor_plans() == ()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    successor_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'scheduler_successor_plans'"
    ).fetchone()[0]
    assert "yield_set TEXT NOT NULL CHECK(yield_set = '[]')" not in successor_sql
    connection.close()
