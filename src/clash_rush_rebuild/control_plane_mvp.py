"""Fail-closed local control-plane-only MVP.

This module owns only opaque setup, stopped lifecycle state, and read-only slot
selection. It deliberately has no runtime ports for capture, processes, input,
credentials, networking, Discord, spending, or gameplay.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationGeneration,
    ConfigurationValidationError,
    SchemaV2Configuration,
)
from .lifecycle_state import LifecycleStateError, WriteThroughFilePort
from .lifecycle_state_v2 import ReadyV2, SchemaV2LifecycleStateStore
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort


class ControlPlaneError(RuntimeError):
    """Control-plane state is missing, malformed, or unsafe."""


class CapabilityUnavailable(ControlPlaneError):
    """A deliberately excluded runtime capability was requested."""


class ControlPlaneMode(StrEnum):
    STOPPED = "STOPPED"


class ControlPlaneCapability(StrEnum):
    CAPTURE = "capture"
    IMAGE_PERSISTENCE = "image_persistence"
    READINESS = "readiness"
    HOME_CLASSIFICATION = "home_classification"
    BLUESTACKS_EXECUTION = "bluestacks_execution"
    GAMEPLAY_INPUT = "gameplay_input"
    GAMEPLAY_ACTION = "gameplay_action"
    SPENDING = "spending"
    NETWORK = "network"
    DISCORD = "discord"
    CREDENTIAL_ACCESS = "credential_access"


@dataclass(frozen=True, slots=True)
class ConfiguredControlPlane:
    mode: ControlPlaneMode
    account_count: int


@dataclass(frozen=True, slots=True)
class ControlPlaneStatus:
    mode: ControlPlaneMode
    lifecycle: str
    next_slot: int
    account_count: int
    capabilities: tuple[tuple[ControlPlaneCapability, str], ...]


@dataclass(frozen=True, slots=True)
class ScheduledSlot:
    slot_index: int
    account_key: str
    dispatch_allowed: bool


_SETUP_GUARD = b'{"schema":2,"transaction":"control-plane-setup"}\n'


class _ProtectedControlPlaneStore:
    """Protect configuration activation around the reviewed lifecycle store."""

    def __init__(self, root: Path, file_port: WriteThroughFilePort) -> None:
        self._port = file_port
        self.configuration_path = root / "var" / "control-plane-config.json"
        self.transition_path = root / "var" / ".control-plane-setup.transition"

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
                    raise ControlPlaneError("control-plane write was incomplete")
                offset += written
            self._port.flush(writer)
        finally:
            self._port.close(writer)

    def _read_optional(self, path: Path, limit: int) -> bytes | None:
        try:
            reader = self._port.open_read_exclusive_no_reparse(path)
        except FileNotFoundError:
            return None
        except BaseException as exc:
            raise ControlPlaneError("control-plane state could not be opened") from exc
        try:
            return self._port.read_bounded(reader, limit)
        except BaseException as exc:
            raise ControlPlaneError("control-plane state could not be read") from exc
        finally:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise ControlPlaneError("control-plane state handle could not close") from exc

    def require_no_transition(self) -> None:
        guard = self._read_optional(self.transition_path, 129)
        if guard is None:
            return
        if type(guard) is not bytes or guard != _SETUP_GUARD:
            raise ControlPlaneError("control-plane setup guard is malformed")
        raise ControlPlaneError("incomplete control-plane setup requires reconciliation")

    def begin_setup(self) -> None:
        try:
            self._write_new(self.transition_path, _SETUP_GUARD)
        except FileExistsError as exc:
            raise ControlPlaneError(
                "incomplete or concurrent control-plane setup"
            ) from exc

    def configuration_exists(self) -> bool:
        return self._read_optional(self.configuration_path, 4097) is not None

    def write_configuration(self, payload: bytes) -> None:
        temporary = self.configuration_path.with_name(
            f".{self.configuration_path.name}.{secrets.token_hex(8)}.tmp"
        )
        self._write_new(temporary, payload)
        self._port.replace_write_through(temporary, self.configuration_path)
        read_back = self._read_optional(self.configuration_path, 4097)
        if type(read_back) is not bytes or read_back != payload:
            raise ControlPlaneError("control-plane configuration read-back mismatch")

    def load_configuration(self) -> bytes:
        self.require_no_transition()
        payload = self._read_optional(self.configuration_path, 4097)
        if payload is None:
            raise ControlPlaneError("control-plane configuration is missing")
        return payload

    def finalize_setup(self) -> None:
        deletion_error: BaseException | None = None
        try:
            self._port.delete_file_no_reparse(self.transition_path)
        except BaseException as exc:
            deletion_error = exc
        try:
            remaining = self._read_optional(self.transition_path, 129)
        except BaseException as exc:
            try:
                self._write_new(self.transition_path, _SETUP_GUARD)
            except FileExistsError:
                pass
            except BaseException as restore_error:
                raise ControlPlaneError(
                    "control-plane setup guard could not be preserved"
                ) from restore_error
            raise ControlPlaneError(
                "control-plane setup guard finalization is unprovable"
            ) from exc
        if remaining is None:
            return
        raise ControlPlaneError(
            "incomplete control-plane setup requires reconciliation"
        ) from deletion_error


class ControlPlane:
    """Persist and inspect stopped scheduling state without runtime authority."""

    def __init__(
        self,
        project_root: Path,
        *,
        file_port: WriteThroughFilePort | None = None,
    ) -> None:
        if not isinstance(project_root, Path):
            raise ControlPlaneError("project root must be an exact Path")
        try:
            root = project_root.resolve(strict=True)
        except OSError as exc:
            raise ControlPlaneError("project root is unavailable") from exc
        if not root.is_dir() or root != project_root:
            raise ControlPlaneError("project root must be canonical")
        var = root / "var"
        try:
            var.mkdir(exist_ok=True)
            resolved = var.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ControlPlaneError("control-plane path escaped project root") from exc
        if resolved != var or var.is_symlink():
            raise ControlPlaneError("control-plane path escaped project root")
        try:
            port = (
                Win32StateFilePort(root, NativeWin32StateApi())
                if file_port is None
                else file_port
            )
        except BaseException as exc:
            raise ControlPlaneError("protected control-plane storage is unavailable") from exc
        self._root = root
        self._file_port = port
        self._store = _ProtectedControlPlaneStore(root, port)

    @staticmethod
    def _canonical(payload: dict[str, object]) -> bytes:
        return (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            .encode("ascii")
            + b"\n"
        )

    def setup(
        self, account_keys: tuple[str, ...], *, generation: str
    ) -> ConfiguredControlPlane:
        if type(account_keys) is not tuple:
            raise ControlPlaneError("control-plane configuration is invalid")
        try:
            configuration_value = SchemaV2Configuration(
                ConfigurationGeneration(generation),
                tuple(AccountRecord(AccountKey(key)) for key in account_keys),
            )
            ready = ReadyV2(configuration_value.generation, None)
        except (ConfigurationValidationError, LifecycleStateError, TypeError) as exc:
            raise ControlPlaneError("control-plane configuration is invalid") from exc
        configuration = self._canonical(
            {
                "accounts": [account.key.value for account in configuration_value.accounts],
                "generation": configuration_value.generation.value,
                "schema": 2,
            }
        )
        try:
            self._store.begin_setup()
            if self._store.configuration_exists():
                self._store.finalize_setup()
                raise ControlPlaneError("control plane is already configured")
            self._store.write_configuration(configuration)
            SchemaV2LifecycleStateStore(
                self._root, configuration_value, self._file_port
            ).seed_ready(ready.tie_after_account_key)
            self._store.finalize_setup()
        except ControlPlaneError:
            raise
        except BaseException as exc:
            raise ControlPlaneError("control-plane setup failed") from exc
        return ConfiguredControlPlane(
            ControlPlaneMode.STOPPED, configuration_value.cardinality
        )

    @staticmethod
    def _load_canonical(payload: bytes) -> dict[str, object]:
        try:
            raw = json.loads(payload.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ControlPlaneError("control-plane state is unavailable") from exc
        if type(raw) is not dict or ControlPlane._canonical(raw) != payload:
            raise ControlPlaneError("control-plane state is malformed")
        return raw

    def _load(self) -> tuple[tuple[str, ...], int]:
        configuration = self._load_canonical(self._store.load_configuration())
        if set(configuration) != {"accounts", "generation", "schema"}:
            raise ControlPlaneError("control-plane configuration is malformed")
        accounts = configuration["accounts"]
        generation = configuration["generation"]
        if type(configuration["schema"]) is not int or configuration["schema"] != 2:
            raise ControlPlaneError("control-plane state is malformed")
        try:
            if type(accounts) is not list:
                raise TypeError
            configured = SchemaV2Configuration(
                ConfigurationGeneration(generation),
                tuple(AccountRecord(AccountKey(key)) for key in accounts),
            )
            ready = SchemaV2LifecycleStateStore(
                self._root, configured, self._file_port
            ).load()
            if (
                type(ready) is not ReadyV2
                or ready.configuration_generation != configured.generation
                or (
                    ready.tie_after_account_key is not None
                    and ready.tie_after_account_key
                    not in tuple(account.key for account in configured.accounts)
                )
            ):
                raise LifecycleStateError("control-plane lifecycle is not stopped")
        except (
            ConfigurationValidationError,
            LifecycleStateError,
            TypeError,
        ) as exc:
            raise ControlPlaneError("control-plane state is malformed") from exc
        keys = tuple(account.key.value for account in configured.accounts)
        next_slot = (
            0
            if ready.tie_after_account_key is None
            else (keys.index(ready.tie_after_account_key.value) + 1) % len(keys)
        )
        return keys, next_slot

    def status(self) -> ControlPlaneStatus:
        accounts, next_slot = self._load()
        return ControlPlaneStatus(
            ControlPlaneMode.STOPPED,
            "READY",
            next_slot,
            len(accounts),
            tuple((capability, "UNAVAILABLE") for capability in ControlPlaneCapability),
        )

    def schedule_next(self) -> ScheduledSlot:
        accounts, next_slot = self._load()
        return ScheduledSlot(next_slot, accounts[next_slot], False)

    def dispatch(self, selection: ScheduledSlot) -> None:
        if type(selection) is not ScheduledSlot:
            raise CapabilityUnavailable("GAMEPLAY_UNAVAILABLE")
        raise CapabilityUnavailable("GAMEPLAY_UNAVAILABLE")


__all__ = [
    "CapabilityUnavailable",
    "ConfiguredControlPlane",
    "ControlPlane",
    "ControlPlaneCapability",
    "ControlPlaneError",
    "ControlPlaneMode",
    "ControlPlaneStatus",
    "ScheduledSlot",
]
