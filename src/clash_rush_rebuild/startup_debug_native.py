"""Diagnostic-only composition. Importing this module performs no live work."""

from __future__ import annotations

import ctypes
import secrets
import time
from ctypes import wintypes
from pathlib import Path

from .approval_reconciliation import (
    ApprovalAction,
    OneShotApprovalService,
    PrivateApprovalStorage,
)
from .basepilot_window import WindowService
from .config import load_private_registry
from .cycle import StartupDebugCycle
from .input_authorization import InputAuthorization
from .lifecycle import AcquiredMutexLease
from .lifecycle_state import Ready
from .mvp_local_native import Win32BoundInput
from .no_input_home_diagnostic import NoInputHomeDiagnosticController
from .startup_continue_recovery import StartupContinueResult
from .startup_debug import (
    CAPS,
    DebugEvidence,
    StartupDebugController,
    StartupDebugError,
    StartupDetector,
)
from .startup_geometry import NativeGeometryApi, StartupGeometrySupervisor
from .startup_failure import StartupReason, at_stage
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import Win32Runtime


class StartupNativeInput:
    """Closed counted gesture; fresh detector and Job capture immediately before down."""

    def __init__(self, binding, capture, detect, evidence, gate, safety_check):
        self._binding = binding
        self._capture, self._detect, self._evidence, self._gate = (
            capture,
            detect,
            evidence,
            gate,
        )
        self._counts = {action: 0 for action in CAPS}
        self._expires = time.monotonic() + 120
        self._authorization = InputAuthorization.startup_debug(gate)
        self._native = Win32BoundInput(binding, safety_check, self._authorization)

    def __call__(self, action, point, prepare, deadline):
        if action not in self._counts or self._counts[action] >= CAPS[action]:
            return False
        at_stage(StartupReason.AUTHORIZATION, lambda: self._authorization.require(action))
        x, y = point
        if any(type(v) not in (int, float) or not 0 < v < 1 for v in point):
            return False
        self._counts[action] += 1
        self._native._require_binding(self._binding)
        if not self._native._foreground():
            return False
        at_stage(StartupReason.EVIDENCE_BEFORE, self._evidence.verify)
        screen = wintypes.POINT(
            round(x * (self._binding.width - 1)), round(y * (self._binding.height - 1))
        )
        api = self._native._user32
        if not api.ClientToScreen(
            wintypes.HWND(self._binding.render_hwnd), ctypes.byref(screen)
        ):
            return False
        at_stage(StartupReason.AUTHORIZATION, lambda: self._authorization.require(action))
        if not api.SetCursorPos(screen.x, screen.y):
            return False
        time.sleep(0.04)
        observed_at = time.monotonic()
        frame = at_stage(StartupReason.CAPTURE, self._capture)
        try:
            accepted = at_stage(StartupReason.ICON_VERIFY, lambda: self._detect(frame)) == (action, point)
            if accepted:
                prepare(frame)
        finally:
            frame.fill(0)
        if not accepted:
            return False
        at_stage(StartupReason.AUTHORIZATION, lambda: self._authorization.require(action))
        # Cursor and window placement are shared mutable desktop state.
        api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        api.GetCursorPos.restype = wintypes.BOOL
        current = wintypes.POINT()
        mapped = wintypes.POINT(
            round(x * (self._binding.width - 1)), round(y * (self._binding.height - 1))
        )
        if (
            not api.GetCursorPos(ctypes.byref(current))
            or not api.ClientToScreen(
                wintypes.HWND(self._binding.render_hwnd), ctypes.byref(mapped)
            )
            or (current.x, current.y) != (screen.x, screen.y)
            or (mapped.x, mapped.y) != (screen.x, screen.y)
        ):
            return False
        active = api.GetForegroundWindow()
        if (
            not active
            or int(api.GetAncestor(active, 2)) != self._binding.root_hwnd
            or time.monotonic() >= min(self._expires, deadline, observed_at + 2.0)
        ):
            return False
        try:
            api.mouse_event(0x0002, 0, 0, 0, None)
            time.sleep(0.02)
        finally:
            api.mouse_event(0x0004, 0, 0, 0, None)
        return True


def issue_approval(project_root, lifetime_seconds):
    from .cli import _candidate_tree, build_native_state_store

    project = Path(project_root).resolve(strict=True)
    runtime = Win32Runtime(NativeLifecycleApi())
    lease = runtime.acquire_mutex()
    try:
        if lease.abandoned:
            raise StartupDebugError("APPROVAL_UNAVAILABLE")
        lease.require_usable()
        state, raw = build_native_state_store(project).load_with_bytes()
        if type(state) is not Ready:
            raise StartupDebugError("APPROVAL_UNAVAILABLE")
        return OneShotApprovalService(PrivateApprovalStorage(project)).grant(
            ApprovalAction.STARTUP_DEBUG,
            _candidate_tree(project),
            state,
            raw,
            lifetime_seconds=lifetime_seconds,
        )
    finally:
        lease.release()


def build_cycle(project_root, slots_path):
    from .cli import _BLUESTACKS_CONF, _candidate_tree, build_native_state_store

    project = Path(project_root).resolve(strict=True)
    native = NativeLifecycleApi()
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))

    def player_count():
        snapshot = host.complete_player_snapshot()
        try:
            return len(snapshot.identities)
        finally:
            snapshot.close()

    def recover(supervisor, binding):
        live = True
        expires = time.monotonic() + 120

        def gate():
            return live and time.monotonic() < expires

        window = WindowService(binding, supervisor.capture_owned)
        village = NoInputHomeDiagnosticController(window)

        def classify(frame):
            return StartupContinueResult(
                village._detect_village_type(
                    frame, expected_size=(binding.width, binding.height)
                ).value
            )

        evidence = at_stage(StartupReason.EVIDENCE_INIT, lambda: DebugEvidence(project))
        detector = StartupDetector(project / "private" / "assets" / "CCBackBeat.ttf")
        port = StartupNativeInput(
            binding,
            window.screenshot,
            detector,
            evidence,
            gate,
            supervisor.capture_owned,
        )
        try:
            return StartupDebugController(
                capture=window.screenshot,
                classify=classify,
                detect=detector,
                deliver=port,
                evidence=evidence,
                gate=gate,
            ).run()
        finally:
            live = False

    return StartupDebugCycle(
        Win32Runtime(native),
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
            geometry_api=NativeGeometryApi(native._user32),
        ),
        recover=recover,
    )
