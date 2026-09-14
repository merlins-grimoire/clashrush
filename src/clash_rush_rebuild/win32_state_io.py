"""Crash-durable Win32 file I/O for lifecycle state."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_WRITE_THROUGH = 0x80000000
MOVEFILE_REPLACE_EXISTING = 0x00000001
MOVEFILE_WRITE_THROUGH = 0x00000008


class Win32StateIoError(OSError):
    """A Win32 state-file operation failed closed."""


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class StateFileApi(Protocol):
    def get_file_attributes(self, path: str) -> int | None: ...
    def create_file(
        self, path: str, access: int, share: int, creation: int, flags: int
    ) -> object | None: ...
    def final_path(self, handle: object) -> str: ...
    def handle_attributes(self, handle: object) -> int: ...
    def write_file(self, handle: object, data: bytes) -> int: ...
    def flush_file_buffers(self, handle: object) -> bool: ...
    def close_handle(self, handle: object) -> bool: ...
    def move_file_ex(self, source: str, target: str, flags: int) -> bool: ...
    def read_file(self, handle: object, size: int) -> bytes: ...
    def delete_file(self, path: str) -> bool: ...


def configure_state_io_signatures(kernel32: object) -> None:
    """Declare pointer-width-safe signatures for lifecycle-state file calls."""
    handle = wintypes.HANDLE
    dword_pointer = ctypes.POINTER(wintypes.DWORD)
    kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetFileAttributesW.restype = wintypes.DWORD
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
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        handle,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFileInformationByHandle.argtypes = [
        handle,
        ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
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
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.MoveFileExW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    kernel32.MoveFileExW.restype = wintypes.BOOL
    kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    kernel32.DeleteFileW.restype = wintypes.BOOL


class NativeWin32StateApi:
    """ctypes adapter for the concrete lifecycle-state operations."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "WinDLL"):
            raise Win32StateIoError("native Win32 APIs are unavailable")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        configure_state_io_signatures(self._kernel32)

    @staticmethod
    def _error(operation: str) -> Win32StateIoError:
        return Win32StateIoError(ctypes.get_last_error(), f"{operation} failed")

    def get_file_attributes(self, path: str) -> int | None:
        attributes = int(self._kernel32.GetFileAttributesW(path))
        if attributes == 0xFFFFFFFF:
            return None
        return attributes

    def create_file(
        self, path: str, access: int, share: int, creation: int, flags: int
    ) -> object | None:
        handle = self._kernel32.CreateFileW(
            path, access, share, None, creation, flags, None
        )
        if handle == ctypes.c_void_p(-1).value:
            return None
        return handle

    def final_path(self, handle: object) -> str:
        capacity = 512
        for _ in range(4):
            buffer = ctypes.create_unicode_buffer(capacity)
            length = int(
                self._kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0)
            )
            if length == 0:
                raise self._error("GetFinalPathNameByHandleW")
            if length < capacity:
                path = buffer.value
                if path.startswith("\\\\?\\UNC\\"):
                    return "\\\\" + path[8:]
                if path.startswith("\\\\?\\"):
                    return path[4:]
                return path
            capacity = length + 1
        raise Win32StateIoError("final state path exceeded native bounds")

    def handle_attributes(self, handle: object) -> int:
        information = BY_HANDLE_FILE_INFORMATION()
        if not self._kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            raise self._error("GetFileInformationByHandle")
        return int(information.dwFileAttributes)

    def write_file(self, handle: object, data: bytes) -> int:
        if len(data) > 0xFFFFFFFF:
            raise Win32StateIoError("WriteFile request exceeds DWORD")
        buffer = ctypes.create_string_buffer(data)
        written = wintypes.DWORD()
        if not self._kernel32.WriteFile(
            handle, buffer, len(data), ctypes.byref(written), None
        ):
            raise self._error("WriteFile")
        return int(written.value)

    def flush_file_buffers(self, handle: object) -> bool:
        return bool(self._kernel32.FlushFileBuffers(handle))

    def close_handle(self, handle: object) -> bool:
        return bool(self._kernel32.CloseHandle(handle))

    def move_file_ex(self, source: str, target: str, flags: int) -> bool:
        return bool(self._kernel32.MoveFileExW(source, target, flags))

    def read_file(self, handle: object, size: int) -> bytes:
        if type(size) is not int or not 0 <= size <= 0xFFFFFFFF:
            raise Win32StateIoError("ReadFile request exceeds DWORD")
        if size == 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self._kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            raise self._error("ReadFile")
        return buffer.raw[: read.value]

    def delete_file(self, path: str) -> bool:
        return bool(self._kernel32.DeleteFileW(path))


class Win32StateFilePort:
    """Concrete write-through port confined to one project's ``var`` directory."""

    def __init__(self, project_root: Path, api: StateFileApi) -> None:
        project = Path(project_root).resolve(strict=True)
        var = project / "var"
        if not var.is_dir() or var.resolve(strict=True) != var:
            raise Win32StateIoError("exact existing project var directory required")
        self._api = api
        self._project = project
        self._var = var
        self._failed_temps: dict[object, Path] = {}
        self._open_temps: dict[object, Path] = {}
        self._require_plain_existing(project)
        self._require_plain_existing(var)

    @staticmethod
    def _path_key(path: Path | str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def _require_plain_existing(self, path: Path) -> None:
        attributes = self._api.get_file_attributes(str(path))
        if attributes is None or attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise Win32StateIoError("state path is missing or a reparse point")

    def _require_direct_child(self, path: Path, *, allow_missing: bool) -> Path:
        candidate = Path(path)
        if self._path_key(candidate.parent) != self._path_key(self._var):
            raise Win32StateIoError("state path escaped project var")
        self._require_plain_existing(self._project)
        self._require_plain_existing(self._var)
        attributes = self._api.get_file_attributes(str(candidate))
        if attributes is not None and attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise Win32StateIoError("state path is a reparse point")
        if not allow_missing and attributes is None:
            raise FileNotFoundError(candidate)
        return candidate

    def _verify_open_path(self, handle: object, expected: Path) -> None:
        if self._api.handle_attributes(handle) & FILE_ATTRIBUTE_REPARSE_POINT:
            raise Win32StateIoError("opened state path is a reparse point")
        if self._path_key(self._api.final_path(handle)) != self._path_key(expected):
            raise Win32StateIoError("opened state path escaped project var")

    def create_new_temp_write_through(self, path: Path) -> object:
        candidate = self._require_direct_child(path, allow_missing=True)
        handle = self._api.create_file(
            str(candidate),
            GENERIC_WRITE,
            0,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL
            | FILE_FLAG_OPEN_REPARSE_POINT
            | FILE_FLAG_WRITE_THROUGH,
        )
        if handle is None or handle == 0:
            raise Win32StateIoError("write-through temporary create failed")
        try:
            self._verify_open_path(handle, candidate)
        except BaseException:
            self._api.close_handle(handle)
            self._api.delete_file(str(candidate))
            raise
        self._open_temps[handle] = candidate
        return handle

    def write(self, file: object, data: memoryview) -> int:
        if type(data) is not memoryview or data.ndim != 1:
            raise Win32StateIoError("byte memoryview required")
        payload = data.tobytes()
        try:
            written = self._api.write_file(file, payload)
        except BaseException:
            if file in self._open_temps:
                self._failed_temps[file] = self._open_temps[file]
            raise
        if type(written) is not int or written <= 0 or written > len(payload):
            if file in self._open_temps:
                self._failed_temps[file] = self._open_temps[file]
            raise Win32StateIoError("WriteFile returned an invalid byte count")
        return written

    def flush(self, file: object) -> None:
        try:
            flushed = self._api.flush_file_buffers(file)
        except BaseException:
            if file in self._open_temps:
                self._failed_temps[file] = self._open_temps[file]
            raise
        if flushed is not True:
            if file in self._open_temps:
                self._failed_temps[file] = self._open_temps[file]
            raise Win32StateIoError("FlushFileBuffers failed")

    def close(self, file: object) -> None:
        temporary = self._open_temps.pop(file, None)
        failed_temporary = self._failed_temps.pop(file, None)
        close_ok = self._api.close_handle(file) is True
        if failed_temporary is not None or (temporary is not None and not close_ok):
            self._api.delete_file(str(failed_temporary or temporary))
        if not close_ok:
            raise Win32StateIoError("CloseHandle failed")

    def replace_write_through(self, source: Path, target: Path) -> None:
        source_path = self._require_direct_child(source, allow_missing=False)
        target_path = self._require_direct_child(target, allow_missing=True)
        if not self._api.move_file_ex(
            str(source_path),
            str(target_path),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        ):
            self._api.delete_file(str(source_path))
            raise Win32StateIoError("MoveFileExW write-through replace failed")

    def delete_file_no_reparse(self, path: Path) -> None:
        candidate = self._require_direct_child(path, allow_missing=False)
        if self._api.delete_file(str(candidate)) is not True:
            raise Win32StateIoError("DeleteFileW failed")

    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        candidate = self._require_direct_child(path, allow_missing=False)
        handle = self._api.create_file(
            str(candidate),
            GENERIC_READ,
            0,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
        )
        if handle is None or handle == 0:
            raise Win32StateIoError("exclusive state-file open failed")
        try:
            self._verify_open_path(handle, candidate)
        except BaseException:
            if self._api.close_handle(handle) is not True:
                raise Win32StateIoError("escaped state handle close failed") from None
            raise
        return handle

    def read_bounded(self, file: object, limit: int) -> bytes:
        if type(limit) is not int or limit < 0:
            raise Win32StateIoError("nonnegative read bound required")
        result = bytearray()
        while True:
            remaining_with_probe = limit + 1 - len(result)
            chunk = self._api.read_file(file, remaining_with_probe)
            if type(chunk) is not bytes or len(chunk) > remaining_with_probe:
                raise Win32StateIoError("ReadFile returned invalid data")
            result.extend(chunk)
            if len(result) > limit:
                raise Win32StateIoError("state file exceeds read bound")
            if not chunk:
                return bytes(result)
