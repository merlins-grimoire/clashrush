from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clash_rush_rebuild import mvp_local_native
from clash_rush_rebuild.config import load_private_registry
from clash_rush_rebuild.input_authorization import (
    InputAction,
    InputAuthorization,
    InputPurpose,
)
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.mvp_local_gameplay import LocalBotMode, MvpConfiguration
from clash_rush_rebuild.mvp_account_ready import AccountReady
from clash_rush_rebuild.mvp_session_authority import ActionPhase, ControlMode
from clash_rush_rebuild.mvp_local_runtime import (
    LocalControlStore,
    PersistentControl,
    RuntimeSafetyError,
)


TAG_HASH = "a" * 64

BINDING = PlayerBinding(
    ProcessIdentity(100, 200),
    ProcessIdentity(100, 200),
    10,
    11,
    1280,
    720,
    "0" * 32,
)


def test_native_input_requires_exact_closed_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _user32: None)

    with pytest.raises(RuntimeSafetyError, match="authorization"):
        mvp_local_native.Win32BoundInput(BINDING, lambda _binding: None, lambda: True)

    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.monitored_attack(lambda: True),
    )
    assert subject._binding == BINDING


def test_cleanup_release_emits_only_for_keys_proven_held_by_this_input_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[int, int]] = []

    class User32:
        def keybd_event(self, key, _scan, flags, _extra):
            events.append((key, flags))

    user32 = User32()
    monkeypatch.setattr(
        mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: user32
    )
    monkeypatch.setattr(
        mvp_local_native, "_configure_input_signatures", lambda _user32: None
    )
    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.monitored_attack(lambda: True),
    )
    monkeypatch.setattr(subject, "_foreground", lambda: True)
    monkeypatch.setattr(subject, "_authorized", lambda _action: True)

    assert subject.key_up(BINDING, 0x52, action=InputAction.CLEANUP_RELEASE) is False
    assert events == []
    assert subject.key_down(
        BINDING, 0x52, action=InputAction.TROOP_DEPLOYMENT
    ) is True
    assert subject.key_up(BINDING, 0x4A, action=InputAction.CLEANUP_RELEASE) is False
    assert subject.key_up(BINDING, 0x52, action=InputAction.CLEANUP_RELEASE) is True
    assert subject.key_up(BINDING, 0x52, action=InputAction.CLEANUP_RELEASE) is False
    assert events == [(0x52, 0), (0x52, 0x0002)]

    assert subject.key_down(
        BINDING, 0x52, action=InputAction.TROOP_DEPLOYMENT
    ) is True
    assert subject.key_up(
        BINDING,
        0x52,
        0x4A,
        0x56,
        0x52,
        action=InputAction.CLEANUP_RELEASE,
    ) is True
    assert events[-2:] == [(0x52, 0), (0x52, 0x0002)]
    assert subject._held_keys == set()


def test_bound_input_releases_mouse_after_post_down_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[int] = []

    class User32:
        def ClientToScreen(self, _hwnd, _point):
            return True

        def SetCursorPos(self, _x, _y):
            return True

        def mouse_event(self, flag, *_args):
            events.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _user32: None)
    monkeypatch.setattr(
        mvp_local_native.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("synthetic post-down failure")),
    )
    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.account_readiness(lambda: True),
    )
    monkeypatch.setattr(subject, "_foreground", lambda: True)

    with pytest.raises(RuntimeError, match="post-down"):
        subject.click(
            BINDING,
            0.5,
            0.5,
            action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
        )
    assert events == [0x0002, 0x0004]


def test_bound_drag_releases_mouse_after_post_down_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[int] = []

    class User32:
        def ClientToScreen(self, _hwnd, _point):
            return True

        def SetCursorPos(self, _x, _y):
            return True

        def mouse_event(self, flag, *_args):
            events.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _user32: None)
    monkeypatch.setattr(
        mvp_local_native.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("synthetic post-down failure")),
    )
    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.account_readiness(lambda: True),
    )
    monkeypatch.setattr(subject, "_foreground", lambda: True)

    with pytest.raises(RuntimeError, match="post-down"):
        subject.drag(
            BINDING,
            0.5,
            0.7,
            0.5,
            0.3,
            action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
        )
    assert events == [0x0002, 0x0004]


def test_bound_input_deadline_expiry_immediately_before_down_emits_no_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[int] = []

    class User32:
        def ClientToScreen(self, _hwnd, _point):
            return True

        def SetCursorPos(self, _x, _y):
            return True

        def mouse_event(self, flag, *_args):
            events.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _user32: None)
    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.account_readiness(lambda: True),
        deadline=10.0,
        monotonic=lambda: 10.0,
    )
    monkeypatch.setattr(subject, "_foreground", lambda: True)

    assert subject.click(
        BINDING,
        0.5,
        0.5,
        action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
    ) is False
    assert events == []


@pytest.mark.parametrize("outcome", [False, RuntimeError("synthetic pre-input failure")])
def test_bound_input_pre_input_check_fails_closed_before_mouse_down(
    monkeypatch: pytest.MonkeyPatch, outcome: object,
) -> None:
    events: list[object] = []

    class User32:
        def ClientToScreen(self, _hwnd, _point):
            events.append("mapped")
            return True

        def SetCursorPos(self, _x, _y):
            events.append("cursor")
            return True

        def mouse_event(self, flag, *_args):
            events.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_args, **_kwargs: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _user32: None)
    subject = mvp_local_native.Win32BoundInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.account_readiness(lambda: True),
    )
    monkeypatch.setattr(subject, "_foreground", lambda: True)

    def check() -> bool:
        events.append("check")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    assert subject.click(
        BINDING,
        0.5,
        0.5,
        action=InputAction.STARTUP_LAUNCH_GAME,
        pre_input_check=check,
    ) is False
    assert events == ["mapped", "cursor", "check"]


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


def test_retirement_does_not_relabel_proved_stop_when_receipt_write_fails() -> None:
    outcomes: list[bool] = []
    supervisor = SimpleNamespace(stop=lambda *_args: None)
    lease = SimpleNamespace(release=lambda: None)

    def record(_nonce: str, *, succeeded: bool) -> None:
        outcomes.append(succeeded)
        raise RuntimeError("receipt fault")

    authority = SimpleNamespace(record_retirement=record)
    with pytest.raises(RuntimeSafetyError, match="receipt"):
        mvp_local_native._retire_owned(
            supervisor, BINDING, lease, authority, "1" * 32
        )
    assert outcomes == [True]


def test_retirement_receipt_waits_for_mutex_release() -> None:
    events: list[str] = []
    supervisor = SimpleNamespace(stop=lambda *_args: events.append("stop"))

    def release() -> None:
        events.append("release")
        raise RuntimeError("release fault")

    lease = SimpleNamespace(release=release)
    authority = SimpleNamespace(
        record_retirement=lambda _nonce, *, succeeded: events.append(
            f"retirement:{succeeded}"
        )
    )
    with pytest.raises(RuntimeSafetyError, match="cleanup"):
        mvp_local_native._retire_owned(
            supervisor, BINDING, lease, authority, "1" * 32
        )
    assert events == ["stop", "release", "retirement:False"]


def test_native_visit_runs_separate_account_readiness_before_attack_capability(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    configuration = MvpConfiguration("synthetic-team", "synthetic-account", "slot-2", TAG_HASH)
    status = SimpleNamespace(
        mode=ControlMode.RUNNING,
        run_nonce="1" * 32,
        configuration_revision=1,
        control_revision=2,
    )
    def admit(**kwargs):
        assert kwargs["purpose"] == "MVP_ACCOUNT_READINESS"
        events.append("admit")

    authority = SimpleNamespace(
        status=lambda: status,
        configuration=lambda: configuration,
        admit=admit,
        record_retirement=lambda *_args, **_kwargs: events.append("retire"),
    )
    lease = SimpleNamespace(
        abandoned=False,
        require_usable=lambda: None,
        release=lambda: events.append("release"),
    )
    snapshot = SimpleNamespace(
        identities=(), close=lambda: events.append("snapshot-close")
    )
    supervisor = SimpleNamespace(
        start=lambda _slot: events.append("start") or BINDING,
        capture_owned=lambda _binding: (_ for _ in ()).throw(
            AssertionError("fake readiness owns capture")
        ),
        stop=lambda *_args: events.append("stop"),
    )
    monkeypatch.setattr(mvp_local_native, "DurableSessionAuthority", lambda _path: authority)
    monkeypatch.setattr(mvp_local_native, "NativeLifecycleApi", lambda: None)
    monkeypatch.setattr(
        mvp_local_native,
        "Win32Runtime",
        lambda _api: SimpleNamespace(acquire_mutex=lambda: lease),
    )
    monkeypatch.setattr(
        mvp_local_native,
        "Win32LifecycleHost",
        lambda *_args, **_kwargs: SimpleNamespace(
            complete_player_snapshot=lambda: snapshot
        ),
    )
    monkeypatch.setattr(
        mvp_local_native, "_state_store", lambda _project: SimpleNamespace(load=lambda: Ready(2))
    )
    monkeypatch.setattr(mvp_local_native, "_candidate_tree", lambda _project: "a" * 40)
    monkeypatch.setattr(mvp_local_native, "load_private_registry", lambda *_args: tuple(range(5)))
    monkeypatch.setattr(mvp_local_native, "_select_configured_slot", lambda *_args: 2)
    monkeypatch.setattr(mvp_local_native, "LifecycleSupervisor", lambda *_args, **_kwargs: supervisor)
    visual_profile = object()
    monkeypatch.setattr(
        mvp_local_native,
        "load_private_visual_profile",
        lambda *_args: events.append("profile") or visual_profile,
    )
    bootstrap_locator = object()
    monkeypatch.setattr(
        mvp_local_native,
        "PrivateBootstrapLocator",
        lambda *_args: events.append("bootstrap") or bootstrap_locator,
    )

    readiness_input = object()
    deadlines: list[float] = []

    def input_constructor(_binding, _capture, authorization, **kwargs):
        events.append(f"input:{authorization.purpose.value}")
        assert authorization.purpose is InputPurpose.ACCOUNT_READINESS
        assert callable(kwargs["monotonic"])
        deadlines.append(kwargs["deadline"])
        return readiness_input

    def startup_constructor(_binding, _capture, authorization, **kwargs):
        events.append(f"startup:{authorization.purpose.value}")
        assert authorization.purpose is InputPurpose.STARTUP_CONTINUE_ONLY
        assert callable(kwargs["monotonic"])
        deadlines.append(kwargs["deadline"])
        return object()

    monkeypatch.setattr(mvp_local_native, "Win32BoundInput", input_constructor)
    monkeypatch.setattr(
        mvp_local_native,
        "Win32StartupContinueInput",
        startup_constructor,
    )

    class Readiness:
        def __init__(self, **kwargs):
            assert kwargs["export_input"] is readiness_input
            assert kwargs["locate_bootstrap"] is bootstrap_locator
            events.append("readiness:constructed")

        def run(self, *, deadline):
            assert type(deadline) is float
            assert deadlines == [deadline, deadline]
            events.append("readiness:run")
            return AccountReady("1" * 32, "ordinary-card-v1")

    monkeypatch.setattr(mvp_local_native, "AccountReadinessController", Readiness)
    monkeypatch.setattr(
        mvp_local_native.time,
        "monotonic",
        lambda: events.append("deadline") or 1.0,
    )

    result = mvp_local_native.run_native_mvp_account_ready(
        str(tmp_path), "synthetic-slots", "synthetic-transaction"
    )

    assert result == AccountReady("1" * 32, "ordinary-card-v1")
    assert events == [
        "bootstrap",
        "profile",
        "admit",
        "snapshot-close",
        "start",
        "deadline",
        "input:ACCOUNT_READINESS",
        "startup:STARTUP_CONTINUE_ONLY",
        "readiness:constructed",
        "readiness:run",
        "stop",
        "release",
        "retire",
    ]


def test_private_loader_failure_has_no_admission_start_or_input(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    status = SimpleNamespace(
        mode=ControlMode.RUNNING,
        run_nonce="1" * 32,
        configuration_revision=1,
        control_revision=2,
    )
    authority = SimpleNamespace(
        status=lambda: status,
        configuration=lambda: MvpConfiguration(
            "synthetic-team", "synthetic-account", "slot-2", TAG_HASH
        ),
        admit=lambda **_kwargs: events.append("admit"),
        transaction=lambda _ref: SimpleNamespace(phase=ActionPhase.PLANNED),
        transition_action=lambda _ref, **kwargs: events.append(
            f"transition:{kwargs['expected'].value}:{kwargs['target'].value}:"
            f"{kwargs['reason_code']}"
        ),
        record_retirement=lambda *_args, **_kwargs: events.append("retire"),
    )
    lease = SimpleNamespace(
        abandoned=False,
        require_usable=lambda: None,
        release=lambda: events.append("release"),
    )
    snapshot = SimpleNamespace(identities=(), close=lambda: None)
    supervisor = SimpleNamespace(
        start=lambda _slot: BINDING,
        capture_owned=lambda _binding: (_ for _ in ()).throw(AssertionError),
        stop=lambda *_args: events.append("stop"),
    )
    monkeypatch.setattr(mvp_local_native, "DurableSessionAuthority", lambda _path: authority)
    monkeypatch.setattr(mvp_local_native, "NativeLifecycleApi", lambda: None)
    monkeypatch.setattr(
        mvp_local_native, "Win32Runtime",
        lambda _api: SimpleNamespace(acquire_mutex=lambda: lease),
    )
    monkeypatch.setattr(
        mvp_local_native, "Win32LifecycleHost",
        lambda *_args, **_kwargs: SimpleNamespace(
            complete_player_snapshot=lambda: snapshot
        ),
    )
    monkeypatch.setattr(
        mvp_local_native, "_state_store", lambda _project: SimpleNamespace(load=lambda: Ready(2))
    )
    monkeypatch.setattr(mvp_local_native, "_candidate_tree", lambda _project: "a" * 40)
    monkeypatch.setattr(mvp_local_native, "load_private_registry", lambda *_args: tuple(range(5)))
    monkeypatch.setattr(mvp_local_native, "_select_configured_slot", lambda *_args: 2)
    monkeypatch.setattr(mvp_local_native, "LifecycleSupervisor", lambda *_args, **_kwargs: supervisor)
    monkeypatch.setattr(mvp_local_native, "load_private_visual_profile", lambda *_args: object())

    def reject_bootstrap(*_args):
        raise RuntimeError("synthetic readiness failure")

    monkeypatch.setattr(mvp_local_native, "PrivateBootstrapLocator", reject_bootstrap)
    monkeypatch.setattr(
        mvp_local_native,
        "Win32BoundInput",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("input must not be constructed")
        ),
    )
    monkeypatch.setattr(
        mvp_local_native,
        "Win32StartupContinueInput",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("startup input must not be constructed")
        ),
    )

    with pytest.raises(RuntimeError, match="synthetic readiness failure"):
        mvp_local_native.run_native_mvp_account_ready(
            str(tmp_path), "synthetic-slots", "synthetic-transaction"
        )

    assert events == ["release"]


def test_bookkeeping_failure_preserves_readiness_error_and_owned_cleanup_order(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original = RuntimeError("original readiness failure")
    status = SimpleNamespace(
        mode=ControlMode.RUNNING,
        run_nonce="1" * 32,
        configuration_revision=1,
        control_revision=2,
    )

    def transition(*_args, **_kwargs):
        events.append("bookkeeping")
        raise RuntimeError("synthetic bookkeeping failure")

    authority = SimpleNamespace(
        status=lambda: status,
        configuration=lambda: MvpConfiguration(
            "synthetic-team", "synthetic-account", "slot-2", TAG_HASH
        ),
        admit=lambda **_kwargs: events.append("admit"),
        transaction=lambda _ref: SimpleNamespace(phase=ActionPhase.PLANNED),
        transition_action=transition,
        record_retirement=lambda *_args, **_kwargs: events.append("retire"),
    )
    lease = SimpleNamespace(
        abandoned=False,
        require_usable=lambda: None,
        release=lambda: events.append("release"),
    )
    supervisor = SimpleNamespace(
        start=lambda _slot: events.append("start") or BINDING,
        capture_owned=lambda _binding: (_ for _ in ()).throw(AssertionError),
        stop=lambda *_args: events.append("stop"),
    )
    monkeypatch.setattr(mvp_local_native, "DurableSessionAuthority", lambda _path: authority)
    monkeypatch.setattr(mvp_local_native, "NativeLifecycleApi", lambda: None)
    monkeypatch.setattr(
        mvp_local_native,
        "Win32Runtime",
        lambda _api: SimpleNamespace(acquire_mutex=lambda: lease),
    )
    monkeypatch.setattr(
        mvp_local_native,
        "Win32LifecycleHost",
        lambda *_args, **_kwargs: SimpleNamespace(
            complete_player_snapshot=lambda: SimpleNamespace(
                identities=(), close=lambda: None
            )
        ),
    )
    monkeypatch.setattr(
        mvp_local_native, "_state_store", lambda _project: SimpleNamespace(load=lambda: Ready(2))
    )
    monkeypatch.setattr(mvp_local_native, "_candidate_tree", lambda _project: "a" * 40)
    monkeypatch.setattr(mvp_local_native, "load_private_registry", lambda *_args: tuple(range(5)))
    monkeypatch.setattr(mvp_local_native, "_select_configured_slot", lambda *_args: 2)
    monkeypatch.setattr(mvp_local_native, "PrivateBootstrapLocator", lambda *_args: object())
    monkeypatch.setattr(mvp_local_native, "load_private_visual_profile", lambda *_args: object())
    monkeypatch.setattr(mvp_local_native, "LifecycleSupervisor", lambda *_args, **_kwargs: supervisor)
    monkeypatch.setattr(mvp_local_native, "Win32BoundInput", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        mvp_local_native, "Win32StartupContinueInput", lambda *_args, **_kwargs: object()
    )

    class Readiness:
        def __init__(self, **_kwargs):
            pass

        def run(self, *, deadline):
            assert type(deadline) is float
            events.append("readiness")
            raise original

    monkeypatch.setattr(mvp_local_native, "AccountReadinessController", Readiness)

    with pytest.raises(RuntimeError) as caught:
        mvp_local_native.run_native_mvp_account_ready(
            str(tmp_path), "synthetic-slots", "synthetic-transaction"
        )

    assert caught.value is original
    assert caught.value.__notes__ == ["pre-input failure bookkeeping was unavailable"]
    assert events == [
        "admit",
        "start",
        "readiness",
        "bookkeeping",
        "stop",
        "release",
        "retire",
    ]
