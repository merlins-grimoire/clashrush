"""Native composition for one approval-bound private readiness calibration."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import cv2
import numpy as np

from .approval_reconciliation import (
    ApprovalAction,
    OneShotApprovalService,
    PrivateApprovalStorage,
)
from .basepilot_vision import BasePilotGeometry, VisionService
from .basepilot_window import WindowService
from .config import load_private_registry
from .cycle import ReadinessCalibrationCycle
from .input_authorization import InputAction, InputAuthorization
from .lifecycle import AcquiredMutexLease
from .lifecycle_state import Ready
from .mvp_local_native import Win32BoundInput
from .mvp_local_world_export import clear_windows_clipboard, read_windows_clipboard
from .readiness_calibration import (
    CalibrationError,
    CalibrationResult,
    LocatedControl,
    PrivateBootstrapLocator,
    ReadinessCalibrationController,
)
from .no_input_home_diagnostic import NoInputHomeDiagnosticController
from .startup_debug import (
    DebugEvidence,
    StartupDebugController,
    StartupDetector,
)
from .startup_debug_native import StartupNativeInput
from .startup_continue_recovery import StartupContinueResult
from .startup_geometry import NativeGeometryApi, StartupGeometrySupervisor
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import Win32Runtime


class DonorCalibrationStateProbe:
    """BasePilot-positive Home/Settings proof plus bounded More-panel evidence."""

    def __init__(self) -> None:
        self._settings_frame: np.ndarray | None = None
        self._more_seen = False

    @staticmethod
    def _vision(frame: np.ndarray) -> VisionService:
        return VisionService(BasePilotGeometry.from_frame(frame))

    @staticmethod
    def _red_close(frame: np.ndarray) -> bool:
        height, width = frame.shape[:2]
        region = frame[
            int(height * 0.02) : int(height * 0.25),
            int(width * 0.62) : int(width * 0.93),
        ]
        if region.size == 0:
            return False
        blue, green, red = cv2.split(region)
        mask = (red >= 160) & (green <= 150) & (blue <= 130) & ((red - blue) >= 60)
        # Positive bounded component evidence, not merely one red pixel.
        count = int(np.count_nonzero(mask))
        return 24 <= count <= region.shape[0] * region.shape[1] // 3

    def __call__(self, frame: np.ndarray, state: str) -> bool:
        if type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim != 3:
            return False
        if state == "home":
            return self._vision(frame).find_template(
                frame, "settings.png", threshold=0.80
            ) != (None, None)
        if state == "settings":
            matched = self._vision(frame).find_template(
                frame, "changeuser.png", threshold=0.80
            ) != (None, None)
            if matched:
                if self._settings_frame is not None:
                    self._settings_frame.fill(0)
                self._settings_frame = frame.copy()
            return matched
        if state != "more" or not self._red_close(frame):
            return False
        if not self._more_seen:
            if self._settings_frame is None or self._settings_frame.shape != frame.shape:
                return False
            difference = float(cv2.absdiff(frame, self._settings_frame).mean())
            if not np.isfinite(difference) or difference < 8.0:
                return False
            self._more_seen = True
            self._settings_frame.fill(0)
            self._settings_frame = None
        return True


class BootstrapStartupDetector:
    """Use the reviewed private launcher and donor popup/Continue detectors."""

    def __init__(self, bootstrap, delegate) -> None:
        self._bootstrap = bootstrap
        self._delegate = delegate
        has = getattr(bootstrap, "has", None)
        if callable(has) and has("launcher") is not True:
            raise CalibrationError("launcher bootstrap evidence unavailable")

    def __call__(self, frame: np.ndarray):
        try:
            located = self._bootstrap(frame, "launcher")
        except CalibrationError:
            return self._delegate.detect_non_launcher(frame)
        if not hasattr(located, "center"):
            raise CalibrationError("launcher bootstrap evidence unavailable")
        # The current launcher layout moved the app icon away from the pinned
        # donor coordinate. Exact private template+ROI evidence is authoritative
        # for this installation; no fixed-coordinate fallback is retained.
        return InputAction.STARTUP_LAUNCH_GAME, located.center


class CalibrationControlLocator:
    """BasePilot Settings plus owner-reviewed private bootstrap controls."""

    def __init__(self, bootstrap: PrivateBootstrapLocator) -> None:
        self._bootstrap = bootstrap

    def __call__(self, frame: np.ndarray, name: str) -> LocatedControl:
        if name != "settings_button":
            return self._bootstrap(frame, name)
        point = VisionService(BasePilotGeometry.from_frame(frame)).find_template(
            frame, "settings.png", threshold=0.80
        )
        if point == (None, None):
            raise CalibrationError("settings_button target evidence unavailable")
        x, y = point
        height, width = frame.shape[:2]
        # Bounds are only publication pixels; click permission comes from the
        # independent BasePilot match and controller donor-proximity check.
        half_width, half_height = int(width * 0.05), int(height * 0.11)
        return LocatedControl(
            x / (width - 1),
            y / (height - 1),
            (
                max(0, x - half_width) / width,
                max(0, y - half_height) / height,
                min(width, x + half_width) / width,
                min(height, y + half_height) / height,
            ),
        )


def issue_approval(project_root: str, lifetime_seconds: int) -> object:
    """Issue an exact-tree/READY one-use calibration approval without launch."""
    from .cli import _candidate_tree, build_native_state_store

    project = Path(project_root).resolve(strict=True)
    runtime = Win32Runtime(NativeLifecycleApi())
    lease = runtime.acquire_mutex()
    try:
        if lease.abandoned:
            raise RuntimeError("calibration approval unavailable")
        lease.require_usable()
        state, raw = build_native_state_store(project).load_with_bytes()
        if type(state) is not Ready:
            raise RuntimeError("calibration approval unavailable")
        return OneShotApprovalService(PrivateApprovalStorage(project)).grant(
            ApprovalAction.READINESS_CALIBRATION,
            _candidate_tree(project),
            state,
            raw,
            lifetime_seconds=lifetime_seconds,
        )
    finally:
        lease.release()


def build_cycle(project_root: str, slots_path: str) -> ReadinessCalibrationCycle:
    """Compose calibration without launching until the owned cycle is visited."""
    from .cli import _BLUESTACKS_CONF, _candidate_tree, build_native_state_store

    project = Path(project_root).resolve(strict=True)
    native_api = NativeLifecycleApi()
    host = Win32LifecycleHost(native_api, nonce_factory=lambda: secrets.token_hex(16))

    def player_count() -> int:
        snapshot = host.complete_player_snapshot()
        try:
            return len(snapshot.identities)
        finally:
            snapshot.close()

    def recover(supervisor, binding) -> StartupContinueResult:
        live = True
        deadline = time.monotonic() + 150.0
        window = WindowService(binding, supervisor.capture_owned)

        def gate() -> bool:
            return live and time.monotonic() < deadline

        bootstrap = PrivateBootstrapLocator(project)
        startup_detector = BootstrapStartupDetector(
            bootstrap,
            StartupDetector(project / "private" / "assets" / "CCBackBeat.ttf"),
        )
        startup_evidence = DebugEvidence(project)
        village = NoInputHomeDiagnosticController(window)

        def classify(frame):
            return StartupContinueResult(
                village._detect_village_type(
                    frame, expected_size=(binding.width, binding.height)
                ).value
            )

        startup_input = StartupNativeInput(
            binding,
            window.screenshot,
            startup_detector,
            startup_evidence,
            gate,
            supervisor.capture_owned,
        )
        startup = StartupDebugController(
            capture=window.screenshot,
            classify=classify,
            detect=startup_detector,
            deliver=startup_input,
            evidence=startup_evidence,
            gate=gate,
        )
        input_port = Win32BoundInput(
            binding,
            supervisor.capture_owned,
            InputAuthorization.account_readiness(gate),
            deadline=deadline,
            monotonic=time.monotonic,
        )
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("calibration deadline expired")
            startup_result = startup.run(
                timeout=min(120.0, remaining),
                wait=time.sleep,
                clock=time.monotonic,
            )
            if startup_result is not StartupContinueResult.HOME:
                raise RuntimeError("calibration startup did not reach Home")
            # Success evidence is no longer needed; any startup failure exits
            # before this deletion and remains private for owner review.
            startup_evidence.delete()
            result = ReadinessCalibrationController(
                project_root=project,
                binding=binding,
                capture=window.screenshot,
                input_port=input_port,
                prove_state=DonorCalibrationStateProbe(),
                locate_control=CalibrationControlLocator(bootstrap),
                live_gate=gate,
                clipboard_read=read_windows_clipboard,
                clipboard_clear=clear_windows_clipboard,
                monotonic=time.monotonic,
                wait=time.sleep,
            ).run(deadline=deadline)
            if type(result) is not CalibrationResult:
                raise RuntimeError("calibration result malformed")
            return StartupContinueResult.HOME
        finally:
            live = False

    cycle = ReadinessCalibrationCycle(
        Win32Runtime(native_api),
        load_registry=lambda: load_private_registry(
            project, Path(slots_path), _BLUESTACKS_CONF
        ),
        make_state_store=lambda: build_native_state_store(project),
        observe_player_count=player_count,
        approvals=OneShotApprovalService(PrivateApprovalStorage(project)),
        candidate_tree=lambda: _candidate_tree(project),
        make_supervisor=lambda store, name: StartupGeometrySupervisor(
            host,
            store,
            AcquiredMutexLease(name),
            nonce_factory=lambda: secrets.token_hex(16),
            preserve_ready_cursor=True,
            geometry_api=NativeGeometryApi(native_api._user32),
        ),
        recover=recover,
    )
    return cycle


__all__ = [
    "DonorCalibrationStateProbe",
    "build_cycle",
    "issue_approval",
]
