"""Pure frozen protocol values for central-service runner control.

This module owns no transport, Discord, scheduler, lifecycle, gameplay, input,
resource-authority, or spending capability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

from .configuration_v2 import ConfigurationGeneration


PROTOCOL_VERSION = 1
MAX_COMMAND_LIFETIME_SECONDS = 300
_MAX_SIGNED_64 = 2**63 - 1
_OPAQUE_128 = re.compile(r"[0-9a-f]{32}")
_TEAM_KEY = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_MAX_WIRE_BYTES = 4096


class ProtocolValidationError(ValueError):
    """A value is outside the frozen central-runner protocol."""


_Result = TypeVar("_Result")


def _sanitized_call(operation: Callable[[], _Result], message: str) -> _Result:
    try:
        return operation()
    except Exception:
        pass
    raise ProtocolValidationError(message)


@dataclass(frozen=True, slots=True)
class CommandId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _OPAQUE_128.fullmatch(self.value) is None:
            raise ProtocolValidationError(
                "command ID must be exactly 32 lowercase hex characters"
            )


@dataclass(frozen=True, slots=True)
class TeamKey:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _TEAM_KEY.fullmatch(self.value) is None:
            raise ProtocolValidationError("team key must be an exact lower-ASCII slug")


@dataclass(frozen=True, slots=True)
class IssuerRef:
    """Opaque central-service issuer identity; never a raw Discord user ID."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _OPAQUE_128.fullmatch(self.value) is None:
            raise ProtocolValidationError(
                "issuer reference must be exactly 32 lowercase hex characters"
            )


@dataclass(frozen=True, slots=True)
class ControlEpoch:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 1 <= self.value <= _MAX_SIGNED_64:
            raise ProtocolValidationError(
                "control epoch must be a positive signed 64-bit integer"
            )


class DesiredControlState(Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class AcknowledgementDisposition(Enum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class AcknowledgementReason(Enum):
    APPLIED = "APPLIED"
    COMMAND_CONFLICT = "COMMAND_CONFLICT"
    TEAM_MISMATCH = "TEAM_MISMATCH"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"
    STALE_EPOCH = "STALE_EPOCH"


class DeliveryAction(Enum):
    """Required runner action after evaluating one authenticated delivery."""

    PERSIST_AND_ACK = "PERSIST_AND_ACK"
    REPLAY_ACK = "REPLAY_ACK"
    REJECT = "REJECT"


class RegistryDecision(Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class ControlCommand:
    command_id: CommandId
    team_key: TeamKey
    issuer: IssuerRef
    desired_state: DesiredControlState
    issued_at: int
    expires_at: int
    control_epoch: ControlEpoch
    configuration_generation: ConfigurationGeneration

    def __post_init__(self) -> None:
        if type(self.command_id) is not CommandId:
            raise ProtocolValidationError("command ID is invalid")
        if type(self.team_key) is not TeamKey:
            raise ProtocolValidationError("team key is invalid")
        if type(self.issuer) is not IssuerRef:
            raise ProtocolValidationError("issuer reference is invalid")
        if type(self.desired_state) is not DesiredControlState:
            raise ProtocolValidationError("desired state is invalid")
        if type(self.issued_at) is not int or not 0 <= self.issued_at <= _MAX_SIGNED_64:
            raise ProtocolValidationError("issued time is invalid")
        if type(self.expires_at) is not int or not 0 <= self.expires_at <= _MAX_SIGNED_64:
            raise ProtocolValidationError("expiry time is invalid")
        if not self.issued_at < self.expires_at:
            raise ProtocolValidationError("expiry must follow issue time")
        if self.expires_at - self.issued_at > MAX_COMMAND_LIFETIME_SECONDS:
            raise ProtocolValidationError("command lifetime exceeds five minutes")
        if type(self.control_epoch) is not ControlEpoch:
            raise ProtocolValidationError("control epoch is invalid")
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise ProtocolValidationError("configuration generation is invalid")


@dataclass(frozen=True, slots=True)
class CommandAcknowledgement:
    command_id: CommandId
    team_key: TeamKey
    issuer: IssuerRef
    desired_state: DesiredControlState
    issued_at: int
    expires_at: int
    control_epoch: ControlEpoch
    configuration_generation: ConfigurationGeneration
    disposition: AcknowledgementDisposition
    reason: AcknowledgementReason

    def __post_init__(self) -> None:
        if type(self.command_id) is not CommandId:
            raise ProtocolValidationError("acknowledgement command ID is invalid")
        if type(self.team_key) is not TeamKey:
            raise ProtocolValidationError("acknowledgement team key is invalid")
        if type(self.issuer) is not IssuerRef:
            raise ProtocolValidationError("acknowledgement issuer is invalid")
        if type(self.desired_state) is not DesiredControlState:
            raise ProtocolValidationError("acknowledgement desired state is invalid")
        if type(self.issued_at) is not int or not 0 <= self.issued_at <= _MAX_SIGNED_64:
            raise ProtocolValidationError("acknowledgement issue time is invalid")
        if type(self.expires_at) is not int or not 0 <= self.expires_at <= _MAX_SIGNED_64:
            raise ProtocolValidationError("acknowledgement expiry time is invalid")
        if (
            not self.issued_at < self.expires_at
            or self.expires_at - self.issued_at > MAX_COMMAND_LIFETIME_SECONDS
        ):
            raise ProtocolValidationError("acknowledgement expiry is invalid")
        if type(self.control_epoch) is not ControlEpoch:
            raise ProtocolValidationError("acknowledgement control epoch is invalid")
        if type(self.configuration_generation) is not ConfigurationGeneration:
            raise ProtocolValidationError(
                "acknowledgement configuration generation is invalid"
            )
        if type(self.disposition) is not AcknowledgementDisposition:
            raise ProtocolValidationError("acknowledgement disposition is invalid")
        if type(self.reason) is not AcknowledgementReason:
            raise ProtocolValidationError("acknowledgement reason is invalid")
        if (self.disposition is AcknowledgementDisposition.APPLIED) != (
            self.reason is AcknowledgementReason.APPLIED
        ):
            raise ProtocolValidationError(
                "acknowledgement disposition and reason disagree"
            )


def _acknowledge(
    command: ControlCommand,
    disposition: AcknowledgementDisposition,
    reason: AcknowledgementReason,
) -> CommandAcknowledgement:
    return CommandAcknowledgement(
        command_id=CommandId(command.command_id.value),
        team_key=TeamKey(command.team_key.value),
        issuer=IssuerRef(command.issuer.value),
        desired_state=command.desired_state,
        issued_at=command.issued_at,
        expires_at=command.expires_at,
        control_epoch=ControlEpoch(command.control_epoch.value),
        configuration_generation=ConfigurationGeneration(
            command.configuration_generation.value
        ),
        disposition=disposition,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class AppliedControl:
    """The one durable desired-state record owned by a team runner."""

    command: ControlCommand
    acknowledgement: CommandAcknowledgement

    def __post_init__(self) -> None:
        valid = False
        try:
            if type(self.command) is not ControlCommand:
                raise ValueError
            if type(self.acknowledgement) is not CommandAcknowledgement:
                raise ValueError
            command_copy = decode_command(encode_command(self.command))
            acknowledgement_copy = decode_acknowledgement(
                encode_acknowledgement(self.acknowledgement)
            )
            expected = _acknowledge(
                command_copy,
                AcknowledgementDisposition.APPLIED,
                AcknowledgementReason.APPLIED,
            )
            if encode_acknowledgement(acknowledgement_copy) != encode_acknowledgement(
                expected
            ):
                raise ValueError
            valid = True
        except Exception:
            pass
        if not valid:
            raise ProtocolValidationError(
                "applied acknowledgement does not match its exact command"
            )

    @property
    def control_epoch(self) -> ControlEpoch:
        return self.command.control_epoch


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    action: DeliveryAction
    acknowledgement: CommandAcknowledgement
    replacement: AppliedControl | None

    def __post_init__(self) -> None:
        if type(self.action) is not DeliveryAction:
            raise ProtocolValidationError("delivery action is invalid")
        if type(self.acknowledgement) is not CommandAcknowledgement:
            raise ProtocolValidationError("delivery acknowledgement is invalid")
        if self.replacement is not None and type(self.replacement) is not AppliedControl:
            raise ProtocolValidationError("delivery replacement is invalid")
        structurally_valid = False
        try:
            decode_acknowledgement(encode_acknowledgement(self.acknowledgement))
            if self.replacement is not None:
                AppliedControl(
                    self.replacement.command,
                    self.replacement.acknowledgement,
                )
            structurally_valid = True
        except Exception:
            pass
        if not structurally_valid:
            raise ProtocolValidationError("delivery result is structurally malformed")
        if self.action is DeliveryAction.PERSIST_AND_ACK:
            try:
                matches = self.replacement is not None and encode_acknowledgement(
                    self.acknowledgement
                ) == encode_acknowledgement(self.replacement.acknowledgement)
            except Exception:
                matches = False
            if not matches:
                raise ProtocolValidationError(
                    "persist action requires one matching applied record"
                )
        elif self.action is DeliveryAction.REPLAY_ACK:
            if (
                self.replacement is not None
                or self.acknowledgement.disposition
                is not AcknowledgementDisposition.APPLIED
            ):
                raise ProtocolValidationError(
                    "replay action requires an applied acknowledgement and no replacement"
                )
        elif (
            self.replacement is not None
            or self.acknowledgement.disposition
            is not AcknowledgementDisposition.REJECTED
        ):
            raise ProtocolValidationError(
                "rejected delivery requires a rejected acknowledgement and no replacement"
            )


def _rejected(
    command: ControlCommand, reason: AcknowledgementReason
) -> DeliveryResult:
    return DeliveryResult(
        action=DeliveryAction.REJECT,
        acknowledgement=_acknowledge(
            command, AcknowledgementDisposition.REJECTED, reason
        ),
        replacement=None,
    )


def evaluate_delivery(
    command: ControlCommand,
    *,
    current: AppliedControl | None,
    active_team: TeamKey,
    active_generation: ConfigurationGeneration,
    now: int,
) -> DeliveryResult:
    """Evaluate one authenticated command without performing any side effect.

    ``PERSIST_AND_ACK`` means the caller must atomically persist ``replacement``
    as the runner's desired state before transmitting its terminal acknowledgement.
    Persisting the desired state is the whole protocol-level application; later
    scheduler/lifecycle work remains under the local application service.
    """

    if type(command) is not ControlCommand:
        raise ProtocolValidationError("command must be an exact ControlCommand")
    if current is not None and type(current) is not AppliedControl:
        raise ProtocolValidationError("current control must be an exact AppliedControl")
    if type(active_team) is not TeamKey:
        raise ProtocolValidationError("active team must be an exact TeamKey")
    if type(active_generation) is not ConfigurationGeneration:
        raise ProtocolValidationError(
            "active generation must be an exact ConfigurationGeneration"
        )
    if type(now) is not int or not 0 <= now <= _MAX_SIGNED_64:
        raise ProtocolValidationError("current time must be a non-negative integer")

    command_copy = decode_command(encode_command(command))

    def copy_active_identity() -> tuple[TeamKey, ConfigurationGeneration]:
        return (
            TeamKey(active_team.value),
            ConfigurationGeneration(active_generation.value),
        )

    active_team_copy, active_generation_copy = _sanitized_call(
        copy_active_identity, "active identity is structurally malformed"
    )
    current_copy: AppliedControl | None = None
    if current is not None:

        def copy_current() -> AppliedControl:
            return AppliedControl(
                decode_command(encode_command(current.command)),
                decode_acknowledgement(
                    encode_acknowledgement(current.acknowledgement)
                ),
            )

        current_copy = _sanitized_call(
            copy_current, "current control is structurally malformed"
        )

    if current_copy is not None and command_copy.command_id == current_copy.command.command_id:
        if command_copy == current_copy.command:
            if command_copy.team_key != active_team_copy:
                return _rejected(command_copy, AcknowledgementReason.TEAM_MISMATCH)
            if command_copy.configuration_generation != active_generation_copy:
                return _rejected(
                    command_copy, AcknowledgementReason.CONFIGURATION_MISMATCH
                )
            return DeliveryResult(
                DeliveryAction.REPLAY_ACK,
                decode_acknowledgement(
                    encode_acknowledgement(current.acknowledgement)
                ),
                None,
            )
        return _rejected(command_copy, AcknowledgementReason.COMMAND_CONFLICT)
    if command_copy.team_key != active_team_copy:
        return _rejected(command_copy, AcknowledgementReason.TEAM_MISMATCH)
    if command_copy.configuration_generation != active_generation_copy:
        return _rejected(command_copy, AcknowledgementReason.CONFIGURATION_MISMATCH)
    if now < command_copy.issued_at:
        return _rejected(command_copy, AcknowledgementReason.NOT_YET_VALID)
    if now >= command_copy.expires_at:
        return _rejected(command_copy, AcknowledgementReason.EXPIRED)
    if (
        current_copy is not None
        and command_copy.control_epoch.value <= current_copy.control_epoch.value
    ):
        return _rejected(command_copy, AcknowledgementReason.STALE_EPOCH)

    acknowledgement = _acknowledge(
        command_copy,
        AcknowledgementDisposition.APPLIED,
        AcknowledgementReason.APPLIED,
    )
    replacement = AppliedControl(command_copy, acknowledgement)
    return DeliveryResult(
        DeliveryAction.PERSIST_AND_ACK,
        decode_acknowledgement(encode_acknowledgement(acknowledgement)),
        replacement,
    )


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def encode_command(command: ControlCommand) -> bytes:
    if type(command) is not ControlCommand:
        raise ProtocolValidationError("command must be an exact ControlCommand")

    def encode() -> bytes:
        return _canonical_bytes(
            {
                "command_id": command.command_id.value,
                "configuration_generation": command.configuration_generation.value,
                "control_epoch": command.control_epoch.value,
                "desired_state": command.desired_state.value,
                "expires_at": command.expires_at,
                "issued_at": command.issued_at,
                "issuer": command.issuer.value,
                "message_type": "CONTROL_COMMAND",
                "protocol_version": PROTOCOL_VERSION,
                "team_key": command.team_key.value,
            }
        )

    return _sanitized_call(encode, "command is structurally malformed")


def encode_acknowledgement(acknowledgement: CommandAcknowledgement) -> bytes:
    if type(acknowledgement) is not CommandAcknowledgement:
        raise ProtocolValidationError(
            "acknowledgement must be an exact CommandAcknowledgement"
        )
    def encode() -> bytes:
        return _canonical_bytes(
            {
                "command_id": acknowledgement.command_id.value,
                "configuration_generation": (
                    acknowledgement.configuration_generation.value
                ),
                "control_epoch": acknowledgement.control_epoch.value,
                "desired_state": acknowledgement.desired_state.value,
                "disposition": acknowledgement.disposition.value,
                "expires_at": acknowledgement.expires_at,
                "issued_at": acknowledgement.issued_at,
                "issuer": acknowledgement.issuer.value,
                "message_type": "COMMAND_ACKNOWLEDGEMENT",
                "protocol_version": PROTOCOL_VERSION,
                "reason": acknowledgement.reason.value,
                "team_key": acknowledgement.team_key.value,
            }
        )

    return _sanitized_call(encode, "acknowledgement is structurally malformed")


def _load_wire_object(payload: bytes, label: str) -> dict[str, object]:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def load() -> dict[str, object]:
        if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_WIRE_BYTES:
            raise ValueError
        decoded = payload.decode("ascii")
        value = json.loads(decoded, object_pairs_hook=reject_duplicate_pairs)
        if type(value) is not dict:
            raise ValueError
        return value

    return _sanitized_call(load, f"{label} payload is malformed")


def decode_command(payload: bytes) -> ControlCommand:
    value = _load_wire_object(payload, "command")
    expected_keys = {
        "command_id",
        "configuration_generation",
        "control_epoch",
        "desired_state",
        "expires_at",
        "issued_at",
        "issuer",
        "message_type",
        "protocol_version",
        "team_key",
    }
    def decode() -> ControlCommand:
        if set(value) != expected_keys:
            raise ValueError
        if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
            raise ValueError
        if type(value["message_type"]) is not str or value["message_type"] != "CONTROL_COMMAND":
            raise ValueError
        if type(value["desired_state"]) is not str:
            raise ValueError
        command = ControlCommand(
            command_id=CommandId(value["command_id"]),  # type: ignore[arg-type]
            team_key=TeamKey(value["team_key"]),  # type: ignore[arg-type]
            issuer=IssuerRef(value["issuer"]),  # type: ignore[arg-type]
            desired_state=DesiredControlState(value["desired_state"]),
            issued_at=value["issued_at"],  # type: ignore[arg-type]
            expires_at=value["expires_at"],  # type: ignore[arg-type]
            control_epoch=ControlEpoch(value["control_epoch"]),  # type: ignore[arg-type]
            configuration_generation=ConfigurationGeneration(
                value["configuration_generation"]  # type: ignore[arg-type]
            ),
        )
        if encode_command(command) != payload:
            raise ValueError
        return command

    return _sanitized_call(decode, "command payload is malformed")


def decode_acknowledgement(payload: bytes) -> CommandAcknowledgement:
    value = _load_wire_object(payload, "acknowledgement")
    expected_keys = {
        "command_id",
        "configuration_generation",
        "control_epoch",
        "desired_state",
        "disposition",
        "expires_at",
        "issued_at",
        "issuer",
        "message_type",
        "protocol_version",
        "reason",
        "team_key",
    }
    def decode() -> CommandAcknowledgement:
        if set(value) != expected_keys:
            raise ValueError
        if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
            raise ValueError
        if (
            type(value["message_type"]) is not str
            or value["message_type"] != "COMMAND_ACKNOWLEDGEMENT"
        ):
            raise ValueError
        enum_fields = ("desired_state", "disposition", "reason")
        if any(type(value[field]) is not str for field in enum_fields):
            raise ValueError
        acknowledgement = CommandAcknowledgement(
            command_id=CommandId(value["command_id"]),  # type: ignore[arg-type]
            team_key=TeamKey(value["team_key"]),  # type: ignore[arg-type]
            issuer=IssuerRef(value["issuer"]),  # type: ignore[arg-type]
            desired_state=DesiredControlState(value["desired_state"]),
            issued_at=value["issued_at"],  # type: ignore[arg-type]
            expires_at=value["expires_at"],  # type: ignore[arg-type]
            control_epoch=ControlEpoch(value["control_epoch"]),  # type: ignore[arg-type]
            configuration_generation=ConfigurationGeneration(
                value["configuration_generation"]  # type: ignore[arg-type]
            ),
            disposition=AcknowledgementDisposition(value["disposition"]),
            reason=AcknowledgementReason(value["reason"]),
        )
        if encode_acknowledgement(acknowledgement) != payload:
            raise ValueError
        return acknowledgement

    return _sanitized_call(decode, "acknowledgement payload is malformed")


def classify_registration(
    existing: ControlCommand | None, candidate: ControlCommand
) -> RegistryDecision:
    """Enforce immutable command IDs at the central durable registry boundary."""

    if type(candidate) is not ControlCommand:
        raise ProtocolValidationError("candidate must be an exact ControlCommand")
    if existing is not None and type(existing) is not ControlCommand:
        raise ProtocolValidationError("existing command must be exact or None")
    candidate_copy = decode_command(encode_command(candidate))
    if existing is None:
        return RegistryDecision.NEW
    existing_copy = decode_command(encode_command(existing))
    if existing_copy.command_id != candidate_copy.command_id:
        return RegistryDecision.NEW
    if existing_copy == candidate_copy:
        return RegistryDecision.DUPLICATE
    raise ProtocolValidationError("immutable command ID was reused with changed fields")
