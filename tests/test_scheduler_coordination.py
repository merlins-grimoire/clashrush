from __future__ import annotations

from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state_v2 import ActiveV2, ReadyV2
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    YieldToken,
)
from clash_rush_rebuild.scheduler_coordination import (
    FleetHalted,
    IdleWait,
    refresh_and_wait_when_idle,
)
from clash_rush_rebuild.scheduler_store import SQLiteJobStore
from clash_rush_rebuild.scheduler_successor import SuccessorKind


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
ACCOUNT_C = AccountKey("account-c")
ORDER = (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C)


def job(
    account: AccountKey,
    *,
    generation: int = 1,
    due_at: float = 100.0,
    state: QueueState = QueueState.ELIGIBLE,
) -> QueueJob:
    marker = {ACCOUNT_A: "a", ACCOUNT_B: "b", ACCOUNT_C: "c"}[account]
    return QueueJob(
        account_key=account,
        configuration_generation=GENERATION,
        job_generation=JobGeneration(generation),
        availability_event=AvailabilityEvent(marker * 32),
        available_at=50.0,
        due_at=due_at,
        pending_lane=Lane.HOME,
        state=state,
    )


def test_unknown_deferral_snapshots_each_other_eligible_generation_and_waits_for_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = iter(("1" * 32, "2" * 32, "3" * 32, "4" * 32, "5" * 32, "6" * 32))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(generated),
    )
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.randbelow", lambda ceiling: 0
    )
    path = tmp_path / "scheduler.sqlite3"

    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A))
        store.insert(job(ACCOUNT_B))
        store.insert(job(ACCOUNT_C, due_at=500.0, state=QueueState.WAITING))
        admitted_a = store.admit_oldest(
            ready=ReadyV2(GENERATION, ACCOUNT_C), now=100.0, account_order=ORDER
        )
        assert admitted_a is not None
        plan_a = store.plan_successor(
            admitted_a,
            kind=SuccessorKind.RECONCILE_UNKNOWN,
            observed_at=200.0,
            pending_lane=Lane.BUILDER,
        )
        assert plan_a.successor.yield_set == (
            YieldToken(ACCOUNT_B, JobGeneration(1)),
        )
        store.finalize_successor(plan_a)

        initially_promoted = store.refresh_due(GENERATION, now=500.0)
        assert tuple(item.account_key for item in initially_promoted) == (ACCOUNT_C,)
        assert store.load(GENERATION)[0].state is QueueState.DEFERRED

        admitted_b = store.admit_oldest(
            ready=ReadyV2(GENERATION, ACCOUNT_A), now=500.0, account_order=ORDER
        )
        assert admitted_b is not None
        assert admitted_b.admission.account_key == ACCOUNT_B
        plan_b = store.plan_successor(
            admitted_b,
            kind=SuccessorKind.FUTURE_COMPLETION,
            observed_at=500.0,
            future_completion_at=1000.0,
            pending_lane=Lane.HOME,
        )
        store.finalize_successor(plan_b)

        promoted = store.refresh_due(GENERATION, now=500.0)
        assert tuple(item.account_key for item in promoted) == (ACCOUNT_A,)
        assert {item.account_key: item.state for item in store.load(GENERATION)} == {
            ACCOUNT_A: QueueState.ELIGIBLE,
            ACCOUNT_B: QueueState.WAITING,
            ACCOUNT_C: QueueState.ELIGIBLE,
        }


def test_quarantine_is_durable_visible_and_does_not_block_another_due_account(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    original_a = job(ACCOUNT_A)
    original_b = job(ACCOUNT_B)
    with SQLiteJobStore(path) as store:
        store.insert(original_a)
        store.insert(original_b)
        locked = store.quarantine_current(original_a)
        assert locked.state is QueueState.QUARANTINED
        admitted = store.admit_oldest(
            ready=ReadyV2(GENERATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A, ACCOUNT_B),
        )
        assert admitted is not None
        assert admitted.admission.account_key == ACCOUNT_B

    with SQLiteJobStore(path) as restarted:
        persisted = {item.account_key: item for item in restarted.load(GENERATION)}
        assert persisted[ACCOUNT_A] == locked
        released = restarted.release_quarantined(locked)
        assert released == original_a


def test_no_due_wait_requires_ready_and_never_admits_or_starts_a_player(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    sleeps: list[float] = []
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=160.0, state=QueueState.WAITING))
        result = refresh_and_wait_when_idle(
            store,
            lifecycle_state=ReadyV2(GENERATION, ACCOUNT_A),
            now=100.0,
            account_order=(ACCOUNT_A,),
            sleep=lambda delay: sleeps.append(delay),
        )
        assert result == IdleWait(160.0, 60.0)
        assert sleeps == [60.0]
        assert store.load_admitted_visits() == ()
        assert store.load(GENERATION) == (
            job(ACCOUNT_A, due_at=160.0, state=QueueState.WAITING),
        )


def test_active_or_malformed_lifecycle_state_halts_fleet_without_sleep(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    sleeps: list[float] = []
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=160.0, state=QueueState.WAITING))
        with pytest.raises(FleetHalted, match="stopped lifecycle"):
            refresh_and_wait_when_idle(
                store,
                lifecycle_state=ActiveV2(GENERATION, ACCOUNT_A, 0, "d" * 32, None),
                now=100.0,
                account_order=(ACCOUNT_A,),
                sleep=lambda delay: sleeps.append(delay),
            )
        assert sleeps == []


def test_initial_null_tie_cursor_may_wait_while_stopped(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    sleeps: list[float] = []
    with SQLiteJobStore(path) as store:
        store.insert(job(ACCOUNT_A, due_at=101.0, state=QueueState.WAITING))
        assert refresh_and_wait_when_idle(
            store,
            lifecycle_state=ReadyV2(GENERATION, None),
            now=100.0,
            account_order=(ACCOUNT_A,),
            sleep=lambda delay: sleeps.append(delay),
        ) == IdleWait(101.0, 1.0)
    assert sleeps == [1.0]


def test_overdue_missing_yield_token_halts_instead_of_silently_idling(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    deferred = QueueJob(
        account_key=ACCOUNT_A,
        configuration_generation=GENERATION,
        job_generation=JobGeneration(2),
        availability_event=AvailabilityEvent("d" * 32),
        available_at=100.0,
        due_at=100.0,
        pending_lane=Lane.BUILDER,
        state=QueueState.DEFERRED,
        reconcile_at=400.0,
        yield_set=(YieldToken(ACCOUNT_B, JobGeneration(1)),),
    )
    sleeps: list[float] = []
    with SQLiteJobStore(path) as store:
        store.insert(deferred)
        with pytest.raises(FleetHalted, match="fairness barrier"):
            refresh_and_wait_when_idle(
                store,
                lifecycle_state=ReadyV2(GENERATION, None),
                now=500.0,
                account_order=(ACCOUNT_A, ACCOUNT_B),
                sleep=lambda delay: sleeps.append(delay),
            )
        assert store.load(GENERATION) == (deferred,)
    assert sleeps == []
