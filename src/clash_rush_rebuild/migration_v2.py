"""Crash-consistent schema-v2 migration intent and activation pointer."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationContentHash,
    ConfigurationGeneration,
    ConfigurationValidationError,
    SchemaV2Configuration,
)
from clash_rush_rebuild.generation_change_v2 import (
    ConfigurationGenerationChange,
    GenerationChangeProbePort,
    require_safe_generation_activation,
)
from clash_rush_rebuild.lifecycle_state import (
    LifecycleStateError,
    Ready,
    WriteThroughFilePort,
    decode_state,
    encode_state,
)
from clash_rush_rebuild.lifecycle_state_v2 import (
    ReadyV2,
    decode_state_v2,
    encode_state_v2,
)


_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class MigrationStateError(LifecycleStateError):
    """Migration intent or active-generation selection is unprovable."""


class StoppedMigrationProbePort(Protocol):
    """Read-only proofs required while the protected host mutex is held."""

    def mutex_owned(self) -> bool: ...
    def complete_player_count(self) -> int: ...
    def complete_window_count(self) -> int: ...
    def open_visit_count(self) -> int: ...
    def open_action_count(self) -> int: ...
    def open_blocker_delivery_count(self) -> int: ...
    def unresolved_queue_count(self) -> int: ...


def _require_hash(value: object, label: str) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise MigrationStateError(f"{label} must be exactly 64 lowercase hex characters")
    return value


def _require_generation_path(value: object, prefix: str, generation: str) -> str:
    expected = f"{prefix}.{generation}.json"
    if type(value) is not str or value != expected:
        raise MigrationStateError(f"{prefix} path must be the exact generation file")
    return value


@dataclass(frozen=True, slots=True)
class StoppedMigrationAdmission:
    """Exact canonical v1 READY snapshot admitted for stopped-only migration."""

    source_state: Ready
    source_state_payload: bytes
    source_state_hash: str

    def __post_init__(self) -> None:
        if type(self.source_state) is not Ready:
            raise MigrationStateError("migration source must be canonical v1 READY")
        if (
            type(self.source_state_payload) is not bytes
            or encode_state(self.source_state) != self.source_state_payload
        ):
            raise MigrationStateError("migration source v1 READY payload is not canonical")
        _require_hash(self.source_state_hash, "source v1 state hash")
        if hashlib.sha256(self.source_state_payload).hexdigest() != self.source_state_hash:
            raise MigrationStateError("source v1 state hash mismatch")


@dataclass(frozen=True, slots=True)
class MigrationIntent:
    """Durable PREPARED intent written before the activation commit point."""

    source_v1_state_hash: str
    target_generation: ConfigurationGeneration
    configuration_path: str
    configuration_hash: ConfigurationContentHash
    initial_lifecycle_path: str
    initial_lifecycle_seed_hash: str
    v1_slot_account_keys: tuple[AccountKey, ...]

    def __post_init__(self) -> None:
        _require_hash(self.source_v1_state_hash, "source v1 state hash")
        if type(self.target_generation) is not ConfigurationGeneration:
            raise MigrationStateError("target configuration generation is invalid")
        if type(self.configuration_hash) is not ConfigurationContentHash:
            raise MigrationStateError("configuration hash is invalid")
        generation = self.target_generation.value
        _require_generation_path(
            self.configuration_path, "configuration-v2", generation
        )
        _require_generation_path(
            self.initial_lifecycle_path, "lifecycle-state-v2", generation
        )
        _require_hash(
            self.initial_lifecycle_seed_hash, "initial lifecycle seed hash"
        )
        if type(self.v1_slot_account_keys) is not tuple or len(
            self.v1_slot_account_keys
        ) != 5:
            raise MigrationStateError("v1 slot mapping must contain exactly five keys")
        if any(type(key) is not AccountKey for key in self.v1_slot_account_keys):
            raise MigrationStateError("v1 slot mapping contains an invalid account key")
        if len(set(self.v1_slot_account_keys)) != 5:
            raise MigrationStateError("v1 slot mapping account keys must be unique")


@dataclass(frozen=True, slots=True)
class ActiveGenerationPointer:
    """The sole durable commit point selecting schema v2."""

    configuration_generation: ConfigurationGeneration
    configuration_path: str
    configuration_hash: ConfigurationContentHash
    lifecycle_path: str
    lifecycle_schema: int

    def __post_init__(self) -> None:
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise MigrationStateError("active configuration generation is invalid")
        if type(self.configuration_hash) is not ConfigurationContentHash:
            raise MigrationStateError("active configuration hash is invalid")
        generation = self.configuration_generation.value
        _require_generation_path(
            self.configuration_path, "configuration-v2", generation
        )
        _require_generation_path(self.lifecycle_path, "lifecycle-state-v2", generation)
        if type(self.lifecycle_schema) is not int or self.lifecycle_schema != 2:
            raise MigrationStateError("active lifecycle schema must be exactly 2")


@dataclass(frozen=True, slots=True)
class GenerationSelection:
    """Startup selection made from the active-generation pointer only."""

    schema: int
    pointer: ActiveGenerationPointer | None
    migration_recovery_required: bool

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema not in (1, 2):
            raise MigrationStateError("selected lifecycle schema is invalid")
        if self.schema == 1:
            if self.pointer is not None or self.migration_recovery_required is not False:
                raise MigrationStateError("schema-v1 selection cannot carry v2 state")
        elif type(self.pointer) is not ActiveGenerationPointer:
            raise MigrationStateError("schema-v2 selection requires an exact pointer")
        if type(self.migration_recovery_required) is not bool:
            raise MigrationStateError("migration recovery flag must be a boolean")

    @classmethod
    def v1(cls) -> GenerationSelection:
        return cls(1, None, False)

    @classmethod
    def v2(
        cls,
        pointer: ActiveGenerationPointer,
        *,
        migration_recovery_required: bool,
    ) -> GenerationSelection:
        return cls(2, pointer, migration_recovery_required)


class MigrationStore:
    """Write PREPARED intent and select v2 only through one durable pointer."""

    def __init__(self, project_root: Path, file_port: WriteThroughFilePort) -> None:
        try:
            project = Path(project_root).resolve(strict=True)
        except OSError as exc:
            raise MigrationStateError("existing project root required") from exc
        if not project.is_dir():
            raise MigrationStateError("project root must be a directory")
        candidate = project / "var"
        try:
            candidate.mkdir(exist_ok=True)
            root = candidate.resolve(strict=True)
            root.relative_to(project)
        except (OSError, ValueError) as exc:
            raise MigrationStateError("migration path escaped project root") from exc
        if root != candidate:
            raise MigrationStateError("migration path escaped project root")
        self._root = root
        self._port = file_port
        self._migration_path = root / "migration-v2.json"
        self._migration_transition = root / ".migration-v2.transition"
        self._pointer_path = root / "active-generation.json"
        self._pointer_transition = root / ".active-generation.transition"
        self._v1_path = root / "lifecycle-state.json"
        self._v1_transition = root / ".lifecycle-state.transition"

    @property
    def migration_path(self) -> Path:
        return self._migration_path

    @property
    def pointer_path(self) -> Path:
        return self._pointer_path

    def _read_optional(self, path: Path, limit: int, label: str) -> bytes | None:
        try:
            reader = self._port.open_read_exclusive_no_reparse(path)
        except FileNotFoundError:
            return None
        except BaseException as exc:
            raise MigrationStateError(f"{label} could not be opened") from exc
        try:
            return self._port.read_bounded(reader, limit)
        except BaseException as exc:
            raise MigrationStateError(f"{label} could not be read") from exc
        finally:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise MigrationStateError(f"{label} handle could not close") from exc

    def _write_new(self, path: Path, payload: bytes) -> None:
        writer = self._port.create_new_temp_write_through(path)
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = self._port.write(writer, view[offset:])
                if (
                    type(written) is not int
                    or written <= 0
                    or written > len(view) - offset
                ):
                    raise MigrationStateError("migration write was incomplete")
                offset += written
            self._port.flush(writer)
        finally:
            self._port.close(writer)

    def _require_absent(self, path: Path, label: str) -> None:
        if self._read_optional(path, 1, label) is not None:
            raise MigrationStateError(f"{label} already exists")

    @staticmethod
    def _probe_exact_zero(
        probes: StoppedMigrationProbePort, method_name: str, label: str
    ) -> None:
        try:
            probe = getattr(probes, method_name)
            value = probe()
        except BaseException as exc:
            raise MigrationStateError(f"{label} absence is unprovable") from exc
        if type(value) is not int or value < 0:
            raise MigrationStateError(f"{label} absence is unprovable")
        if value != 0:
            raise MigrationStateError(f"open or unresolved {label} blocks migration")

    @staticmethod
    def _require_mutex_owned(probes: StoppedMigrationProbePort) -> None:
        try:
            owned = probes.mutex_owned()
        except BaseException as exc:
            raise MigrationStateError("protected host mutex ownership is unprovable") from exc
        if type(owned) is not bool or owned is not True:
            raise MigrationStateError("protected host mutex ownership is unprovable")

    def _read_canonical_v1_ready(self) -> tuple[Ready, bytes]:
        if self._read_optional(
            self._v1_transition, 129, "schema-v1 lifecycle transition"
        ) is not None:
            raise MigrationStateError("schema-v1 lifecycle transition blocks migration")
        payload = self._read_optional(self._v1_path, 513, "schema-v1 lifecycle state")
        if payload is None:
            raise MigrationStateError("schema-v1 lifecycle state is missing")
        try:
            state = decode_state(payload)
        except LifecycleStateError as exc:
            raise MigrationStateError(
                "migration source must be canonical v1 READY"
            ) from exc
        if type(state) is not Ready:
            raise MigrationStateError("migration source must be canonical v1 READY")
        return state, payload

    def require_stopped_v1_admission(
        self, probes: StoppedMigrationProbePort
    ) -> StoppedMigrationAdmission:
        """Prove a stable stopped v1 READY snapshot without modifying schema v1."""
        self._require_mutex_owned(probes)
        state, payload = self._read_canonical_v1_ready()
        self._probe_exact_zero(probes, "complete_player_count", "player")
        self._probe_exact_zero(probes, "complete_window_count", "window")
        self._probe_exact_zero(probes, "open_visit_count", "visit")
        self._probe_exact_zero(probes, "open_action_count", "action")
        self._probe_exact_zero(
            probes, "open_blocker_delivery_count", "blocker delivery"
        )
        self._probe_exact_zero(probes, "unresolved_queue_count", "queue state")
        final_state, final_payload = self._read_canonical_v1_ready()
        self._require_mutex_owned(probes)
        if final_state != state or final_payload != payload:
            raise MigrationStateError("schema-v1 READY changed during admission")
        return StoppedMigrationAdmission(
            source_state=state,
            source_state_payload=payload,
            source_state_hash=hashlib.sha256(payload).hexdigest(),
        )

    def _hold_stopped_v1_admission(
        self,
        probes: StoppedMigrationProbePort,
        intent: MigrationIntent,
    ) -> Callable[[], None]:
        """Hold the final exclusive v1 read until pointer replacement completes."""
        self._require_mutex_owned(probes)
        if self._read_optional(
            self._v1_transition, 129, "schema-v1 lifecycle transition"
        ) is not None:
            raise MigrationStateError("schema-v1 lifecycle transition blocks migration")
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._v1_path)
        except FileNotFoundError as exc:
            raise MigrationStateError("schema-v1 lifecycle state is missing") from exc
        except BaseException as exc:
            raise MigrationStateError("schema-v1 lifecycle state could not be opened") from exc
        try:
            payload = self._port.read_bounded(reader, 513)
            state = decode_state(payload)
            if type(state) is not Ready:
                raise MigrationStateError("migration source must be canonical v1 READY")
            admission = StoppedMigrationAdmission(
                source_state=state,
                source_state_payload=payload,
                source_state_hash=hashlib.sha256(payload).hexdigest(),
            )
            self._require_admission_matches_intent(admission, intent)
            self._probe_exact_zero(probes, "complete_player_count", "player")
            self._probe_exact_zero(probes, "complete_window_count", "window")
            self._probe_exact_zero(probes, "open_visit_count", "visit")
            self._probe_exact_zero(probes, "open_action_count", "action")
            self._probe_exact_zero(
                probes, "open_blocker_delivery_count", "blocker delivery"
            )
            self._probe_exact_zero(probes, "unresolved_queue_count", "queue state")
            if self._read_optional(
                self._v1_transition, 129, "schema-v1 lifecycle transition"
            ) is not None:
                raise MigrationStateError(
                    "schema-v1 lifecycle transition blocks migration"
                )
            self._require_mutex_owned(probes)
        except BaseException:
            self._port.close(reader)
            raise

        def release() -> None:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise MigrationStateError(
                    "schema-v1 lifecycle state handle could not close"
                ) from exc

        return release

    def _hold_initial_migration_commit(
        self,
        pointer: ActiveGenerationPointer,
        intent: MigrationIntent,
        admission: StoppedMigrationAdmission,
        probes: StoppedMigrationProbePort,
    ) -> Callable[[], None]:
        """Hold every artifact selecting the initial v2 generation."""
        release_v1 = self._hold_stopped_v1_admission(probes, intent)
        held: list[object] = []

        def close_all() -> None:
            close_error: BaseException | None = None
            for reader in reversed(held):
                try:
                    self._port.close(reader)
                except BaseException as exc:
                    if close_error is None:
                        close_error = exc
            try:
                release_v1()
            except BaseException as exc:
                if close_error is None:
                    close_error = exc
            if close_error is not None:
                raise MigrationStateError(
                    "initial migration held state handle could not close"
                ) from close_error

        try:
            if self._read_optional(
                self._pointer_transition,
                12,
                "active-generation transition",
            ) != b"transition\n":
                raise MigrationStateError(
                    "active-generation transition changed during migration"
                )
            if self._read_optional(
                self._migration_transition,
                12,
                "migration transition",
            ) is not None:
                raise MigrationStateError("migration transition blocks migration commit")
            lifecycle_transition = self._root / (
                f".lifecycle-state-v2.{pointer.configuration_generation.value}.transition"
            )
            if self._read_optional(
                lifecycle_transition,
                129,
                "selected lifecycle transition",
            ) is not None:
                raise MigrationStateError(
                    "selected lifecycle transition blocks migration commit"
                )
            if self._read_optional(
                self._pointer_path,
                1,
                "active-generation pointer",
            ) is not None:
                raise MigrationStateError("active-generation pointer already exists")

            artifacts = (
                (self._migration_path, "migration intent", 2049),
                (
                    self._root / pointer.configuration_path,
                    "selected immutable configuration",
                    4097,
                ),
                (
                    self._root / pointer.lifecycle_path,
                    "selected lifecycle state",
                    513,
                ),
            )
            payloads: list[bytes] = []
            for path, label, limit in artifacts:
                try:
                    reader = self._port.open_read_exclusive_no_reparse(path)
                except BaseException as exc:
                    raise MigrationStateError(f"{label} could not be held") from exc
                held.append(reader)
                try:
                    payloads.append(self._port.read_bounded(reader, limit))
                except BaseException as exc:
                    raise MigrationStateError(f"{label} could not be read") from exc

            expected_configuration = json.dumps(
                {
                    "accounts": [key.value for key in intent.v1_slot_account_keys],
                    "schema": 2,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            expected_lifecycle = encode_state_v2(
                ReadyV2(
                    pointer.configuration_generation,
                    intent.v1_slot_account_keys[
                        (admission.source_state.next_slot - 1) % 5
                    ],
                )
            )
            if payloads[0] != encode_migration_intent(intent):
                raise MigrationStateError("migration intent changed during pointer commit")
            if (
                payloads[1] != expected_configuration
                or hashlib.sha256(payloads[1]).hexdigest()
                != pointer.configuration_hash.value
            ):
                raise MigrationStateError(
                    "selected immutable configuration changed during pointer commit"
                )
            if (
                payloads[2] != expected_lifecycle
                or hashlib.sha256(payloads[2]).hexdigest()
                != intent.initial_lifecycle_seed_hash
            ):
                raise MigrationStateError(
                    "selected lifecycle state changed during pointer commit"
                )
        except BaseException:
            close_all()
            raise

        return close_all

    @staticmethod
    def _require_admission_matches_intent(
        admission: StoppedMigrationAdmission, intent: MigrationIntent
    ) -> None:
        if admission.source_state_hash != intent.source_v1_state_hash:
            raise MigrationStateError(
                "stopped migration admission does not match source v1 state"
            )

    def _replace_and_read_back(
        self,
        *,
        path: Path,
        transition: Path,
        payload: bytes,
        limit: int,
        label: str,
        pre_replace: Callable[[], Callable[[], None]] | None = None,
    ) -> bytes:
        self._require_absent(transition, f"{label} transition")
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        release: Callable[[], None] | None = None
        try:
            self._write_new(transition, b"transition\n")
            self._write_new(temporary, payload)
            if pre_replace is not None:
                release = pre_replace()
            self._port.replace_write_through(temporary, path)
            read_back = self._read_optional(path, limit, label)
            if type(read_back) is not bytes or read_back != payload:
                raise MigrationStateError(f"{label} read-back mismatch")
            self._port.delete_file_no_reparse(transition)
            return read_back
        except BaseException as exc:
            if isinstance(exc, MigrationStateError):
                raise
            raise MigrationStateError(f"{label} write-through commit failed") from exc
        finally:
            if release is not None:
                release()

    def prepare(
        self, intent: MigrationIntent, probes: StoppedMigrationProbePort
    ) -> MigrationIntent:
        """Durably write PREPARED while leaving pointer-based selection on v1."""
        payload = encode_migration_intent(intent)
        self._require_absent(self._pointer_path, "active-generation pointer")
        self._require_absent(self._migration_path, "migration intent")
        admission = self.require_stopped_v1_admission(probes)
        self._require_admission_matches_intent(admission, intent)
        read_back = self._replace_and_read_back(
            path=self._migration_path,
            transition=self._migration_transition,
            payload=payload,
            limit=2049,
            label="migration intent",
        )
        return decode_migration_intent(read_back)

    def load_prepared(self) -> MigrationIntent:
        guard = self._read_optional(
            self._migration_transition, 12, "migration transition"
        )
        if guard is not None:
            raise MigrationStateError("incomplete migration intent write requires recovery")
        payload = self._read_optional(self._migration_path, 2049, "migration intent")
        if payload is None:
            raise MigrationStateError("migration intent is missing")
        return decode_migration_intent(payload)

    @staticmethod
    def _require_pointer_matches_intent(
        pointer: ActiveGenerationPointer, intent: MigrationIntent
    ) -> None:
        if (
            pointer.configuration_generation != intent.target_generation
            or pointer.configuration_path != intent.configuration_path
            or pointer.configuration_hash != intent.configuration_hash
            or pointer.lifecycle_path != intent.initial_lifecycle_path
            or pointer.lifecycle_schema != 2
        ):
            raise MigrationStateError(
                "active-generation pointer does not match PREPARED migration intent"
            )

    def _validate_selected_artifacts(
        self,
        pointer: ActiveGenerationPointer,
        intent: MigrationIntent | None,
        admission: StoppedMigrationAdmission | None = None,
    ) -> None:
        configuration_payload = self._read_optional(
            self._root / pointer.configuration_path,
            4097,
            "selected immutable configuration",
        )
        if configuration_payload is None:
            raise MigrationStateError("selected immutable configuration is missing")
        if (
            type(configuration_payload) is not bytes
            or len(configuration_payload) > 4096
            or hashlib.sha256(configuration_payload).hexdigest()
            != pointer.configuration_hash.value
        ):
            raise MigrationStateError("selected immutable configuration hash mismatch")
        try:
            raw_configuration = json.loads(configuration_payload.decode("ascii"))
            if (
                type(raw_configuration) is not dict
                or set(raw_configuration) != {"accounts", "schema"}
                or type(raw_configuration["schema"]) is not int
                or raw_configuration["schema"] != 2
                or type(raw_configuration["accounts"]) is not list
            ):
                raise MigrationStateError(
                    "selected immutable configuration encoding is invalid"
                )
            configuration = SchemaV2Configuration(
                generation=pointer.configuration_generation,
                accounts=tuple(
                    AccountRecord(AccountKey(value))
                    for value in raw_configuration["accounts"]
                ),
            )
        except (UnicodeError, json.JSONDecodeError, ConfigurationValidationError) as exc:
            raise MigrationStateError(
                "selected immutable configuration encoding is invalid"
            ) from exc
        canonical_configuration = json.dumps(
            {
                "accounts": [account.key.value for account in configuration.accounts],
                "schema": 2,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if canonical_configuration != configuration_payload:
            raise MigrationStateError(
                "selected immutable configuration encoding is not canonical"
            )
        if configuration.content_hash != pointer.configuration_hash:
            raise MigrationStateError("selected immutable configuration hash mismatch")
        if intent is not None and tuple(
            account.key for account in configuration.accounts
        ) != intent.v1_slot_account_keys:
            raise MigrationStateError(
                "v1 slot mapping does not match selected configuration"
            )

        generation = pointer.configuration_generation.value
        lifecycle_transition = self._root / (
            f".lifecycle-state-v2.{generation}.transition"
        )
        if self._read_optional(
            lifecycle_transition, 129, "selected lifecycle transition"
        ) is not None:
            raise MigrationStateError("selected lifecycle transition requires recovery")
        lifecycle_payload = self._read_optional(
            self._root / pointer.lifecycle_path, 513, "selected lifecycle state"
        )
        if lifecycle_payload is None:
            raise MigrationStateError("selected lifecycle state is missing")
        state = decode_state_v2(lifecycle_payload)
        if state.configuration_generation != pointer.configuration_generation:
            raise MigrationStateError("selected lifecycle generation mismatch")
        if intent is not None:
            if type(state) is not ReadyV2:
                raise MigrationStateError("PREPARED migration lifecycle seed is not READY")
            if (
                hashlib.sha256(lifecycle_payload).hexdigest()
                != intent.initial_lifecycle_seed_hash
            ):
                raise MigrationStateError("PREPARED migration lifecycle seed hash mismatch")
            if admission is not None:
                expected_cursor = intent.v1_slot_account_keys[
                    (admission.source_state.next_slot - 1) % 5
                ]
                if state.tie_after_account_key != expected_cursor:
                    raise MigrationStateError(
                        "PREPARED migration lifecycle cursor does not preserve v1 next slot"
                    )

    def commit_pointer(
        self,
        pointer: ActiveGenerationPointer,
        probes: StoppedMigrationProbePort,
    ) -> ActiveGenerationPointer:
        """Atomically select v2 after an exact PREPARED intent is durable."""
        payload = encode_active_generation_pointer(pointer)
        self._require_absent(self._pointer_path, "active-generation pointer")
        intent = self.load_prepared()
        self._require_pointer_matches_intent(pointer, intent)
        admission = self.require_stopped_v1_admission(probes)
        self._require_admission_matches_intent(admission, intent)
        self._validate_selected_artifacts(pointer, intent, admission)
        final_admission = self.require_stopped_v1_admission(probes)
        self._require_admission_matches_intent(final_admission, intent)

        def revalidate_before_pointer_replace() -> Callable[[], None]:
            return self._hold_initial_migration_commit(
                pointer,
                intent,
                final_admission,
                probes,
            )

        read_back = self._replace_and_read_back(
            path=self._pointer_path,
            transition=self._pointer_transition,
            payload=payload,
            limit=1025,
            label="active-generation pointer",
            pre_replace=revalidate_before_pointer_replace,
        )
        return decode_active_generation_pointer(read_back)

    def select_generation(self) -> GenerationSelection:
        """Read the pointer first; its absence alone keeps schema v1 selected."""
        payload = self._read_optional(
            self._pointer_path, 1025, "active-generation pointer"
        )
        if payload is None:
            return GenerationSelection.v1()
        pointer = decode_active_generation_pointer(payload)
        pointer_guard = self._read_optional(
            self._pointer_transition, 12, "active-generation transition"
        )
        if pointer_guard is not None and pointer_guard != b"transition\n":
            raise MigrationStateError("active-generation transition is malformed")
        migration_guard = self._read_optional(
            self._migration_transition, 12, "migration transition"
        )
        if migration_guard is not None:
            raise MigrationStateError(
                "migration intent transition conflicts with selected schema v2"
            )
        migration_payload = self._read_optional(
            self._migration_path, 2049, "migration intent"
        )
        intent = None
        if migration_payload is not None:
            intent = decode_migration_intent(migration_payload)
            self._require_pointer_matches_intent(pointer, intent)
        self._validate_selected_artifacts(pointer, intent)
        return GenerationSelection.v2(
            pointer,
            migration_recovery_required=(
                migration_payload is not None or pointer_guard is not None
            ),
        )

    def activate_generation_change(
        self,
        change: ConfigurationGenerationChange,
        pointer: ActiveGenerationPointer,
        probes: GenerationChangeProbePort,
    ) -> ActiveGenerationPointer:
        """Replace a settled v2 pointer after proving a safe staged generation."""
        if type(change) is not ConfigurationGenerationChange:
            raise MigrationStateError("exact generation change plan required")
        if type(pointer) is not ActiveGenerationPointer:
            raise MigrationStateError("exact target generation pointer required")

        selection = self.select_generation()
        if (
            selection.schema != 2
            or selection.migration_recovery_required
            or type(selection.pointer) is not ActiveGenerationPointer
        ):
            raise MigrationStateError(
                "generation change requires a settled active schema-v2 generation"
            )
        current_pointer = selection.pointer
        if (
            current_pointer.configuration_generation != change.source.generation
            or current_pointer.configuration_hash != change.source.content_hash
        ):
            raise MigrationStateError(
                "generation change source does not match the active configuration"
            )
        if (
            pointer.configuration_generation != change.target.generation
            or pointer.configuration_hash != change.target.content_hash
        ):
            raise MigrationStateError(
                "generation change target does not match the target configuration"
            )

        current_lifecycle_payload = self._read_optional(
            self._root / current_pointer.lifecycle_path,
            513,
            "active source lifecycle state",
        )
        if current_lifecycle_payload is None:
            raise MigrationStateError("active source lifecycle state is missing")
        current_state = decode_state_v2(current_lifecycle_payload)
        if type(current_state) is not ReadyV2:
            raise MigrationStateError(
                "generation change requires a stopped source READY state"
            )

        source_keys = {account.key for account in change.source.accounts}
        if (
            current_state.tie_after_account_key is not None
            and current_state.tie_after_account_key not in source_keys
        ):
            raise MigrationStateError(
                "generation change source lifecycle cursor is not configured"
            )

        target_keys = {account.key for account in change.target.accounts}
        expected_cursor = (
            current_state.tie_after_account_key
            if current_state.tie_after_account_key in target_keys
            else None
        )

        def validate_target() -> None:
            self._validate_selected_artifacts(pointer, None)
            target_payload = self._read_optional(
                self._root / pointer.lifecycle_path,
                513,
                "target lifecycle state",
            )
            if target_payload is None:
                raise MigrationStateError("target lifecycle state is missing")
            target_state = decode_state_v2(target_payload)
            if type(target_state) is not ReadyV2:
                raise MigrationStateError(
                    "generation change target lifecycle seed is not READY"
                )
            if target_state.tie_after_account_key != expected_cursor:
                raise MigrationStateError(
                    "generation change target lifecycle cursor is invalid"
                )
            if (
                target_state.tie_after_account_key is not None
                and target_state.tie_after_account_key not in target_keys
            ):
                raise MigrationStateError(
                    "generation change target lifecycle cursor is not configured"
                )

        validate_target()
        require_safe_generation_activation(change, probes)
        payload = encode_active_generation_pointer(pointer)
        expected_source_configuration = json.dumps(
            {
                "accounts": [
                    account.key.value for account in change.source.accounts
                ],
                "schema": 2,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        expected_target_configuration = json.dumps(
            {
                "accounts": [
                    account.key.value for account in change.target.accounts
                ],
                "schema": 2,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        expected_target_lifecycle = encode_state_v2(
            ReadyV2(pointer.configuration_generation, expected_cursor)
        )

        def revalidate_before_pointer_replace() -> Callable[[], None]:
            active_payload = self._read_optional(
                self._pointer_path,
                1025,
                "active-generation pointer",
            )
            if active_payload != encode_active_generation_pointer(current_pointer):
                raise MigrationStateError(
                    "active-generation pointer changed during generation activation"
                )
            try:
                reader = self._port.open_read_exclusive_no_reparse(
                    self._root / current_pointer.lifecycle_path
                )
            except BaseException as exc:
                raise MigrationStateError(
                    "active source lifecycle state could not be held"
                ) from exc
            source_configuration_reader: object | None = None
            try:
                source_configuration_reader = (
                    self._port.open_read_exclusive_no_reparse(
                        self._root / current_pointer.configuration_path
                    )
                )
                require_safe_generation_activation(change, probes)
                for generation in (
                    current_pointer.configuration_generation.value,
                    pointer.configuration_generation.value,
                ):
                    transition = self._root / (
                        f".lifecycle-state-v2.{generation}.transition"
                    )
                    if self._read_optional(
                        transition,
                        129,
                        "generation lifecycle transition",
                    ) is not None:
                        raise MigrationStateError(
                            "generation lifecycle transition blocks activation"
                        )
                if self._read_optional(
                    self._pointer_transition,
                    12,
                    "active-generation transition",
                ) != b"transition\n":
                    raise MigrationStateError(
                        "active-generation transition changed during activation"
                    )
                if self._read_optional(
                    self._migration_transition,
                    12,
                    "migration transition",
                ) is not None:
                    raise MigrationStateError(
                        "migration transition blocks generation activation"
                    )
                if self._read_optional(
                    self._migration_path,
                    1,
                    "migration intent",
                ) is not None:
                    raise MigrationStateError(
                        "unarchived migration intent blocks generation activation"
                    )
                final_active_payload = self._read_optional(
                    self._pointer_path,
                    1025,
                    "active-generation pointer",
                )
                if final_active_payload != encode_active_generation_pointer(
                    current_pointer
                ):
                    raise MigrationStateError(
                        "active-generation pointer changed during generation activation"
                    )
                held_payload = self._port.read_bounded(reader, 513)
                if held_payload != current_lifecycle_payload:
                    raise MigrationStateError(
                        "active source lifecycle state changed during activation"
                    )
                held_state = decode_state_v2(held_payload)
                if type(held_state) is not ReadyV2:
                    raise MigrationStateError(
                        "generation change requires a stopped source READY state"
                    )
                source_configuration_payload = self._port.read_bounded(
                    source_configuration_reader,
                    4097,
                )
                if (
                    source_configuration_payload != expected_source_configuration
                    or hashlib.sha256(source_configuration_payload).hexdigest()
                    != current_pointer.configuration_hash.value
                ):
                    raise MigrationStateError(
                        "active source configuration changed during generation activation"
                    )
                target_lifecycle_reader = self._port.open_read_exclusive_no_reparse(
                    self._root / pointer.lifecycle_path
                )
                try:
                    target_configuration_reader = (
                        self._port.open_read_exclusive_no_reparse(
                            self._root / pointer.configuration_path
                        )
                    )
                    try:
                        target_lifecycle_payload = self._port.read_bounded(
                            target_lifecycle_reader, 513
                        )
                        if target_lifecycle_payload != expected_target_lifecycle:
                            raise MigrationStateError(
                                "generation change target lifecycle seed is invalid"
                            )
                        target_configuration_payload = self._port.read_bounded(
                            target_configuration_reader, 4097
                        )
                        if (
                            target_configuration_payload
                            != expected_target_configuration
                            or hashlib.sha256(target_configuration_payload).hexdigest()
                            != pointer.configuration_hash.value
                        ):
                            raise MigrationStateError(
                                "generation change target configuration is invalid"
                            )
                    except BaseException:
                        self._port.close(target_configuration_reader)
                        raise
                except BaseException:
                    self._port.close(target_lifecycle_reader)
                    raise
            except BaseException:
                if source_configuration_reader is not None:
                    self._port.close(source_configuration_reader)
                self._port.close(reader)
                raise

            def release() -> None:
                close_error: BaseException | None = None
                for held_reader in (
                    target_configuration_reader,
                    target_lifecycle_reader,
                    source_configuration_reader,
                    reader,
                ):
                    try:
                        self._port.close(held_reader)
                    except BaseException as exc:
                        if close_error is None:
                            close_error = exc
                if close_error is not None:
                    raise MigrationStateError(
                        "generation activation held state handle could not close"
                    ) from close_error

            return release

        read_back = self._replace_and_read_back(
            path=self._pointer_path,
            transition=self._pointer_transition,
            payload=payload,
            limit=1025,
            label="active-generation pointer",
            pre_replace=revalidate_before_pointer_replace,
        )
        return decode_active_generation_pointer(read_back)


def encode_migration_intent(intent: MigrationIntent) -> bytes:
    if type(intent) is not MigrationIntent:
        raise MigrationStateError("migration intent is invalid")
    raw = {
        "configuration_hash": intent.configuration_hash.value,
        "configuration_path": intent.configuration_path,
        "initial_lifecycle_path": intent.initial_lifecycle_path,
        "initial_lifecycle_seed_hash": intent.initial_lifecycle_seed_hash,
        "schema": 2,
        "source_v1_state_hash": intent.source_v1_state_hash,
        "state": "PREPARED",
        "target_generation": intent.target_generation.value,
        "v1_slot_account_keys": [key.value for key in intent.v1_slot_account_keys],
    }
    return json.dumps(raw, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def decode_migration_intent(payload: bytes) -> MigrationIntent:
    if type(payload) is not bytes or len(payload) > 2048:
        raise MigrationStateError("migration intent payload is invalid")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationStateError("migration intent payload is unreadable") from exc
    expected_fields = {
        "configuration_hash",
        "configuration_path",
        "initial_lifecycle_path",
        "initial_lifecycle_seed_hash",
        "schema",
        "source_v1_state_hash",
        "state",
        "target_generation",
        "v1_slot_account_keys",
    }
    if type(raw) is not dict or set(raw) != expected_fields:
        raise MigrationStateError("migration intent fields are invalid")
    if type(raw["schema"]) is not int or raw["schema"] != 2:
        raise MigrationStateError("migration intent schema is invalid")
    if raw["state"] != "PREPARED":
        raise MigrationStateError("migration intent state is invalid")
    if type(raw["v1_slot_account_keys"]) is not list:
        raise MigrationStateError("migration intent slot mapping is invalid")
    try:
        intent = MigrationIntent(
            source_v1_state_hash=raw["source_v1_state_hash"],
            target_generation=ConfigurationGeneration(raw["target_generation"]),
            configuration_path=raw["configuration_path"],
            configuration_hash=ConfigurationContentHash(raw["configuration_hash"]),
            initial_lifecycle_path=raw["initial_lifecycle_path"],
            initial_lifecycle_seed_hash=raw["initial_lifecycle_seed_hash"],
            v1_slot_account_keys=tuple(
                AccountKey(value) for value in raw["v1_slot_account_keys"]
            ),
        )
    except ConfigurationValidationError as exc:
        raise MigrationStateError("migration intent identity is invalid") from exc
    if encode_migration_intent(intent) != payload:
        raise MigrationStateError("migration intent encoding is not canonical")
    return intent


def encode_active_generation_pointer(pointer: ActiveGenerationPointer) -> bytes:
    if type(pointer) is not ActiveGenerationPointer:
        raise MigrationStateError("active-generation pointer is invalid")
    raw = {
        "configuration_generation": pointer.configuration_generation.value,
        "configuration_hash": pointer.configuration_hash.value,
        "configuration_path": pointer.configuration_path,
        "lifecycle_path": pointer.lifecycle_path,
        "lifecycle_schema": pointer.lifecycle_schema,
        "schema": 2,
    }
    return json.dumps(raw, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def decode_active_generation_pointer(payload: bytes) -> ActiveGenerationPointer:
    if type(payload) is not bytes or len(payload) > 1024:
        raise MigrationStateError("active-generation pointer payload is invalid")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationStateError("active-generation pointer is unreadable") from exc
    expected_fields = {
        "configuration_generation",
        "configuration_hash",
        "configuration_path",
        "lifecycle_path",
        "lifecycle_schema",
        "schema",
    }
    if type(raw) is not dict or set(raw) != expected_fields:
        raise MigrationStateError("active-generation pointer fields are invalid")
    if type(raw["schema"]) is not int or raw["schema"] != 2:
        raise MigrationStateError("active-generation pointer schema is invalid")
    try:
        pointer = ActiveGenerationPointer(
            configuration_generation=ConfigurationGeneration(
                raw["configuration_generation"]
            ),
            configuration_path=raw["configuration_path"],
            configuration_hash=ConfigurationContentHash(raw["configuration_hash"]),
            lifecycle_path=raw["lifecycle_path"],
            lifecycle_schema=raw["lifecycle_schema"],
        )
    except ConfigurationValidationError as exc:
        raise MigrationStateError("active-generation pointer identity is invalid") from exc
    if encode_active_generation_pointer(pointer) != payload:
        raise MigrationStateError("active-generation pointer encoding is not canonical")
    return pointer
