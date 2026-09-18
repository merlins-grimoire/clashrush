"""Fail-closed local control-plane-only MVP.

This module owns only opaque setup, stopped lifecycle state, and read-only slot
selection. It deliberately has no runtime ports for capture, processes, input,
credentials, networking, Discord, spending, or gameplay.
"""

from __future__ import annotations

import json
import os
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
from .lifecycle_state import LifecycleStateError
from .lifecycle_state_v2 import ReadyV2, decode_state_v2, encode_state_v2


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


class ControlPlane:
    """Persist and inspect stopped scheduling state without runtime authority."""

    def __init__(self, project_root: Path) -> None:
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
        self._configuration_path = var / "control-plane-config.json"
        self._state_path = var / "control-plane-state.json"

    @staticmethod
    def _canonical(payload: dict[str, object]) -> bytes:
        return (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            .encode("ascii")
            + b"\n"
        )

    @staticmethod
    def _exclusive_write(path: Path, payload: bytes) -> None:
        try:
            with path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise ControlPlaneError("control plane is already configured") from exc
        try:
            if path.read_bytes() != payload:
                raise ControlPlaneError("control-plane write read-back mismatch")
        except OSError as exc:
            raise ControlPlaneError("control-plane write read-back failed") from exc

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
        state = encode_state_v2(ready)
        self._exclusive_write(self._configuration_path, configuration)
        try:
            self._exclusive_write(self._state_path, state)
        except BaseException:
            try:
                self._configuration_path.unlink()
            except OSError:
                pass
            raise
        return ConfiguredControlPlane(
            ControlPlaneMode.STOPPED, configuration_value.cardinality
        )

    @staticmethod
    def _load_canonical(path: Path) -> dict[str, object]:
        try:
            payload = path.read_bytes()
            raw = json.loads(payload.decode("ascii"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ControlPlaneError("control-plane state is unavailable") from exc
        if type(raw) is not dict or ControlPlane._canonical(raw) != payload:
            raise ControlPlaneError("control-plane state is malformed")
        return raw

    def _load(self) -> tuple[tuple[str, ...], int]:
        configuration = self._load_canonical(self._configuration_path)
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
            ready = decode_state_v2(self._state_path.read_bytes())
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
            OSError,
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
