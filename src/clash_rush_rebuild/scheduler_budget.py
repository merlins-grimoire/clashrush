"""Pure visit and run budgets for scheduler orchestration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable

from .scheduler_contract import DEFAULT_LIMITS, Lane, SchedulerContractError, SchedulerLimits


_VISIT_SECONDS = 600
_LANE_SECONDS = 300
_MAX_TOTAL_ITERATIONS = 1000
_MAX_CONSECUTIVE_FAILURES = 3


def _finite(value: object, label: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SchedulerContractError(f"{label} must be finite")


class IterationOutcome(Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class RotationDirective(Enum):
    CONTINUE = "CONTINUE"
    SWAP = "SWAP"
    STOP = "STOP"


class StopReason(Enum):
    REQUESTED = "REQUESTED"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    FAILURE_LIMIT = "FAILURE_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"


@dataclass(frozen=True, slots=True)
class RunBudget:
    started_at: float
    deadline: float
    checked_at: float
    total_iterations: int = 0
    consecutive_failures: int = 0
    _canonical: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _finite(self.started_at, "run start")
        _finite(self.deadline, "run deadline")
        _finite(self.checked_at, "last run check")
        if self.deadline <= self.started_at:
            raise SchedulerContractError("run deadline must follow its start")
        if self.checked_at < self.started_at:
            raise SchedulerContractError("last run check precedes the run")
        if (
            type(self.total_iterations) is not int
            or not 0 <= self.total_iterations <= _MAX_TOTAL_ITERATIONS
            or type(self.consecutive_failures) is not int
            or not 0 <= self.consecutive_failures <= _MAX_CONSECUTIVE_FAILURES
            or self.consecutive_failures > self.total_iterations
        ):
            raise SchedulerContractError("run counters are invalid")
        object.__setattr__(
            self,
            "_canonical",
            (
                self.started_at,
                self.deadline,
                self.checked_at,
                self.total_iterations,
                self.consecutive_failures,
            ),
        )


@dataclass(frozen=True, slots=True)
class IterationResult:
    outcome: IterationOutcome
    directive: RotationDirective

    def __post_init__(self) -> None:
        if type(self.outcome) is not IterationOutcome:
            raise SchedulerContractError("iteration outcome must be exact")
        if type(self.directive) is not RotationDirective:
            raise SchedulerContractError("rotation directive must be exact")


@dataclass(frozen=True, slots=True)
class RotationResult:
    budget: RunBudget
    reason: StopReason

    def __post_init__(self) -> None:
        if type(self.budget) is not RunBudget or type(self.reason) is not StopReason:
            raise SchedulerContractError("rotation result is invalid")


def start_run_budget(*, now: float, deadline: float) -> RunBudget:
    return RunBudget(now, deadline, now)


def _stop_reason(budget: RunBudget, now: float) -> StopReason | None:
    if budget.total_iterations >= _MAX_TOTAL_ITERATIONS:
        return StopReason.ITERATION_LIMIT
    if budget.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        return StopReason.FAILURE_LIMIT
    if now >= budget.deadline:
        return StopReason.TIME_LIMIT
    return None


def _copy_run_budget(budget: RunBudget) -> RunBudget:
    values = (
        budget.started_at,
        budget.deadline,
        budget.checked_at,
        budget.total_iterations,
        budget.consecutive_failures,
    )
    if type(budget._canonical) is not tuple or budget._canonical != values:
        raise SchedulerContractError("run budget was mutated after construction")
    return RunBudget(
        *values,
    )


def run_bounded_rotations(
    budget: RunBudget,
    *,
    clock: Callable[[], float],
    run_iteration: Callable[[RunBudget], IterationResult],
) -> RotationResult:
    """Run atomic iterations while preserving one outer budget across swaps."""
    if type(budget) is not RunBudget:
        raise SchedulerContractError("budget must be an exact RunBudget")
    if not callable(clock) or not callable(run_iteration):
        raise SchedulerContractError("rotation callbacks must be callable")
    current = _copy_run_budget(budget)
    while True:
        now = clock()
        _finite(now, "current time")
        if now < current.checked_at:
            raise SchedulerContractError("global clock regressed")
        reason = _stop_reason(current, now)
        if reason is not None:
            return RotationResult(replace(current, checked_at=now), reason)
        current = replace(current, checked_at=now)
        callback_budget = _copy_run_budget(current)
        result = run_iteration(callback_budget)
        if type(result) is not IterationResult:
            raise SchedulerContractError("iteration callback returned an invalid result")
        result = IterationResult(result.outcome, result.directive)
        current = replace(
            current,
            total_iterations=current.total_iterations + 1,
            consecutive_failures=(
                0
                if result.outcome is IterationOutcome.SUCCESS
                else current.consecutive_failures + 1
            ),
        )
        reason = _stop_reason(current, now)
        if reason is not None:
            return RotationResult(current, reason)
        if result.directive is RotationDirective.STOP:
            return RotationResult(current, StopReason.REQUESTED)


@dataclass(frozen=True, slots=True)
class VisitBudget:
    started_at: float
    reservation_boundary: float
    deadline: float
    first_lane: Lane
    observed_lanes: frozenset[Lane] = frozenset()
    _canonical: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _finite(self.started_at, "visit start")
        _finite(self.reservation_boundary, "lane reservation boundary")
        _finite(self.deadline, "visit deadline")
        if type(self.first_lane) is not Lane:
            raise SchedulerContractError("first lane must be an exact Lane")
        if type(self.observed_lanes) is not frozenset or any(
            type(lane) is not Lane for lane in self.observed_lanes
        ):
            raise SchedulerContractError("observed lanes must contain exact Lane values")
        if (
            self.reservation_boundary != self.started_at + _LANE_SECONDS
            or self.deadline != self.started_at + _VISIT_SECONDS
        ):
            raise SchedulerContractError("visit boundaries must be exactly 5 and 10 minutes")
        object.__setattr__(
            self,
            "_canonical",
            (
                self.started_at,
                self.reservation_boundary,
                self.deadline,
                self.first_lane,
                self.observed_lanes,
            ),
        )


def _copy_visit_budget(budget: VisitBudget) -> VisitBudget:
    values = (
        budget.started_at,
        budget.reservation_boundary,
        budget.deadline,
        budget.first_lane,
        budget.observed_lanes,
    )
    if type(budget._canonical) is not tuple or budget._canonical != values:
        raise SchedulerContractError("visit budget was mutated after construction")
    return VisitBudget(
        *values,
    )


def _require_frozen_limits(limits: SchedulerLimits) -> None:
    values = (
        limits.reconciliation_min_seconds,
        limits.reconciliation_max_seconds,
        limits.visit_seconds,
        limits.initial_home_seconds,
        limits.initial_builder_seconds,
        limits.max_total_iterations,
        limits.max_consecutive_failures,
    )
    if any(type(value) is not int for value in values) or values != (
        300,
        3600,
        _VISIT_SECONDS,
        _LANE_SECONDS,
        _LANE_SECONDS,
        _MAX_TOTAL_ITERATIONS,
        _MAX_CONSECUTIVE_FAILURES,
    ):
        raise SchedulerContractError("scheduler limits were mutated after construction")


def start_visit_budget(
    *,
    now: float,
    first_lane: Lane,
    limits: SchedulerLimits = DEFAULT_LIMITS,
) -> VisitBudget:
    _finite(now, "visit start")
    if type(first_lane) is not Lane:
        raise SchedulerContractError("first lane must be an exact Lane")
    if type(limits) is not SchedulerLimits:
        raise SchedulerContractError("limits must be exact SchedulerLimits")
    _require_frozen_limits(limits)
    return VisitBudget(
        started_at=now,
        reservation_boundary=now + _LANE_SECONDS,
        deadline=now + _VISIT_SECONDS,
        first_lane=first_lane,
    )


def record_lane_opportunity(
    budget: VisitBudget,
    *,
    lane: Lane,
    now: float,
) -> VisitBudget:
    if type(budget) is not VisitBudget:
        raise SchedulerContractError("budget must be an exact VisitBudget")
    budget = _copy_visit_budget(budget)
    if type(lane) is not Lane:
        raise SchedulerContractError("lane must be an exact Lane")
    _finite(now, "current time")
    if now < budget.started_at or now >= budget.deadline:
        raise SchedulerContractError("lane opportunity is outside the visit")
    return replace(budget, observed_lanes=budget.observed_lanes | {lane})


def choose_visit_lane(
    budget: VisitBudget,
    *,
    now: float,
    runnable_lanes: tuple[Lane, ...],
    preferred_lane: Lane | None,
) -> Lane | None:
    if type(budget) is not VisitBudget:
        raise SchedulerContractError("budget must be an exact VisitBudget")
    budget = _copy_visit_budget(budget)
    _finite(now, "current time")
    if type(runnable_lanes) is not tuple or any(
        type(lane) is not Lane for lane in runnable_lanes
    ):
        raise SchedulerContractError("runnable lanes must contain exact Lane values")
    if len(set(runnable_lanes)) != len(runnable_lanes):
        raise SchedulerContractError("runnable lanes must be unique")
    if preferred_lane is not None and type(preferred_lane) is not Lane:
        raise SchedulerContractError("preferred lane must be an exact Lane or None")
    if now < budget.started_at:
        raise SchedulerContractError("current time precedes the visit")
    if now >= budget.deadline or not runnable_lanes:
        return None

    other_lane = Lane.BUILDER if budget.first_lane is Lane.HOME else Lane.HOME
    if now < budget.reservation_boundary:
        protected_order = (budget.first_lane, other_lane)
    else:
        protected_order = tuple(
            lane
            for lane in (other_lane, budget.first_lane)
            if lane not in budget.observed_lanes
        )
    for lane in protected_order:
        if lane in runnable_lanes:
            return lane
    if preferred_lane in runnable_lanes:
        return preferred_lane
    return next(lane for lane in Lane if lane in runnable_lanes)
