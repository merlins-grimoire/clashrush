from __future__ import annotations

import ctypes
from collections.abc import Iterable

import pytest

from clash_rush_rebuild.win32_primitives import (
    ERROR_NO_MORE_FILES,
    FILETIME,
    PROCESSENTRY32W,
    JobMembershipList,
    Win32PrimitiveError,
    atomic_write_through,
    configure_kernel32_signatures,
    enumerate_processes,
    filetime_to_100ns,
    identity_from_retained_handle,
    job_process_id_list_type,
    stable_job_members,
)


class FakeToolhelpApi:
    def __init__(
        self,
        entries: Iterable[tuple[int, int, str]],
        *,
        terminal_error: int = ERROR_NO_MORE_FILES,
    ) -> None:
        self._entries = tuple(entries)
        self._index = 0
        self._terminal_error = terminal_error
        self.last_error = 0
        self.closed: list[object] = []

    def create_toolhelp32_snapshot(self) -> object:
        return "snapshot"

    def process32_first(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        assert snapshot == "snapshot"
        self._index = 0
        return self._copy_current(entry)

    def process32_next(self, snapshot: object, entry: PROCESSENTRY32W) -> bool:
        assert snapshot == "snapshot"
        self._index += 1
        return self._copy_current(entry)

    def _copy_current(self, entry: PROCESSENTRY32W) -> bool:
        if self._index >= len(self._entries):
            self.last_error = self._terminal_error
            return False
        pid, parent_pid, image_name = self._entries[self._index]
        entry.th32ProcessID = pid
        entry.th32ParentProcessID = parent_pid
        entry.szExeFile = image_name
        return True

    def get_last_error(self) -> int:
        return self.last_error

    def close_handle(self, handle: object) -> bool:
        self.closed.append(handle)
        return True


def test_toolhelp_enumeration_returns_every_entry_and_closes_snapshot() -> None:
    api = FakeToolhelpApi(
        ((10, 1, "first.exe"), (20, 10, "second.exe"), (30, 20, "third.exe"))
    )

    entries = enumerate_processes(api)

    assert [(item.pid, item.parent_pid, item.image_name) for item in entries] == [
        (10, 1, "first.exe"),
        (20, 10, "second.exe"),
        (30, 20, "third.exe"),
    ]
    assert api.closed == ["snapshot"]


def test_toolhelp_first_failure_requires_no_more_files() -> None:
    api = FakeToolhelpApi((), terminal_error=5)

    with pytest.raises(Win32PrimitiveError, match="incomplete Toolhelp"):
        enumerate_processes(api)

    assert api.closed == ["snapshot"]


def test_toolhelp_next_failure_requires_no_more_files() -> None:
    api = FakeToolhelpApi(((10, 1, "first.exe"),), terminal_error=5)

    with pytest.raises(Win32PrimitiveError, match="incomplete Toolhelp"):
        enumerate_processes(api)

    assert api.closed == ["snapshot"]


def test_toolhelp_rejects_an_invalid_snapshot_handle() -> None:
    api = FakeToolhelpApi(())
    api.create_toolhelp32_snapshot = lambda: None  # type: ignore[method-assign]

    with pytest.raises(Win32PrimitiveError, match="snapshot creation"):
        enumerate_processes(api)

    assert api.closed == []


class FakeIdentityApi:
    def __init__(self, pid: int, creation: FILETIME) -> None:
        self.pid = pid
        self.creation = creation
        self.calls: list[tuple[str, object]] = []

    def get_process_id(self, handle: object) -> int:
        self.calls.append(("get_process_id", handle))
        return self.pid

    def get_process_creation_time(self, handle: object) -> FILETIME:
        self.calls.append(("get_process_creation_time", handle))
        return self.creation


def test_filetime_conversion_is_unsigned_64_bit_100ns_identity() -> None:
    assert filetime_to_100ns(FILETIME(0xFFFFFFFF, 0xFFFFFFFF)) == 0xFFFFFFFFFFFFFFFF


def test_identity_is_constructed_from_the_exact_retained_handle() -> None:
    api = FakeIdentityApi(42, FILETIME(0x89ABCDEF, 0x01234567))

    identity = identity_from_retained_handle(api, "retained", expected_pid=42)

    assert identity.pid == 42
    assert identity.creation_time_100ns == 0x0123456789ABCDEF
    assert api.calls == [
        ("get_process_id", "retained"),
        ("get_process_creation_time", "retained"),
    ]


def test_identity_rejects_pid_reuse_or_the_wrong_retained_handle() -> None:
    api = FakeIdentityApi(43, FILETIME(1, 0))

    with pytest.raises(Win32PrimitiveError, match="PID mismatch"):
        identity_from_retained_handle(api, "retained", expected_pid=42)

    assert api.calls == [("get_process_id", "retained")]


class FakeJobApi:
    def __init__(self, lists: Iterable[JobMembershipList]) -> None:
        self.lists = iter(lists)
        self.query_capacities: list[int] = []
        self.opened: list[tuple[int, str]] = []
        self.closed: list[str] = []
        self.membership_checks: list[tuple[str, object]] = []
        self._generation = 0

    def query_job_members(self, job: object, capacity: int) -> JobMembershipList:
        assert job == "job"
        self.query_capacities.append(capacity)
        self._generation += 1
        return next(self.lists)

    def open_process_for_identity(self, pid: int) -> str:
        handle = f"handle-{self._generation}-{pid}"
        self.opened.append((pid, handle))
        return handle

    def get_process_id(self, handle: object) -> int:
        return int(str(handle).rsplit("-", 1)[1])

    def get_process_creation_time(self, handle: object) -> FILETIME:
        pid = self.get_process_id(handle)
        return FILETIME(pid * 10, 0)

    def is_process_in_job(self, handle: object, job: object) -> bool:
        self.membership_checks.append((str(handle), job))
        return True

    def close_handle(self, handle: object) -> bool:
        self.closed.append(str(handle))
        return True


def test_job_membership_requires_two_complete_handle_backed_stable_sets() -> None:
    complete = JobMembershipList(2, 2, (10, 20))
    api = FakeJobApi((complete, complete))

    snapshot = stable_job_members(api, "job", initial_capacity=2, max_queries=4)

    assert snapshot.identities[0].pid == 10
    assert snapshot.identities[1].pid == 20
    assert api.query_capacities == [2, 2]
    assert api.closed == ["handle-1-10", "handle-1-20"]
    assert api.membership_checks == [
        ("handle-1-10", "job"),
        ("handle-1-20", "job"),
        ("handle-2-10", "job"),
        ("handle-2-20", "job"),
    ]

    snapshot.close()

    assert api.closed == [
        "handle-1-10",
        "handle-1-20",
        "handle-2-10",
        "handle-2-20",
    ]


def test_invalid_later_job_list_closes_the_previously_retained_set() -> None:
    api = FakeJobApi(
        (
            JobMembershipList(2, 2, (10, 20)),
            JobMembershipList(2, 1, (10, 20)),
        )
    )

    with pytest.raises(Win32PrimitiveError, match="membership counts"):
        stable_job_members(api, "job", initial_capacity=2, max_queries=2)

    assert api.closed == ["handle-1-10", "handle-1-20"]


class RaisingSecondJobQueryApi(FakeJobApi):
    def query_job_members(self, job: object, capacity: int) -> JobMembershipList:
        if self._generation == 1:
            raise OSError("synthetic second query failure")
        return super().query_job_members(job, capacity)


def test_later_job_query_exception_closes_the_previously_retained_set() -> None:
    api = RaisingSecondJobQueryApi((JobMembershipList(1, 1, (10,)),))

    with pytest.raises(OSError, match="second query"):
        stable_job_members(api, "job", initial_capacity=1, max_queries=2)

    assert api.closed == ["handle-1-10"]


def test_partial_job_list_resizes_without_opening_unlisted_members() -> None:
    complete = JobMembershipList(2, 2, (10, 20))
    api = FakeJobApi((JobMembershipList(2, 1, (10,)), complete, complete))

    snapshot = stable_job_members(api, "job", initial_capacity=1, max_queries=3)

    assert api.query_capacities == [1, 2, 2]
    assert [pid for pid, _ in api.opened] == [10, 20, 10, 20]
    snapshot.close()


class NonMemberJobApi(FakeJobApi):
    def is_process_in_job(self, handle: object, job: object) -> bool:
        self.membership_checks.append((str(handle), job))
        return False


def test_job_list_rejects_a_handle_not_confirmed_by_is_process_in_job() -> None:
    api = NonMemberJobApi((JobMembershipList(1, 1, (10,)),))

    with pytest.raises(Win32PrimitiveError, match="not in the expected Job"):
        stable_job_members(api, "job", initial_capacity=1, max_queries=2)

    assert api.closed == ["handle-1-10"]


class FakeFileApi:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.contents: dict[str, bytes] = {}
        self.written_count: int | None = None
        self.flush_ok = True
        self.close_ok = True
        self.move_ok = True
        self.read_back_override: bytes | None = None

    def create_file_write_through(self, path: str) -> object:
        self.calls.append(("create", path))
        self.contents[path] = b""
        return "write-handle"

    def write_file(self, handle: object, data: bytes) -> int:
        self.calls.append(("write", handle, data))
        count = len(data) if self.written_count is None else self.written_count
        self.contents["state.tmp"] = data[:count]
        return count

    def flush_file_buffers(self, handle: object) -> bool:
        self.calls.append(("flush", handle))
        return self.flush_ok

    def close_handle(self, handle: object) -> bool:
        self.calls.append(("close", handle))
        return self.close_ok

    def move_file_replace_write_through(self, source: str, target: str) -> bool:
        self.calls.append(("move", source, target))
        if self.move_ok:
            self.contents[target] = self.contents.pop(source)
        return self.move_ok

    def read_file_without_write_share(self, path: str) -> bytes:
        self.calls.append(("read_back", path))
        if self.read_back_override is not None:
            return self.read_back_override
        return self.contents[path]


def test_atomic_file_write_uses_flush_close_write_through_move_and_read_back() -> None:
    api = FakeFileApi()

    atomic_write_through(api, "state.tmp", "state.json", b'{"state":"READY"}')

    assert api.calls == [
        ("create", "state.tmp"),
        ("write", "write-handle", b'{"state":"READY"}'),
        ("flush", "write-handle"),
        ("close", "write-handle"),
        ("move", "state.tmp", "state.json"),
        ("read_back", "state.json"),
    ]


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("written_count", 1, "complete write"),
        ("flush_ok", False, "flush"),
        ("close_ok", False, "close"),
        ("move_ok", False, "replace"),
        ("read_back_override", b"different", "read-back"),
    ],
)
def test_atomic_file_write_fails_closed_at_every_durability_boundary(
    attribute: str, value: object, message: str
) -> None:
    api = FakeFileApi()
    setattr(api, attribute, value)

    with pytest.raises(Win32PrimitiveError, match=message):
        atomic_write_through(api, "state.tmp", "state.json", b"expected")


class FakeFunction:
    argtypes: list[object] | None = None
    restype: object | None = None


class FakeKernel32:
    def __init__(self) -> None:
        for name in (
            "CreateToolhelp32Snapshot",
            "Process32FirstW",
            "Process32NextW",
            "CloseHandle",
            "GetProcessId",
            "GetProcessTimes",
            "OpenProcess",
            "IsProcessInJob",
            "QueryInformationJobObject",
            "CreateFileW",
            "WriteFile",
            "FlushFileBuffers",
            "MoveFileExW",
            "ReadFile",
        ):
            setattr(self, name, FakeFunction())


def test_ctypes_layouts_and_pointer_width_kernel_signatures_are_exact() -> None:
    kernel32 = FakeKernel32()

    configure_kernel32_signatures(kernel32)

    assert ctypes.sizeof(FILETIME) == 8
    assert ctypes.sizeof(PROCESSENTRY32W) == 568
    assert ctypes.sizeof(job_process_id_list_type(3)) == 32
    assert kernel32.Process32NextW.argtypes == [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    assert kernel32.Process32NextW.restype is ctypes.wintypes.BOOL
    assert kernel32.GetProcessId.restype is ctypes.wintypes.DWORD
    assert kernel32.MoveFileExW.argtypes == [
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
    ]
    assert kernel32.MoveFileExW.restype is ctypes.wintypes.BOOL
