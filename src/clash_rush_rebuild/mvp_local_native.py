"""Fresh-approval-gated native composition for one local attack-only visit.

Importing this module performs no native action.  ``run_native_mvp_visit`` is the
only live entry and is reached only after the CLI's explicit approval flag.
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import time
from ctypes import wintypes
from pathlib import Path

from .config import load_private_registry
from .lifecycle import AcquiredMutexLease, LifecycleSupervisor, PlayerBinding
from .lifecycle_state import LifecycleStateStore, Ready
from .mvp_local_gameplay import LocalBotMode, VisitResult
from .mvp_local_runtime import (
    BgraGameplayRecognizer,
    BoundedAttackExecutor,
    ConcreteAttackVisitPorts,
    LocalControlStore,
    LocalMvpComposition,
    RuntimeSafetyError,
)
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import DEFAULT_MUTEX_NAME, Win32Runtime
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort

_BLUESTACKS_CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")


class LocalAuditLog:
    """Append only transaction phase facts without Team/account/instance values."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _append(self, payload: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def intent(self, transaction_ref: str) -> None:
        self._append({"phase": "INTENT", "transaction_ref": transaction_ref})

    def outcome(self, transaction_ref: str, confirmed: bool) -> None:
        self._append(
            {
                "phase": "OUTCOME",
                "transaction_ref": transaction_ref,
                "confirmed": confirmed,
            }
        )


def _configure_input_signatures(user32: object) -> None:
    hwnd = wintypes.HWND
    user32.GetForegroundWindow.restype = hwnd
    user32.GetAncestor.argtypes = [hwnd, wintypes.UINT]
    user32.GetAncestor.restype = hwnd
    user32.SetForegroundWindow.argtypes = [hwnd]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [hwnd, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [hwnd]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [hwnd, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    user32.keybd_event.argtypes = [
        wintypes.BYTE,
        wintypes.BYTE,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]


class Win32BoundInput:
    """Foreground native input restricted to one immutable render binding."""

    def __init__(self, binding: PlayerBinding, safety_check) -> None:
        if type(binding) is not PlayerBinding or not callable(safety_check):
            raise RuntimeSafetyError("exact native input binding required")
        self._binding = binding
        self._safety_check = safety_check
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        _configure_input_signatures(self._user32)

    def _require_binding(self, binding: PlayerBinding) -> None:
        if binding != self._binding:
            raise RuntimeSafetyError("native input binding changed")
        self._safety_check(binding)

    def _foreground(self) -> bool:
        self._user32.ShowWindow(wintypes.HWND(self._binding.root_hwnd), 9)
        self._user32.BringWindowToTop(wintypes.HWND(self._binding.root_hwnd))
        self._user32.SetForegroundWindow(wintypes.HWND(self._binding.root_hwnd))
        active = self._user32.GetForegroundWindow()
        return bool(
            active
            and int(self._user32.GetAncestor(active, 2)) == self._binding.root_hwnd
        )

    def click(self, binding: PlayerBinding, x: float, y: float) -> bool:
        self._require_binding(binding)
        if (
            type(x) not in (int, float)
            or type(y) not in (int, float)
            or not 0.0 <= x <= 1.0
            or not 0.0 <= y <= 1.0
            or not self._foreground()
        ):
            return False
        point = wintypes.POINT(
            round(x * (binding.width - 1)), round(y * (binding.height - 1))
        )
        if not self._user32.ClientToScreen(
            wintypes.HWND(binding.render_hwnd), ctypes.byref(point)
        ):
            return False
        if not self._user32.SetCursorPos(point.x, point.y):
            return False
        time.sleep(0.04)
        self._user32.mouse_event(0x0002, 0, 0, 0, None)
        time.sleep(0.02)
        self._user32.mouse_event(0x0004, 0, 0, 0, None)
        return True

    def key_down(self, binding: PlayerBinding, *keys: int) -> bool:
        self._require_binding(binding)
        if not self._foreground() or any(
            type(key) is not int or not 1 <= key <= 255 for key in keys
        ):
            return False
        for key in keys:
            self._user32.keybd_event(key, 0, 0, None)
        return True

    def key_up(self, binding: PlayerBinding, *keys: int) -> bool:
        # Releasing held controls is cleanup and must survive lost authorization.
        if binding != self._binding:
            return False
        for key in keys:
            if type(key) is int and 1 <= key <= 255:
                self._user32.keybd_event(key, 0, 0x0002, None)
        return True


def _state_store(project: Path) -> LifecycleStateStore:
    var = project / "var"
    var.mkdir(exist_ok=True)
    return LifecycleStateStore(
        project,
        Win32StateFilePort(project, NativeWin32StateApi()),
    )


def run_native_mvp_visit(
    project_root: str, slots_path: str, transaction_ref: str
) -> VisitResult:
    """Launch, bind, attack, prove HOME, and stop one configured local account."""
    project = Path(project_root).resolve(strict=True)
    control = LocalControlStore(project / "var" / "mvp-local-control.json")
    configured = control.load()
    if configured.mode is not LocalBotMode.RUNNING:
        raise RuntimeSafetyError("persistent control mode is not RUNNING")

    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))
    lease = runtime.acquire_mutex()
    supervisor: LifecycleSupervisor | None = None
    binding: PlayerBinding | None = None
    result: VisitResult | None = None
    try:
        if lease.abandoned:
            raise RuntimeSafetyError("WINDOW_BINDING")
        lease.require_usable()
        slots = load_private_registry(project, Path(slots_path), _BLUESTACKS_CONF)
        if type(slots) is not tuple or len(slots) != 5:
            raise RuntimeSafetyError("WINDOW_BINDING")
        store = _state_store(project)
        lifecycle = store.load()
        if type(lifecycle) is not Ready:
            raise RuntimeSafetyError("WINDOW_BINDING")
        slot = slots[lifecycle.next_slot]
        if slot.display_name != configured.configuration.instance_ref:
            raise RuntimeSafetyError("WINDOW_BINDING")
        players = host.complete_player_snapshot()
        try:
            if players.identities:
                raise RuntimeSafetyError("WINDOW_BINDING")
        finally:
            players.close()
        supervisor = LifecycleSupervisor(
            host,
            store,
            AcquiredMutexLease(DEFAULT_MUTEX_NAME),
            nonce_factory=lambda: secrets.token_hex(16),
        )
        binding = supervisor.start(slot)
        recognizer = BgraGameplayRecognizer(binding, supervisor.capture_owned)

        def enabled() -> bool:
            try:
                return control.load().mode is LocalBotMode.RUNNING
            except BaseException:
                return False

        input_port = Win32BoundInput(binding, supervisor.capture_owned)
        executor = BoundedAttackExecutor(
            binding,
            input_port,
            army_ready=recognizer.army_ready,
            scout_ready=recognizer.scout_ready,
            return_home_visible=recognizer.return_home_visible,
            kill_switch_enabled=enabled,
            sleep=time.sleep,
        )
        ports = ConcreteAttackVisitPorts(
            binding=binding,
            account_ref=configured.configuration.account_ref,
            recognizer=recognizer,
            executor=executor,
            kill_switch_enabled=enabled,
            audit=LocalAuditLog(project / "var" / "mvp-local-audit.jsonl"),
            cleanup=lambda: input_port.key_up(binding, 0x52, 0x4A, 0x56),
        )
        result = LocalMvpComposition(control, ports).visit_once(transaction_ref)
    finally:
        stop_error: BaseException | None = None
        if supervisor is not None and binding is not None:
            try:
                supervisor.stop(binding, None)
            except BaseException as exc:
                stop_error = exc
        try:
            lease.release()
        except BaseException as exc:
            stop_error = stop_error or exc
        if stop_error is not None:
            raise RuntimeSafetyError("owned lifecycle cleanup failed") from stop_error
    if type(result) is not VisitResult:
        raise RuntimeSafetyError("bounded visit did not produce a result")
    return result


__all__ = ["LocalAuditLog", "Win32BoundInput", "run_native_mvp_visit"]
