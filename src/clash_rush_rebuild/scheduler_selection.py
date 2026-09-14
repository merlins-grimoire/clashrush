"""Persisted oldest-due selection using the schema-v2 lifecycle tie cursor."""

from __future__ import annotations

from .configuration_v2 import AccountKey
from .lifecycle_state_v2 import ReadyV2
from .scheduler_contract import QueueJob, select_oldest_eligible
from .scheduler_store import SQLiteJobStore


def select_persisted_oldest_eligible(
    store: SQLiteJobStore,
    *,
    ready: ReadyV2,
    now: float,
    account_order: tuple[AccountKey, ...],
) -> QueueJob | None:
    """Select the oldest persisted eligible job, rotating only exact deadline ties."""
    if type(store) is not SQLiteJobStore:
        raise TypeError("selection requires an exact SQLiteJobStore")
    if type(ready) is not ReadyV2:
        raise TypeError("selection requires an exact schema-v2 READY state")
    jobs = SQLiteJobStore.load(store, ready.configuration_generation)
    return select_oldest_eligible(
        jobs,
        now=now,
        account_order=account_order,
        tie_after=ready.tie_after_account_key,
    )
