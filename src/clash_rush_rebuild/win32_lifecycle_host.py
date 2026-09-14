"""Integrated inert Win32 implementation of ``LifecycleHostPort``."""

from __future__ import annotations

import ctypes
import re
from collections.abc import Callable
from ctypes import wintypes
from typing import Protocol

from .capture import capture_health
from .lifecycle import (
    CreatedProcess,
    MemberSnapshot,
    PlayerBinding,
    PlayerSnapshot,
    ProcessIdentity,
)
from .win32_primitives import (
    FILETIME,
    PROCESSENTRY32W,
    JobMembershipList,
    configure_kernel32_signatures,
    enumerate_processes,
    identity_from_retained_handle,
    job_process_id_list_type,
    stable_job_members,
)
from .win32_runtime import NativeWin32Api, RuntimeApi, Win32Runtime
from .window_diagnostic import (
    DiagnosticReason,
    RenderWindowFact,
    RootWindowFact,
    select_render_window,
    select_root_window,
)

BLUESTACKS_PLAYER_PATH = r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
_PLAYER_IMAGE_NAME = "HD-Player.exe"
_INTERNAL_NAME = re.compile(r"Pie64(?:_[A-Za-z0-9_]+)?")
_MAX_WINDOWS = 4096
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
SYNCHRONIZE = 0x00100000
JOB_OBJECT_BASIC_PROCESS_ID_LIST_CLASS = 3
ERROR_MORE_DATA = 234
GA_ROOT = 2
DESKTOP_SWITCHDESKTOP = 0x0100
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _cleanup_truthy(action: Callable[[], object]) -> bool:
    try:
        return bool(action())
    except BaseException:  # noqa: BLE001 - cleanup must continue through every resource
        return False


def _cleanup_equals_one(action: Callable[[], object]) -> bool:
    try:
        return action() == 1
    except BaseException:  # noqa: BLE001 - cleanup must continue through every resource
        return False


def _raise_native_capture(problem: str) -> None:
    raise Win32LifecycleHostError(problem)


def configure_lifecycle_host_signatures(
    kernel32: object, user32: object, gdi32: object
) -> None:
    """Declare every pointer-width native signature used by the host adapter."""
    configure_kernel32_signatures(kernel32)
    handle = wintypes.HANDLE
    hwnd = wintypes.HWND
    hdc = wintypes.HDC
    callback = WNDENUMPROC
    kernel32.TerminateProcess.argtypes = [handle, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [hwnd, callback, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [hwnd]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [hwnd]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        hwnd,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [hwnd]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetAncestor.argtypes = [hwnd, wintypes.UINT]
    user32.GetAncestor.restype = hwnd
    user32.GetClientRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.OpenInputDesktop.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    user32.OpenInputDesktop.restype = handle
    user32.SwitchDesktop.argtypes = [handle]
    user32.SwitchDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [handle]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [hwnd]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetDC.argtypes = [hwnd]
    user32.GetDC.restype = hdc
    user32.ReleaseDC.argtypes = [hwnd, hdc]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [hwnd, hdc, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [hdc]
    gdi32.CreateCompatibleDC.restype = hdc
    gdi32.CreateCompatibleBitmap.argtypes = [hdc, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.SelectObject.argtypes = [hdc, handle]
    gdi32.SelectObject.restype = handle
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [hdc]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        hdc,
        handle,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int


class NativeLifecycleApi(NativeWin32Api):
    """Native Toolhelp foundation for the integrated lifecycle host."""

    def __init__(self) -> None:
        super().__init__()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        configure_lifecycle_host_signatures(self._kernel32, self._user32, self._gdi32)

    def create_toolhelp32_snapshot(self) -> object:
        return self._kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)

    def process32_first(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        ctypes.set_last_error(0)
        return bool(self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))

    def process32_next(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        ctypes.set_last_error(0)
        return bool(self._kernel32.Process32NextW(snapshot, ctypes.byref(entry)))

    def open_process_for_identity(self, pid: int) -> object:
        if type(pid) is not int or pid <= 0:
            raise Win32LifecycleHostError("positive process ID required")
        return self._kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
        )

    def get_process_id(self, handle: object) -> int:
        pid = int(self._kernel32.GetProcessId(handle))
        if pid <= 0:
            raise Win32LifecycleHostError("GetProcessId failed")
        return pid

    def get_process_creation_time(self, handle: object) -> FILETIME:
        creation, exit_time, kernel, user = (
            FILETIME(),
            FILETIME(),
            FILETIME(),
            FILETIME(),
        )
        if not self._kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise Win32LifecycleHostError("GetProcessTimes failed")
        return creation

    def query_job_members(self, job: object, capacity: int) -> JobMembershipList:
        if type(capacity) is not int or capacity < 1:
            raise Win32LifecycleHostError("positive Job member capacity required")
        structure_type = job_process_id_list_type(capacity)
        information = structure_type()
        returned = wintypes.DWORD()
        ctypes.set_last_error(0)
        ok = self._kernel32.QueryInformationJobObject(
            job,
            JOB_OBJECT_BASIC_PROCESS_ID_LIST_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        )
        if not ok and ctypes.get_last_error() != ERROR_MORE_DATA:
            raise Win32LifecycleHostError("QueryInformationJobObject members failed")
        assigned = int(information.NumberOfAssignedProcesses)
        listed = int(information.NumberOfProcessIdsInList)
        if listed < 0 or listed > capacity:
            raise Win32LifecycleHostError("Job member list exceeded supplied capacity")
        pids = tuple(int(information.ProcessIdList[index]) for index in range(listed))
        return JobMembershipList(assigned, listed, pids)

    def terminate_process(self, process: object, exit_code: int) -> bool:
        return bool(self._kernel32.TerminateProcess(process, exit_code))

    @staticmethod
    def _enumerate_windows(
        native_call: Callable[[object, int], object],
    ) -> tuple[int, ...]:
        windows: list[int] = []
        callback_error: BaseException | None = None

        def visit(hwnd: int, _unused: int) -> bool:
            nonlocal callback_error
            try:
                windows.append(int(hwnd))
                return True
            except BaseException as exc:  # noqa: BLE001 - callback must fail closed
                callback_error = exc
                return False

        ctypes.set_last_error(0)
        result = native_call(WNDENUMPROC(visit), 0)
        if callback_error is not None:
            raise Win32LifecycleHostError(
                "window enumeration callback failed"
            ) from callback_error
        if not result:
            raise Win32LifecycleHostError("window enumeration failed")
        return tuple(windows)

    def enum_top_level_windows(self) -> tuple[int, ...]:
        return self._enumerate_windows(self._user32.EnumWindows)

    def enum_child_windows(self, root: int) -> tuple[int, ...]:
        if type(root) is not int or root <= 0:
            raise Win32LifecycleHostError("positive root HWND required")
        return self._enumerate_windows(
            lambda callback, value: self._user32.EnumChildWindows(root, callback, value)
        )

    def is_window(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindow(hwnd))

    def is_window_visible(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindowVisible(hwnd))

    def window_text(self, hwnd: int) -> str:
        ctypes.set_last_error(0)
        length = int(self._user32.GetWindowTextLengthW(hwnd))
        if length < 0:
            raise Win32LifecycleHostError("window text length invalid")
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = int(self._user32.GetWindowTextW(hwnd, buffer, len(buffer)))
        if copied != length:
            raise Win32LifecycleHostError("window text read was incomplete")
        return buffer.value

    def window_process_id(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise Win32LifecycleHostError("window process query failed")
        return int(pid.value)

    def get_ancestor_root(self, hwnd: int) -> int:
        root = self._user32.GetAncestor(hwnd, GA_ROOT)
        if not root:
            raise Win32LifecycleHostError("window root ancestry query failed")
        return int(root)

    def client_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise Win32LifecycleHostError("window client rectangle unavailable")
        return int(rect.right - rect.left), int(rect.bottom - rect.top)

    def interactive_desktop_unlocked(self) -> bool:
        desktop = self._user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return False
        try:
            unlocked = bool(self._user32.SwitchDesktop(desktop))
        finally:
            if not self._user32.CloseDesktop(desktop):
                raise Win32LifecycleHostError("input desktop handle close failed")
        return unlocked

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self._user32.IsIconic(hwnd))

    def capture_printwindow_bgra(
        self, hwnd: int, width: int, height: int
    ) -> tuple[int, int, bytes]:
        if (
            type(hwnd) is not int
            or hwnd <= 0
            or type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
            or width * height * 4 > 64 * 1024 * 1024
        ):
            raise Win32LifecycleHostError("bounded capture geometry required")
        window_dc = self._user32.GetDC(hwnd)
        if not window_dc:
            raise Win32LifecycleHostError("window DC unavailable")
        memory_dc: object | None = None
        bitmap: object | None = None
        old: object | None = None
        buffer: object | None = None
        problem: str | None = None
        try:
            memory_dc = self._gdi32.CreateCompatibleDC(window_dc)
            if not memory_dc:
                problem = "memory DC unavailable"
            if problem is None:
                bitmap = self._gdi32.CreateCompatibleBitmap(window_dc, width, height)
                if not bitmap:
                    problem = "bitmap allocation failed"
            if problem is None:
                old = self._gdi32.SelectObject(memory_dc, bitmap)
                if not old or int(old) in {-1, ctypes.c_void_p(-1).value}:
                    problem = "bitmap selection failed"
            if problem is None and not self._user32.PrintWindow(hwnd, memory_dc, 2):
                problem = "PrintWindow failed"
            if problem is None:
                size = width * height * 4
                header = BITMAPINFOHEADER(
                    ctypes.sizeof(BITMAPINFOHEADER),
                    width,
                    -height,
                    1,
                    32,
                    0,
                    size,
                    0,
                    0,
                    0,
                    0,
                )
                buffer = ctypes.create_string_buffer(size)
                copied = self._gdi32.GetDIBits(
                    memory_dc,
                    bitmap,
                    0,
                    height,
                    buffer,
                    ctypes.byref(header),
                    0,
                )
                if copied != height:
                    problem = "GetDIBits failed"
        except BaseException:  # noqa: BLE001 - remove frame storage before failing closed
            problem = "native capture failed"

        cleanup_failed = False
        if old is not None and memory_dc is not None:
            cleanup_failed = (
                not _cleanup_truthy(lambda: self._gdi32.SelectObject(memory_dc, old))
                or cleanup_failed
            )
        if bitmap is not None:
            cleanup_failed = (
                not _cleanup_truthy(lambda: self._gdi32.DeleteObject(bitmap))
                or cleanup_failed
            )
        if memory_dc is not None:
            cleanup_failed = (
                not _cleanup_truthy(lambda: self._gdi32.DeleteDC(memory_dc))
                or cleanup_failed
            )
        cleanup_failed = (
            not _cleanup_equals_one(lambda: self._user32.ReleaseDC(hwnd, window_dc))
            or cleanup_failed
        )

        if cleanup_failed:
            buffer = None
            _raise_native_capture("capture resource cleanup failed")
        if problem is not None or buffer is None:
            buffer = None
            _raise_native_capture(problem or "native capture failed")
        try:
            pixels = bytes(buffer.raw)
        except BaseException:  # noqa: BLE001 - remove frame storage before failing closed
            buffer = None
            _raise_native_capture("native capture failed")
        buffer = None
        return width, height, pixels


class Win32LifecycleHostError(RuntimeError):
    """The injected native seam could not prove a lifecycle operation."""


class LifecycleNativeApi(RuntimeApi, Protocol):
    """Native seam required by the inert lifecycle host."""


class Win32LifecycleHost:
    """Compose native Win32 facts into the fail-closed lifecycle host port."""

    def __init__(
        self,
        api: LifecycleNativeApi,
        *,
        nonce_factory: Callable[[], str],
    ) -> None:
        if not callable(nonce_factory):
            raise Win32LifecycleHostError("capture nonce factory required")
        self._api = api
        self._runtime = Win32Runtime(api)
        self._nonce_factory = nonce_factory

    def _close_retained(self, handles: list[object]) -> None:
        failed = False
        for handle in handles:
            if self._api.close_handle(handle) is not True:
                failed = True
        if failed:
            raise Win32LifecycleHostError("retained process handle close failed")

    def complete_player_snapshot(self) -> PlayerSnapshot:
        entries = enumerate_processes(self._api)
        handles: list[object] = []
        identities = []
        try:
            for entry in entries:
                if entry.image_name != _PLAYER_IMAGE_NAME:
                    continue
                handle = self._api.open_process_for_identity(entry.pid)
                if handle is None or handle == 0:
                    raise Win32LifecycleHostError("player identity handle unavailable")
                handles.append(handle)
                identities.append(
                    identity_from_retained_handle(
                        self._api, handle, expected_pid=entry.pid
                    )
                )
        except BaseException:
            self._close_retained(handles)
            raise
        ordered = tuple(
            sorted(identities, key=lambda value: (value.pid, value.creation_time_100ns))
        )
        return PlayerSnapshot(ordered, lambda: self._close_retained(handles))

    def create_job(self) -> object:
        return self._runtime.create_job()

    def set_kill_on_close(self, job: object) -> None:
        self._runtime.set_kill_on_close(job)

    def create_suspended(self, internal_name: str) -> CreatedProcess:
        if (
            type(internal_name) is not str
            or _INTERNAL_NAME.fullmatch(internal_name) is None
        ):
            raise Win32LifecycleHostError("strict internal instance name required")
        command_line = f'"{BLUESTACKS_PLAYER_PATH}" --instance {internal_name}'
        return self._runtime.create_suspended(BLUESTACKS_PLAYER_PATH, command_line)

    def identity_from_handle(
        self, process: object, expected_pid: int
    ) -> ProcessIdentity:
        return identity_from_retained_handle(
            self._api, process, expected_pid=expected_pid
        )

    def assign_to_job(self, job: object, process: object) -> None:
        self._runtime.assign_to_job(job, process)

    def is_process_in_job(self, process: object, job: object) -> bool:
        return self._runtime.is_process_in_job(process, job)

    def resume_thread(self, thread: object) -> int:
        return self._runtime.resume_thread(thread)

    def close_thread(self, thread: object) -> None:
        self._runtime.close_thread(thread)

    def stable_job_members(self, job: object) -> MemberSnapshot:
        return stable_job_members(self._api, job)

    def _window_identity(self, hwnd: int, job: object | None = None) -> ProcessIdentity:
        pid = self._api.window_process_id(hwnd)
        if type(pid) is not int or pid <= 0:
            raise Win32LifecycleHostError("window process ID unavailable")
        handle = self._api.open_process_for_identity(pid)
        if handle is None or handle == 0:
            raise Win32LifecycleHostError("window process handle unavailable")
        try:
            identity = identity_from_retained_handle(
                self._api, handle, expected_pid=pid
            )
            if (
                job is not None
                and self._runtime.is_process_in_job(handle, job) is not True
            ):
                raise Win32LifecycleHostError(
                    "window process is outside the private Job"
                )
            return identity
        finally:
            if self._api.close_handle(handle) is not True:
                raise Win32LifecycleHostError("window process handle close failed")

    def _window_is_visible(self, hwnd: int) -> bool:
        result = self._api.is_window_visible(hwnd)
        if type(result) is not bool:
            raise Win32LifecycleHostError("window visibility result invalid")
        return result

    @staticmethod
    def _window_list(value: object) -> tuple[int, ...]:
        if (
            type(value) is not tuple
            or any(type(hwnd) is not int or hwnd <= 0 for hwnd in value)
            or len(set(value)) != len(value)
            or len(value) > _MAX_WINDOWS
        ):
            raise Win32LifecycleHostError("complete exact window enumeration required")
        return value

    def bind_exact(
        self,
        identity: ProcessIdentity,
        expected_title: str,
        members: MemberSnapshot,
    ) -> PlayerBinding | None:
        if (
            type(identity) is not ProcessIdentity
            or type(members) is not MemberSnapshot
            or identity not in members.identities
            or type(expected_title) is not str
            or not expected_title
            or expected_title != expected_title.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in expected_title
            )
        ):
            raise Win32LifecycleHostError("exact member identity and title required")

        root_windows = self._window_list(self._api.enum_top_level_windows())
        root_facts: list[RootWindowFact] = []
        for hwnd in root_windows:
            visible = self._window_is_visible(hwnd)
            title_matches = visible and self._api.window_text(hwnd) == expected_title
            ancestry_matches = (
                title_matches and self._api.get_ancestor_root(hwnd) == hwnd
            )
            identity_matches = (
                ancestry_matches and self._window_identity(hwnd) == identity
            )
            root_facts.append(
                RootWindowFact(
                    visible,
                    title_matches,
                    ancestry_matches,
                    identity_matches,
                )
            )
        root_selection = select_root_window(tuple(root_facts), complete=True)
        if (
            root_selection.reason is not DiagnosticReason.BINDING_READY
            or root_selection.selected_index is None
        ):
            return None
        root = root_windows[root_selection.selected_index]

        render_windows = self._window_list(self._api.enum_child_windows(root))
        render_facts: list[RenderWindowFact] = []
        for hwnd in render_windows:
            visible = self._window_is_visible(hwnd)
            ancestry_matches = (
                visible and self._api.get_ancestor_root(hwnd) == root
            )
            render_identity = (
                self._window_identity(hwnd) if ancestry_matches else None
            )
            in_private_job = render_identity in members.identities
            identity_matches = render_identity == identity
            render_facts.append(
                RenderWindowFact(
                    visible,
                    ancestry_matches,
                    in_private_job,
                    identity_matches,
                    self._api.client_size(hwnd) if identity_matches else (0, 0),
                )
            )
        render_selection = select_render_window(tuple(render_facts), complete=True)
        if (
            render_selection.reason is not DiagnosticReason.BINDING_READY
            or render_selection.selected_index is None
            or render_selection.selected_size is None
        ):
            return None
        render = render_windows[render_selection.selected_index]
        width, height = render_selection.selected_size
        return PlayerBinding(
            identity,
            root,
            render,
            width,
            height,
            self._nonce_factory(),
        )

    def _require_capture_binding(self, binding: PlayerBinding, job: object) -> None:
        if type(binding) is not PlayerBinding or job is None:
            raise Win32LifecycleHostError("exact capture binding and Job required")
        if not self.is_window(binding.root_hwnd) or not self.is_window(
            binding.render_hwnd
        ):
            raise Win32LifecycleHostError("capture HWND is stale")
        if not self._window_is_visible(
            binding.root_hwnd
        ) or not self._window_is_visible(binding.render_hwnd):
            raise Win32LifecycleHostError("capture HWND is not visible")
        if self._api.get_ancestor_root(binding.root_hwnd) != binding.root_hwnd:
            raise Win32LifecycleHostError("capture root ancestry mismatch")
        if self._api.get_ancestor_root(binding.render_hwnd) != binding.root_hwnd:
            raise Win32LifecycleHostError("capture render ancestry mismatch")
        if self._window_identity(binding.root_hwnd, job) != binding.identity:
            raise Win32LifecycleHostError("capture root identity mismatch")
        if self._window_identity(binding.render_hwnd, job) != binding.identity:
            raise Win32LifecycleHostError("capture render identity mismatch")
        if self._api.client_size(binding.render_hwnd) != (
            binding.width,
            binding.height,
        ):
            raise Win32LifecycleHostError("capture render geometry changed")
        unlocked = self._api.interactive_desktop_unlocked()
        if type(unlocked) is not bool or not unlocked:
            raise Win32LifecycleHostError("interactive desktop is locked")
        iconic = self._api.is_iconic(binding.root_hwnd)
        if type(iconic) is not bool or iconic:
            raise Win32LifecycleHostError("minimized capture is unsupported")

    def capture_bgra(self, binding: PlayerBinding) -> tuple[int, int, bytes]:
        return self._api.capture_printwindow_bgra(
            binding.render_hwnd, binding.width, binding.height
        )

    def capture_ready(self, binding: PlayerBinding, job: object) -> None:
        self._require_capture_binding(binding, job)
        capture_health(self, binding)

    def terminate_job(self, job: object) -> None:
        self._runtime.terminate_job(job)

    def terminate_retained_process(self, process: object) -> None:
        if process is None or self._api.terminate_process(process, 1) is not True:
            raise Win32LifecycleHostError("TerminateProcess on retained handle failed")

    def wait_process(self, process: object, milliseconds: int) -> bool:
        return self._runtime.wait_process(process, milliseconds)

    def job_active_count(self, job: object) -> int:
        return self._runtime.job_active_count(job)

    def is_window(self, hwnd: int) -> bool:
        result = self._api.is_window(hwnd)
        if type(result) is not bool:
            raise Win32LifecycleHostError("IsWindow returned an invalid result")
        return result

    def close_handle(self, handle: object) -> None:
        self._runtime.close_handle(handle)
