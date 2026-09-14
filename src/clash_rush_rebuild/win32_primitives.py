"""Injected Win32 lifecycle primitives that never launch a process."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from .lifecycle import MemberSnapshot, ProcessIdentity

ERROR_NO_MORE_FILES = 18
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class Win32PrimitiveError(RuntimeError):
    """A native lifecycle fact could not be established completely."""


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def job_process_id_list_type(capacity: int) -> type[ctypes.Structure]:
    """Build the variable-sized JOBOBJECT_BASIC_PROCESS_ID_LIST layout."""
    if type(capacity) is not int or capacity < 1:
        raise Win32PrimitiveError("positive Job process-list capacity required")

    class JobProcessIdList(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * capacity),
        ]

    return JobProcessIdList


def configure_kernel32_signatures(kernel32: object) -> None:
    """Declare pointer-width-safe signatures used by these primitives."""
    handle = wintypes.HANDLE
    dword_pointer = ctypes.POINTER(wintypes.DWORD)
    filetime_pointer = ctypes.POINTER(FILETIME)

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = handle
    for function in (kernel32.Process32FirstW, kernel32.Process32NextW):
        function.argtypes = [handle, ctypes.POINTER(PROCESSENTRY32W)]
        function.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessId.argtypes = [handle]
    kernel32.GetProcessId.restype = wintypes.DWORD
    kernel32.GetProcessTimes.argtypes = [
        handle,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = handle
    kernel32.IsProcessInJob.argtypes = [
        handle,
        handle,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        dword_pointer,
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        handle,
    ]
    kernel32.CreateFileW.restype = handle
    for function in (kernel32.WriteFile, kernel32.ReadFile):
        function.argtypes = [
            handle,
            ctypes.c_void_p,
            wintypes.DWORD,
            dword_pointer,
            ctypes.c_void_p,
        ]
        function.restype = wintypes.BOOL
    kernel32.FlushFileBuffers.argtypes = [handle]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.MoveFileExW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    kernel32.MoveFileExW.restype = wintypes.BOOL


@dataclass(frozen=True, slots=True)
class ToolhelpProcess:
    pid: int
    parent_pid: int
    image_name: str


class ToolhelpApi(Protocol):
    def create_toolhelp32_snapshot(self) -> object: ...
    def process32_first(self, snapshot: object, entry: PROCESSENTRY32W) -> bool: ...
    def process32_next(self, snapshot: object, entry: PROCESSENTRY32W) -> bool: ...
    def get_last_error(self) -> int: ...
    def close_handle(self, handle: object) -> bool: ...


class IdentityApi(Protocol):
    def get_process_id(self, handle: object) -> int: ...
    def get_process_creation_time(self, handle: object) -> FILETIME: ...


@dataclass(frozen=True, slots=True)
class JobMembershipList:
    assigned_count: int
    listed_count: int
    pids: tuple[int, ...]


class JobMembershipApi(IdentityApi, Protocol):
    def query_job_members(self, job: object, capacity: int) -> JobMembershipList: ...
    def open_process_for_identity(self, pid: int) -> object: ...
    def is_process_in_job(self, handle: object, job: object) -> bool: ...
    def close_handle(self, handle: object) -> bool: ...


def _close_all(api: JobMembershipApi, handles: list[object]) -> None:
    failed = False
    for handle in handles:
        if api.close_handle(handle) is not True:
            failed = True
    if failed:
        raise Win32PrimitiveError("retained process handle close failed")


def stable_job_members(
    api: JobMembershipApi,
    job: object,
    *,
    initial_capacity: int = 8,
    max_queries: int = 8,
) -> MemberSnapshot:
    """Require two consecutive complete, handle-backed Job identity sets."""
    if job is None or initial_capacity < 1 or max_queries < 2:
        raise Win32PrimitiveError("invalid Job membership query bounds")
    capacity = initial_capacity
    previous: tuple[frozenset[ProcessIdentity], list[object]] | None = None
    for _ in range(max_queries):
        try:
            listing = api.query_job_members(job, capacity)
        except BaseException:
            if previous is not None:
                _, previous_handles = previous
                previous = None
                _close_all(api, previous_handles)
            raise
        if type(listing) is not JobMembershipList:
            raise Win32PrimitiveError("invalid Job membership list")
        assigned = listing.assigned_count
        listed = listing.listed_count
        pids = listing.pids
        if (
            type(assigned) is not int
            or type(listed) is not int
            or assigned < 0
            or listed < 0
            or type(pids) is not tuple
            or listed != len(pids)
            or any(type(pid) is not int or pid <= 0 for pid in pids)
            or len(set(pids)) != len(pids)
        ):
            if previous is not None:
                _close_all(api, previous[1])
                previous = None
            raise Win32PrimitiveError("invalid Job membership counts")
        capacity = max(capacity, assigned, 1)
        if assigned != listed:
            if previous is not None:
                _close_all(api, previous[1])
                previous = None
            continue

        handles: list[object] = []
        identities: list[ProcessIdentity] = []
        try:
            for pid in pids:
                handle = api.open_process_for_identity(pid)
                if handle is None:
                    raise Win32PrimitiveError("Job member handle unavailable")
                handles.append(handle)
                identity = identity_from_retained_handle(api, handle, expected_pid=pid)
                if api.is_process_in_job(handle, job) is not True:
                    raise Win32PrimitiveError("process is not in the expected Job")
                identities.append(identity)
        except BaseException:
            _close_all(api, handles)
            if previous is not None:
                _close_all(api, previous[1])
            raise

        identity_set = frozenset(identities)
        if len(identity_set) != listed:
            _close_all(api, handles)
            if previous is not None:
                _close_all(api, previous[1])
            raise Win32PrimitiveError("duplicate Job member identity")
        if previous is not None:
            previous_set, previous_handles = previous
            _close_all(api, previous_handles)
            if identity_set == previous_set:
                ordered = tuple(
                    sorted(
                        identity_set,
                        key=lambda item: (item.pid, item.creation_time_100ns),
                    )
                )
                return MemberSnapshot(ordered, partial(_close_all, api, handles))
        previous = (identity_set, handles)

    if previous is not None:
        _close_all(api, previous[1])
    raise Win32PrimitiveError("stable complete Job membership unavailable")


def filetime_to_100ns(value: FILETIME) -> int:
    """Combine a FILETIME as an unsigned 64-bit 100ns count."""
    if type(value) is not FILETIME:
        raise Win32PrimitiveError("exact FILETIME required")
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def identity_from_retained_handle(
    api: IdentityApi, handle: object, *, expected_pid: int
) -> ProcessIdentity:
    """Create identity only from the retained handle and its exact expected PID."""
    if handle is None or type(expected_pid) is not int or expected_pid <= 0:
        raise Win32PrimitiveError("valid retained process handle and PID required")
    actual_pid = api.get_process_id(handle)
    if type(actual_pid) is not int or actual_pid != expected_pid:
        raise Win32PrimitiveError("retained process PID mismatch")
    creation = api.get_process_creation_time(handle)
    return ProcessIdentity(actual_pid, filetime_to_100ns(creation))


class FileWriteThroughApi(Protocol):
    def create_file_write_through(self, path: str) -> object: ...
    def write_file(self, handle: object, data: bytes) -> int: ...
    def flush_file_buffers(self, handle: object) -> bool: ...
    def close_handle(self, handle: object) -> bool: ...
    def move_file_replace_write_through(self, source: str, target: str) -> bool: ...
    def read_file_without_write_share(self, path: str) -> bytes: ...


def atomic_write_through(
    api: FileWriteThroughApi, temporary_path: str, target_path: str, data: bytes
) -> None:
    """Write, flush, replace, and read back through an injected Win32 file API."""
    handle = api.create_file_write_through(temporary_path)
    if handle is None:
        raise Win32PrimitiveError("write-through temporary file create failed")
    try:
        if api.write_file(handle, data) != len(data):
            raise Win32PrimitiveError("complete write required")
        if api.flush_file_buffers(handle) is not True:
            raise Win32PrimitiveError("file buffer flush failed")
    finally:
        if api.close_handle(handle) is not True:
            raise Win32PrimitiveError("temporary file close failed")
    if api.move_file_replace_write_through(temporary_path, target_path) is not True:
        raise Win32PrimitiveError("write-through replace failed")
    if api.read_file_without_write_share(target_path) != data:
        raise Win32PrimitiveError("file read-back mismatch")


def enumerate_processes(api: ToolhelpApi) -> tuple[ToolhelpProcess, ...]:
    """Return a complete Toolhelp snapshot or fail closed."""
    snapshot = api.create_toolhelp32_snapshot()
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot is None or snapshot == 0 or snapshot == invalid_handle:
        raise Win32PrimitiveError("Toolhelp snapshot creation failed")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    result: list[ToolhelpProcess] = []
    try:
        ok = api.process32_first(snapshot, entry)
        while ok:
            result.append(
                ToolhelpProcess(
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    entry.szExeFile,
                )
            )
            ok = api.process32_next(snapshot, entry)
        if api.get_last_error() != ERROR_NO_MORE_FILES:
            raise Win32PrimitiveError("incomplete Toolhelp process enumeration")
        return tuple(result)
    finally:
        if not api.close_handle(snapshot):
            raise Win32PrimitiveError("Toolhelp snapshot close failed")
