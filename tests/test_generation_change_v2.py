from __future__ import annotations

import json
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationGeneration,
    SchemaV2Configuration,
)
from clash_rush_rebuild.generation_change_v2 import (
    AccountGenerationState,
    ConfigurationGenerationChange,
    GenerationChangeError,
    plan_generation_change,
    require_safe_generation_activation,
)
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2, encode_state_v2
from clash_rush_rebuild.migration_v2 import (
    ActiveGenerationPointer,
    MigrationStateError,
    MigrationStore,
    encode_active_generation_pointer,
)


SOURCE_GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
TARGET_GENERATION = ConfigurationGeneration("fedcba9876543210fedcba9876543210")


def _account(value: str) -> AccountRecord:
    return AccountRecord(AccountKey(value))


def _configuration(
    generation: ConfigurationGeneration,
    *keys: str,
) -> SchemaV2Configuration:
    return SchemaV2Configuration(
        generation=generation,
        accounts=tuple(_account(key) for key in keys),
    )


def test_reorder_resize_plan_uses_fresh_generation_and_exact_affected_source_keys() -> None:
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b", "account-c")
    target = _configuration(TARGET_GENERATION, "account-c", "account-a", "account-d")

    change = plan_generation_change(source, target)

    assert change.source == source
    assert change.target == target
    assert change.affected_source_account_keys == (
        AccountKey("account-a"),
        AccountKey("account-b"),
        AccountKey("account-c"),
    )

    with pytest.raises(GenerationChangeError, match="fresh generation"):
        plan_generation_change(
            source,
            _configuration(SOURCE_GENERATION, "account-b", "account-a", "account-c"),
        )


class StateProbe:
    def __init__(self, states: dict[AccountKey, AccountGenerationState]) -> None:
        self.states = states
        self.queries: list[tuple[AccountKey, ConfigurationGeneration]] = []
        self.mutex_owned_result: object = True

    def mutex_owned(self) -> bool:
        return self.mutex_owned_result  # type: ignore[return-value]

    def account_generation_state(
        self,
        account_key: AccountKey,
        generation: ConfigurationGeneration,
    ) -> AccountGenerationState:
        self.queries.append((account_key, generation))
        return self.states[account_key]


@pytest.mark.parametrize(
    "unsafe_state",
    [
        AccountGenerationState(admitted_count=1),
        AccountGenerationState(uncertain_count=1),
        AccountGenerationState(blocked_count=1),
        AccountGenerationState(unarchived_count=1),
    ],
)
def test_affected_unsafe_account_state_prevents_activation(
    unsafe_state: AccountGenerationState,
) -> None:
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b", "account-c")
    target = _configuration(TARGET_GENERATION, "account-a", "account-c")
    change = plan_generation_change(source, target)
    probe = StateProbe(
        {
            AccountKey("account-b"): unsafe_state,
            AccountKey("account-c"): AccountGenerationState(),
        }
    )

    with pytest.raises(GenerationChangeError, match="unsafe account state"):
        require_safe_generation_activation(change, probe)

    assert probe.queries[0] == (AccountKey("account-b"), SOURCE_GENERATION)


def test_activation_rechecks_mutex_after_all_affected_account_proofs() -> None:
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b", "account-a")
    change = plan_generation_change(source, target)

    class LosingMutexProbe(StateProbe):
        def __init__(self) -> None:
            super().__init__(
                {
                    AccountKey("account-a"): AccountGenerationState(),
                    AccountKey("account-b"): AccountGenerationState(),
                }
            )
            self.mutex_checks = 0

        def mutex_owned(self) -> bool:
            self.mutex_checks += 1
            return self.mutex_checks == 1

    probe = LosingMutexProbe()

    with pytest.raises(GenerationChangeError, match="mutex"):
        require_safe_generation_activation(change, probe)

    assert probe.queries == [
        (AccountKey("account-a"), SOURCE_GENERATION),
        (AccountKey("account-b"), SOURCE_GENERATION),
    ]


def test_generation_change_plan_cannot_hide_an_affected_source_account() -> None:
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b")

    with pytest.raises(GenerationChangeError, match="affected"):
        ConfigurationGenerationChange(source, target, ())


class MemoryFilePort:
    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}

    def create_new_temp_write_through(self, path: Path) -> object:
        if path in self.files:
            raise FileExistsError(path)
        self.files[path] = b""
        return path

    def write(self, file: object, data: memoryview) -> int:
        self.files[Path(file)] += bytes(data)
        return len(data)

    def flush(self, file: object) -> None:
        pass

    def close(self, file: object) -> None:
        pass

    def replace_write_through(self, source: Path, target: Path) -> None:
        self.files[target] = self.files.pop(source)

    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        if path not in self.files:
            raise FileNotFoundError(path)
        return path

    def read_bounded(self, file: object, limit: int) -> bytes:
        return self.files[Path(file)]

    def delete_file_no_reparse(self, path: Path) -> None:
        del self.files[path]


def _configuration_payload(configuration: SchemaV2Configuration) -> bytes:
    return json.dumps(
        {
            "accounts": [account.key.value for account in configuration.accounts],
            "schema": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _pointer(configuration: SchemaV2Configuration) -> ActiveGenerationPointer:
    generation = configuration.generation.value
    return ActiveGenerationPointer(
        configuration_generation=configuration.generation,
        configuration_path=f"configuration-v2.{generation}.json",
        configuration_hash=configuration.content_hash,
        lifecycle_path=f"lifecycle-state-v2.{generation}.json",
        lifecycle_schema=2,
    )


def test_generation_change_activation_replaces_pointer_only_after_safe_revalidation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b", "account-a", "account-c")
    change = plan_generation_change(source, target)
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)
    root = store.pointer_path.parent
    port.files[store.pointer_path] = encode_active_generation_pointer(source_pointer)
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("account-a"))
    )
    port.files[root / target_pointer.configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, AccountKey("account-a"))
    )
    probe = StateProbe(
        {
            AccountKey("account-a"): AccountGenerationState(),
            AccountKey("account-b"): AccountGenerationState(),
        }
    )

    assert store.activate_generation_change(change, target_pointer, probe) == target_pointer
    assert port.files[store.pointer_path] == encode_active_generation_pointer(target_pointer)
    assert probe.queries == [
        (AccountKey("account-a"), SOURCE_GENERATION),
        (AccountKey("account-b"), SOURCE_GENERATION),
        (AccountKey("account-a"), SOURCE_GENERATION),
        (AccountKey("account-b"), SOURCE_GENERATION),
    ]


def test_generation_change_rejects_unconfigured_source_cursor_before_pointer_write(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b")
    change = plan_generation_change(source, target)
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)
    root = store.pointer_path.parent
    port.files[store.pointer_path] = encode_active_generation_pointer(source_pointer)
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("not-configured"))
    )
    port.files[root / target_pointer.configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, None)
    )
    before = dict(port.files)

    with pytest.raises(MigrationStateError, match="source.*cursor"):
        store.activate_generation_change(
            change,
            target_pointer,
            StateProbe(
                {
                    AccountKey("account-a"): AccountGenerationState(),
                    AccountKey("account-b"): AccountGenerationState(),
                }
            ),
        )

    assert port.files == before


@pytest.mark.parametrize(
    "unsafe_state",
    [
        AccountGenerationState(admitted_count=1),
        AccountGenerationState(uncertain_count=1),
        AccountGenerationState(blocked_count=1),
        AccountGenerationState(unarchived_count=1),
    ],
)
def test_unsafe_removed_account_never_replaces_active_generation_pointer(
    tmp_path: Path,
    unsafe_state: AccountGenerationState,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-a")
    change = plan_generation_change(source, target)
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)
    root = store.pointer_path.parent
    source_pointer_payload = encode_active_generation_pointer(source_pointer)
    port.files[store.pointer_path] = source_pointer_payload
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("account-a"))
    )
    port.files[root / target_pointer.configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, AccountKey("account-a"))
    )

    with pytest.raises(GenerationChangeError, match="unsafe account state"):
        store.activate_generation_change(
            change,
            target_pointer,
            StateProbe({AccountKey("account-b"): unsafe_state}),
        )

    assert port.files[store.pointer_path] == source_pointer_payload
    assert root / ".active-generation.transition" not in port.files


@pytest.mark.parametrize("value", [False, -1, 1.5, "0", None])
def test_account_generation_state_rejects_ambiguous_counts(value: object) -> None:
    with pytest.raises(GenerationChangeError, match="nonnegative integers"):
        AccountGenerationState(admitted_count=value)  # type: ignore[arg-type]


def test_final_mutex_probe_cannot_mutate_staged_target_before_pointer_replace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b", "account-a")
    change = plan_generation_change(source, target)
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)
    root = store.pointer_path.parent
    source_pointer_payload = encode_active_generation_pointer(source_pointer)
    target_configuration_path = root / target_pointer.configuration_path
    port.files[store.pointer_path] = source_pointer_payload
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("account-a"))
    )
    port.files[target_configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, AccountKey("account-a"))
    )

    class MutatingFinalMutexProbe(StateProbe):
        def __init__(self) -> None:
            super().__init__(
                {
                    AccountKey("account-a"): AccountGenerationState(),
                    AccountKey("account-b"): AccountGenerationState(),
                }
            )
            self.mutex_checks = 0

        def mutex_owned(self) -> bool:
            self.mutex_checks += 1
            if self.mutex_checks == 4:
                port.files[target_configuration_path] = b'{"accounts":[],"schema":2}'
            return True

    with pytest.raises(MigrationStateError, match="configuration"):
        store.activate_generation_change(
            change,
            target_pointer,
            MutatingFinalMutexProbe(),
        )

    assert port.files[store.pointer_path] == source_pointer_payload


def test_target_lifecycle_read_cannot_mutate_validated_configuration_before_replace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b", "account-a")
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)

    class MutatingLifecycleReadPort(MemoryFilePort):
        def read_bounded(self, file: object, limit: int) -> bytes:
            path = Path(file)
            payload = super().read_bounded(file, limit)
            pointer_transition = path.parent / ".active-generation.transition"
            if (
                path.name == target_pointer.lifecycle_path
                and pointer_transition in self.files
            ):
                self.files[path.parent / target_pointer.configuration_path] = (
                    b'{"accounts":[],"schema":2}'
                )
            return payload

    port = MutatingLifecycleReadPort()
    store = MigrationStore(project, port)
    root = store.pointer_path.parent
    source_pointer_payload = encode_active_generation_pointer(source_pointer)
    port.files[store.pointer_path] = source_pointer_payload
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("account-a"))
    )
    port.files[root / target_pointer.configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, AccountKey("account-a"))
    )

    with pytest.raises(MigrationStateError, match="configuration"):
        store.activate_generation_change(
            plan_generation_change(source, target),
            target_pointer,
            StateProbe(
                {
                    AccountKey("account-a"): AccountGenerationState(),
                    AccountKey("account-b"): AccountGenerationState(),
                }
            ),
        )

    assert port.files[store.pointer_path] == source_pointer_payload


@pytest.mark.parametrize(
    "mutation",
    [
        "source-guard",
        "active-pointer",
        "source-configuration",
        "source-lifecycle",
        "pointer-transition",
        "migration-intent",
        "migration-transition",
    ],
)
def test_final_probe_mutation_cannot_be_overwritten_by_generation_activation(
    tmp_path: Path,
    mutation: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source = _configuration(SOURCE_GENERATION, "account-a", "account-b")
    target = _configuration(TARGET_GENERATION, "account-b", "account-a")
    change = plan_generation_change(source, target)
    source_pointer = _pointer(source)
    target_pointer = _pointer(target)
    root = store.pointer_path.parent
    source_pointer_payload = encode_active_generation_pointer(source_pointer)
    port.files[store.pointer_path] = source_pointer_payload
    port.files[root / source_pointer.configuration_path] = _configuration_payload(source)
    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(SOURCE_GENERATION, AccountKey("account-a"))
    )
    port.files[root / target_pointer.configuration_path] = _configuration_payload(target)
    port.files[root / target_pointer.lifecycle_path] = encode_state_v2(
        ReadyV2(TARGET_GENERATION, AccountKey("account-a"))
    )

    class MutatingProbe(StateProbe):
        def __init__(self) -> None:
            super().__init__(
                {
                    AccountKey("account-a"): AccountGenerationState(),
                    AccountKey("account-b"): AccountGenerationState(),
                }
            )
            self.mutex_checks = 0

        def mutex_owned(self) -> bool:
            self.mutex_checks += 1
            if self.mutex_checks == 4:
                if mutation == "source-guard":
                    port.files[
                        root
                        / f".lifecycle-state-v2.{SOURCE_GENERATION.value}.transition"
                    ] = b"synthetic\n"
                elif mutation == "active-pointer":
                    port.files[store.pointer_path] = b'{"schema":2}\n'
                elif mutation == "source-configuration":
                    port.files[root / source_pointer.configuration_path] = (
                        b'{"accounts":["account-a","account-c"],"schema":2}'
                    )
                elif mutation == "source-lifecycle":
                    port.files[root / source_pointer.lifecycle_path] = encode_state_v2(
                        ReadyV2(SOURCE_GENERATION, AccountKey("account-b"))
                    )
                elif mutation == "pointer-transition":
                    port.files[root / ".active-generation.transition"] = b"corrupt\n"
                elif mutation == "migration-intent":
                    port.files[store.migration_path] = b'{"schema":2}\n'
                else:
                    port.files[root / ".migration-v2.transition"] = b"transition\n"
            return True

    with pytest.raises(MigrationStateError):
        store.activate_generation_change(change, target_pointer, MutatingProbe())

    assert port.files[store.pointer_path] != encode_active_generation_pointer(
        target_pointer
    )
