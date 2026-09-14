"""Public mutex-owning façade for one inert lifecycle visit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .lifecycle import PlayerBinding, StopRecord
from .lifecycle_state import LifecycleStateStore, Ready
from .registry import Slot
from .win32_runtime import DEFAULT_MUTEX_NAME


class CycleError(RuntimeError):
    """The inert visit could not complete without weakening lifecycle safety."""


class MutexLeasePort(Protocol):
    abandoned: bool

    def require_usable(self) -> None: ...

    def release(self) -> None: ...


class MutexRuntimePort(Protocol):
    def acquire_mutex(self) -> MutexLeasePort: ...


class SupervisorPort(Protocol):
    def start(self, slot: Slot) -> PlayerBinding: ...

    def stop(self, binding: PlayerBinding, proof: None) -> StopRecord: ...


class InertCycle:
    """Hold the protected host-wide mutex across discovery, launch, and stop."""

    def __init__(
        self,
        runtime: MutexRuntimePort,
        *,
        load_registry: Callable[[], tuple[Slot, ...]],
        make_state_store: Callable[[], LifecycleStateStore],
        observe_player_count: Callable[[], int],
        make_supervisor: Callable[[LifecycleStateStore, str], SupervisorPort],
    ) -> None:
        self._runtime = runtime
        self._load_registry = load_registry
        self._make_state_store = make_state_store
        self._observe_player_count = observe_player_count
        self._make_supervisor = make_supervisor
        self._disabled = False

    def initialize(self) -> Ready:
        if self._disabled:
            raise CycleError("lifecycle process is permanently disabled")
        lease = self._runtime.acquire_mutex()
        try:
            if lease.abandoned:
                raise CycleError("abandoned mutex requires operator reconciliation")
            lease.require_usable()
            slots = self._load_registry()
            if (
                type(slots) is not tuple
                or len(slots) != 5
                or tuple(slot.index for slot in slots) != (0, 1, 2, 3, 4)
            ):
                raise CycleError("exact ordered five-slot registry required")
            count = self._observe_player_count()
            if type(count) is not int or count != 0:
                raise CycleError("pre-existing player blocks lifecycle initialization")
            return self._make_state_store().initialize_ready()
        finally:
            try:
                lease.release()
            except BaseException as exc:
                self._disabled = True
                raise CycleError("lifecycle process is permanently disabled") from exc

    def visit_once(self) -> StopRecord:
        if self._disabled:
            raise CycleError("lifecycle process is permanently disabled")
        lease = self._runtime.acquire_mutex()
        try:
            if lease.abandoned:
                store = self._make_state_store()
                store.load()
                count = self._observe_player_count()
                if type(count) is not int or count < 0:
                    raise CycleError("read-only player reconciliation failed")
                raise CycleError("abandoned mutex requires operator reconciliation")

            lease.require_usable()
            slots = self._load_registry()
            if (
                type(slots) is not tuple
                or len(slots) != 5
                or tuple(slot.index for slot in slots) != (0, 1, 2, 3, 4)
            ):
                raise CycleError("exact ordered five-slot registry required")
            store = self._make_state_store()
            state = store.load()
            if type(state) is not Ready:
                raise CycleError("ACTIVE lifecycle requires operator reconciliation")
            count = self._observe_player_count()
            if type(count) is not int or count != 0:
                raise CycleError("pre-existing player blocks inert launch")
            supervisor = self._make_supervisor(store, DEFAULT_MUTEX_NAME)
            binding = supervisor.start(slots[state.next_slot])
            return supervisor.stop(binding, None)
        finally:
            try:
                lease.release()
            except BaseException as exc:
                self._disabled = True
                raise CycleError("lifecycle process is permanently disabled") from exc
