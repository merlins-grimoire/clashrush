"""Stopped-only scheduler idling and durable due-state refresh."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .configuration_v2 import AccountKey
from .lifecycle_state_v2 import ReadyV2
from .scheduler_contract import QueueJob, select_oldest_eligible
from .scheduler_store import SQLiteJobStore, SchedulerStoreError


class FleetHalted(RuntimeError):
    """The scheduler cannot prove a stopped lifecycle boundary."""


@dataclass(frozen=True, slots=True)
class IdleWait:
    wake_at: float | None
    delay_seconds: float | None

    def __post_init__(self) -> None:
        if self.wake_at is None:
            if self.delay_seconds is not None:
                raise FleetHalted("untimed idle wait is malformed")
            return
        if (
            type(self.wake_at) not in (int, float)
            or not math.isfinite(self.wake_at)
            or type(self.delay_seconds) not in (int, float)
            or not math.isfinite(self.delay_seconds)
            or self.delay_seconds < 0
        ):
            raise FleetHalted("timed idle wait is malformed")


def refresh_and_wait_when_idle(
    store: SQLiteJobStore,
    *,
    lifecycle_state: ReadyV2,
    now: float,
    account_order: tuple[AccountKey, ...],
    sleep: Callable[[float], None],
) -> QueueJob | IdleWait:
    """Return due work, or sleep only while schema-v2 lifecycle is proved stopped."""
    if type(store) is not SQLiteJobStore:
        raise FleetHalted("idle scheduling requires an exact scheduler store")
    if type(lifecycle_state) is not ReadyV2:
        raise FleetHalted("idle scheduling requires a stopped lifecycle")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise FleetHalted("idle scheduling requires a finite time")
    if (
        type(account_order) is not tuple
        or not account_order
        or any(type(key) is not AccountKey for key in account_order)
        or len(set(account_order)) != len(account_order)
        or (
            lifecycle_state.tie_after_account_key is not None
            and lifecycle_state.tie_after_account_key not in account_order
        )
        or not callable(sleep)
    ):
        raise FleetHalted("idle scheduling inputs are invalid")

    try:
        SQLiteJobStore.refresh_due(
            store,
            lifecycle_state.configuration_generation,
            now=now,
        )
        jobs = SQLiteJobStore.load(store, lifecycle_state.configuration_generation)
        selected = select_oldest_eligible(
            jobs,
            now=now,
            account_order=account_order,
            tie_after=lifecycle_state.tie_after_account_key,
        )
        if selected is not None:
            return selected
        wake_at = SQLiteJobStore.next_wake_at(
            store,
            lifecycle_state.configuration_generation,
            now=now,
        )
    except SchedulerStoreError as exc:
        if str(exc) == "overdue deferred fairness barrier requires intervention":
            raise FleetHalted(str(exc)) from exc
        raise FleetHalted("idle scheduler state is unprovable") from exc
    except (ValueError, TypeError) as exc:
        raise FleetHalted("idle scheduler state is unprovable") from exc

    if wake_at is None:
        return IdleWait(None, None)
    delay = wake_at - float(now)
    if delay <= 0:
        raise FleetHalted("idle scheduler wake boundary is inconsistent")
    try:
        result = sleep(delay)
    except BaseException as exc:
        raise FleetHalted("idle scheduler sleep failed") from exc
    if result is not None:
        raise FleetHalted("idle scheduler sleep result is malformed")
    return IdleWait(wake_at, delay)
