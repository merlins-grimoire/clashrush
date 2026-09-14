"""Pure, closed contracts for the durable due-account scheduler.

This module defines values and transitions only.  It performs no persistence,
clock reads, random generation, lifecycle work, process work, or transport.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType

from .configuration_v2 import AccountKey, ConfigurationGeneration


_NONCE = re.compile(r"[0-9a-f]{32}")


class SchedulerContractError(ValueError):
    """A scheduler value or transition is outside the frozen contract."""


class QueueState(Enum):
    WAITING = "WAITING"
    ELIGIBLE = "ELIGIBLE"
    DEFERRED = "DEFERRED"
    QUARANTINED = "QUARANTINED"
    ADMITTED = "ADMITTED"


class Lane(Enum):
    HOME = "HOME"
    BUILDER = "BUILDER"


ALLOWED_TRANSITIONS = MappingProxyType({
    QueueState.WAITING: frozenset({QueueState.ELIGIBLE, QueueState.QUARANTINED}),
    QueueState.ELIGIBLE: frozenset(
        {QueueState.ADMITTED, QueueState.DEFERRED, QueueState.QUARANTINED}
    ),
    QueueState.DEFERRED: frozenset({QueueState.ELIGIBLE, QueueState.QUARANTINED}),
    QueueState.QUARANTINED: frozenset(
        {QueueState.WAITING, QueueState.ELIGIBLE, QueueState.DEFERRED}
    ),
    QueueState.ADMITTED: frozenset(
        {QueueState.WAITING, QueueState.DEFERRED, QueueState.QUARANTINED}
    ),
})


def _positive_generation(value: object, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise SchedulerContractError(f"{label} must be a positive integer")


def _finite(value: object, label: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SchedulerContractError(f"{label} must be finite")


@dataclass(frozen=True, slots=True)
class JobGeneration:
    value: int

    def __post_init__(self) -> None:
        _positive_generation(self.value, "job generation")


@dataclass(frozen=True, slots=True)
class VisitGeneration:
    value: int

    def __post_init__(self) -> None:
        _positive_generation(self.value, "visit generation")


@dataclass(frozen=True, slots=True)
class AvailabilityEvent:
    """Opaque identity of the original observed availability event."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _NONCE.fullmatch(self.value) is None:
            raise SchedulerContractError(
                "availability event must be 32 lowercase hex characters"
            )


@dataclass(frozen=True, slots=True)
class YieldToken:
    """One exact competing job generation that must receive a fair yield."""

    account_key: AccountKey
    job_generation: JobGeneration

    def __post_init__(self) -> None:
        if type(self.account_key) is not AccountKey:
            raise SchedulerContractError("yield account key is invalid")
        if type(self.job_generation) is not JobGeneration:
            raise SchedulerContractError("yield job generation is invalid")


@dataclass(frozen=True, slots=True)
class SchedulerLimits:
    reconciliation_min_seconds: int = 300
    reconciliation_max_seconds: int = 3600
    visit_seconds: int = 600
    initial_home_seconds: int = 300
    initial_builder_seconds: int = 300
    max_total_iterations: int = 1000
    max_consecutive_failures: int = 3

    def __post_init__(self) -> None:
        if (
            type(self.max_total_iterations) is not int
            or self.max_total_iterations != 1000
            or type(self.max_consecutive_failures) is not int
            or self.max_consecutive_failures != 3
        ):
            raise SchedulerContractError("run limits must be exactly 1000 iterations and 3 failures")
        integer_values = tuple(getattr(self, field) for field in self.__slots__)
        if any(type(value) is not int or value <= 0 for value in integer_values):
            raise SchedulerContractError("scheduler limits must be positive integers")
        if self.reconciliation_min_seconds != 300 or self.reconciliation_max_seconds != 3600:
            raise SchedulerContractError("reconciliation bounds must be exactly 5 to 60 minutes")
        if self.visit_seconds != 600:
            raise SchedulerContractError("visit bound must be exactly 10 minutes")
        if self.initial_home_seconds != 300 or self.initial_builder_seconds != 300:
            raise SchedulerContractError("initial lane opportunities must be exactly 5/5 minutes")
        if self.initial_home_seconds + self.initial_builder_seconds != self.visit_seconds:
            raise SchedulerContractError("initial lane opportunities must fill the visit")


DEFAULT_LIMITS = SchedulerLimits()


@dataclass(frozen=True, slots=True)
class QueueJob:
    account_key: AccountKey
    configuration_generation: ConfigurationGeneration
    job_generation: JobGeneration
    availability_event: AvailabilityEvent
    available_at: float
    due_at: float
    pending_lane: Lane
    state: QueueState
    reconcile_at: float | None = None
    yield_set: tuple[YieldToken, ...] = ()
    prior_state: QueueState | None = None

    def __post_init__(self) -> None:
        if type(self.account_key) is not AccountKey:
            raise SchedulerContractError("account key is invalid")
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise SchedulerContractError("configuration generation is invalid")
        if type(self.job_generation) is not JobGeneration:
            raise SchedulerContractError("job generation is invalid")
        if type(self.availability_event) is not AvailabilityEvent:
            raise SchedulerContractError("availability event is invalid")
        _finite(self.available_at, "availability time")
        _finite(self.due_at, "deadline")
        if self.due_at < self.available_at:
            raise SchedulerContractError("deadline cannot precede availability")
        if type(self.pending_lane) is not Lane or type(self.state) is not QueueState:
            raise SchedulerContractError("queue lane or state is invalid")
        if self.prior_state is not None and type(self.prior_state) is not QueueState:
            raise SchedulerContractError("queue prior state is invalid")
        if type(self.yield_set) is not tuple or any(
            type(token) is not YieldToken for token in self.yield_set
        ):
            raise SchedulerContractError("yield set is invalid")
        if len(set(self.yield_set)) != len(self.yield_set):
            raise SchedulerContractError("yield set must not contain duplicates")
        yield_accounts = tuple(token.account_key for token in self.yield_set)
        if len(set(yield_accounts)) != len(yield_accounts):
            raise SchedulerContractError("yield set must contain at most one generation per account")
        if self.account_key in yield_accounts:
            raise SchedulerContractError("a job cannot yield to its own account")
        if self.reconcile_at is not None:
            _finite(self.reconcile_at, "reconciliation time")
        if self.state is QueueState.DEFERRED:
            if self.reconcile_at is None or self.prior_state is not None:
                raise SchedulerContractError("deferred state requires one reconciliation deadline")
            if self.reconcile_at <= self.due_at:
                raise SchedulerContractError("reconciliation deadline must follow the job deadline")
        elif self.state is QueueState.QUARANTINED:
            if self.prior_state not in (
                QueueState.WAITING,
                QueueState.ELIGIBLE,
                QueueState.DEFERRED,
            ):
                raise SchedulerContractError("quarantine must preserve a restorable state")
            if self.prior_state is QueueState.DEFERRED and self.reconcile_at is None:
                raise SchedulerContractError("quarantined deferral lost its deadline")
            if (
                self.prior_state is QueueState.DEFERRED
                and self.reconcile_at is not None
                and self.reconcile_at <= self.due_at
            ):
                raise SchedulerContractError("reconciliation deadline must follow the job deadline")
            if self.prior_state is not QueueState.DEFERRED and (
                self.reconcile_at is not None or self.yield_set
            ):
                raise SchedulerContractError("quarantine metadata does not match prior state")
        elif self.reconcile_at is not None or self.yield_set or self.prior_state is not None:
            raise SchedulerContractError("queue state carries forbidden transition metadata")


def _require_exact_job(job: object) -> None:
    if type(job) is not QueueJob:
        raise SchedulerContractError("job must be an exact QueueJob")


@dataclass(frozen=True, slots=True)
class VisitAdmission:
    configuration_generation: ConfigurationGeneration
    account_key: AccountKey
    job_generation: JobGeneration
    visit_generation: VisitGeneration
    visit_nonce: str

    def __post_init__(self) -> None:
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise SchedulerContractError("admission configuration generation is invalid")
        if type(self.account_key) is not AccountKey:
            raise SchedulerContractError("admission account key is invalid")
        if type(self.job_generation) is not JobGeneration:
            raise SchedulerContractError("admission job generation is invalid")
        if type(self.visit_generation) is not VisitGeneration:
            raise SchedulerContractError("admission visit generation is invalid")
        if type(self.visit_nonce) is not str or _NONCE.fullmatch(self.visit_nonce) is None:
            raise SchedulerContractError("visit nonce must be 32 lowercase hex characters")


@dataclass(frozen=True, slots=True)
class AdmittedVisit:
    job: QueueJob
    admission: VisitAdmission

    def __post_init__(self) -> None:
        if type(self.job) is not QueueJob:
            raise SchedulerContractError("admitted visit job is invalid")
        if type(self.admission) is not VisitAdmission:
            raise SchedulerContractError("admitted visit admission is invalid")
        if self.job.state is not QueueState.ADMITTED:
            raise SchedulerContractError("admitted visit job must be admitted")
        if self.job.account_key != self.admission.account_key:
            raise SchedulerContractError("admitted visit account does not match")
        if self.job.configuration_generation != self.admission.configuration_generation:
            raise SchedulerContractError("admitted visit configuration generation does not match")
        if self.job.job_generation != self.admission.job_generation:
            raise SchedulerContractError("admitted visit job generation does not match")

    @property
    def visit_nonce(self) -> str:
        return self.admission.visit_nonce

    @property
    def visit_generation(self) -> VisitGeneration:
        return self.admission.visit_generation

    @property
    def job_generation(self) -> JobGeneration:
        return self.admission.job_generation

    @property
    def configuration_generation(self) -> ConfigurationGeneration:
        return self.admission.configuration_generation


def mark_eligible(
    job: QueueJob,
    *,
    now: float,
    unresolved_yield_set: tuple[YieldToken, ...] = (),
) -> QueueJob:
    _require_exact_job(job)
    _finite(now, "current time")
    if type(unresolved_yield_set) is not tuple or any(
        type(token) is not YieldToken for token in unresolved_yield_set
    ):
        raise SchedulerContractError("unresolved yield set is invalid")
    if job.state is QueueState.WAITING:
        if now < job.due_at:
            raise SchedulerContractError("persisted deadline has not arrived")
    elif job.state is QueueState.DEFERRED:
        if now < job.reconcile_at:  # type: ignore[operator]
            raise SchedulerContractError("reconciliation deadline has not arrived")
        if unresolved_yield_set:
            raise SchedulerContractError("deferred fairness barrier is unresolved")
    else:
        raise SchedulerContractError("only waiting or deferred work can become eligible")
    return replace(
        job,
        state=QueueState.ELIGIBLE,
        reconcile_at=None,
        yield_set=(),
        prior_state=None,
    )


def defer(
    job: QueueJob,
    *,
    now: float,
    reconcile_at: float,
    yield_set: tuple[YieldToken, ...],
    limits: SchedulerLimits = DEFAULT_LIMITS,
) -> QueueJob:
    _require_exact_job(job)
    _finite(now, "current time")
    _finite(reconcile_at, "reconciliation time")
    if type(limits) is not SchedulerLimits:
        raise SchedulerContractError("limits must be exact SchedulerLimits")
    if job.state not in (QueueState.ELIGIBLE, QueueState.ADMITTED):
        raise SchedulerContractError("only eligible or admitted work can be deferred")
    delay = reconcile_at - now
    if not limits.reconciliation_min_seconds <= delay <= limits.reconciliation_max_seconds:
        raise SchedulerContractError("reconciliation must be within inclusive 5 to 60 minute bounds")
    return replace(
        job,
        state=QueueState.DEFERRED,
        reconcile_at=reconcile_at,
        yield_set=yield_set,
        prior_state=None,
    )


def quarantine(job: QueueJob) -> QueueJob:
    _require_exact_job(job)
    if job.state not in (QueueState.WAITING, QueueState.ELIGIBLE, QueueState.DEFERRED):
        raise SchedulerContractError("queue state cannot enter quarantine")
    return replace(job, state=QueueState.QUARANTINED, prior_state=job.state)


def release_quarantine(job: QueueJob) -> QueueJob:
    _require_exact_job(job)
    if job.state is not QueueState.QUARANTINED or job.prior_state is None:
        raise SchedulerContractError("only quarantined work can be released")
    return replace(job, state=job.prior_state, prior_state=None)


def admit(
    job: QueueJob,
    *,
    visit_generation: VisitGeneration,
    visit_nonce: str,
    now: float,
) -> AdmittedVisit:
    _require_exact_job(job)
    _finite(now, "current time")
    if job.state is not QueueState.ELIGIBLE or now < job.due_at:
        raise SchedulerContractError("job is not currently eligible")
    admission = VisitAdmission(
        job.configuration_generation,
        job.account_key,
        job.job_generation,
        visit_generation,
        visit_nonce,
    )
    return AdmittedVisit(replace(job, state=QueueState.ADMITTED), admission)


def complete_visit(
    admitted_visit: AdmittedVisit,
    *,
    next_generation: JobGeneration,
    availability_event: AvailabilityEvent,
    available_at: float,
    due_at: float,
    pending_lane: Lane,
    next_state: QueueState,
) -> QueueJob:
    """Consume one admission into one fresh waiting or quarantined successor."""
    if type(admitted_visit) is not AdmittedVisit:
        raise SchedulerContractError("visit completion requires an exact AdmittedVisit")
    job = admitted_visit.job
    if (
        type(next_generation) is not JobGeneration
        or next_generation.value <= job.job_generation.value
    ):
        raise SchedulerContractError("visit completion requires a fresh job generation")
    if next_state not in (QueueState.WAITING, QueueState.QUARANTINED):
        raise SchedulerContractError("visit completion successor state is invalid")
    return QueueJob(
        account_key=job.account_key,
        configuration_generation=job.configuration_generation,
        job_generation=next_generation,
        availability_event=availability_event,
        available_at=available_at,
        due_at=due_at,
        pending_lane=pending_lane,
        state=next_state,
        prior_state=(QueueState.WAITING if next_state is QueueState.QUARANTINED else None),
    )


def select_oldest_eligible(
    jobs: tuple[QueueJob, ...],
    *,
    now: float,
    account_order: tuple[AccountKey, ...],
    tie_after: AccountKey | None,
) -> QueueJob | None:
    """Select by persisted deadline, rotating only the oldest equal-deadline set."""
    _finite(now, "current time")
    if type(jobs) is not tuple or type(account_order) is not tuple:
        raise SchedulerContractError("jobs and account order must be tuples")
    if any(type(item) is not QueueJob for item in jobs):
        raise SchedulerContractError("jobs must contain exact QueueJob values")
    if any(type(key) is not AccountKey for key in account_order):
        raise SchedulerContractError("account order must contain unique account keys")
    if len(set(account_order)) != len(account_order):
        raise SchedulerContractError("account order must contain unique account keys")
    if tie_after is not None and type(tie_after) is not AccountKey:
        raise SchedulerContractError("tie cursor must be an exact AccountKey or None")
    if tie_after is not None and tie_after not in account_order:
        raise SchedulerContractError("tie cursor is not in account order")
    candidates = tuple(
        item
        for item in jobs
        if item.state is QueueState.ELIGIBLE and item.due_at <= now
    )
    if not candidates:
        return None
    candidate_accounts = tuple(item.account_key for item in candidates)
    if len(set(candidate_accounts)) != len(candidate_accounts):
        raise SchedulerContractError("account has multiple candidate jobs")
    if any(item.account_key not in account_order for item in candidates):
        raise SchedulerContractError("eligible job is absent from account order")
    oldest = min(item.due_at for item in candidates)
    tied = {item.account_key: item for item in candidates if item.due_at == oldest}
    start = 0 if tie_after is None else (account_order.index(tie_after) + 1) % len(account_order)
    rotated = account_order[start:] + account_order[:start]
    return next(tied[key] for key in rotated if key in tied)
