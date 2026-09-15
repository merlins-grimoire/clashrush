"""Crash-safe handoff across scheduler successor planning and lifecycle stop."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .lifecycle import StopRecord
from .lifecycle_state_v2 import ActiveV2, ReadyV2
from .lifecycle_v2 import SchemaV2LifecycleSupervisor
from .scheduler_admission import StartedAdmission
from .scheduler_contract import Lane, QueueJob
from .scheduler_store import SQLiteJobStore, SchedulerStoreError
from .scheduler_successor import SuccessorKind


class SchedulerVisitError(RuntimeError):
    """A complete visit could not cross stores without ambiguity."""


class RecoveryOutcome(Enum):
    NORMAL = "NORMAL"
    RELEASED_UNSTARTED = "RELEASED_UNSTARTED"
    FINALIZED_SUCCESSOR = "FINALIZED_SUCCESSOR"


@dataclass(frozen=True, slots=True)
class SuccessorRequest:
    kind: SuccessorKind
    observed_at: float
    pending_lane: Lane
    future_completion_at: float | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not SuccessorKind or type(self.pending_lane) is not Lane:
            raise SchedulerVisitError("successor request vocabulary is invalid")
        times = (self.observed_at,) + (
            () if self.future_completion_at is None else (self.future_completion_at,)
        )
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in times):
            raise SchedulerVisitError("successor request time is invalid")


def finish_started_visit(
    store: SQLiteJobStore,
    lifecycle: SchemaV2LifecycleSupervisor,
    started: StartedAdmission,
    request: SuccessorRequest,
) -> QueueJob:
    """Persist a successor, prove stop, then atomically finalize that successor."""
    if (
        type(store) is not SQLiteJobStore
        or type(lifecycle) is not SchemaV2LifecycleSupervisor
        or type(started) is not StartedAdmission
        or type(request) is not SuccessorRequest
    ):
        raise SchedulerVisitError("complete visit inputs are invalid")
    try:
        plan = SQLiteJobStore.plan_successor(
            store,
            started.admitted_visit,
            kind=request.kind,
            observed_at=request.observed_at,
            pending_lane=request.pending_lane,
            future_completion_at=request.future_completion_at,
        )
    except BaseException as planning_error:
        try:
            SchemaV2LifecycleSupervisor.emergency_stop_admitted(
                lifecycle, started.binding
            )
        except BaseException as stop_error:
            raise SchedulerVisitError(
                "successor plan failed and emergency stop was unproved"
            ) from stop_error
        raise SchedulerVisitError(
            "successor plan failed; lifecycle remains blocked"
        ) from planning_error

    try:
        stopped = SchemaV2LifecycleSupervisor.stop_admitted(
            lifecycle, started.binding, None
        )
    except BaseException as exc:
        raise SchedulerVisitError("owned visit stop was unproved") from exc
    if type(stopped) is not StopRecord or stopped.run_nonce != started.admitted_visit.visit_nonce:
        raise SchedulerVisitError("owned visit stop record is invalid")
    try:
        return SQLiteJobStore.finalize_successor(store, plan)
    except SchedulerStoreError as exc:
        raise SchedulerVisitError("successor finalization remains pending") from exc


def reconcile_stopped_visit(
    store: SQLiteJobStore,
    *,
    lifecycle_state: ReadyV2 | ActiveV2,
    player_count: int,
) -> RecoveryOutcome:
    """Recover only crash seams whose lifecycle is exactly stopped READY."""
    if type(store) is not SQLiteJobStore:
        raise SchedulerVisitError("scheduler recovery requires an exact store")
    if type(player_count) is not int or player_count < 0:
        raise SchedulerVisitError("scheduler recovery player count is invalid")
    if type(lifecycle_state) is ActiveV2:
        raise SchedulerVisitError("ACTIVE lifecycle requires operator reconciliation")
    if type(lifecycle_state) is not ReadyV2 or player_count != 0:
        raise SchedulerVisitError("scheduler recovery requires stopped READY")

    try:
        visits = SQLiteJobStore.load_admitted_visits(store)
        if not visits:
            return RecoveryOutcome.NORMAL
        if len(visits) != 1:
            raise SchedulerVisitError("scheduler recovery admission cardinality is invalid")
        admitted = visits[0]
        if admitted.configuration_generation != lifecycle_state.configuration_generation:
            raise SchedulerVisitError("scheduler recovery generation mismatch")
        plan = SQLiteJobStore.load_successor_plan(store, admitted)
        if plan is not None:
            if lifecycle_state.tie_after_account_key != admitted.job.account_key:
                raise SchedulerVisitError("scheduler recovery READY cursor mismatch")
            SQLiteJobStore.finalize_successor(store, plan)
            return RecoveryOutcome.FINALIZED_SUCCESSOR
        SQLiteJobStore.release_unstarted_admission(store, admitted)
        return RecoveryOutcome.RELEASED_UNSTARTED
    except SchedulerVisitError:
        raise
    except SchedulerStoreError as exc:
        raise SchedulerVisitError("scheduler recovery state is unprovable") from exc