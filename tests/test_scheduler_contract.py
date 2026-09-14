from __future__ import annotations

import math
from dataclasses import replace

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.scheduler_contract import (
    ALLOWED_TRANSITIONS,
    DEFAULT_LIMITS,
    AdmittedVisit,
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    SchedulerContractError,
    SchedulerLimits,
    VisitAdmission,
    VisitGeneration,
    YieldToken,
    admit,
    complete_visit,
    defer,
    mark_eligible,
    quarantine,
    release_quarantine,
    select_oldest_eligible,
)

CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
ACCOUNT_C = AccountKey("account-c")
NONCE = "fedcba9876543210fedcba9876543210"


def job(
    account: AccountKey = ACCOUNT_A,
    *,
    generation: int = 1,
    state: QueueState = QueueState.WAITING,
    due_at: float = 100.0,
    lane: Lane = Lane.HOME,
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


def non_exact_job(value: QueueJob, *, subclass: bool) -> object:
    attributes = {name: getattr(value, name) for name in QueueJob.__slots__}
    if subclass:
        derived_type = type("DerivedQueueJob", (QueueJob,), {})
        return derived_type(**attributes)
    fake_type = type("FakeQueueJob", (), {})
    fake = fake_type()
    for name, attribute in attributes.items():
        setattr(fake, name, attribute)
    return fake


def test_limits_freeze_reconciliation_visit_lane_and_run_bounds() -> None:
    assert DEFAULT_LIMITS.reconciliation_min_seconds == 5 * 60
    assert DEFAULT_LIMITS.reconciliation_max_seconds == 60 * 60
    assert DEFAULT_LIMITS.visit_seconds == 10 * 60
    assert DEFAULT_LIMITS.initial_home_seconds == 5 * 60
    assert DEFAULT_LIMITS.initial_builder_seconds == 5 * 60
    assert DEFAULT_LIMITS.max_total_iterations == 1000
    assert DEFAULT_LIMITS.max_consecutive_failures == 3


def test_allowed_transition_mapping_cannot_be_modified_globally() -> None:
    with pytest.raises(TypeError):
        ALLOWED_TRANSITIONS[QueueState.WAITING] = frozenset()  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_total_iterations", True),
        ("max_total_iterations", 1000.0),
        ("max_total_iterations", 999),
        ("max_total_iterations", 1001),
        ("max_consecutive_failures", False),
        ("max_consecutive_failures", 3.0),
        ("max_consecutive_failures", 2),
        ("max_consecutive_failures", 4),
    ],
)
def test_run_limits_reject_every_override(field: str, value: object) -> None:
    with pytest.raises(SchedulerContractError, match="run limits"):
        SchedulerLimits(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reconciliation_min_seconds", 301),
        ("reconciliation_max_seconds", 3599),
    ],
)
def test_reconciliation_limits_reject_narrower_overrides(field: str, value: int) -> None:
    with pytest.raises(SchedulerContractError, match="exactly 5 to 60 minutes"):
        SchedulerLimits(**{field: value})


def test_waiting_becomes_eligible_only_at_its_one_persisted_deadline() -> None:
    waiting = job()

    with pytest.raises(SchedulerContractError, match="deadline"):
        mark_eligible(waiting, now=99.999)

    eligible = mark_eligible(waiting, now=100.0)
    assert eligible.state is QueueState.ELIGIBLE
    assert eligible.availability_event == waiting.availability_event
    assert eligible.available_at == 50.0
    assert eligible.due_at == 100.0
    assert eligible.job_generation == waiting.job_generation


def test_oldest_currently_eligible_order_and_restart_stable_equal_deadline_tie() -> None:
    jobs = (
        job(ACCOUNT_A, state=QueueState.ELIGIBLE, due_at=100.0),
        job(ACCOUNT_B, state=QueueState.ELIGIBLE, due_at=90.0),
        job(ACCOUNT_C, state=QueueState.WAITING, due_at=80.0),
    )
    assert select_oldest_eligible(jobs, now=100.0, account_order=(ACCOUNT_A, ACCOUNT_B, ACCOUNT_C), tie_after=None) == jobs[1]

    equal = tuple(job(key, state=QueueState.ELIGIBLE) for key in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C))
    assert select_oldest_eligible(equal, now=100.0, account_order=(ACCOUNT_A, ACCOUNT_B, ACCOUNT_C), tie_after=ACCOUNT_A) == equal[1]
    assert select_oldest_eligible(equal, now=100.0, account_order=(ACCOUNT_A, ACCOUNT_B, ACCOUNT_C), tie_after=ACCOUNT_C) == equal[0]


@pytest.mark.parametrize("second_due_at", [100.0, 101.0])
def test_selection_rejects_multiple_candidate_jobs_for_one_account(
    second_due_at: float,
) -> None:
    duplicate_account_jobs = (
        job(ACCOUNT_A, generation=1, state=QueueState.ELIGIBLE, due_at=100.0),
        job(ACCOUNT_A, generation=2, state=QueueState.ELIGIBLE, due_at=second_due_at),
    )

    with pytest.raises(SchedulerContractError, match="multiple candidate jobs"):
        select_oldest_eligible(
            duplicate_account_jobs,
            now=101.0,
            account_order=(ACCOUNT_A,),
            tie_after=None,
        )


def test_selection_rejects_attribute_compatible_non_queue_job() -> None:
    fake = type("FakeQueueJob", (), {})()
    fake.state = QueueState.ELIGIBLE
    fake.due_at = 90.0
    fake.account_key = ACCOUNT_A

    with pytest.raises(SchedulerContractError, match="QueueJob"):
        select_oldest_eligible(
            (fake,),  # type: ignore[arg-type]
            now=100.0,
            account_order=(ACCOUNT_A,),
            tie_after=None,
        )


def test_selection_rejects_an_equality_forging_non_account_tie_cursor() -> None:
    class ForgedCursor:
        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(SchedulerContractError, match="exact AccountKey"):
        select_oldest_eligible(
            (job(state=QueueState.ELIGIBLE),),
            now=100.0,
            account_order=(ACCOUNT_A,),
            tie_after=ForgedCursor(),  # type: ignore[arg-type]
        )


def test_selection_rejects_non_account_order_values_before_hashing_them() -> None:
    with pytest.raises(SchedulerContractError, match="account keys"):
        select_oldest_eligible(
            (),
            now=100.0,
            account_order=([],),  # type: ignore[arg-type]
            tie_after=None,
        )


def test_eligible_admission_binds_generations_lane_and_exact_nonce() -> None:
    eligible = job(state=QueueState.ELIGIBLE, lane=Lane.BUILDER)
    admitted = admit(eligible, visit_generation=VisitGeneration(7), visit_nonce=NONCE, now=100.0)

    assert admitted.job.state is QueueState.ADMITTED
    assert admitted.job.pending_lane is Lane.BUILDER
    assert admitted.visit_nonce == NONCE
    assert admitted.visit_generation == VisitGeneration(7)
    assert admitted.job_generation == JobGeneration(1)
    assert admitted.configuration_generation == CONFIGURATION


def test_admitted_visit_direct_construction_rejects_malformed_or_mismatched_values() -> None:
    admitted_job = job(state=QueueState.ADMITTED)
    admission = VisitAdmission(
        CONFIGURATION,
        ACCOUNT_A,
        admitted_job.job_generation,
        VisitGeneration(1),
        NONCE,
    )
    assert AdmittedVisit(admitted_job, admission).job is admitted_job

    invalid_values = (
        (object(), admission, "job"),
        (admitted_job, object(), "admission"),
        (replace(admitted_job, state=QueueState.ELIGIBLE), admission, "admitted"),
        (replace(admitted_job, account_key=ACCOUNT_B), admission, "account"),
        (
            replace(
                admitted_job,
                configuration_generation=ConfigurationGeneration("f" * 32),
            ),
            admission,
            "configuration",
        ),
        (replace(admitted_job, job_generation=JobGeneration(2)), admission, "job generation"),
    )
    for job_value, admission_value, message in invalid_values:
        with pytest.raises(SchedulerContractError, match=message):
            AdmittedVisit(job_value, admission_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("nonce", ["A" * 32, "a" * 31, "g" * 32, "", 1])
def test_admission_rejects_noncanonical_visit_nonce(nonce: object) -> None:
    with pytest.raises(SchedulerContractError, match="nonce"):
        VisitAdmission(CONFIGURATION, ACCOUNT_A, JobGeneration(1), VisitGeneration(1), nonce)  # type: ignore[arg-type]


def test_eligible_defers_for_bounded_time_and_yields_to_snapshot_generations() -> None:
    eligible = job(state=QueueState.ELIGIBLE)
    token = YieldToken(ACCOUNT_B, JobGeneration(4))
    deferred = defer(eligible, now=100.0, reconcile_at=400.0, yield_set=(token,))

    assert deferred.state is QueueState.DEFERRED
    assert deferred.reconcile_at == 400.0
    assert deferred.yield_set == (token,)
    with pytest.raises(SchedulerContractError, match="fairness barrier"):
        mark_eligible(deferred, now=400.0, unresolved_yield_set=(token,))
    restored = mark_eligible(deferred, now=400.0, unresolved_yield_set=())
    assert restored.state is QueueState.ELIGIBLE
    assert restored.due_at == eligible.due_at


@pytest.mark.parametrize(
    ("reconcile_at", "yield_set"),
    [
        (100.0, ()),
        (400.0, (YieldToken(ACCOUNT_A, JobGeneration(2)),)),
        (
            400.0,
            (
                YieldToken(ACCOUNT_B, JobGeneration(2)),
                YieldToken(ACCOUNT_B, JobGeneration(3)),
            ),
        ),
    ],
)
def test_direct_deferred_construction_rejects_invalid_reconciliation_and_yield_state(
    reconcile_at: float,
    yield_set: tuple[YieldToken, ...],
) -> None:
    with pytest.raises(SchedulerContractError):
        replace(
            job(state=QueueState.ELIGIBLE),
            state=QueueState.DEFERRED,
            reconcile_at=reconcile_at,
            yield_set=yield_set,
        )


def test_persisted_deferred_reconciliation_deadline_may_already_be_overdue() -> None:
    deferred = replace(
        job(state=QueueState.ELIGIBLE),
        state=QueueState.DEFERRED,
        reconcile_at=400.0,
    )

    assert mark_eligible(deferred, now=500.0).state is QueueState.ELIGIBLE


def test_quarantine_rejects_an_equality_forging_non_state_prior_value() -> None:
    class ForgedPriorState:
        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(SchedulerContractError, match="prior state"):
        replace(
            job(),
            state=QueueState.QUARANTINED,
            prior_state=ForgedPriorState(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("offset", [299.999, 3600.001])
def test_deferral_rejects_reconciliation_outside_inclusive_five_to_sixty_minutes(offset: float) -> None:
    with pytest.raises(SchedulerContractError, match="reconciliation"):
        defer(job(state=QueueState.ELIGIBLE), now=100.0, reconcile_at=100.0 + offset, yield_set=())


def test_deferral_rejects_attribute_compatible_non_scheduler_limits() -> None:
    fake = type("FakeSchedulerLimits", (), {})()
    fake.reconciliation_min_seconds = 0
    fake.reconciliation_max_seconds = 10_000

    with pytest.raises(SchedulerContractError, match="SchedulerLimits"):
        defer(
            job(state=QueueState.ELIGIBLE),
            now=100.0,
            reconcile_at=101.0,
            yield_set=(),
            limits=fake,  # type: ignore[arg-type]
        )


def test_quarantine_preserves_due_lane_and_previous_state_then_restores_it() -> None:
    deferred = defer(job(state=QueueState.ELIGIBLE, lane=Lane.BUILDER), now=100.0, reconcile_at=400.0, yield_set=())
    locked = quarantine(deferred)
    assert locked.state is QueueState.QUARANTINED
    assert locked.prior_state is QueueState.DEFERRED
    assert locked.pending_lane is Lane.BUILDER
    assert release_quarantine(locked).state is QueueState.DEFERRED


@pytest.mark.parametrize(
    ("operation", "value"),
    [
        ("eligible", job(state=QueueState.ADMITTED)),
        ("admit", job(state=QueueState.WAITING)),
        ("defer", job(state=QueueState.WAITING)),
        ("release", job(state=QueueState.ELIGIBLE)),
    ],
)
def test_representative_illegal_or_stale_transitions_fail_closed(operation: str, value: QueueJob) -> None:
    with pytest.raises(SchedulerContractError):
        if operation == "eligible":
            mark_eligible(value, now=100.0)
        elif operation == "admit":
            admit(value, visit_generation=VisitGeneration(1), visit_nonce=NONCE, now=100.0)
        elif operation == "defer":
            defer(value, now=100.0, reconcile_at=400.0, yield_set=())
        else:
            release_quarantine(value)


@pytest.mark.parametrize("subclass", [False, True])
@pytest.mark.parametrize("operation", ["eligible", "defer", "quarantine", "release", "admit"])
def test_job_helpers_reject_non_exact_queue_jobs(operation: str, subclass: bool) -> None:
    source = {
        "eligible": job(),
        "defer": job(state=QueueState.ELIGIBLE),
        "quarantine": job(),
        "release": quarantine(job()),
        "admit": job(state=QueueState.ELIGIBLE),
    }[operation]
    value = non_exact_job(source, subclass=subclass)

    with pytest.raises(SchedulerContractError, match="exact QueueJob"):
        if operation == "eligible":
            mark_eligible(value, now=100.0)  # type: ignore[arg-type]
        elif operation == "defer":
            defer(value, now=100.0, reconcile_at=400.0, yield_set=())  # type: ignore[arg-type]
        elif operation == "quarantine":
            quarantine(value)  # type: ignore[arg-type]
        elif operation == "release":
            release_quarantine(value)  # type: ignore[arg-type]
        else:
            admit(  # type: ignore[arg-type]
                value,
                visit_generation=VisitGeneration(1),
                visit_nonce=NONCE,
                now=100.0,
            )


def test_stale_selection_and_stale_admission_fail_closed() -> None:
    eligible = job(state=QueueState.ELIGIBLE)
    assert select_oldest_eligible((eligible,), now=99.0, account_order=(ACCOUNT_A,), tie_after=None) is None
    with pytest.raises(SchedulerContractError, match="currently eligible"):
        admit(eligible, visit_generation=VisitGeneration(1), visit_nonce=NONCE, now=99.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "100"])
def test_non_finite_or_non_numeric_times_are_rejected(bad: object) -> None:
    with pytest.raises(SchedulerContractError, match="finite"):
        QueueJob(
            ACCOUNT_A,
            CONFIGURATION,
            JobGeneration(1),
            AvailabilityEvent("a" * 32),
            50.0,
            bad,  # type: ignore[arg-type]
            Lane.HOME,
            QueueState.WAITING,
        )


def test_closed_enums_and_typed_positive_generations_reject_unknowns_and_boundaries() -> None:
    assert {state.value for state in QueueState} == {"WAITING", "ELIGIBLE", "DEFERRED", "QUARANTINED", "ADMITTED"}
    assert {lane.value for lane in Lane} == {"HOME", "BUILDER"}
    for constructor in (JobGeneration, VisitGeneration):
        with pytest.raises(SchedulerContractError):
            constructor(0)
        with pytest.raises(SchedulerContractError):
            constructor(True)


def test_every_legal_transition_is_closed_and_admitted_completion_creates_one_fresh_job() -> None:
    assert ALLOWED_TRANSITIONS == {
        QueueState.WAITING: frozenset({QueueState.ELIGIBLE, QueueState.QUARANTINED}),
        QueueState.ELIGIBLE: frozenset({QueueState.ADMITTED, QueueState.DEFERRED, QueueState.QUARANTINED}),
        QueueState.DEFERRED: frozenset({QueueState.ELIGIBLE, QueueState.QUARANTINED}),
        QueueState.QUARANTINED: frozenset({QueueState.WAITING, QueueState.ELIGIBLE, QueueState.DEFERRED}),
        QueueState.ADMITTED: frozenset({QueueState.WAITING, QueueState.DEFERRED, QueueState.QUARANTINED}),
    }

    admitted = admit(
        job(state=QueueState.ELIGIBLE),
        visit_generation=VisitGeneration(1),
        visit_nonce=NONCE,
        now=100.0,
    )
    successor = complete_visit(
        admitted,
        next_generation=JobGeneration(2),
        availability_event=AvailabilityEvent("b" * 32),
        available_at=200.0,
        due_at=200.0,
        pending_lane=Lane.HOME,
        next_state=QueueState.WAITING,
    )
    assert successor.state is QueueState.WAITING
    assert successor.job_generation == JobGeneration(2)
    assert successor.availability_event == AvailabilityEvent("b" * 32)
    assert successor.due_at == 200.0

    with pytest.raises(SchedulerContractError, match="fresh"):
        complete_visit(
            admitted,
            next_generation=JobGeneration(1),
            availability_event=AvailabilityEvent("b" * 32),
            available_at=200.0,
            due_at=200.0,
            pending_lane=Lane.HOME,
            next_state=QueueState.WAITING,
        )


def test_visit_completion_rejects_an_unbound_direct_admitted_job() -> None:
    with pytest.raises(SchedulerContractError, match="AdmittedVisit"):
        complete_visit(
            job(state=QueueState.ADMITTED),  # type: ignore[arg-type]
            next_generation=JobGeneration(2),
            availability_event=AvailabilityEvent("b" * 32),
            available_at=200.0,
            due_at=200.0,
            pending_lane=Lane.HOME,
            next_state=QueueState.WAITING,
        )


@pytest.mark.parametrize("kind", ["fake", "subclass"])
def test_visit_completion_rejects_non_exact_admitted_visits(kind: str) -> None:
    admitted = admit(
        job(state=QueueState.ELIGIBLE),
        visit_generation=VisitGeneration(1),
        visit_nonce=NONCE,
        now=100.0,
    )
    if kind == "subclass":
        derived_type = type("DerivedAdmittedVisit", (AdmittedVisit,), {})
        value = derived_type(admitted.job, admitted.admission)
    else:
        fake_type = type("FakeAdmittedVisit", (), {})
        value = fake_type()
        value.job = admitted.job
        value.admission = admitted.admission

    with pytest.raises(SchedulerContractError, match="exact AdmittedVisit"):
        complete_visit(  # type: ignore[arg-type]
            value,
            next_generation=JobGeneration(2),
            availability_event=AvailabilityEvent("b" * 32),
            available_at=200.0,
            due_at=200.0,
            pending_lane=Lane.HOME,
            next_state=QueueState.WAITING,
        )
