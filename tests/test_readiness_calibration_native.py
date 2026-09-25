from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

import clash_rush_rebuild.cli as cli_module
import clash_rush_rebuild.readiness_calibration_native as native
from clash_rush_rebuild.approval_reconciliation import ApprovalAction
from clash_rush_rebuild.input_authorization import InputPurpose
from clash_rush_rebuild.input_authorization import InputAction
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.readiness_calibration import CalibrationResult
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult


BINDING = PlayerBinding(
    ProcessIdentity(100, 200), ProcessIdentity(100, 200),
    10, 11, 1280, 720, "1" * 32,
)


def test_native_composition_consumes_distinct_approval_and_builds_only_readiness_input(
    tmp_path: Path, monkeypatch,
) -> None:
    events: list[str] = []

    class Cycle:
        _approval_action = ApprovalAction.READINESS_CALIBRATION

        def __init__(self, _runtime, **kwargs):
            events.append("cycle")
            self.kwargs = kwargs

    class Controller:
        def __init__(self, **kwargs):
            assert kwargs["binding"] == BINDING
            events.append("controller")

        def run(self, *, deadline):
            assert type(deadline) is float
            events.append("calibrate")
            return CalibrationResult("readiness-s003-" + "2" * 12)

    class StartupController:
        def __init__(self, **_kwargs):
            events.append("startup-controller")

        def run(self, **kwargs):
            assert 0 < kwargs["timeout"] <= 120
            events.append("startup-home")
            return StartupContinueResult.HOME

    class Evidence:
        def __init__(self, _project):
            events.append("startup-evidence")

        def delete(self):
            events.append("startup-evidence-delete")

    monkeypatch.setattr(native, "ReadinessCalibrationCycle", Cycle)
    monkeypatch.setattr(native, "ReadinessCalibrationController", Controller)
    monkeypatch.setattr(native, "StartupDebugController", StartupController)
    monkeypatch.setattr(native, "DebugEvidence", Evidence)
    monkeypatch.setattr(native, "StartupNativeInput", lambda *_a, **_k: object())
    monkeypatch.setattr(native, "BootstrapStartupDetector", lambda *_a: object())
    monkeypatch.setattr(native, "PrivateBootstrapLocator", lambda _project: object())
    monkeypatch.setattr(native, "NativeLifecycleApi", lambda: SimpleNamespace(_user32=object()))
    monkeypatch.setattr(native, "Win32Runtime", lambda _api: "runtime")
    monkeypatch.setattr(native, "Win32LifecycleHost", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(native, "OneShotApprovalService", lambda _storage: "approvals")
    monkeypatch.setattr(native, "PrivateApprovalStorage", lambda _project: "storage")
    monkeypatch.setattr(native, "load_private_registry", lambda *_a: ())
    monkeypatch.setattr(cli_module, "build_native_state_store", lambda _p: "store")
    monkeypatch.setattr(cli_module, "_candidate_tree", lambda _p: "a" * 40)
    monkeypatch.setattr(native, "StartupGeometrySupervisor", lambda *_a, **_k: object())
    monkeypatch.setattr(native, "WindowService", lambda *_a: SimpleNamespace(
        screenshot=lambda: np.zeros((720, 1280, 3), np.uint8)
    ))
    monkeypatch.setattr(native, "DonorCalibrationStateProbe", lambda: lambda _f, _s: True)
    monkeypatch.setattr(native, "NoInputHomeDiagnosticController", lambda _window: SimpleNamespace(
        _detect_village_type=lambda _frame, **_kwargs: SimpleNamespace(value="HOME")
    ))
    monkeypatch.setattr(native, "Win32BoundInput", lambda _b, _c, authorization, **_k: (
        events.append(f"input:{authorization.purpose.value}") or object()
    ))
    monkeypatch.setattr(native, "clear_windows_clipboard", lambda: None)
    monkeypatch.setattr(native, "read_windows_clipboard", lambda: "fresh export")

    cycle = native.build_cycle(str(tmp_path), "slots")
    result = cycle.kwargs["recover"](SimpleNamespace(capture_owned=lambda _b: None), BINDING)

    assert result is StartupContinueResult.HOME
    assert cycle._approval_action is ApprovalAction.READINESS_CALIBRATION
    assert events == [
        "cycle", "startup-evidence", "startup-controller", "input:ACCOUNT_READINESS",
        "startup-home", "startup-evidence-delete", "controller", "calibrate",
    ]
    assert InputPurpose.MONITORED_ATTACK.value not in " ".join(events)


def test_probe_uses_basepilot_settings_and_changeuser_templates(monkeypatch) -> None:
    requested: list[str] = []

    class Vision:
        def __init__(self, _geometry):
            pass

        def find_template(self, _frame, name, **_kwargs):
            requested.append(name)
            return (10, 10)

    monkeypatch.setattr(native, "VisionService", Vision)
    monkeypatch.setattr(native, "BasePilotGeometry", SimpleNamespace(
        from_frame=lambda _frame: object()
    ))
    probe = native.DonorCalibrationStateProbe()
    frame = np.zeros((720, 1280, 3), np.uint8)

    assert probe(frame, "home") is True
    assert probe(frame, "settings") is True
    assert requested == ["settings.png", "changeuser.png"]


def test_more_probe_does_not_treat_uint8_blue_minus_red_underflow_as_red_close() -> None:
    probe = native.DonorCalibrationStateProbe()
    frame = np.zeros((720, 1280, 3), np.uint8)
    # Blue-heavy pixels used to satisfy ``red - blue >= 60`` after uint8 wrap.
    frame[30:36, 810:820] = (250, 0, 170)

    assert probe._red_close(frame) is False


def test_bootstrap_startup_detector_uses_private_launcher_then_delegates_nonlauncher() -> None:
    calls: list[str] = []

    class Locator:
        def __call__(self, _frame, name):
            calls.append(name)
            if len(calls) == 1:
                # Current reviewed private launcher geometry differs from the
                # stale donor coordinate; exact template+ROI evidence governs.
                return SimpleNamespace(center=(0.526, 0.355))
            raise native.CalibrationError("launcher bootstrap evidence unavailable")

    class Delegate:
        def detect_non_launcher(self, _frame):
            calls.append("delegate")
            return (InputAction.STARTUP_CONTINUE, (0.5, 0.5))

    detector = native.BootstrapStartupDetector(Locator(), Delegate())

    assert detector(np.zeros((720, 1280, 3), np.uint8)) == (
        InputAction.STARTUP_LAUNCH_GAME, (0.526, 0.355)
    )
    assert detector(np.zeros((720, 1280, 3), np.uint8)) == (
        InputAction.STARTUP_CONTINUE, (0.5, 0.5)
    )
    assert calls == ["launcher", "launcher", "delegate"]
