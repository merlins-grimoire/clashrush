"""Fresh-approval-gated native composition for one local attack-only visit.

Importing this module performs no native action.  ``run_native_mvp_visit`` is the
only live entry and is reached only after the CLI's explicit approval flag.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from .config import load_private_registry
from .input_authorization import (
    InputAction,
    InputAuthorization,
    InputAuthorizationError,
)
from .lifecycle import AcquiredMutexLease, LifecycleSupervisor, PlayerBinding
from .lifecycle_state import LifecycleStateStore, Ready, encode_state
from .mvp_local_approval import LiveApprovalStore
from .mvp_local_gameplay import LocalBotMode, MvpConfiguration, VisitResult
from .mvp_local_runtime import (
    BgraGameplayRecognizer,
    BoundedAttackExecutor,
    ConcreteAttackVisitPorts,
    LocalControlStore,
    LocalMvpComposition,
    RuntimeSafetyError,
    encode_control_state,
)
from .mvp_local_world_export import (
    capture_world_export,
    clear_windows_clipboard,
    read_windows_clipboard,
)
from .registry import Slot
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import DEFAULT_MUTEX_NAME, Win32Runtime
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort

_BLUESTACKS_CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")
_SETTINGS_CLOSE_X = (0.802, 0.119)


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

    def __init__(
        self,
        binding: PlayerBinding,
        safety_check,
        authorization: InputAuthorization,
    ) -> None:
        if (
            type(binding) is not PlayerBinding
            or not callable(safety_check)
            or type(authorization) is not InputAuthorization
        ):
            raise RuntimeSafetyError("exact native input binding and authorization required")
        self._binding = binding
        self._safety_check = safety_check
        self._authorization = authorization
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

    def _authorized(self, action: InputAction) -> bool:
        try:
            self._authorization.require(action)
            return True
        except InputAuthorizationError:
            return False

    def click(
        self,
        binding: PlayerBinding,
        x: float,
        y: float,
        *,
        action: InputAction,
    ) -> bool:
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
        if not self._authorized(action) or not self._user32.SetCursorPos(point.x, point.y):
            return False
        if not self._authorized(action):
            return False
        self._user32.mouse_event(0x0002, 0, 0, 0, None)
        time.sleep(0.02)
        self._user32.mouse_event(0x0004, 0, 0, 0, None)
        return True

    def drag(
        self,
        binding: PlayerBinding,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        action: InputAction,
    ) -> bool:
        self._require_binding(binding)
        coordinates = (x0, y0, x1, y1)
        if any(
            type(value) not in (int, float) or not 0.0 <= value <= 1.0
            for value in coordinates
        ) or not self._foreground():
            return False
        start = wintypes.POINT(
            round(x0 * (binding.width - 1)), round(y0 * (binding.height - 1))
        )
        end = wintypes.POINT(
            round(x1 * (binding.width - 1)), round(y1 * (binding.height - 1))
        )
        for point in (start, end):
            if not self._user32.ClientToScreen(
                wintypes.HWND(binding.render_hwnd), ctypes.byref(point)
            ):
                return False
        if not self._authorized(action) or not self._user32.SetCursorPos(start.x, start.y):
            return False
        if not self._authorized(action):
            return False
        self._user32.mouse_event(0x0002, 0, 0, 0, None)
        time.sleep(0.05)
        moved = self._authorized(action) and self._user32.SetCursorPos(end.x, end.y)
        self._user32.mouse_event(0x0004, 0, 0, 0, None)
        return bool(moved)

    def key_down(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool:
        self._require_binding(binding)
        if (
            not self._foreground()
            or any(type(key) is not int or not 1 <= key <= 255 for key in keys)
        ):
            return False
        for key in keys:
            if not self._authorized(action):
                return False
            self._user32.keybd_event(key, 0, 0, None)
        return True

    def key_up(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool:
        # Releasing held controls is cleanup and must survive lost authorization.
        if binding != self._binding or action is not InputAction.CLEANUP_RELEASE:
            return False
        for key in keys:
            if type(key) is int and 1 <= key <= 255:
                self._user32.keybd_event(key, 0, 0x0002, None)
        return True


class Win32StartupContinueInput:
    """One-use wrapper exposing only a positively located Continue click."""

    def __init__(
        self,
        binding: PlayerBinding,
        safety_check,
        authorization: InputAuthorization,
    ) -> None:
        if (
            type(authorization) is not InputAuthorization
            or authorization.purpose.value != "STARTUP_CONTINUE_ONLY"
        ):
            raise RuntimeSafetyError("exact Continue authorization required")
        self._input = Win32BoundInput(binding, safety_check, authorization)
        self._used = False

    def click_continue(
        self, binding: PlayerBinding, x: float, y: float
    ) -> bool:
        if self._used:
            return False
        self._used = True
        return self._input.click(
            binding, x, y, action=InputAction.STARTUP_CONTINUE
        )


def _state_store(project: Path) -> LifecycleStateStore:
    var = project / "var"
    var.mkdir(exist_ok=True)
    return LifecycleStateStore(
        project,
        Win32StateFilePort(project, NativeWin32StateApi()),
    )


def _candidate_tree(project: Path) -> str:
    try:
        status = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status.stdout:
            raise RuntimeSafetyError("candidate tree is not immutable")
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeSafetyError("candidate tree is unavailable") from exc
    tree = result.stdout.strip()
    if len(tree) != 40 or any(
        character not in "0123456789abcdef" for character in tree
    ):
        raise RuntimeSafetyError("candidate tree is malformed")
    return tree


def _select_configured_slot(
    slots: tuple[Slot, ...], lifecycle: Ready, instance_ref: str
) -> Slot:
    """Bind the public opaque reference to the private lifecycle-selected slot."""
    expected_ref = f"slot-{lifecycle.next_slot}"
    if type(instance_ref) is not str or instance_ref != expected_ref:
        raise RuntimeSafetyError("WINDOW_BINDING")
    return slots[lifecycle.next_slot]


class StoppedControlPreparer:
    """Prepare the lifecycle-selected STOPPED control under the host mutex."""

    def __init__(
        self,
        runtime: object,
        *,
        make_state_store: Callable[[], object],
        control: object,
        observe_absence: Callable[[int], tuple[int, int]],
        approval_exists: Callable[[], bool],
    ) -> None:
        self._runtime = runtime
        self._make_state_store = make_state_store
        self._control = control
        self._observe_absence = observe_absence
        self._approval_exists = approval_exists

    def _require_boundary(self, slot_index: int) -> None:
        try:
            approval = self._approval_exists()
        except BaseException as exc:
            raise RuntimeSafetyError("active approval state is unavailable") from exc
        if type(approval) is not bool or approval:
            raise RuntimeSafetyError("active approval forbids control preparation")
        try:
            observation = self._observe_absence(slot_index)
        except BaseException as exc:
            raise RuntimeSafetyError("complete process/window absence is unproved") from exc
        if (
            type(observation) is not tuple
            or len(observation) != 2
            or any(type(count) is not int or count != 0 for count in observation)
        ):
            raise RuntimeSafetyError("complete process/window absence is unproved")

    def prepare(self, configurations: tuple[MvpConfiguration, ...]) -> Ready:
        if (
            type(configurations) is not tuple
            or len(configurations) != 5
            or any(
                type(configuration) is not MvpConfiguration
                or configuration.instance_ref != f"slot-{index}"
                for index, configuration in enumerate(configurations)
            )
        ):
            raise RuntimeSafetyError("exact five-slot control mapping required")
        lease = self._runtime.acquire_mutex()
        try:
            if type(lease.abandoned) is not bool or lease.abandoned:
                raise RuntimeSafetyError("abandoned mutex forbids control preparation")
            lease.require_usable()
            store = self._make_state_store()
            lifecycle = store.load()
            if type(lifecycle) is not Ready:
                raise RuntimeSafetyError("lifecycle is not READY")
            self._require_boundary(lifecycle.next_slot)
            configuration = configurations[lifecycle.next_slot]
            prepared = self._control.prepare_stopped(configuration)
            current = store.load()
            if type(current) is not Ready or current != lifecycle:
                raise RuntimeSafetyError("lifecycle changed during control preparation")
            self._require_boundary(lifecycle.next_slot)
            read_back = self._control.load()
            if (
                type(prepared) is not type(read_back)
                or prepared != read_back
                or read_back.configuration != configuration
                or read_back.mode is not LocalBotMode.STOPPED
            ):
                raise RuntimeSafetyError("control preparation read-back mismatch")
            return lifecycle
        finally:
            try:
                lease.release()
            except BaseException as exc:
                raise RuntimeSafetyError(
                    "mutex release is unresolved after control preparation"
                ) from exc


def prepare_native_mvp_control(
    project_root: str,
    slots_path: str,
    player_tag_hashes: tuple[str, ...],
) -> Ready:
    """Prepare the current READY slot without launching or sending input."""
    if (
        type(player_tag_hashes) is not tuple
        or len(player_tag_hashes) != 5
        or any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in player_tag_hashes
        )
    ):
        raise RuntimeSafetyError("exact five-slot tag mapping required")
    project = Path(project_root).resolve(strict=True)
    slots_file = Path(slots_path)
    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))

    def observe_absence(slot_index: int) -> tuple[int, int]:
        registry = load_private_registry(project, slots_file, _BLUESTACKS_CONF)
        if (
            type(registry) is not tuple
            or len(registry) != 5
            or tuple(slot.index for slot in registry) != (0, 1, 2, 3, 4)
        ):
            raise RuntimeSafetyError("exact ordered five-slot registry required")

        def player_count() -> int:
            snapshot = host.complete_player_snapshot()
            try:
                return len(snapshot.identities)
            finally:
                snapshot.close()

        before = player_count()
        windows = host.complete_relevant_root_window_count(
            registry[slot_index].display_name
        )
        after = player_count()
        return max(before, after), windows

    configurations = tuple(
        MvpConfiguration(
            "local-team-0",
            f"local-account-{index}",
            f"slot-{index}",
            player_tag_hashes[index],
        )
        for index in range(5)
    )
    return StoppedControlPreparer(
        runtime,
        make_state_store=lambda: _state_store(project),
        control=LocalControlStore(project / "var" / "mvp-local-control.json"),
        observe_absence=observe_absence,
        approval_exists=lambda: (project / "var" / "mvp-live-approval.json").exists(),
    ).prepare(configurations)


def run_native_mvp_visit(
    project_root: str, slots_path: str, transaction_ref: str
) -> VisitResult:
    """Launch, bind, attack, prove HOME, and stop one configured local account."""
    project = Path(project_root).resolve(strict=True)
    control = LocalControlStore(project / "var" / "mvp-local-control.json")
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
        configured = control.load()
        if configured.mode is not LocalBotMode.RUNNING:
            raise RuntimeSafetyError("persistent control mode is not RUNNING")
        store = _state_store(project)
        lifecycle = store.load()
        if type(lifecycle) is not Ready:
            raise RuntimeSafetyError("WINDOW_BINDING")
        LiveApprovalStore(project).validate_and_consume(
            candidate_tree=_candidate_tree(project),
            lifecycle_bytes=encode_state(lifecycle),
            control_bytes=encode_control_state(configured),
            now=int(time.time()),
        )
        slots = load_private_registry(project, Path(slots_path), _BLUESTACKS_CONF)
        if type(slots) is not tuple or len(slots) != 5:
            raise RuntimeSafetyError("WINDOW_BINDING")
        slot = _select_configured_slot(
            slots, lifecycle, configured.configuration.instance_ref
        )
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

        def enabled() -> bool:
            try:
                return control.load().mode is LocalBotMode.RUNNING
            except BaseException:
                return False

        source_recognizer = BgraGameplayRecognizer(
            binding, supervisor.capture_owned, account_verified=False
        )
        BgraGameplayRecognizer.recognize(
            source_recognizer, binding, configured.configuration.account_ref,
            require_home=True,
        )
        input_authorization = InputAuthorization.monitored_attack(enabled)
        input_port = Win32BoundInput(
            binding, supervisor.capture_owned, input_authorization
        )
        summary = capture_world_export(
            binding,
            input_port=input_port,
            expected_tag_sha256=configured.configuration.player_tag_sha256,
            live_gate=enabled,
            clipboard_read=read_windows_clipboard,
            clipboard_clear=clear_windows_clipboard,
            sleep=time.sleep,
        )
        if summary.account_matches is not True:
            raise RuntimeSafetyError("ACCOUNT_MISMATCH")
        recognizer = BgraGameplayRecognizer(
            binding, supervisor.capture_owned, account_verified=True
        )
        home_restored = False
        for _ in range(3):
            if (
                enabled() is not True
                or input_port.click(
                    binding,
                    *_SETTINGS_CLOSE_X,
                    action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
                ) is not True
            ):
                raise RuntimeSafetyError("KILL_SWITCH")
            time.sleep(1.0)
            if recognizer.recognize(
                binding, configured.configuration.account_ref
            ).home is True:
                home_restored = True
                break
        if not home_restored:
            raise RuntimeSafetyError("HOME_NOT_RESTORED")
        executor = BoundedAttackExecutor(
            binding,
            input_port,
            army_ready=recognizer.army_ready,
            begin_scout_transition=recognizer.capture_scout_source,
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
            cleanup=lambda: input_port.key_up(
                binding,
                0x52,
                0x4A,
                0x56,
                action=InputAction.CLEANUP_RELEASE,
            ),
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


__all__ = [
    "LocalAuditLog",
    "StoppedControlPreparer",
    "Win32BoundInput",
    "Win32StartupContinueInput",
    "prepare_native_mvp_control",
    "run_native_mvp_visit",
]
