"""Atomic scheduler admission handoff to schema-v2 lifecycle start."""

from __future__ import annotations

from dataclasses import dataclass

from .configuration_v2 import AccountKey
from .lifecycle import PlayerBinding
from .lifecycle_state_v2 import ReadyV2
from .lifecycle_v2 import SchemaV2LifecycleSupervisor
from .scheduler_contract import AdmittedVisit
from .scheduler_store import SQLiteJobStore


class SchedulerAdmissionError(RuntimeError):
    """The durable admission could not be handed to lifecycle exactly."""


@dataclass(frozen=True, slots=True)
class StartedAdmission:
    admitted_visit: AdmittedVisit
    binding: PlayerBinding

    def __post_init__(self) -> None:
        if type(self.admitted_visit) is not AdmittedVisit:
            raise SchedulerAdmissionError("started admission requires an exact admitted visit")
        if type(self.binding) is not PlayerBinding:
            raise SchedulerAdmissionError("started admission requires an exact player binding")


def admit_and_start_oldest(
    store: SQLiteJobStore,
    lifecycle: SchemaV2LifecycleSupervisor,
    *,
    ready: ReadyV2,
    now: float,
    account_order: tuple[AccountKey, ...],
) -> StartedAdmission | None:
    """Persist one admission, then pass its exact identity and nonce to lifecycle."""
    if type(store) is not SQLiteJobStore:
        raise SchedulerAdmissionError("admission requires an exact SQLite job store")
    if type(lifecycle) is not SchemaV2LifecycleSupervisor:
        raise SchedulerAdmissionError("admission requires an exact schema-v2 lifecycle")
    admitted = SQLiteJobStore.admit_oldest(
        store,
        ready=ready,
        now=now,
        account_order=account_order,
    )
    if admitted is None:
        return None
    admission = admitted.admission
    binding = SchemaV2LifecycleSupervisor.start_admitted(
        lifecycle,
        admission.account_key,
        admission.configuration_generation,
        admission.visit_nonce,
    )
    if type(binding) is not PlayerBinding:
        raise SchedulerAdmissionError("lifecycle returned a malformed player binding")
    return StartedAdmission(admitted, binding)
