import hashlib
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    ConfigurationContentHash,
    ConfigurationGeneration,
)
from clash_rush_rebuild.migration_v2 import (
    ActiveGenerationPointer,
    GenerationSelection,
    MigrationIntent,
    MigrationStateError,
    MigrationStore,
    StoppedMigrationAdmission,
    decode_active_generation_pointer,
    decode_migration_intent,
    encode_active_generation_pointer,
    encode_migration_intent,
)
from clash_rush_rebuild.lifecycle_state import Active, Ready, encode_state
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2, encode_state_v2


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
CONFIGURATION_PAYLOAD = (
    b'{"accounts":["account-0","account-1","account-2","account-3","account-4"],'
    b'"schema":2}'
)
CONFIGURATION_HASH = ConfigurationContentHash(
    "710645649c8c2259f19c417b352e0e3cb696c6369f52d84005788ab32422729a"
)
SOURCE_HASH = "46db68885259e9a5625f1a5a8f874316533a00163c173efc9fc08e18d2b012a1"
SEED_HASH = "ba020d786b05a8f3eb59a99a410ab55b7446183c98eb5483720e2161b4a0ec4e"


class MemoryFilePort:
    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}

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
        if path not in self.files:
            raise FileNotFoundError(path)
        return path

    def read_bounded(self, file: object, limit: int) -> bytes:
        return self.files[Path(file)]

    def delete_file_no_reparse(self, path: Path) -> None:
        del self.files[path]


class AdmissionProbes:
    def __init__(self) -> None:
        self.mutex_owned_result: object = True
        self.player_count_result: object = 0
        self.window_count_result: object = 0
        self.open_visit_count_result: object = 0
        self.open_action_count_result: object = 0
        self.open_blocker_delivery_count_result: object = 0
        self.unresolved_queue_count_result: object = 0

    def mutex_owned(self) -> bool:
        return self.mutex_owned_result  # type: ignore[return-value]

    def complete_player_count(self) -> int:
        return self.player_count_result  # type: ignore[return-value]

    def complete_window_count(self) -> int:
        return self.window_count_result  # type: ignore[return-value]

    def open_visit_count(self) -> int:
        return self.open_visit_count_result  # type: ignore[return-value]

    def open_action_count(self) -> int:
        return self.open_action_count_result  # type: ignore[return-value]

    def open_blocker_delivery_count(self) -> int:
        return self.open_blocker_delivery_count_result  # type: ignore[return-value]

    def unresolved_queue_count(self) -> int:
        return self.unresolved_queue_count_result  # type: ignore[return-value]


def _stage_v1_ready(store: MigrationStore, port: MemoryFilePort, slot: int = 1) -> bytes:
    payload = encode_state(Ready(slot))
    port.files[store.pointer_path.parent / "lifecycle-state.json"] = payload
    return payload


def _intent() -> MigrationIntent:
    return MigrationIntent(
        source_v1_state_hash=SOURCE_HASH,
        target_generation=GENERATION,
        configuration_path="configuration-v2.0123456789abcdef0123456789abcdef.json",
        configuration_hash=CONFIGURATION_HASH,
        initial_lifecycle_path=(
            "lifecycle-state-v2.0123456789abcdef0123456789abcdef.json"
        ),
        initial_lifecycle_seed_hash=SEED_HASH,
        v1_slot_account_keys=tuple(AccountKey(f"account-{index}") for index in range(5)),
    )


def _pointer() -> ActiveGenerationPointer:
    return ActiveGenerationPointer(
        configuration_generation=GENERATION,
        configuration_path="configuration-v2.0123456789abcdef0123456789abcdef.json",
        configuration_hash=CONFIGURATION_HASH,
        lifecycle_path="lifecycle-state-v2.0123456789abcdef0123456789abcdef.json",
        lifecycle_schema=2,
    )


def _stage_selected_artifacts(store: MigrationStore, port: MemoryFilePort) -> None:
    _stage_v1_ready(store, port)
    port.files[store.pointer_path.parent / _intent().configuration_path] = (
        CONFIGURATION_PAYLOAD
    )
    port.files[store.pointer_path.parent / _intent().initial_lifecycle_path] = (
        encode_state_v2(ReadyV2(GENERATION, AccountKey("account-0")))
    )


def test_prepared_intent_and_active_pointer_have_exact_canonical_encodings() -> None:
    intent_payload = (
        b'{"configuration_hash":"710645649c8c2259f19c417b352e0e3cb696c6369f52d84005788ab32422729a",'
        b'"configuration_path":"configuration-v2.0123456789abcdef0123456789abcdef.json",'
        b'"initial_lifecycle_path":"lifecycle-state-v2.0123456789abcdef0123456789abcdef.json",'
        b'"initial_lifecycle_seed_hash":"ba020d786b05a8f3eb59a99a410ab55b7446183c98eb5483720e2161b4a0ec4e",'
        b'"schema":2,"source_v1_state_hash":"46db68885259e9a5625f1a5a8f874316533a00163c173efc9fc08e18d2b012a1",'
        b'"state":"PREPARED","target_generation":"0123456789abcdef0123456789abcdef",'
        b'"v1_slot_account_keys":["account-0","account-1","account-2","account-3","account-4"]}\n'
    )
    pointer_payload = (
        b'{"configuration_generation":"0123456789abcdef0123456789abcdef",'
        b'"configuration_hash":"710645649c8c2259f19c417b352e0e3cb696c6369f52d84005788ab32422729a",'
        b'"configuration_path":"configuration-v2.0123456789abcdef0123456789abcdef.json",'
        b'"lifecycle_path":"lifecycle-state-v2.0123456789abcdef0123456789abcdef.json",'
        b'"lifecycle_schema":2,"schema":2}\n'
    )

    assert encode_migration_intent(_intent()) == intent_payload
    assert decode_migration_intent(intent_payload) == _intent()
    assert encode_active_generation_pointer(_pointer()) == pointer_payload
    assert decode_active_generation_pointer(pointer_payload) == _pointer()


def test_prepare_is_write_through_read_back_and_does_not_select_v2(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source_payload = _stage_v1_ready(store, port)

    assert store.prepare(_intent(), AdmissionProbes()) == _intent()

    assert store.load_prepared() == _intent()
    assert store.select_generation() == GenerationSelection.v1()
    assert store.migration_path == project / "var" / "migration-v2.json"
    assert store.pointer_path == project / "var" / "active-generation.json"
    assert port.files[project / "var" / "lifecycle-state.json"] == source_payload


def test_pointer_commit_is_the_only_v2_selection_point_and_binds_prepared_intent(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    store.prepare(_intent(), AdmissionProbes())

    assert store.commit_pointer(_pointer(), AdmissionProbes()) == _pointer()

    assert store.select_generation() == GenerationSelection.v2(
        _pointer(), migration_recovery_required=True
    )
    assert b"lifecycle_hash" not in port.files[store.pointer_path]

    port.files[store.migration_path] = encode_migration_intent(
        MigrationIntent(
            source_v1_state_hash=SOURCE_HASH,
            target_generation=GENERATION,
            configuration_path=_intent().configuration_path,
            configuration_hash=ConfigurationContentHash("d" * 64),
            initial_lifecycle_path=_intent().initial_lifecycle_path,
            initial_lifecycle_seed_hash=SEED_HASH,
            v1_slot_account_keys=_intent().v1_slot_account_keys,
        )
    )
    with pytest.raises(MigrationStateError, match="does not match"):
        store.select_generation()


@pytest.mark.parametrize(
    "pre_pointer_files",
    [
        {},
        {"migration-v2.json": encode_migration_intent(_intent())},
        {
            "migration-v2.json": encode_migration_intent(_intent()),
            ".active-generation.transition": b"transition\n",
        },
        {
            "migration-v2.json": encode_migration_intent(_intent()),
            ".active-generation.transition": b"transition\n",
            ".active-generation.json.synthetic.tmp": encode_active_generation_pointer(
                _pointer()
            ),
        },
    ],
)
def test_every_pre_pointer_crash_state_selects_v1(
    tmp_path, pre_pointer_files: dict[str, bytes]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    for name, payload in pre_pointer_files.items():
        port.files[project / "var" / name] = payload

    assert store.select_generation() == GenerationSelection.v1()


def test_every_post_pointer_crash_state_selects_or_blocks_v2(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    port.files[store.migration_path] = encode_migration_intent(_intent())
    port.files[store.pointer_path] = encode_active_generation_pointer(_pointer())
    pointer_transition = project / "var" / ".active-generation.transition"

    port.files[pointer_transition] = b"transition\n"
    assert store.select_generation() == GenerationSelection.v2(
        _pointer(), migration_recovery_required=True
    )

    del port.files[pointer_transition]
    assert store.select_generation() == GenerationSelection.v2(
        _pointer(), migration_recovery_required=True
    )

    port.files[store.pointer_path] = b'{"schema":2}\n'
    with pytest.raises(MigrationStateError, match="pointer"):
        store.select_generation()


def test_post_pointer_migration_transition_cannot_be_ignored(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    port.files[store.pointer_path] = encode_active_generation_pointer(_pointer())
    port.files[project / "var" / ".migration-v2.transition"] = b"transition\n"

    with pytest.raises(MigrationStateError, match="migration.*transition"):
        store.select_generation()


def test_pointer_cannot_select_v2_when_referenced_artifacts_are_missing(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    port.files[store.pointer_path] = encode_active_generation_pointer(_pointer())

    with pytest.raises(MigrationStateError, match="configuration.*missing"):
        store.select_generation()


@pytest.mark.parametrize("replace_completed", [False, True])
def test_pointer_replace_crash_never_falls_back_from_a_committed_v2_pointer(
    tmp_path, replace_completed: bool
) -> None:
    class CrashingPointerPort(MemoryFilePort):
        def replace_write_through(self, source: Path, target: Path) -> None:
            if target.name != "active-generation.json":
                return super().replace_write_through(source, target)
            if replace_completed:
                super().replace_write_through(source, target)
            raise OSError("synthetic pointer replace crash")

    project = tmp_path / "project"
    project.mkdir()
    port = CrashingPointerPort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    store.prepare(_intent(), AdmissionProbes())

    with pytest.raises(MigrationStateError, match="pointer.*commit"):
        store.commit_pointer(_pointer(), AdmissionProbes())

    expected = (
        GenerationSelection.v2(_pointer(), migration_recovery_required=True)
        if replace_completed
        else GenerationSelection.v1()
    )
    assert store.select_generation() == expected


def test_stopped_admission_requires_canonical_v1_ready_and_all_absence_proofs(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    probes = AdmissionProbes()
    payload = _stage_v1_ready(store, port)

    admission = store.require_stopped_v1_admission(probes)

    assert admission == StoppedMigrationAdmission(
        source_state=Ready(1),
        source_state_payload=payload,
        source_state_hash=hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("active", "READY"),
        ("guard", "transition"),
        ("mutex", "mutex"),
        ("player", "player"),
        ("window", "window"),
        ("visit", "visit"),
        ("action", "action"),
        ("delivery", "blocker delivery"),
        ("queue", "queue"),
    ],
)
def test_each_unresolved_stopped_admission_state_blocks_without_writes(
    tmp_path, fault: str, message: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    probes = AdmissionProbes()
    _stage_v1_ready(store, port)
    if fault == "active":
        port.files[project / "var" / "lifecycle-state.json"] = encode_state(
            Active(1, "a" * 32, None)
        )
    elif fault == "guard":
        port.files[project / "var" / ".lifecycle-state.transition"] = b"transition\n"
    elif fault == "mutex":
        probes.mutex_owned_result = False
    elif fault == "player":
        probes.player_count_result = 1
    elif fault == "window":
        probes.window_count_result = 1
    elif fault == "visit":
        probes.open_visit_count_result = 1
    elif fault == "action":
        probes.open_action_count_result = 1
    elif fault == "delivery":
        probes.open_blocker_delivery_count_result = 1
    else:
        probes.unresolved_queue_count_result = 1
    before = dict(port.files)

    with pytest.raises(MigrationStateError, match=message):
        store.require_stopped_v1_admission(probes)

    assert port.files == before


def test_absence_proofs_reject_ambiguous_values_and_probe_failures_without_writes(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    probes = AdmissionProbes()
    _stage_v1_ready(store, port)
    probes.open_action_count_result = False
    before = dict(port.files)

    with pytest.raises(MigrationStateError, match="action.*unprovable"):
        store.require_stopped_v1_admission(probes)
    assert port.files == before

    probes.open_action_count_result = 0
    probes.complete_player_count = lambda: (_ for _ in ()).throw(OSError("synthetic"))
    with pytest.raises(MigrationStateError, match="player.*unprovable"):
        store.require_stopped_v1_admission(probes)
    assert port.files == before

    probes = object()
    with pytest.raises(MigrationStateError, match="mutex.*unprovable"):
        store.require_stopped_v1_admission(probes)  # type: ignore[arg-type]
    assert port.files == before

    class MissingWindowProbe(AdmissionProbes):
        def __getattribute__(self, name: str) -> object:
            if name == "complete_window_count":
                raise AttributeError(name)
            return super().__getattribute__(name)

    with pytest.raises(MigrationStateError, match="window.*unprovable"):
        store.require_stopped_v1_admission(MissingWindowProbe())
    assert port.files == before


def test_prepare_and_pointer_commit_each_revalidate_stopped_admission(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    probes = AdmissionProbes()
    source_payload = _stage_v1_ready(store, port)
    intent = MigrationIntent(
        source_v1_state_hash=hashlib.sha256(source_payload).hexdigest(),
        target_generation=_intent().target_generation,
        configuration_path=_intent().configuration_path,
        configuration_hash=_intent().configuration_hash,
        initial_lifecycle_path=_intent().initial_lifecycle_path,
        initial_lifecycle_seed_hash=_intent().initial_lifecycle_seed_hash,
        v1_slot_account_keys=_intent().v1_slot_account_keys,
    )

    assert store.prepare(intent, probes) == intent
    _stage_selected_artifacts(store, port)
    probes.open_visit_count_result = 1
    before = dict(port.files)

    with pytest.raises(MigrationStateError, match="visit"):
        store.commit_pointer(_pointer(), probes)

    assert port.files == before
    assert port.files[project / "var" / "lifecycle-state.json"] == source_payload


@pytest.mark.parametrize("next_slot", range(5))
def test_pointer_commit_rejects_seed_cursor_that_does_not_preserve_v1_next_slot(
    tmp_path, next_slot: int
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    source_payload = _stage_v1_ready(store, port, slot=next_slot)
    port.files[project / "var" / _intent().configuration_path] = CONFIGURATION_PAYLOAD
    wrong_seed = encode_state_v2(
        ReadyV2(GENERATION, AccountKey(f"account-{next_slot}"))
    )
    port.files[project / "var" / _intent().initial_lifecycle_path] = wrong_seed
    intent = MigrationIntent(
        source_v1_state_hash=hashlib.sha256(source_payload).hexdigest(),
        target_generation=_intent().target_generation,
        configuration_path=_intent().configuration_path,
        configuration_hash=_intent().configuration_hash,
        initial_lifecycle_path=_intent().initial_lifecycle_path,
        initial_lifecycle_seed_hash=hashlib.sha256(wrong_seed).hexdigest(),
        v1_slot_account_keys=_intent().v1_slot_account_keys,
    )
    store.prepare(intent, AdmissionProbes())

    with pytest.raises(MigrationStateError, match="cursor"):
        store.commit_pointer(_pointer(), AdmissionProbes())

    assert store.pointer_path not in port.files


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("configuration", "configuration"),
        ("lifecycle", "lifecycle"),
        ("intent", "migration intent"),
    ],
)
def test_pointer_commit_rejects_artifact_mutation_during_final_admission(
    tmp_path, artifact: str, message: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    store.prepare(_intent(), AdmissionProbes())

    class MutatingFinalProbe(AdmissionProbes):
        def __init__(self) -> None:
            super().__init__()
            self.mutex_checks = 0

        def mutex_owned(self) -> bool:
            self.mutex_checks += 1
            if self.mutex_checks == 6:
                if artifact == "configuration":
                    port.files[project / "var" / _intent().configuration_path] = (
                        b'{"accounts":[],"schema":2}'
                    )
                elif artifact == "lifecycle":
                    port.files[project / "var" / _intent().initial_lifecycle_path] = (
                        encode_state_v2(ReadyV2(GENERATION, AccountKey("account-1")))
                    )
                else:
                    port.files[store.migration_path] = b'{"schema":2}\n'
            return True

    with pytest.raises(MigrationStateError, match=message):
        store.commit_pointer(_pointer(), MutatingFinalProbe())

    assert store.pointer_path not in port.files


def test_pointer_commit_rechecks_v1_immediately_before_pointer_replace(tmp_path) -> None:
    class MutatingPointerPort(MemoryFilePort):
        def create_new_temp_write_through(self, path: Path) -> object:
            if path.name == ".active-generation.transition":
                self.files[path.parent / "lifecycle-state.json"] = encode_state(Ready(2))
            return super().create_new_temp_write_through(path)

    project = tmp_path / "project"
    project.mkdir()
    port = MutatingPointerPort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    store.prepare(_intent(), AdmissionProbes())

    with pytest.raises(MigrationStateError, match="source v1|READY changed"):
        store.commit_pointer(_pointer(), AdmissionProbes())

    assert store.pointer_path not in port.files


def test_pointer_commit_holds_final_exclusive_v1_read_through_pointer_replace(
    tmp_path,
) -> None:
    class OrderingPort(MemoryFilePort):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[tuple[str, str]] = []

        def close(self, file: object) -> None:
            self.events.append(("close", Path(file).name))

        def replace_write_through(self, source: Path, target: Path) -> None:
            self.events.append(("replace", target.name))
            super().replace_write_through(source, target)

    project = tmp_path / "project"
    project.mkdir()
    port = OrderingPort()
    store = MigrationStore(project, port)
    _stage_selected_artifacts(store, port)
    store.prepare(_intent(), AdmissionProbes())
    port.events.clear()

    store.commit_pointer(_pointer(), AdmissionProbes())

    pointer_replace = port.events.index(("replace", "active-generation.json"))
    final_v1_close = max(
        index
        for index, event in enumerate(port.events)
        if event == ("close", "lifecycle-state.json")
    )
    assert final_v1_close > pointer_replace
    for held_name in (
        "migration-v2.json",
        _intent().configuration_path,
        _intent().initial_lifecycle_path,
    ):
        final_close = max(
            index
            for index, event in enumerate(port.events)
            if event == ("close", held_name)
        )
        assert final_close > pointer_replace


def test_pointer_commit_rejects_v1_mapping_unrelated_to_selected_configuration(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    port = MemoryFilePort()
    store = MigrationStore(project, port)
    _stage_v1_ready(store, port, slot=1)
    port.files[project / "var" / _intent().configuration_path] = CONFIGURATION_PAYLOAD
    unrelated_keys = tuple(AccountKey(f"other-{index}") for index in range(5))
    unrelated_seed = encode_state_v2(ReadyV2(GENERATION, unrelated_keys[0]))
    port.files[project / "var" / _intent().initial_lifecycle_path] = unrelated_seed
    intent = MigrationIntent(
        source_v1_state_hash=SOURCE_HASH,
        target_generation=_intent().target_generation,
        configuration_path=_intent().configuration_path,
        configuration_hash=_intent().configuration_hash,
        initial_lifecycle_path=_intent().initial_lifecycle_path,
        initial_lifecycle_seed_hash=hashlib.sha256(unrelated_seed).hexdigest(),
        v1_slot_account_keys=unrelated_keys,
    )
    store.prepare(intent, AdmissionProbes())

    with pytest.raises(MigrationStateError, match="mapping.*configuration"):
        store.commit_pointer(_pointer(), AdmissionProbes())

    assert store.pointer_path not in port.files
