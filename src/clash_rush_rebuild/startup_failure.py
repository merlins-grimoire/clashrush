"""Closed startup failure envelope; adapted BasePilot worker/core separation.

No raw exception is retained. Callers raise the copied envelope only AFTER their
except blocks, so neither __cause__ nor __context__ retains private native data.
"""
from dataclasses import dataclass
from enum import StrEnum


class StartupReason(StrEnum):
    COMPOSITION = "COMPOSITION"
    MUTEX = "MUTEX"
    REGISTRY = "REGISTRY"
    STATE = "STATE"
    PLAYER_ENUMERATION = "PLAYER_ENUMERATION"
    AUTHORIZATION = "AUTHORIZATION"
    JOB_SETUP = "JOB_SETUP"
    PROCESS_CREATE = "PROCESS_CREATE"
    IDENTITY = "IDENTITY"
    ASSIGNMENT = "ASSIGNMENT"
    RESUME = "RESUME"
    THREAD_CLOSE = "THREAD_CLOSE"
    MEMBERSHIP = "MEMBERSHIP"
    WINDOW_BINDING = "WINDOW_BINDING"
    CAPTURE_READINESS = "CAPTURE_READINESS"
    STOP_PROOF = "STOP_PROOF"
    SUCCESSOR_PLAN = "SUCCESSOR_PLAN"
    ROLLBACK_UNPROVED = "ROLLBACK_UNPROVED"
    GEOMETRY_BINDING = "GEOMETRY_BINDING"
    GEOMETRY_RESTORE = "GEOMETRY_RESTORE"
    GEOMETRY_BOUNDS = "GEOMETRY_BOUNDS"
    GEOMETRY_RESIZE = "GEOMETRY_RESIZE"
    GEOMETRY_VERIFY = "GEOMETRY_VERIFY"
    GEOMETRY_CAPTURE = "GEOMETRY_CAPTURE"
    EVIDENCE_INIT = "EVIDENCE_INIT"
    INITIAL_CAPTURE = "INITIAL_CAPTURE"
    INITIAL_EVIDENCE = "INITIAL_EVIDENCE"
    ICON_VERIFY = "ICON_VERIFY"
    RECOGNITION = "RECOGNITION"
    CAPTURE = "CAPTURE"
    EVIDENCE_BEFORE = "EVIDENCE_BEFORE"
    INPUT = "INPUT"
    POST_CAPTURE = "POST_CAPTURE"
    POST_EVIDENCE = "POST_EVIDENCE"
    FINAL_EVIDENCE = "FINAL_EVIDENCE"
    CLEANUP = "CLEANUP"


@dataclass(frozen=True, slots=True)
class StartupFault:
    reason: StartupReason
    prior: tuple[StartupReason, ...] = ()
    input_completed: bool = False

    def __post_init__(self):
        if (type(self.reason) is not StartupReason or type(self.prior) is not tuple
                or any(type(r) is not StartupReason for r in self.prior)
                or type(self.input_completed) is not bool):
            raise ValueError("invalid startup fault")

    def superseded(self, reason):
        return StartupFault(reason, self.prior + (self.reason,), self.input_completed)


class StartupFailure(RuntimeError):
    def __init__(self, reason, *, prior=(), input_completed=False):
        self.fault = StartupFault(reason, prior, input_completed)
        super().__init__(reason.value)

    @property
    def reason(self):
        return self.fault.reason

    @property
    def prior(self):
        return self.fault.prior

    @property
    def input_completed(self):
        return self.fault.input_completed


def fault_from(exc, fallback, *, input_completed=False):
    """Copy only exact closed fields, never the exception or its traceback."""
    fault = getattr(exc, "fault", None) if isinstance(exc, StartupFailure) else None
    if type(fault) is StartupFault:
        return StartupFault(fault.reason, fault.prior, fault.input_completed or input_completed)
    return StartupFault(fallback, input_completed=input_completed)


def raise_fault(fault, error_type=StartupFailure):
    raise error_type(fault.reason, prior=fault.prior, input_completed=fault.input_completed) from None


def at_stage(reason, operation, *, error_type=StartupFailure):
    fault = None
    try:
        return operation()
    except BaseException as exc:
        fault = fault_from(exc, reason)
    raise_fault(fault, error_type)
