from __future__ import annotations

from pathlib import Path

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2, decode_state_v2, encode_state_v2
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
)
from clash_rush_rebuild.scheduler_selection import select_persisted_oldest_eligible
from clash_rush_rebuild.scheduler_store import SQLiteJobStore


CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
ACCOUNT_ORDER = (ACCOUNT_A, ACCOUNT_B)


def job(account: AccountKey, *, due_at: float) -> QueueJob:
    return QueueJob(
        account_key=account,
        configuration_generation=CONFIGURATION,
        job_generation=JobGeneration(1),
        availability_event=AvailabilityEvent("a" * 32),
        available_at=50.0,
        due_at=due_at,
        pending_lane=Lane.HOME,
        state=QueueState.ELIGIBLE,
    )


def restarted_ready(tie_after: AccountKey) -> ReadyV2:
    state = decode_state_v2(encode_state_v2(ReadyV2(CONFIGURATION, tie_after)))
    assert type(state) is ReadyV2
    return state


def test_non_equal_deadlines_ignore_account_order_and_tie_cursor(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    oldest = job(ACCOUNT_B, due_at=90.0)
    later = job(ACCOUNT_A, due_at=100.0)

    with SQLiteJobStore(path) as store:
        store.insert(later)
        store.insert(oldest)
        selected = select_persisted_oldest_eligible(
            store,
            ready=restarted_ready(ACCOUNT_B),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        )

    assert selected == oldest


def test_equal_deadline_round_robin_remains_fair_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    first = job(ACCOUNT_A, due_at=100.0)
    second = job(ACCOUNT_B, due_at=100.0)

    with SQLiteJobStore(path) as store:
        store.insert(first)
        store.insert(second)
        assert select_persisted_oldest_eligible(
            store,
            ready=restarted_ready(ACCOUNT_A),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        ) == second

    with SQLiteJobStore(path) as restarted_store:
        assert select_persisted_oldest_eligible(
            restarted_store,
            ready=restarted_ready(ACCOUNT_B),
            now=100.0,
            account_order=ACCOUNT_ORDER,
        ) == first
