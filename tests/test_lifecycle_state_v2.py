from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationGeneration,
    SchemaV2Configuration,
)
from clash_rush_rebuild.lifecycle_state import BlockReason, LifecycleStateError
from clash_rush_rebuild.lifecycle_state_v2 import (
    ActiveV2,
    ReadyV2,
    SchemaV2LifecycleStateStore,
    decode_state_v2,
    encode_state_v2,
)


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
NONCE = "fedcba9876543210fedcba9876543210"


class MemoryFilePort:
    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.opened: list[Path] = []

    def create_new_temp_write_through(self, path: Path) -> object:
        if path in self.files:
            raise FileExistsError(path)
        self.files[path] = b""
        return path

    def write(self, file: object, data: memoryview) -> int:
        path = Path(file)
        self.files[path] += bytes(data)
        return len(data)

    def flush(self, file: object) -> None:
        pass

    def close(self, file: object) -> None:
        pass

    def replace_write_through(self, source: Path, target: Path) -> None:
        self.files[target] = self.files.pop(source)

    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        self.opened.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return path

    def read_bounded(self, file: object, limit: int) -> bytes:
        return self.files[Path(file)]

    def delete_file_no_reparse(self, path: Path) -> None:
        del self.files[path]


def _configuration() -> SchemaV2Configuration:
    return SchemaV2Configuration(
        GENERATION,
        (
            AccountRecord(AccountKey("account-a")),
            AccountRecord(AccountKey("account-b")),
        ),
    )


def test_schema_v2_ready_and_active_have_exact_canonical_encodings() -> None:
    ready = ReadyV2(GENERATION, AccountKey("account-a"))
    active = ActiveV2(
        GENERATION,
        AccountKey("account-b"),
        1,
        NONCE,
        BlockReason.WINDOW_BINDING,
    )

    ready_payload = (
        b'{"configuration_generation":"0123456789abcdef0123456789abcdef",'
        b'"schema":2,"state":"READY","tie_after_account_key":"account-a"}\n'
    )
    active_payload = (
        b'{"account_key":"account-b","blocked_reason":"WINDOW_BINDING",'
        b'"configuration_generation":"0123456789abcdef0123456789abcdef",'
        b'"run_nonce":"fedcba9876543210fedcba9876543210","schema":2,'
        b'"slot_index":1,"state":"ACTIVE"}\n'
    )

    assert encode_state_v2(ready) == ready_payload
    assert decode_state_v2(ready_payload) == ready
    assert encode_state_v2(active) == active_payload
    assert decode_state_v2(active_payload) == active


def test_generation_specific_store_uses_guarded_private_path_and_round_trips(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)

    seeded = store.seed_ready(AccountKey("account-a"))
    active = ActiveV2(GENERATION, AccountKey("account-b"), 1, NONCE, None)

    assert seeded == ReadyV2(GENERATION, AccountKey("account-a"))
    assert store.commit(active) == active
    assert store.load() == active
    assert store.path.relative_to(project) == Path(
        "var/lifecycle-state-v2.0123456789abcdef0123456789abcdef.json"
    )
    assert store.path.parent == project / "var"
    assert store.transition_path.parent == project / "var"
    assert store.transition_path not in port.files


def test_store_rejects_generation_account_slot_nonce_and_guard_mismatches(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)
    store.seed_ready(None)

    wrong_generation = ConfigurationGeneration("1" * 32)
    invalid_states = (
        ReadyV2(wrong_generation, AccountKey("account-a")),
        ReadyV2(GENERATION, AccountKey("not-configured")),
        ActiveV2(GENERATION, AccountKey("account-a"), 1, NONCE, None),
    )
    for state in invalid_states:
        with pytest.raises(LifecycleStateError):
            store.commit(state)

    malformed_nonce = encode_state_v2(
        ActiveV2(GENERATION, AccountKey("account-a"), 0, NONCE, None)
    ).replace(NONCE.encode(), b"A" * 32)
    port.files[store.path] = malformed_nonce
    with pytest.raises(LifecycleStateError, match="nonce|identity"):
        store.load()

    port.files[store.transition_path] = b'{"schema":1}\n'
    with pytest.raises(LifecycleStateError, match="guard.*mismatch"):
        store.load()


def test_transition_refuses_to_replace_missing_or_mismatched_current_state(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)
    next_state = ReadyV2(GENERATION, AccountKey("account-a"))

    with pytest.raises(LifecycleStateError, match="missing"):
        store.commit(next_state)

    port.files[store.path] = encode_state_v2(
        ReadyV2(ConfigurationGeneration("1" * 32), AccountKey("account-a"))
    )
    with pytest.raises(LifecycleStateError, match="generation mismatch"):
        store.commit(next_state)

    assert port.files[store.path] != encode_state_v2(next_state)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"configuration_generation":"0123456789abcdef0123456789abcdef",'
        b'"schema":1,"state":"READY","tie_after_account_key":"account-a"}\n',
        encode_state_v2(
            ReadyV2(ConfigurationGeneration("1" * 32), AccountKey("account-a"))
        ),
        encode_state_v2(ReadyV2(GENERATION, AccountKey("not-configured"))),
        encode_state_v2(
            ActiveV2(GENERATION, AccountKey("account-a"), 0, NONCE, None)
        ).replace(NONCE.encode(), b"A" * 32),
    ],
)
def test_load_rejects_schema_generation_account_and_nonce_mismatches(
    tmp_path,
    payload: bytes,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)
    port.files[store.path] = payload

    with pytest.raises(LifecycleStateError):
        store.load()


def test_post_replace_mismatch_preserves_generation_bound_transition_guard(
    tmp_path,
) -> None:
    class CorruptingPort(MemoryFilePort):
        def replace_write_through(self, source: Path, target: Path) -> None:
            super().replace_write_through(source, target)
            self.files[target] = self.files[target].replace(b'"schema":2', b'"schema":1')

    project = tmp_path / "project"
    project.mkdir()
    port = CorruptingPort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)

    with pytest.raises(LifecycleStateError, match="read-back mismatch"):
        store.seed_ready(AccountKey("account-a"))

    assert port.files[store.transition_path] == (
        b'{"configuration_generation":"0123456789abcdef0123456789abcdef",'
        b'"schema":2}\n'
    )
    with pytest.raises(LifecycleStateError, match="requires reconciliation"):
        store.load()


def test_delete_that_completes_then_raises_is_verified_as_success(tmp_path) -> None:
    class DeleteAfterRemovalRaisesPort(MemoryFilePort):
        def delete_file_no_reparse(self, path: Path) -> None:
            super().delete_file_no_reparse(path)
            raise OSError("synthetic post-delete error")

    project = tmp_path / "project"
    project.mkdir()
    port = DeleteAfterRemovalRaisesPort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)

    seeded = store.seed_ready(AccountKey("account-a"))

    assert seeded == ReadyV2(GENERATION, AccountKey("account-a"))
    assert store.transition_path not in port.files
    assert store.load() == seeded


def test_unprovable_post_delete_absence_restores_guard_and_blocks(tmp_path) -> None:
    class UnprovablePostDeletePort(MemoryFilePort):
        def __init__(self) -> None:
            super().__init__()
            self.fail_guard_probe = False

        def delete_file_no_reparse(self, path: Path) -> None:
            super().delete_file_no_reparse(path)
            self.fail_guard_probe = True

        def open_read_exclusive_no_reparse(self, path: Path) -> object:
            if self.fail_guard_probe and ".transition" in path.name:
                self.fail_guard_probe = False
                raise OSError("synthetic absence probe failure")
            return super().open_read_exclusive_no_reparse(path)

    project = tmp_path / "project"
    project.mkdir()
    port = UnprovablePostDeletePort()
    store = SchemaV2LifecycleStateStore(project, _configuration(), port)

    with pytest.raises(LifecycleStateError, match="guard finalization"):
        store.seed_ready(AccountKey("account-a"))

    assert store.transition_path in port.files
    with pytest.raises(LifecycleStateError, match="requires reconciliation"):
        store.load()
