from __future__ import annotations

from dataclasses import dataclass

import pytest

from clash_rush_rebuild.cycle import CycleError, InertCycle, NoInputDiagnosticCycle
from clash_rush_rebuild.no_input_home_diagnostic import HomeDiagnosticResult
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity, StopRecord
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.registry import Slot


@dataclass
class FakeLease:
    events: list[str]
    abandoned: bool = False
    fail_release: bool = False

    def require_usable(self) -> None:
        self.events.append("mutex:require-usable")
        if self.abandoned:
            raise RuntimeError("abandoned")

    def release(self) -> None:
        self.events.append("mutex:release")
        if self.fail_release:
            raise RuntimeError("release failed")


class FakeRuntime:
    def __init__(self, events: list[str], lease: FakeLease) -> None:
        self.events = events
        self.lease = lease

    def acquire_mutex(self) -> FakeLease:
        self.events.append("mutex:acquire")
        return self.lease


class FakeStateStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def load(self) -> Ready:
        self.events.append("state:load")
        return Ready(0)

    def initialize_ready(self) -> Ready:
        self.events.append("state:initialize")
        return Ready(0)


class FakeSupervisor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.binding = PlayerBinding(
            ProcessIdentity(100, 9001),
            ProcessIdentity(100, 9001),
            101,
            102,
            1280,
            720,
            "a" * 32,
        )

    def start(self, slot: Slot) -> PlayerBinding:
        self.events.append(f"start:{slot.index}")
        return self.binding

    def stop(self, binding: PlayerBinding, proof: None) -> StopRecord:
        self.events.append("stop")
        return StopRecord(0, "0" * 32, 1, 2, True)


def _cycle(events: list[str], lease: FakeLease) -> InertCycle:
    slots = tuple(
        Slot(
            index,
            "Pie64" if index == 0 else f"Pie64_{index + 1}",
            f"Generic Slot {index}",
            1280,
            720,
            240,
        )
        for index in range(5)
    )

    def load_registry() -> tuple[Slot, ...]:
        events.append("registry:load")
        return slots

    def make_state_store() -> FakeStateStore:
        events.append("state-store:create")
        return FakeStateStore(events)

    def observe_player_count() -> int:
        events.append("players:observe")
        return 0

    def make_supervisor(store: FakeStateStore, mutex_name: str) -> FakeSupervisor:
        del store, mutex_name
        events.append("supervisor:create")
        return FakeSupervisor(events)

    return InertCycle(
        FakeRuntime(events, lease),
        load_registry=load_registry,
        make_state_store=make_state_store,
        observe_player_count=observe_player_count,
        make_supervisor=make_supervisor,
    )


def test_mutex_precedes_configuration_state_and_process_discovery() -> None:
    events: list[str] = []
    cycle = _cycle(events, FakeLease(events))

    record = cycle.visit_once()

    assert record.slot == 0
    assert events == [
        "mutex:acquire",
        "mutex:require-usable",
        "registry:load",
        "state-store:create",
        "state:load",
        "players:observe",
        "supervisor:create",
        "start:0",
        "stop",
        "mutex:release",
    ]


def test_explicit_initialization_is_mutex_owned_and_never_creates_supervisor() -> None:
    events: list[str] = []
    cycle = _cycle(events, FakeLease(events))

    state = cycle.initialize()

    assert state == Ready(0)
    assert events == [
        "mutex:acquire",
        "mutex:require-usable",
        "registry:load",
        "players:observe",
        "state-store:create",
        "state:initialize",
        "mutex:release",
    ]
    assert "supervisor:create" not in events


def test_abandoned_mutex_allows_only_read_only_reconciliation() -> None:
    events: list[str] = []
    cycle = _cycle(events, FakeLease(events, abandoned=True))

    with pytest.raises(CycleError, match="reconciliation"):
        cycle.visit_once()

    assert events == [
        "mutex:acquire",
        "state-store:create",
        "state:load",
        "players:observe",
        "mutex:release",
    ]
    assert not any(event.startswith("start:") for event in events)


def test_mutex_release_failure_permanently_disables_cycle_process() -> None:
    events: list[str] = []
    lease = FakeLease(events, fail_release=True)
    cycle = _cycle(events, lease)

    with pytest.raises(CycleError, match="permanently disabled"):
        cycle.visit_once()
    with pytest.raises(CycleError, match="permanently disabled"):
        cycle.visit_once()

    assert events.count("mutex:acquire") == 1


def test_exact_five_visits_emit_start_stop_zero_through_four_across_reopen() -> None:
    events: list[str] = []
    slots = tuple(
        Slot(
            index,
            "Pie64" if index == 0 else f"Pie64_{index + 1}",
            f"Generic Slot {index}",
            1280,
            720,
            240,
        )
        for index in range(5)
    )

    class PersistentStore:
        state = Ready(0)

        def load(self) -> Ready:
            return self.state

    store = PersistentStore()

    class RotatingSupervisor(FakeSupervisor):
        def __init__(self, selected_store: PersistentStore) -> None:
            super().__init__(events)
            self.store = selected_store
            self.slot = 0

        def start(self, slot: Slot) -> PlayerBinding:
            self.slot = slot.index
            events.append(f"START{slot.index}")
            return self.binding

        def stop(self, binding: PlayerBinding, proof: None) -> StopRecord:
            del binding, proof
            events.append(f"STOP{self.slot}")
            self.store.state = Ready((self.slot + 1) % 5)
            return StopRecord(self.slot, "0" * 32, 1, 2, True)

    def make_cycle() -> InertCycle:
        return InertCycle(
            FakeRuntime(events, FakeLease(events)),
            load_registry=lambda: slots,
            make_state_store=lambda: store,
            observe_player_count=lambda: 0,
            make_supervisor=lambda selected_store, _mutex: RotatingSupervisor(
                selected_store
            ),
        )

    cycle = make_cycle()
    for _ in range(2):
        cycle.visit_once()
    cycle = make_cycle()
    for _ in range(3):
        cycle.visit_once()

    trace = [event for event in events if event.startswith(("START", "STOP"))]
    assert trace == [
        "START0",
        "STOP0",
        "START1",
        "STOP1",
        "START2",
        "STOP2",
        "START3",
        "STOP3",
        "START4",
        "STOP4",
    ]


class FakeDiagnosticStore(FakeStateStore):
    def load_with_bytes(self) -> tuple[Ready, bytes]:
        self.events.append("state:load-with-bytes")
        return Ready(0), b'{"next_slot":0,"schema":1,"state":"READY"}\n'


class FakeDiagnosticApprovals:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def validate_then_consume(self, action, tree, state, state_bytes) -> object:
        self.events.append(
            f"approval:{action.value}:{tree}:{state.next_slot}:{len(state_bytes)}"
        )
        if self.fail:
            raise RuntimeError("approval refused")
        return object()


def _diagnostic_cycle(
    events: list[str],
    lease: FakeLease,
    *,
    approval_fails: bool = False,
    observation_fails: bool = False,
) -> NoInputDiagnosticCycle:
    slots = tuple(
        Slot(
            index,
            "Pie64" if index == 0 else f"Pie64_{index + 1}",
            f"Generic Slot {index}",
            1280,
            720,
            240,
        )
        for index in range(5)
    )

    def make_supervisor(_store, _mutex_name) -> FakeSupervisor:
        events.append("supervisor:create")
        return FakeSupervisor(events)

    def observe(_supervisor, _binding) -> HomeDiagnosticResult:
        events.append("observe")
        if observation_fails:
            raise RuntimeError("recognition failed")
        return HomeDiagnosticResult.HOME

    return NoInputDiagnosticCycle(
        FakeRuntime(events, lease),
        load_registry=lambda: (events.append("registry:load") or slots),
        make_state_store=lambda: (
            events.append("state-store:create") or FakeDiagnosticStore(events)
        ),
        observe_player_count=lambda: (events.append("players:observe") or 0),
        approvals=FakeDiagnosticApprovals(events, fail=approval_fails),
        candidate_tree=lambda: (events.append("tree") or "a" * 40),
        make_supervisor=make_supervisor,
        observe=observe,
    )


def test_no_input_diagnostic_consumes_exact_approval_before_launch_and_stops() -> None:
    events: list[str] = []
    result = _diagnostic_cycle(events, FakeLease(events)).visit_once()

    assert result is HomeDiagnosticResult.HOME
    assert events == [
        "mutex:acquire",
        "mutex:require-usable",
        "registry:load",
        "state-store:create",
        "state:load-with-bytes",
        "players:observe",
        "tree",
        "approval:LAUNCH_WINDOW_BINDING_DIAGNOSTIC:" + "a" * 40 + ":0:43",
        "supervisor:create",
        "start:0",
        "observe",
        "stop",
        "mutex:release",
    ]


def test_no_input_diagnostic_stops_when_recognition_raises() -> None:
    events: list[str] = []
    cycle = _diagnostic_cycle(
        events,
        FakeLease(events),
        observation_fails=True,
    )

    with pytest.raises(RuntimeError, match="recognition failed"):
        cycle.visit_once()

    assert events[-3:] == ["observe", "stop", "mutex:release"]


def test_no_input_diagnostic_refuses_launch_when_approval_fails() -> None:
    events: list[str] = []
    cycle = _diagnostic_cycle(events, FakeLease(events), approval_fails=True)

    with pytest.raises(RuntimeError, match="approval refused"):
        cycle.visit_once()

    assert "supervisor:create" not in events
    assert not any(event.startswith("start:") for event in events)
    assert events[-1] == "mutex:release"
