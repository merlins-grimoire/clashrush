from __future__ import annotations

import json

import pytest

from clash_rush_rebuild import mvp_local_native
from clash_rush_rebuild.config import load_private_registry
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.mvp_local_gameplay import LocalBotMode, MvpConfiguration
from clash_rush_rebuild.mvp_local_runtime import (
    LocalControlStore,
    PersistentControl,
    RuntimeSafetyError,
)


TAG_HASH = "a" * 64


def _private_registry(tmp_path):
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    names = [f"Private Slot {index} / Owner" for index in range(5)]
    slots_path = private / "slots.json"
    slots_path.write_text(
        json.dumps({"schema": 1, "display_names": names}), encoding="utf-8"
    )
    conf_path = tmp_path / "bluestacks.conf"
    conf_path.write_text(
        "\n".join(
            line
            for index, name in enumerate(names)
            for line in (
                f'bst.instance.Pie64_{index}.display_name="{name}"',
                f'bst.instance.Pie64_{index}.fb_width="1280"',
                f'bst.instance.Pie64_{index}.fb_height="720"',
                f'bst.instance.Pie64_{index}.dpi="240"',
            )
        ),
        encoding="utf-8",
    )
    return project, load_private_registry(project, slots_path, conf_path)


def test_private_display_name_binds_through_opaque_slot_reference(tmp_path) -> None:
    project, slots = _private_registry(tmp_path)
    configuration = MvpConfiguration("team-0", "account-2", "slot-2", TAG_HASH)
    store = LocalControlStore(project / "var" / "mvp-local-control.json")

    persisted = store.setup(configuration)
    selected = mvp_local_native._select_configured_slot(
        slots, Ready(2), persisted.configuration.instance_ref
    )

    assert selected is slots[2]
    raw = (project / "var" / "mvp-local-control.json").read_text(encoding="utf-8")
    assert '"instance_ref":"slot-2"' in raw
    assert slots[2].display_name not in raw


def test_opaque_slot_reference_must_exactly_match_lifecycle_cursor(tmp_path) -> None:
    _project, slots = _private_registry(tmp_path)

    with pytest.raises(RuntimeSafetyError, match="WINDOW_BINDING"):
        mvp_local_native._select_configured_slot(slots, Ready(2), "slot-1")


class _Lease:
    def __init__(self, events: list[str], *, abandoned: bool = False) -> None:
        self.events = events
        self.abandoned = abandoned

    def require_usable(self) -> None:
        self.events.append("mutex:usable")

    def release(self) -> None:
        self.events.append("mutex:release")


class _Runtime:
    def __init__(self, events: list[str], *, abandoned: bool = False) -> None:
        self.events = events
        self.abandoned = abandoned

    def acquire_mutex(self) -> _Lease:
        self.events.append("mutex:acquire")
        return _Lease(self.events, abandoned=self.abandoned)


class _StateStore:
    def __init__(self, events: list[str], state: object) -> None:
        self.events = events
        self.state = state

    def load(self) -> object:
        self.events.append("lifecycle:load")
        return self.state


class _Control:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.state: PersistentControl | None = None

    def prepare_stopped(self, configuration: MvpConfiguration) -> PersistentControl:
        self.events.append("control:prepare")
        self.state = PersistentControl(configuration, LocalBotMode.STOPPED)
        return self.state

    def load(self) -> PersistentControl:
        self.events.append("control:load")
        assert self.state is not None
        return self.state


def _configurations() -> tuple[MvpConfiguration, ...]:
    return tuple(
        MvpConfiguration(
            "local-team-0",
            f"local-account-{index}",
            f"slot-{index}",
            chr(97 + index) * 64,
        )
        for index in range(5)
    )


def _preparer(*, state: object = Ready(1), approval: bool = False, observations=None, abandoned=False):
    events: list[str] = []
    store = _StateStore(events, state)
    control = _Control(events)
    remaining = iter(observations or [(0, 0), (0, 0)])
    subject = mvp_local_native.StoppedControlPreparer(
        _Runtime(events, abandoned=abandoned),
        make_state_store=lambda: store,
        control=control,
        observe_absence=lambda slot: events.append(f"absence:{slot}") or next(remaining),
        approval_exists=lambda: events.append("approval:check") or approval,
    )
    return subject, events, store, control


def test_stopped_preparer_selects_ready_one_without_rewinding_cursor() -> None:
    subject, events, store, control = _preparer()

    lifecycle = subject.prepare(_configurations())

    assert lifecycle == Ready(1)
    assert store.state == Ready(1)
    assert control.state == PersistentControl(_configurations()[1], LocalBotMode.STOPPED)
    assert events == [
        "mutex:acquire",
        "mutex:usable",
        "lifecycle:load",
        "approval:check",
        "absence:1",
        "control:prepare",
        "lifecycle:load",
        "approval:check",
        "absence:1",
        "control:load",
        "mutex:release",
    ]


@pytest.mark.parametrize(
    ("state", "approval", "observations", "abandoned", "message"),
    [
        (object(), False, None, False, "READY"),
        (Ready(1), True, None, False, "approval"),
        (Ready(1), False, [(1, 0)], False, "absence"),
        (Ready(1), False, None, True, "abandoned"),
    ],
)
def test_stopped_preparer_rejects_invalid_boundary_without_control_write(
    state, approval, observations, abandoned, message
) -> None:
    subject, events, _store, control = _preparer(
        state=state,
        approval=approval,
        observations=observations,
        abandoned=abandoned,
    )

    with pytest.raises(RuntimeSafetyError, match=message):
        subject.prepare(_configurations())

    assert control.state is None
    assert "control:prepare" not in events
    assert events[-1] == "mutex:release"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("lifecycle", "changed"),
        ("approval", "approval"),
        ("absence", "absence"),
        ("control", "read-back"),
    ],
)
def test_stopped_preparer_fails_closed_when_postcondition_changes(mutation: str, message: str) -> None:
    subject, events, store, control = _preparer()
    if mutation == "lifecycle":
        original = store.load
        loads = 0
        def changed_load():
            nonlocal loads
            loads += 1
            return original() if loads == 1 else Ready(2)
        store.load = changed_load  # type: ignore[method-assign]
    elif mutation == "approval":
        checks = iter([False, True])
        subject._approval_exists = lambda: events.append("approval:check") or next(checks)
    elif mutation == "absence":
        observations = iter([(0, 0), (0, 1)])
        subject._observe_absence = lambda slot: events.append(f"absence:{slot}") or next(observations)
    else:
        original_control_load = control.load
        control.load = lambda: PersistentControl(  # type: ignore[method-assign]
            _configurations()[2], original_control_load().mode
        )

    with pytest.raises(RuntimeSafetyError, match=message):
        subject.prepare(_configurations())

    assert events[-1] == "mutex:release"
