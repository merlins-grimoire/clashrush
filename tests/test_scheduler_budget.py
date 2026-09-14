from __future__ import annotations

import pytest

from clash_rush_rebuild.scheduler_budget import (
    IterationOutcome,
    IterationResult,
    RotationDirective,
    RunBudget,
    StopReason,
    VisitBudget,
    choose_visit_lane,
    record_lane_opportunity,
    run_bounded_rotations,
    start_run_budget,
    start_visit_budget,
)
from clash_rush_rebuild.scheduler_contract import DEFAULT_LIMITS, Lane, SchedulerContractError


def test_visit_budget_protects_both_lane_opportunities_without_extending_deadline() -> None:
    budget = start_visit_budget(now=100.0, first_lane=Lane.HOME)
    runnable = (Lane.HOME, Lane.BUILDER)

    assert budget.deadline == 700.0
    assert choose_visit_lane(
        budget,
        now=399.999,
        runnable_lanes=runnable,
        preferred_lane=Lane.HOME,
    ) is Lane.HOME

    budget = record_lane_opportunity(budget, lane=Lane.HOME, now=200.0)
    assert choose_visit_lane(
        budget,
        now=400.0,
        runnable_lanes=runnable,
        preferred_lane=Lane.HOME,
    ) is Lane.BUILDER

    budget = record_lane_opportunity(budget, lane=Lane.BUILDER, now=401.0)
    assert choose_visit_lane(
        budget,
        now=402.0,
        runnable_lanes=runnable,
        preferred_lane=Lane.HOME,
    ) is Lane.HOME
    assert choose_visit_lane(
        budget,
        now=700.0,
        runnable_lanes=runnable,
        preferred_lane=Lane.HOME,
    ) is None


def test_repeated_swaps_cannot_reset_the_global_iteration_limit() -> None:
    observed_iterations: list[int] = []

    def swap(budget: object) -> IterationResult:
        observed_iterations.append(budget.total_iterations)  # type: ignore[attr-defined]
        return IterationResult(IterationOutcome.SUCCESS, RotationDirective.SWAP)

    result = run_bounded_rotations(
        start_run_budget(now=0.0, deadline=10_000.0),
        clock=lambda: 1.0,
        run_iteration=swap,
    )

    assert observed_iterations == list(range(1000))
    assert result.reason is StopReason.ITERATION_LIMIT
    assert result.budget.total_iterations == 1000


def test_lane_that_becomes_runnable_after_its_peer_was_observed_cannot_starve() -> None:
    budget = start_visit_budget(now=0.0, first_lane=Lane.HOME)
    assert choose_visit_lane(
        budget,
        now=10.0,
        runnable_lanes=(Lane.BUILDER,),
        preferred_lane=Lane.BUILDER,
    ) is Lane.BUILDER
    budget = record_lane_opportunity(budget, lane=Lane.BUILDER, now=10.0)

    assert choose_visit_lane(
        budget,
        now=300.0,
        runnable_lanes=(Lane.HOME, Lane.BUILDER),
        preferred_lane=Lane.BUILDER,
    ) is Lane.HOME


def test_rotation_rejects_a_regressing_global_clock_before_another_iteration() -> None:
    times = iter((100.0, 200.0, 150.0))
    calls = 0

    def swap(_budget: object) -> IterationResult:
        nonlocal calls
        calls += 1
        return IterationResult(IterationOutcome.SUCCESS, RotationDirective.SWAP)

    with pytest.raises(SchedulerContractError, match="regressed"):
        run_bounded_rotations(
            start_run_budget(now=0.0, deadline=1000.0),
            clock=lambda: next(times),
            run_iteration=swap,
        )

    assert calls == 2


@pytest.mark.parametrize(
    ("reservation_boundary", "deadline"),
    [(299.0, 600.0), (300.0, 601.0)],
)
def test_visit_budget_direct_construction_cannot_change_frozen_time_bounds(
    reservation_boundary: float,
    deadline: float,
) -> None:
    with pytest.raises(SchedulerContractError, match="exactly"):
        VisitBudget(0.0, reservation_boundary, deadline, Lane.HOME)


def test_time_limit_records_the_actual_global_check_without_starting_work() -> None:
    calls = 0

    def forbidden(_budget: RunBudget) -> IterationResult:
        nonlocal calls
        calls += 1
        return IterationResult(IterationOutcome.SUCCESS, RotationDirective.SWAP)

    result = run_bounded_rotations(
        start_run_budget(now=0.0, deadline=10.0),
        clock=lambda: 11.0,
        run_iteration=forbidden,
    )

    assert calls == 0
    assert result.reason is StopReason.TIME_LIMIT
    assert result.budget.checked_at == 11.0


def test_third_failure_wins_over_a_simultaneous_requested_stop() -> None:
    directives = iter(
        (RotationDirective.SWAP, RotationDirective.SWAP, RotationDirective.STOP)
    )

    result = run_bounded_rotations(
        start_run_budget(now=0.0, deadline=100.0),
        clock=lambda: 1.0,
        run_iteration=lambda _budget: IterationResult(
            IterationOutcome.FAILURE,
            next(directives),
        ),
    )

    assert result.reason is StopReason.FAILURE_LIMIT
    assert result.budget.total_iterations == 3
    assert result.budget.consecutive_failures == 3


def test_success_resets_only_the_global_consecutive_failure_counter() -> None:
    outcomes = iter(
        (
            IterationOutcome.FAILURE,
            IterationOutcome.SUCCESS,
            IterationOutcome.FAILURE,
            IterationOutcome.FAILURE,
            IterationOutcome.FAILURE,
        )
    )
    observed: list[tuple[int, int]] = []

    def rotate(budget: RunBudget) -> IterationResult:
        observed.append((budget.total_iterations, budget.consecutive_failures))
        return IterationResult(next(outcomes), RotationDirective.SWAP)

    result = run_bounded_rotations(
        start_run_budget(now=0.0, deadline=100.0),
        clock=lambda: 1.0,
        run_iteration=rotate,
    )

    assert observed == [(0, 0), (1, 1), (2, 0), (3, 1), (4, 2)]
    assert result.reason is StopReason.FAILURE_LIMIT
    assert result.budget.total_iterations == 5


def test_time_bound_is_checked_before_each_rotated_iteration() -> None:
    times = iter((1.0, 9.999, 10.0))
    observed_checks: list[float] = []

    def swap(budget: RunBudget) -> IterationResult:
        observed_checks.append(budget.checked_at)
        return IterationResult(IterationOutcome.SUCCESS, RotationDirective.SWAP)

    result = run_bounded_rotations(
        start_run_budget(now=0.0, deadline=10.0),
        clock=lambda: next(times),
        run_iteration=swap,
    )

    assert observed_checks == [1.0, 9.999]
    assert result.reason is StopReason.TIME_LIMIT
    assert result.budget.total_iterations == 2


def test_callback_cannot_mutate_the_authoritative_global_budget() -> None:
    initial = RunBudget(
        started_at=0.0,
        deadline=10.0,
        checked_at=0.0,
        total_iterations=999,
        consecutive_failures=0,
    )
    calls = 0

    def hostile(callback_budget: RunBudget) -> IterationResult:
        nonlocal calls
        calls += 1
        object.__setattr__(callback_budget, "total_iterations", 0)
        object.__setattr__(callback_budget, "deadline", 10_000.0)
        return IterationResult(IterationOutcome.SUCCESS, RotationDirective.SWAP)

    result = run_bounded_rotations(
        initial,
        clock=lambda: 1.0,
        run_iteration=hostile,
    )

    assert calls == 1
    assert result.reason is StopReason.ITERATION_LIMIT
    assert result.budget.total_iterations == 1000
    assert result.budget.deadline == 10.0


def test_clock_callback_cannot_mutate_the_callers_aliased_budget() -> None:
    initial = RunBudget(
        started_at=0.0,
        deadline=10.0,
        checked_at=0.0,
        total_iterations=999,
        consecutive_failures=0,
    )

    def hostile_clock() -> float:
        object.__setattr__(initial, "total_iterations", 0)
        object.__setattr__(initial, "deadline", 10_000.0)
        return 1.0

    result = run_bounded_rotations(
        initial,
        clock=hostile_clock,
        run_iteration=lambda _budget: IterationResult(
            IterationOutcome.SUCCESS,
            RotationDirective.STOP,
        ),
    )

    assert result.reason is StopReason.ITERATION_LIMIT
    assert result.budget.total_iterations == 1000
    assert result.budget.deadline == 10.0


def test_lane_selection_rejects_a_post_construction_mutated_visit_budget() -> None:
    budget = start_visit_budget(now=0.0, first_lane=Lane.HOME)
    object.__setattr__(budget, "deadline", 10_000.0)

    with pytest.raises(SchedulerContractError, match="mutated"):
        choose_visit_lane(
            budget,
            now=700.0,
            runnable_lanes=(Lane.HOME,),
            preferred_lane=Lane.HOME,
        )


def test_observed_lane_mutation_cannot_suppress_a_protected_opportunity() -> None:
    budget = start_visit_budget(now=0.0, first_lane=Lane.HOME)
    object.__setattr__(budget, "observed_lanes", frozenset(Lane))

    with pytest.raises(SchedulerContractError, match="mutated"):
        choose_visit_lane(
            budget,
            now=300.0,
            runnable_lanes=(Lane.HOME, Lane.BUILDER),
            preferred_lane=Lane.HOME,
        )


def test_valid_post_construction_run_budget_mutation_is_rejected_at_ingress() -> None:
    budget = start_run_budget(now=0.0, deadline=10.0)
    object.__setattr__(budget, "deadline", 10_000.0)

    with pytest.raises(SchedulerContractError, match="mutated"):
        run_bounded_rotations(
            budget,
            clock=lambda: 1.0,
            run_iteration=lambda _budget: IterationResult(
                IterationOutcome.SUCCESS,
                RotationDirective.STOP,
            ),
        )


def test_mutated_shared_limits_cannot_extend_a_visit() -> None:
    original = DEFAULT_LIMITS.visit_seconds
    object.__setattr__(DEFAULT_LIMITS, "visit_seconds", 6000)
    try:
        with pytest.raises(SchedulerContractError, match="mutated"):
            start_visit_budget(now=0.0, first_lane=Lane.HOME)
    finally:
        object.__setattr__(DEFAULT_LIMITS, "visit_seconds", original)
