from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from clash_rush_rebuild.lifecycle_state import (
    LifecycleStateError,
    LifecycleStateStore,
    Ready,
)
from clash_rush_rebuild.win32_state_io import (
    CREATE_NEW,
    FILE_ATTRIBUTE_NORMAL,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_FLAG_WRITE_THROUGH,
    GENERIC_READ,
    GENERIC_WRITE,
    MOVEFILE_REPLACE_EXISTING,
    MOVEFILE_WRITE_THROUGH,
    OPEN_EXISTING,
    NativeWin32StateApi,
    Win32StateFilePort,
    configure_state_io_signatures,
)


class FakeStateFileApi:
    def __init__(self, var: Path) -> None:
        self.var = var
        self.calls: list[tuple[object, ...]] = []
        self.files: dict[str, bytes] = {}
        self.handles: dict[object, str] = {}
        self.next_handle = 1
        self.reparse_paths: set[str] = set()
        self.final_path_override: str | None = None
        self.handle_attributes_override: int | None = None
        self.write_counts: list[int] = []
        self.flush_ok = True
        self.close_ok = True
        self.move_ok = True
        self.read_error: OSError | None = None

    def get_file_attributes(self, path: str) -> int | None:
        if path in self.reparse_paths:
            return FILE_ATTRIBUTE_NORMAL | FILE_ATTRIBUTE_REPARSE_POINT
        return (
            FILE_ATTRIBUTE_NORMAL if Path(path).exists() or path in self.files else None
        )

    def create_file(
        self, path: str, access: int, share: int, creation: int, flags: int
    ) -> object | None:
        self.calls.append(("create_file", path, access, share, creation, flags))
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = path
        if creation == CREATE_NEW:
            if path in self.files:
                return None
            self.files[path] = b""
        return handle

    def final_path(self, handle: object) -> str:
        return self.final_path_override or self.handles[handle]

    def handle_attributes(self, handle: object) -> int:
        assert handle in self.handles
        return self.handle_attributes_override or FILE_ATTRIBUTE_NORMAL

    def write_file(self, handle: object, data: bytes) -> int:
        self.calls.append(("write_file", handle, data))
        count = self.write_counts.pop(0) if self.write_counts else len(data)
        self.files[self.handles[handle]] += data[:count]
        return count

    def flush_file_buffers(self, handle: object) -> bool:
        self.calls.append(("flush", handle))
        return self.flush_ok

    def close_handle(self, handle: object) -> bool:
        self.calls.append(("close", handle))
        return self.close_ok

    def move_file_ex(self, source: str, target: str, flags: int) -> bool:
        self.calls.append(("move", source, target, flags))
        if self.move_ok:
            self.files[target] = self.files.pop(source)
        return self.move_ok

    def read_file(self, handle: object, size: int) -> bytes:
        self.calls.append(("read_file", handle, size))
        if self.read_error is not None:
            raise self.read_error
        payload = self.files[self.handles[handle]]
        result = payload[:size]
        self.files[self.handles[handle]] = payload[size:]
        return result

    def delete_file(self, path: str) -> bool:
        self.calls.append(("delete", path))
        self.files.pop(path, None)
        return True


def test_temp_creation_is_exclusive_same_directory_and_write_through(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    port = Win32StateFilePort(project, api)
    temporary = var / ".lifecycle-state.json.unique.tmp"

    handle = port.create_new_temp_write_through(temporary)

    assert handle == 1
    assert api.calls == [
        (
            "create_file",
            str(temporary),
            GENERIC_WRITE,
            0,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL
            | FILE_FLAG_OPEN_REPARSE_POINT
            | FILE_FLAG_WRITE_THROUGH,
        )
    ]


def test_complete_write_flush_close_replace_and_bounded_read_use_exact_flags(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    port = Win32StateFilePort(project, api)
    temporary = var / ".state.unique.tmp"
    target = var / "lifecycle-state.json"

    writer = port.create_new_temp_write_through(temporary)
    assert port.write(writer, memoryview(b"state")) == 5
    port.flush(writer)
    port.close(writer)
    port.replace_write_through(temporary, target)
    reader = port.open_read_exclusive_no_reparse(target)
    assert port.read_bounded(reader, 5) == b"state"
    port.close(reader)

    assert api.calls[1:] == [
        ("write_file", 1, b"state"),
        ("flush", 1),
        ("close", 1),
        (
            "move",
            str(temporary),
            str(target),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        ),
        (
            "create_file",
            str(target),
            GENERIC_READ,
            0,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
        ),
        ("read_file", 2, 6),
        ("read_file", 2, 1),
        ("close", 2),
    ]


def test_transition_guard_delete_rejects_reparse_and_uses_exact_private_path(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    port = Win32StateFilePort(project, api)
    guard = var / ".lifecycle-state.transition"
    api.files[str(guard)] = b"transition\n"

    port.delete_file_no_reparse(guard)

    assert api.calls[-1] == ("delete", str(guard))
    assert str(guard) not in api.files


def test_store_retries_short_writes_before_flush_and_read_back(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.write_counts = [len(b"transition\n"), 2]
    store = LifecycleStateStore(project, Win32StateFilePort(project, api))

    assert store.commit(Ready(3)) == Ready(3)

    write_calls = [call for call in api.calls if call[0] == "write_file"]
    assert len(write_calls) == 3
    assert write_calls[2][2] == write_calls[1][2][2:]
    call_names = [call[0] for call in api.calls]
    state_flush = [index for index, name in enumerate(call_names) if name == "flush"][1]
    state_writes = [
        index for index, name in enumerate(call_names) if name == "write_file"
    ]
    assert state_flush > state_writes[-1]


def test_failed_flush_closes_and_deletes_temporary_file(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.flush_ok = False
    store = LifecycleStateStore(project, Win32StateFilePort(project, api))

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit(Ready(1))

    assert [call[0] for call in api.calls][-3:] == ["flush", "close", "delete"]
    assert not any(path.endswith(".tmp") for path in api.files)


def test_failed_replace_deletes_closed_temporary_file(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.move_ok = False
    store = LifecycleStateStore(project, Win32StateFilePort(project, api))

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit(Ready(1))

    assert [call[0] for call in api.calls][-2:] == ["move", "delete"]
    assert not any(path.endswith(".tmp") for path in api.files)


def test_failed_close_attempts_temp_cleanup_and_blocks_replace(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.close_ok = False
    store = LifecycleStateStore(project, Win32StateFilePort(project, api))

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit(Ready(1))

    assert [call[0] for call in api.calls][-2:] == ["close", "delete"]
    assert not any(call[0] == "move" for call in api.calls)


def test_stale_temp_is_never_truncated_or_reused(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    temporary = var / ".state.stale.tmp"
    api = FakeStateFileApi(var)
    api.files[str(temporary)] = b"stale"
    port = Win32StateFilePort(project, api)

    with pytest.raises(OSError, match="temporary create"):
        port.create_new_temp_write_through(temporary)

    assert api.files[str(temporary)] == b"stale"
    assert api.calls[0][4] == CREATE_NEW


def test_bounded_read_rejects_oversized_file(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    target = var / "lifecycle-state.json"
    api = FakeStateFileApi(var)
    api.files[str(target)] = b"123456"
    port = Win32StateFilePort(project, api)
    reader = port.open_read_exclusive_no_reparse(target)

    with pytest.raises(OSError, match="exceeds read bound"):
        port.read_bounded(reader, 5)
    port.close(reader)

    assert ("read_file", reader, 6) in api.calls
    assert api.calls[-1] == ("close", reader)


def test_read_failure_still_closes_exclusive_handle(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    target = var / "lifecycle-state.json"
    api = FakeStateFileApi(var)
    api.files[str(target)] = b"payload"
    api.read_error = OSError("synthetic read failure")
    store = LifecycleStateStore(project, Win32StateFilePort(project, api))

    with pytest.raises(LifecycleStateError, match="could not be read"):
        store.load()

    assert api.calls[-1][0] == "close"


def test_paths_outside_exact_var_and_reparse_paths_are_rejected(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    port = Win32StateFilePort(project, api)

    with pytest.raises(OSError, match="escaped project var"):
        port.create_new_temp_write_through(project / "outside.tmp")

    api.reparse_paths.add(str(var))
    with pytest.raises(OSError, match="reparse point"):
        port.create_new_temp_write_through(var / "inside.tmp")
    assert api.calls == []


def test_opened_final_path_escape_is_closed_and_temp_is_cleaned(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.final_path_override = str(tmp_path / "escaped" / "state.tmp")
    port = Win32StateFilePort(project, api)
    temporary = var / ".state.unique.tmp"

    with pytest.raises(OSError, match="opened state path escaped"):
        port.create_new_temp_write_through(temporary)

    assert [call[0] for call in api.calls][-2:] == ["close", "delete"]
    assert str(temporary) not in api.files


def test_reparse_substitution_between_check_and_open_is_rejected(tmp_path) -> None:
    project = tmp_path / "project"
    var = project / "var"
    var.mkdir(parents=True)
    api = FakeStateFileApi(var)
    api.handle_attributes_override = (
        FILE_ATTRIBUTE_NORMAL | FILE_ATTRIBUTE_REPARSE_POINT
    )
    port = Win32StateFilePort(project, api)
    temporary = var / ".state.unique.tmp"

    with pytest.raises(OSError, match="opened state path is a reparse point"):
        port.create_new_temp_write_through(temporary)

    assert [call[0] for call in api.calls][-2:] == ["close", "delete"]


class FakeFunction:
    argtypes: list[object] | None = None
    restype: object | None = None


class FakeKernel32:
    def __init__(self) -> None:
        for name in (
            "GetFileAttributesW",
            "CreateFileW",
            "GetFinalPathNameByHandleW",
            "GetFileInformationByHandle",
            "WriteFile",
            "FlushFileBuffers",
            "CloseHandle",
            "MoveFileExW",
            "ReadFile",
            "DeleteFileW",
        ):
            setattr(self, name, FakeFunction())


def test_native_ctypes_signatures_are_pointer_width_safe() -> None:
    kernel32 = FakeKernel32()

    configure_state_io_signatures(kernel32)

    assert kernel32.CreateFileW.argtypes[-1] is ctypes.wintypes.HANDLE
    assert kernel32.CreateFileW.restype is ctypes.wintypes.HANDLE
    assert kernel32.WriteFile.argtypes == [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.c_void_p,
    ]
    assert kernel32.GetFinalPathNameByHandleW.argtypes == [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.LPWSTR,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
    ]


@pytest.mark.skipif(os.name != "nt", reason="native Win32 smoke test")
def test_native_temp_dir_commit_and_load_smoke(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "var").mkdir(parents=True)
    store = LifecycleStateStore(
        project, Win32StateFilePort(project, NativeWin32StateApi())
    )

    assert store.commit(Ready(4)) == Ready(4)
    assert store.load() == Ready(4)
