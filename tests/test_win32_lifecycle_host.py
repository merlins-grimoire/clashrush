from __future__ import annotations

import ctypes
import inspect
import os

import pytest

from clash_rush_rebuild.capture import CaptureError
from clash_rush_rebuild.lifecycle import (
    AcquiredMutexLease,
    LifecycleSupervisor,
    MemberSnapshot,
    ProcessIdentity,
)
from clash_rush_rebuild.lifecycle_state import Active, Ready
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.win32_lifecycle_host import (
    BLUESTACKS_PLAYER_PATH,
    PROCESS_QUERY_LIMITED_INFORMATION,
    SYNCHRONIZE,
    NativeLifecycleApi,
    Win32LifecycleHost,
    Win32LifecycleHostError,
    configure_lifecycle_host_signatures,
)
from clash_rush_rebuild.win32_primitives import (
    ERROR_NO_MORE_FILES,
    FILETIME,
    PROCESSENTRY32W,
    JobMembershipList,
    enumerate_processes,
)
from clash_rush_rebuild.win32_runtime import CreatedProcess


class SyntheticNative:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.processes: list[tuple[int, int, str]] = []
        self.creation: dict[int, int] = {}
        self.windows: dict[int, dict[str, object]] = {}
        self.job_lists = [
            JobMembershipList(1, 1, (100,)),
            JobMembershipList(1, 1, (100,)),
        ]
        self._process_index = 0
        self.last_error = ERROR_NO_MORE_FILES
        self.frame = bytes([40, 50, 60, 0]) * (1280 * 720)
        self.unlocked = True
        self.iconic = False
        self.launch_on_create = False

    # Toolhelp / retained identity seam
    def create_toolhelp32_snapshot(self) -> object:
        return "snapshot"

    def process32_first(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        self._process_index = 0
        return self._copy_process(entry)

    def process32_next(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        self._process_index += 1
        return self._copy_process(entry)

    def _copy_process(self, entry: PROCESSENTRY32W) -> bool:
        if self._process_index >= len(self.processes):
            self.last_error = ERROR_NO_MORE_FILES
            return False
        pid, parent, image = self.processes[self._process_index]
        entry.th32ProcessID = pid
        entry.th32ParentProcessID = parent
        entry.szExeFile = image
        return True

    def get_last_error(self) -> int:
        return self.last_error

    def open_process_for_identity(self, pid: int) -> object:
        self.calls.append(("open", pid))
        return ("process", pid)

    def get_process_id(self, handle: object) -> int:
        return int(handle[1])  # type: ignore[index]

    def get_process_creation_time(self, handle: object) -> FILETIME:
        value = self.creation[self.get_process_id(handle)]
        return FILETIME(value & 0xFFFFFFFF, value >> 32)

    def close_handle(self, handle: object) -> bool:
        self.calls.append(("close", handle))
        return True

    # Runtime seam
    def create_job_object(self) -> object:
        return "job"

    def set_job_limit_flags(self, job: object, flags: int) -> bool:
        return True

    def create_process_w(
        self, executable, command_line, inherit_handles, creation_flags
    ):
        self.calls.append(
            ("create", executable, command_line.value, inherit_handles, creation_flags)
        )
        if self.launch_on_create:
            self.processes = [(100, 1, "HD-Player.exe")]
            self.creation[100] = 9001
            _windows(self)
        return CreatedProcess(("process", 100), "thread", 100)

    def assign_process_to_job(self, job: object, process: object) -> bool:
        return True

    def is_process_in_job(self, process: object, job: object) -> bool:
        return True

    def resume_thread(self, thread: object) -> int:
        return 1

    def terminate_job_object(self, job: object, exit_code: int) -> bool:
        if self.launch_on_create:
            self.processes = []
            self.windows = {}
        return True

    def terminate_process(self, process: object, exit_code: int) -> bool:
        self.calls.append(("terminate-process", process, exit_code))
        return True

    def wait_for_single_object(self, handle: object, milliseconds: int) -> int:
        return 0

    def job_active_process_count(self, job: object) -> int:
        return 0

    def query_job_members(self, job: object, capacity: int) -> JobMembershipList:
        return self.job_lists.pop(0)

    # Window/capture seam
    def enum_top_level_windows(self) -> tuple[int, ...]:
        return tuple(
            hwnd for hwnd, data in self.windows.items() if data["parent"] is None
        )

    def enum_child_windows(self, root: int) -> tuple[int, ...]:
        return tuple(
            hwnd for hwnd, data in self.windows.items() if data["parent"] == root
        )

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.windows

    def is_window_visible(self, hwnd: int) -> bool:
        return bool(self.windows[hwnd]["visible"])

    def window_text(self, hwnd: int) -> str:
        return str(self.windows[hwnd]["title"])

    def window_process_id(self, hwnd: int) -> int:
        return int(self.windows[hwnd]["pid"])

    def get_ancestor_root(self, hwnd: int) -> int:
        parent = self.windows[hwnd]["parent"]
        return hwnd if parent is None else int(parent)

    def client_size(self, hwnd: int) -> tuple[int, int]:
        return self.windows[hwnd]["size"]  # type: ignore[return-value]

    def interactive_desktop_unlocked(self) -> bool:
        return self.unlocked

    def is_iconic(self, hwnd: int) -> bool:
        return self.iconic

    def capture_printwindow_bgra(
        self, hwnd: int, width: int, height: int
    ) -> tuple[int, int, bytes]:
        self.calls.append(("capture", hwnd, width, height))
        return width, height, self.frame


def _host(api: SyntheticNative) -> Win32LifecycleHost:
    return Win32LifecycleHost(api, nonce_factory=lambda: "a" * 32)


def _windows(api: SyntheticNative) -> None:
    api.windows = {
        10: {
            "parent": None,
            "visible": True,
            "title": "Exact Slot",
            "pid": 100,
            "size": (1300, 760),
        },
        11: {
            "parent": 10,
            "visible": True,
            "title": "",
            "pid": 100,
            "size": (1280, 720),
        },
    }


def test_create_suspended_uses_only_the_exact_bluestacks_command() -> None:
    api = SyntheticNative()
    host = _host(api)

    host.create_suspended("Pie64_slot_1")

    assert api.calls == [
        (
            "create",
            BLUESTACKS_PLAYER_PATH,
            f'"{BLUESTACKS_PLAYER_PATH}" --instance Pie64_slot_1',
            False,
            4,
        )
    ]
    with pytest.raises(Win32LifecycleHostError, match="internal instance"):
        host.create_suspended("Pie64_slot_1 --evil")
    assert len(api.calls) == 1


def test_player_snapshot_filters_exact_name_and_retains_identity_handles() -> None:
    api = SyntheticNative()
    api.processes = [
        (100, 1, "HD-Player.exe"),
        (101, 1, "hd-player.exe"),
        (102, 1, "HD-Player.exe.bak"),
    ]
    api.creation = {100: 9001}

    snapshot = _host(api).complete_player_snapshot()

    assert snapshot.identities == (ProcessIdentity(100, 9001),)
    assert api.calls == [("close", "snapshot"), ("open", 100)]
    snapshot.close()
    assert api.calls[-1] == ("close", ("process", 100))


def test_player_snapshot_closes_retained_handles_when_identity_fails() -> None:
    api = SyntheticNative()
    api.processes = [(100, 1, "HD-Player.exe"), (101, 1, "HD-Player.exe")]
    api.creation = {100: 9001}

    with pytest.raises(KeyError):
        _host(api).complete_player_snapshot()

    assert api.calls[0] == ("close", "snapshot")
    assert ("close", ("process", 100)) in api.calls
    assert ("close", ("process", 101)) in api.calls


def test_stable_job_members_are_handle_backed_and_runtime_methods_delegate() -> None:
    api = SyntheticNative()
    api.creation = {100: 9001}
    host = _host(api)

    assert host.create_job() == "job"
    host.set_kill_on_close("job")
    assert host.identity_from_handle(("process", 100), 100) == ProcessIdentity(
        100, 9001
    )
    host.assign_to_job("job", ("process", 100))
    assert host.is_process_in_job(("process", 100), "job") is True
    assert host.resume_thread("thread") == 1
    host.close_thread("thread")
    members = host.stable_job_members("job")
    assert members.identities == (ProcessIdentity(100, 9001),)
    members.close()
    host.terminate_job("job")
    assert host.wait_process(("process", 100), 30_000) is True
    assert host.job_active_count("job") == 0
    host.close_handle("job")


def test_preassignment_termination_uses_only_the_supplied_retained_handle() -> None:
    api = SyntheticNative()
    host = _host(api)

    host.terminate_retained_process(("process", 100))

    assert api.calls == [("terminate-process", ("process", 100), 1)]


def _members(*identities: ProcessIdentity) -> MemberSnapshot:
    return MemberSnapshot(tuple(identities), lambda: None)


def test_bind_exact_requires_exact_identity_title_root_ancestry_and_geometry() -> None:
    api = SyntheticNative()
    api.creation = {100: 9001}
    _windows(api)

    binding = _host(api).bind_exact(
        ProcessIdentity(100, 9001),
        "Exact Slot",
        _members(ProcessIdentity(100, 9001)),
    )

    assert binding is not None
    assert binding.identity == ProcessIdentity(100, 9001)
    assert (binding.root_hwnd, binding.render_hwnd) == (10, 11)
    assert (binding.width, binding.height) == (1280, 720)
    assert binding.capture_nonce == "a" * 32


@pytest.mark.parametrize(
    "fault", ["title", "root-identity", "render-identity", "visibility", "ancestry"]
)
def test_bind_exact_rejects_every_inexact_window_fact(fault: str) -> None:
    api = SyntheticNative()
    api.creation = {100: 9001, 101: 9002}
    _windows(api)
    if fault == "title":
        api.windows[10]["title"] = "Exact Slot - extra"
    elif fault == "root-identity":
        api.windows[10]["pid"] = 101
    elif fault == "render-identity":
        api.windows[11]["pid"] = 101
    elif fault == "visibility":
        api.windows[11]["visible"] = False
    else:
        api.windows[11]["parent"] = None

    binding = _host(api).bind_exact(
        ProcessIdentity(100, 9001),
        "Exact Slot",
        _members(ProcessIdentity(100, 9001), ProcessIdentity(101, 9002)),
    )

    assert binding is None


def test_bind_exact_rejects_multiple_roots_and_equal_largest_render_areas() -> None:
    api = SyntheticNative()
    api.creation = {100: 9001}
    _windows(api)
    api.windows[12] = {
        "parent": None,
        "visible": True,
        "title": "Exact Slot",
        "pid": 100,
        "size": (1300, 760),
    }
    host = _host(api)
    members = _members(ProcessIdentity(100, 9001))
    assert host.bind_exact(ProcessIdentity(100, 9001), "Exact Slot", members) is None

    del api.windows[12]
    api.windows[12] = {
        "parent": 10,
        "visible": True,
        "title": "",
        "pid": 100,
        "size": (1280, 720),
    }
    assert host.bind_exact(ProcessIdentity(100, 9001), "Exact Slot", members) is None


def test_bind_exact_rejects_child_owned_render_even_when_owner_is_in_private_job() -> None:
    api = SyntheticNative()
    api.creation = {100: 9001, 101: 9002}
    _windows(api)
    api.windows[11]["pid"] = 101

    def geometry_must_not_be_queried(_hwnd: int) -> tuple[int, int]:
        raise AssertionError("geometry queried after exact-identity rejection")

    api.client_size = geometry_must_not_be_queried  # type: ignore[method-assign]

    binding = _host(api).bind_exact(
        ProcessIdentity(100, 9001),
        "Exact Slot",
        _members(ProcessIdentity(100, 9001), ProcessIdentity(101, 9002)),
    )

    assert binding is None


def _binding_and_job(api: SyntheticNative):
    _windows(api)
    api.creation = {100: 9001}
    host = _host(api)
    identity = ProcessIdentity(100, 9001)
    binding = host.bind_exact(identity, "Exact Slot", _members(identity))
    assert binding is not None
    return host, binding


def test_capture_ready_revalidates_binding_job_desktop_and_reduces_frame() -> None:
    api = SyntheticNative()
    host, binding = _binding_and_job(api)
    api.calls.clear()

    host.capture_ready(binding, "job")

    assert ("capture", 11, 1280, 720) in api.calls
    assert not any("frame" in name for name in vars(host))


@pytest.mark.parametrize(
    "fault", ["identity", "ancestry", "geometry", "locked", "iconic", "job"]
)
def test_capture_ready_fails_closed_before_capture_on_stale_or_unsafe_fact(
    fault: str,
) -> None:
    api = SyntheticNative()
    host, binding = _binding_and_job(api)
    if fault == "identity":
        api.creation[100] = 9002
    elif fault == "ancestry":
        api.windows[11]["parent"] = None
    elif fault == "geometry":
        api.windows[11]["size"] = (1279, 720)
    elif fault == "locked":
        api.unlocked = False
    elif fault == "iconic":
        api.iconic = True
    else:
        api.is_process_in_job = lambda process, job: False  # type: ignore[method-assign]
    api.calls.clear()

    with pytest.raises(Win32LifecycleHostError):
        host.capture_ready(binding, "job")

    assert not any(call[0] == "capture" for call in api.calls)


def test_capture_readiness_exception_traceback_retains_no_full_frame() -> None:
    api = SyntheticNative()
    host, binding = _binding_and_job(api)
    api.frame = bytes([0, 0, 0, 0]) * (1280 * 720)
    secret_frame = api.frame

    try:
        host.capture_ready(binding, "job")
    except CaptureError as exc:
        traceback = exc.__traceback__
        while traceback is not None:
            filename = traceback.tb_frame.f_code.co_filename.replace("\\", "/")
            if filename.endswith(
                (
                    "/src/clash_rush_rebuild/win32_lifecycle_host.py",
                    "/src/clash_rush_rebuild/capture.py",
                )
            ):
                assert all(
                    value is not secret_frame
                    for value in traceback.tb_frame.f_locals.values()
                )
            traceback = traceback.tb_next
    else:
        raise AssertionError("near-black frame was accepted")


class SyntheticState:
    def __init__(self) -> None:
        self.current: Ready | Active = Ready(0)
        self.commits: list[Ready | Active] = []

    def load(self) -> Ready | Active:
        return self.current

    def commit(self, state: Ready | Active) -> Ready | Active:
        self.commits.append(state)
        self.current = state
        return state


def test_integrated_host_drives_inert_supervisor_start_bind_capture_stop() -> None:
    api = SyntheticNative()
    api.launch_on_create = True
    complete = JobMembershipList(1, 1, (100,))
    api.job_lists = [complete, complete, complete, complete]
    state = SyntheticState()
    host = _host(api)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "b" * 32,
    )
    slot = Slot(0, "Pie64_slot_1", "Exact Slot", 1280, 720, 240)

    binding = supervisor.start(slot)
    record = supervisor.stop(binding, None)

    assert binding.identity == ProcessIdentity(100, 9001)
    assert record.slot == 0
    assert record.forced is True
    assert state.current == Ready(1)
    assert not any(
        name.startswith(("click", "send", "post"))
        for name, _ in inspect.getmembers(host)
    )


class FakeFunction:
    argtypes: list[object] | None = None
    restype: object | None = None


class FakeDll:
    def __getattr__(self, name: str) -> FakeFunction:
        function = FakeFunction()
        setattr(self, name, function)
        return function


def test_native_lifecycle_signatures_cover_every_process_window_and_capture_call() -> (
    None
):
    kernel32, user32, gdi32 = FakeDll(), FakeDll(), FakeDll()

    configure_lifecycle_host_signatures(kernel32, user32, gdi32)

    assert kernel32.CreateToolhelp32Snapshot.restype is ctypes.wintypes.HANDLE
    assert kernel32.OpenProcess.restype is ctypes.wintypes.HANDLE
    assert kernel32.TerminateProcess.argtypes == [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.UINT,
    ]
    assert user32.EnumWindows.restype is ctypes.wintypes.BOOL
    assert user32.GetWindowThreadProcessId.argtypes == [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    assert user32.GetDC.restype is ctypes.wintypes.HDC
    assert gdi32.CreateCompatibleBitmap.restype is ctypes.wintypes.HANDLE


@pytest.mark.skipif(os.name != "nt", reason="native Win32 smoke test")
def test_native_lifecycle_api_completes_a_harmless_process_snapshot() -> None:
    entries = enumerate_processes(NativeLifecycleApi())

    assert type(entries) is tuple
    assert len(entries) > 0


class FakeProcessKernel:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, bool(inherit), pid))
        return 55

    def GetProcessId(self, handle):
        self.calls.append(("pid", handle))
        return 321

    def GetProcessTimes(self, handle, creation, exit_time, kernel, user):
        self.calls.append(("times", handle))
        creation._obj.dwLowDateTime = 7
        creation._obj.dwHighDateTime = 3
        return True

    def QueryInformationJobObject(self, job, info_class, information, size, returned):
        self.calls.append(("members", job, info_class, size))
        value = information._obj
        value.NumberOfAssignedProcesses = 2
        value.NumberOfProcessIdsInList = 2
        value.ProcessIdList[0] = 321
        value.ProcessIdList[1] = 654
        return True

    def TerminateProcess(self, process, exit_code):
        self.calls.append(("terminate", process, exit_code))
        return True


def test_native_retained_identity_job_listing_and_termination_are_handle_based() -> (
    None
):
    kernel = FakeProcessKernel()
    api = object.__new__(NativeLifecycleApi)
    api._kernel32 = kernel

    handle = api.open_process_for_identity(321)
    assert handle == 55
    assert api.get_process_id(handle) == 321
    creation = api.get_process_creation_time(handle)
    listing = api.query_job_members("job", 4)
    assert api.terminate_process(handle, 1) is True

    assert kernel.calls[0] == (
        "open",
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        321,
    )
    assert (creation.dwHighDateTime, creation.dwLowDateTime) == (3, 7)
    assert listing == JobMembershipList(2, 2, (321, 654))
    assert kernel.calls[-1] == ("terminate", 55, 1)


class FakeWindowUser:
    def EnumWindows(self, callback, lparam):
        return callback(10, lparam) and callback(20, lparam)

    def EnumChildWindows(self, root, callback, lparam):
        assert root == 10
        return callback(11, lparam) and callback(12, lparam)

    def IsWindow(self, hwnd):
        return hwnd in {10, 11, 12, 20}

    def IsWindowVisible(self, hwnd):
        return hwnd != 20

    def GetWindowTextLengthW(self, hwnd):
        return len("Exact Slot" if hwnd == 10 else "")

    def GetWindowTextW(self, hwnd, buffer, capacity):
        value = "Exact Slot" if hwnd == 10 else ""
        buffer.value = value
        return len(value)

    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = 321
        return 7

    def GetAncestor(self, hwnd, flag):
        assert flag == 2
        return 10 if hwnd in {10, 11, 12} else 20

    def GetClientRect(self, hwnd, rect):
        rect._obj.left = 0
        rect._obj.top = 0
        rect._obj.right = 1280
        rect._obj.bottom = 720
        return True

    def OpenInputDesktop(self, flags, inherit, access):
        return "desktop"

    def SwitchDesktop(self, desktop):
        return desktop == "desktop"

    def CloseDesktop(self, desktop):
        return desktop == "desktop"

    def IsIconic(self, hwnd):
        return False


def test_native_window_and_unlocked_desktop_facts_are_exact() -> None:
    api = object.__new__(NativeLifecycleApi)
    api._user32 = FakeWindowUser()

    assert api.enum_top_level_windows() == (10, 20)
    assert api.enum_child_windows(10) == (11, 12)
    assert api.is_window(10) is True
    assert api.is_window_visible(20) is False
    assert api.window_text(10) == "Exact Slot"
    assert api.window_process_id(11) == 321
    assert api.get_ancestor_root(11) == 10
    assert api.client_size(11) == (1280, 720)
    assert api.interactive_desktop_unlocked() is True
    assert api.is_iconic(10) is False


class FakeCaptureUser:
    def __init__(self, *, print_ok: bool = True) -> None:
        self.print_ok = print_ok
        self.calls: list[tuple[object, ...]] = []

    def GetDC(self, hwnd):
        self.calls.append(("get-dc", hwnd))
        return 100

    def PrintWindow(self, hwnd, hdc, flags):
        self.calls.append(("print", hwnd, hdc, flags))
        return self.print_ok

    def ReleaseDC(self, hwnd, hdc):
        self.calls.append(("release-dc", hwnd, hdc))
        return 1


class FakeCaptureGdi:
    def __init__(self, *, raise_cleanup_select: bool = False) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.raise_cleanup_select = raise_cleanup_select
        self.select_calls = 0

    def CreateCompatibleDC(self, hdc):
        self.calls.append(("create-dc", hdc))
        return 200

    def CreateCompatibleBitmap(self, hdc, width, height):
        self.calls.append(("bitmap", hdc, width, height))
        return 300

    def SelectObject(self, hdc, value):
        self.calls.append(("select", hdc, value))
        self.select_calls += 1
        if self.raise_cleanup_select and self.select_calls == 2:
            raise OSError("synthetic cleanup failure")
        return 400

    def GetDIBits(self, hdc, bitmap, first, height, buffer, header, usage):
        self.calls.append(("bits", hdc, bitmap, first, height, usage))
        ctypes.memmove(buffer, b"\x01\x02\x03\x00\x04\x05\x06\x00", 8)
        return height

    def DeleteObject(self, value):
        self.calls.append(("delete-object", value))
        return True

    def DeleteDC(self, value):
        self.calls.append(("delete-dc", value))
        return True


def test_native_printwindow_capture_returns_bgra_and_releases_every_gdi_resource() -> (
    None
):
    user, gdi = FakeCaptureUser(), FakeCaptureGdi()
    api = object.__new__(NativeLifecycleApi)
    api._user32 = user
    api._gdi32 = gdi

    assert api.capture_printwindow_bgra(11, 2, 1) == (
        2,
        1,
        b"\x01\x02\x03\x00\x04\x05\x06\x00",
    )
    assert user.calls == [
        ("get-dc", 11),
        ("print", 11, 200, 2),
        ("release-dc", 11, 100),
    ]
    assert gdi.calls[-3:] == [
        ("select", 200, 400),
        ("delete-object", 300),
        ("delete-dc", 200),
    ]


def test_native_printwindow_failure_still_releases_every_gdi_resource() -> None:
    user, gdi = FakeCaptureUser(print_ok=False), FakeCaptureGdi()
    api = object.__new__(NativeLifecycleApi)
    api._user32 = user
    api._gdi32 = gdi

    with pytest.raises(Win32LifecycleHostError, match="PrintWindow"):
        api.capture_printwindow_bgra(11, 2, 1)

    assert user.calls[-1] == ("release-dc", 11, 100)
    assert gdi.calls[-3:] == [
        ("select", 200, 400),
        ("delete-object", 300),
        ("delete-dc", 200),
    ]


def test_native_cleanup_exception_releases_remaining_resources_without_frame_trace() -> (
    None
):
    user, gdi = FakeCaptureUser(), FakeCaptureGdi(raise_cleanup_select=True)
    api = object.__new__(NativeLifecycleApi)
    api._user32 = user
    api._gdi32 = gdi

    try:
        api.capture_printwindow_bgra(11, 1280, 720)
    except Win32LifecycleHostError as exc:
        traceback = exc.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_name == "capture_printwindow_bgra":
                assert not any(
                    type(value) is bytes and len(value) == 1280 * 720 * 4
                    for value in traceback.tb_frame.f_locals.values()
                )
            traceback = traceback.tb_next
    else:
        raise AssertionError("cleanup exception was accepted")

    assert ("delete-object", 300) in gdi.calls
    assert ("delete-dc", 200) in gdi.calls
    assert user.calls[-1] == ("release-dc", 11, 100)
