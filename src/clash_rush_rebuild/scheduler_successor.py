"""Immutable successor plans for one admitted scheduler visit."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .scheduler_contract import AdmittedVisit, Lane, QueueJob, QueueState


class SuccessorPlanError(ValueError):
    """A successor plan is outside the closed scheduler contract."""


class SuccessorKind(Enum):
    IMMEDIATE_FREE = "IMMEDIATE_FREE"
    FUTURE_COMPLETION = "FUTURE_COMPLETION"
    RECONCILE_UNKNOWN = "RECONCILE_UNKNOWN"


@dataclass(frozen=True, slots=True)
class SuccessorPlan:
    admitted_visit: AdmittedVisit
    plan_nonce: str
    kind: SuccessorKind
    offset_seconds: int
    successor: QueueJob

    def __post_init__(self) -> None:
        if type(self.admitted_visit) is not AdmittedVisit:
            raise SuccessorPlanError("successor plan visit is invalid")
        if (
            type(self.plan_nonce) is not str
            or len(self.plan_nonce) != 32
            or any(character not in "0123456789abcdef" for character in self.plan_nonce)
        ):
            raise SuccessorPlanError("successor plan nonce is invalid")
        if type(self.kind) is not SuccessorKind:
            raise SuccessorPlanError("successor plan kind is invalid")
        if type(self.offset_seconds) is not int:
            raise SuccessorPlanError("successor plan offset is invalid")
        if type(self.successor) is not QueueJob:
            raise SuccessorPlanError("successor plan job is invalid")

        source = self.admitted_visit.job
        successor = self.successor
        if (
            successor.account_key != source.account_key
            or successor.configuration_generation != source.configuration_generation
            or successor.job_generation.value != source.job_generation.value + 1
        ):
            raise SuccessorPlanError("successor plan identity is invalid")
        times = (successor.available_at, successor.due_at)
        if successor.reconcile_at is not None:
            times += (successor.reconcile_at,)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in times):
            raise SuccessorPlanError("successor plan time is invalid")
        if successor.prior_state is not None:
            raise SuccessorPlanError("successor plan carries forbidden metadata")

        if self.kind is SuccessorKind.IMMEDIATE_FREE:
            valid = (
                self.offset_seconds == 0
                and successor.state is QueueState.WAITING
                and successor.available_at == successor.due_at
                and successor.reconcile_at is None
                and not successor.yield_set
            )
        elif self.kind is SuccessorKind.FUTURE_COMPLETION:
            valid = (
                0 <= self.offset_seconds <= 3600
                and successor.state is QueueState.WAITING
                and successor.reconcile_at is None
                and successor.due_at == successor.available_at + self.offset_seconds
                and not successor.yield_set
            )
        else:
            valid = (
                300 <= self.offset_seconds <= 3600
                and successor.state is QueueState.DEFERRED
                and successor.due_at == successor.available_at
                and successor.reconcile_at
                == successor.available_at + self.offset_seconds
            )
        if not valid:
            raise SuccessorPlanError("successor plan timing or state is invalid")
        if type(successor.pending_lane) is not Lane:
            raise SuccessorPlanError("successor plan lane is invalid")
