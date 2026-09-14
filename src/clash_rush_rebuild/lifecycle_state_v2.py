"""Generation-bound schema-v2 lifecycle values and storage."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    ConfigurationGeneration,
    ConfigurationValidationError,
    SchemaV2Configuration,
)
from clash_rush_rebuild.lifecycle_state import (
    BlockReason,
    LifecycleStateError,
    WriteThroughFilePort,
)


_NONCE_PATTERN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class ReadyV2:
    """Stopped schema-v2 lifecycle state with an equal-deadline tie cursor."""

    configuration_generation: ConfigurationGeneration
    tie_after_account_key: AccountKey | None

    def __post_init__(self) -> None:
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise LifecycleStateError("READY configuration generation is invalid")
        if self.tie_after_account_key is not None and type(
            self.tie_after_account_key
        ) is not AccountKey:
            raise LifecycleStateError("READY account key is invalid")

    @property
    def state_name(self) -> str:
        return "READY"


@dataclass(frozen=True, slots=True)
class ActiveV2:
    """Unresolved schema-v2 lifecycle state for one stable account identity."""

    configuration_generation: ConfigurationGeneration
    account_key: AccountKey
    slot_index: int
    run_nonce: str
    blocked_reason: BlockReason | None

    def __post_init__(self) -> None:
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise LifecycleStateError("ACTIVE configuration generation is invalid")
        if type(self.account_key) is not AccountKey:
            raise LifecycleStateError("ACTIVE account key is invalid")
        if type(self.slot_index) is not int or not 0 <= self.slot_index < 10:
            raise LifecycleStateError(
                "ACTIVE slot index must be an integer from zero to nine"
            )
        if type(self.run_nonce) is not str or _NONCE_PATTERN.fullmatch(
            self.run_nonce
        ) is None:
            raise LifecycleStateError(
                "ACTIVE nonce must be 32 lowercase hex characters"
            )
        if self.blocked_reason is not None and type(
            self.blocked_reason
        ) is not BlockReason:
            raise LifecycleStateError("ACTIVE blocked reason is invalid")

    @property
    def state_name(self) -> str:
        return "ACTIVE"


def encode_transition_guard_v2(
    configuration_generation: ConfigurationGeneration,
) -> bytes:
    """Bind an in-progress write to schema v2 and one configuration generation."""
    if type(configuration_generation) is not ConfigurationGeneration:
        raise LifecycleStateError(
            "schema-v2 transition guard configuration generation is invalid"
        )
    return (
        json.dumps(
            {
                "configuration_generation": configuration_generation.value,
                "schema": 2,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


class SchemaV2LifecycleStateStore:
    """Write-through lifecycle store bound to one immutable configuration."""

    def __init__(
        self,
        project_root: Path,
        configuration: SchemaV2Configuration,
        file_port: WriteThroughFilePort,
    ) -> None:
        if type(configuration) is not SchemaV2Configuration:
            raise LifecycleStateError("schema-v2 lifecycle configuration is invalid")
        try:
            project = Path(project_root).resolve(strict=True)
        except OSError as exc:
            raise LifecycleStateError("existing project root required") from exc
        if not project.is_dir():
            raise LifecycleStateError("project root must be a directory")

        candidate = project / "var"
        try:
            candidate.mkdir(exist_ok=True)
            root = candidate.resolve(strict=True)
            root.relative_to(project)
        except (OSError, ValueError) as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle path escaped project root"
            ) from exc
        if root != candidate:
            raise LifecycleStateError("schema-v2 lifecycle path escaped project root")

        self._configuration = configuration
        self._account_indexes = {
            account.key: index for index, account in enumerate(configuration.accounts)
        }
        self._port = file_port
        generation = configuration.generation.value
        self._path = root / f"lifecycle-state-v2.{generation}.json"
        self._transition = root / f".lifecycle-state-v2.{generation}.transition"
        self._guard_payload = encode_transition_guard_v2(configuration.generation)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def transition_path(self) -> Path:
        return self._transition

    def _write_new_file(self, path: Path, payload: bytes) -> None:
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
                    raise LifecycleStateError(
                        "schema-v2 lifecycle state write was incomplete"
                    )
                offset += written
            self._port.flush(writer)
        finally:
            self._port.close(writer)

    def _read_transition_guard(self) -> bytes | None:
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._transition)
        except FileNotFoundError:
            return None
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard is unprovable"
            ) from exc
        try:
            payload = self._port.read_bounded(reader, 129)
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard is unprovable"
            ) from exc
        finally:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise LifecycleStateError(
                    "schema-v2 lifecycle transition guard could not close"
                ) from exc
        return payload

    def _require_no_transition(self) -> None:
        payload = self._read_transition_guard()
        if payload is None:
            return
        if type(payload) is not bytes or payload != self._guard_payload:
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard mismatch"
            )
        raise LifecycleStateError(
            "incomplete schema-v2 lifecycle transition requires reconciliation"
        )

    def _restore_transition_guard(self) -> None:
        try:
            self._write_new_file(self._transition, self._guard_payload)
        except FileExistsError:
            return
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard could not be preserved"
            ) from exc

    def _finalize_transition_guard(self) -> None:
        deletion_error: BaseException | None = None
        try:
            self._port.delete_file_no_reparse(self._transition)
        except BaseException as exc:
            deletion_error = exc

        try:
            payload = self._read_transition_guard()
        except BaseException as exc:
            self._restore_transition_guard()
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard finalization is unprovable"
            ) from exc

        if payload is None:
            return
        if type(payload) is not bytes or payload != self._guard_payload:
            raise LifecycleStateError(
                "schema-v2 lifecycle transition guard mismatch"
            )
        raise LifecycleStateError(
            "incomplete schema-v2 lifecycle transition requires reconciliation"
        ) from deletion_error

    def _validate_state(
        self,
        state: ReadyV2 | ActiveV2,
        *,
        allow_seed_cursor: bool,
    ) -> None:
        if type(state) not in (ReadyV2, ActiveV2):
            raise LifecycleStateError("unsupported schema-v2 lifecycle state")
        if state.configuration_generation != self._configuration.generation:
            raise LifecycleStateError(
                "schema-v2 lifecycle configuration generation mismatch"
            )
        if type(state) is ReadyV2:
            if state.tie_after_account_key is None:
                if not allow_seed_cursor:
                    raise LifecycleStateError(
                        "null READY cursor is valid only for a new lifecycle seed"
                    )
                return
            if state.tie_after_account_key not in self._account_indexes:
                raise LifecycleStateError("schema-v2 READY account key mismatch")
            return
        expected_index = self._account_indexes.get(state.account_key)
        if expected_index is None:
            raise LifecycleStateError("schema-v2 ACTIVE account key mismatch")
        if state.slot_index != expected_index:
            raise LifecycleStateError("schema-v2 ACTIVE slot index mismatch")

    def seed_ready(self, tie_after_account_key: AccountKey | None) -> ReadyV2:
        """Create the initial READY state only when this generation is unstaged."""
        state = ReadyV2(self._configuration.generation, tie_after_account_key)
        self._validate_state(state, allow_seed_cursor=True)
        self._require_no_transition()
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._path)
        except FileNotFoundError:
            return self._commit(state, allow_seed_cursor=True)
        except BaseException as exc:
            raise LifecycleStateError(
                "existing schema-v2 lifecycle state is unprovable"
            ) from exc
        try:
            self._port.close(reader)
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle state handle could not close"
            ) from exc
        raise LifecycleStateError("schema-v2 lifecycle state already exists")

    def load(self) -> ReadyV2 | ActiveV2:
        self._require_no_transition()
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._path)
        except FileNotFoundError as exc:
            raise LifecycleStateError("schema-v2 lifecycle state is missing") from exc
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle state could not be opened"
            ) from exc
        try:
            payload = self._port.read_bounded(reader, 513)
        except BaseException as exc:
            raise LifecycleStateError(
                "schema-v2 lifecycle state could not be read"
            ) from exc
        finally:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise LifecycleStateError(
                    "schema-v2 lifecycle state handle could not close"
                ) from exc
        state = decode_state_v2(payload)
        self._validate_state(state, allow_seed_cursor=True)
        return state

    def commit(self, state: ReadyV2 | ActiveV2) -> ReadyV2 | ActiveV2:
        return self._commit(state, allow_seed_cursor=False)

    def _commit(
        self,
        state: ReadyV2 | ActiveV2,
        *,
        allow_seed_cursor: bool,
    ) -> ReadyV2 | ActiveV2:
        self._validate_state(state, allow_seed_cursor=allow_seed_cursor)
        if not allow_seed_cursor:
            self.load()
        self._require_no_transition()
        payload = encode_state_v2(state)
        temporary = self._path.with_name(
            f".{self._path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            self._write_new_file(self._transition, self._guard_payload)
            self._write_new_file(temporary, payload)
            self._port.replace_write_through(temporary, self._path)
            reader = self._port.open_read_exclusive_no_reparse(self._path)
            try:
                read_back = self._port.read_bounded(reader, 513)
            finally:
                self._port.close(reader)
            if type(read_back) is not bytes or read_back != payload:
                raise LifecycleStateError(
                    "schema-v2 lifecycle state read-back mismatch"
                )
            parsed = decode_state_v2(read_back)
            self._validate_state(parsed, allow_seed_cursor=allow_seed_cursor)
            if type(parsed) is not type(state) or parsed != state:
                raise LifecycleStateError(
                    "schema-v2 lifecycle state read-back changed type or value"
                )
            self._finalize_transition_guard()
            return parsed
        except BaseException as exc:
            if isinstance(exc, LifecycleStateError):
                raise
            raise LifecycleStateError("schema-v2 lifecycle state commit failed") from exc


def encode_state_v2(state: ReadyV2 | ActiveV2) -> bytes:
    """Encode one schema-v2 lifecycle state canonically."""
    if type(state) is ReadyV2:
        raw = {
            "configuration_generation": state.configuration_generation.value,
            "schema": 2,
            "state": "READY",
            "tie_after_account_key": (
                None
                if state.tie_after_account_key is None
                else state.tie_after_account_key.value
            ),
        }
    elif type(state) is ActiveV2:
        raw = {
            "account_key": state.account_key.value,
            "blocked_reason": (
                None if state.blocked_reason is None else state.blocked_reason.value
            ),
            "configuration_generation": state.configuration_generation.value,
            "run_nonce": state.run_nonce,
            "schema": 2,
            "slot_index": state.slot_index,
            "state": "ACTIVE",
        }
    else:
        raise LifecycleStateError("unsupported schema-v2 lifecycle state")
    return (
        json.dumps(raw, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )


def decode_state_v2(payload: bytes) -> ReadyV2 | ActiveV2:
    """Decode only an exact canonical schema-v2 lifecycle payload."""
    if type(payload) is not bytes or len(payload) > 512:
        raise LifecycleStateError("schema-v2 lifecycle state payload is invalid")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleStateError(
            "schema-v2 lifecycle state payload is unreadable"
        ) from exc
    if type(raw) is not dict:
        raise LifecycleStateError("schema-v2 lifecycle state object is invalid")
    if type(raw.get("schema")) is not int or raw.get("schema") != 2:
        raise LifecycleStateError("schema-v2 lifecycle state schema is invalid")

    try:
        generation = ConfigurationGeneration(raw.get("configuration_generation"))
        if raw.get("state") == "READY":
            if set(raw) != {
                "configuration_generation",
                "schema",
                "state",
                "tie_after_account_key",
            }:
                raise LifecycleStateError("schema-v2 READY fields are invalid")
            tie_value = raw["tie_after_account_key"]
            state: ReadyV2 | ActiveV2 = ReadyV2(
                generation,
                None if tie_value is None else AccountKey(tie_value),
            )
        elif raw.get("state") == "ACTIVE":
            if set(raw) != {
                "account_key",
                "blocked_reason",
                "configuration_generation",
                "run_nonce",
                "schema",
                "slot_index",
                "state",
            }:
                raise LifecycleStateError("schema-v2 ACTIVE fields are invalid")
            reason = raw["blocked_reason"]
            try:
                parsed_reason = None if reason is None else BlockReason(reason)
            except (TypeError, ValueError) as exc:
                raise LifecycleStateError(
                    "schema-v2 ACTIVE blocked reason is invalid"
                ) from exc
            state = ActiveV2(
                generation,
                AccountKey(raw["account_key"]),
                raw["slot_index"],
                raw["run_nonce"],
                parsed_reason,
            )
        else:
            raise LifecycleStateError("schema-v2 lifecycle state kind is invalid")
    except ConfigurationValidationError as exc:
        raise LifecycleStateError("schema-v2 lifecycle identity is invalid") from exc

    if encode_state_v2(state) != payload:
        raise LifecycleStateError("schema-v2 lifecycle state encoding is not canonical")
    return state
