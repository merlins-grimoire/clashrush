from pathlib import Path

import pytest

from clash_rush_rebuild.lifecycle_state import (
    Active,
    BlockReason,
    LifecycleStateError,
    LifecycleStateStore,
    Ready,
    decode_state,
    encode_state,
)


class RecordingFilePort:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.payload = b""
        self.guard_present = False
        self.state_present = False

    def create_new_temp_write_through(self, path: Path) -> object:
        self.events.append("create")
        if path.name == ".lifecycle-state.transition":
            self.guard_present = True
        return path

    def write(self, file: object, data: memoryview) -> int:
        self.events.append("write")
        if Path(file).name != ".lifecycle-state.transition":
            self.payload += bytes(data)
        return len(data)

    def flush(self, file: object) -> None:
        self.events.append("flush")

    def close(self, file: object) -> None:
        self.events.append("close")

    def replace_write_through(self, source: Path, target: Path) -> None:
        self.events.append("replace")
        self.state_present = True

    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        self.events.append("open-read")
        if path.name == ".lifecycle-state.transition":
            if not self.guard_present:
                raise FileNotFoundError(path)
            return path
        if not self.state_present:
            raise FileNotFoundError(path)
        return path

    def read_bounded(self, file: object, limit: int) -> bytes:
        self.events.append("read-back")
        return self.payload

    def delete_file_no_reparse(self, path: Path) -> None:
        self.events.append("delete")
        if path.name != ".lifecycle-state.transition" or not self.guard_present:
            raise FileNotFoundError(path)
        self.guard_present = False


class FailingFlushPort(RecordingFilePort):
    def flush(self, file: object) -> None:
        self.events.append("flush")
        raise OSError("synthetic flush failure")


class MissingStatePort(RecordingFilePort):
    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        self.events.append("open-read")
        raise FileNotFoundError(path)


class FailedPostReplaceReadPort(RecordingFilePort):
    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        if path.name == "lifecycle-state.json":
            self.events.append("open-read")
            raise OSError("synthetic post-replace read failure")
        return super().open_read_exclusive_no_reparse(path)


def test_ready_state_has_one_canonical_encoding() -> None:
    state = Ready(next_slot=0)
    payload = b'{"next_slot":0,"schema":1,"state":"READY"}\n'

    assert encode_state(state) == payload
    assert decode_state(payload) == state


def test_active_state_preserves_unresolved_slot_nonce_and_reason() -> None:
    state = Active(
        slot=3,
        run_nonce="0123456789abcdef0123456789abcdef",
        blocked_reason=BlockReason.WINDOW_BINDING,
    )
    payload = (
        b'{"blocked_reason":"WINDOW_BINDING","run_nonce":'
        b'"0123456789abcdef0123456789abcdef","schema":1,"slot":3,"state":"ACTIVE"}\n'
    )

    assert encode_state(state) == payload
    assert decode_state(payload) == state


def test_state_decoder_rejects_noncanonical_or_incomplete_payloads() -> None:
    payloads = (
        b'{"state":"READY","next_slot":0}\n',
        b'{"schema":1, "state":"READY", "next_slot":0}\n',
        b'{"next_slot":false,"schema":1,"state":"READY"}\n',
        (
            b'{"blocked_reason":null,"run_nonce":"ABCDEF0123456789ABCDEF0123456789",'
            b'"schema":1,"slot":0,"state":"ACTIVE"}\n'
        ),
    )

    for payload in payloads:
        with pytest.raises(LifecycleStateError):
            decode_state(payload)


def test_durable_commit_flushes_replaces_and_reads_back_before_return(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = RecordingFilePort()
    store = LifecycleStateStore(project, port)

    committed = store.commit(Ready(2))

    assert committed == Ready(2)
    assert port.events == [
        "create",
        "write",
        "flush",
        "close",
        "create",
        "write",
        "flush",
        "close",
        "replace",
        "open-read",
        "read-back",
        "close",
        "delete",
    ]


def test_failed_flush_closes_writer_and_never_replaces_state(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = FailingFlushPort()
    store = LifecycleStateStore(project, port)

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit(Ready(1))

    assert port.events == ["create", "write", "flush", "close"]


def test_missing_lifecycle_state_never_defaults_to_ready(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = LifecycleStateStore(project, MissingStatePort())

    with pytest.raises(LifecycleStateError, match="missing"):
        store.load()


def test_explicit_initialization_creates_only_ready_zero_from_missing_state(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = RecordingFilePort()
    store = LifecycleStateStore(project, port)

    assert store.initialize_ready() == Ready(0)
    assert store.load() == Ready(0)
    assert store.load_with_bytes() == (Ready(0), encode_state(Ready(0)))
    with pytest.raises(LifecycleStateError, match="already exists"):
        store.initialize_ready()


def test_post_replace_read_failure_leaves_transition_guard_that_blocks_ready(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = FailedPostReplaceReadPort()
    store = LifecycleStateStore(project, port)

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit(Ready(1))

    assert port.guard_present is True
    with pytest.raises(LifecycleStateError, match="transition"):
        store.load()


def test_postcondition_failure_leaves_transition_guard_after_exact_readback(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = RecordingFilePort()
    store = LifecycleStateStore(project, port)

    def fail_after_readback() -> None:
        port.events.append("postcondition")
        raise OSError("synthetic outcome failure")

    with pytest.raises(LifecycleStateError, match="commit failed"):
        store.commit_with_postcondition(Ready(0), fail_after_readback)

    assert port.guard_present is True
    assert port.events[-2:] == ["close", "postcondition"]
