"""Authenticated Team routing, heartbeat freshness, and command persistence.

This in-memory reference core composes the frozen DIS1 protocol with the DIS2
service-owned authenticated session boundary.  It owns no network, Discord,
scheduler, lifecycle, gameplay, input, spending, or configuration capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

from .configuration_v2 import ConfigurationGeneration
from .discord_control_protocol import (
    AppliedControl,
    CommandAcknowledgement,
    CommandId,
    ControlCommand,
    DeliveryAction,
    RegistryDecision,
    TeamKey,
    decode_acknowledgement,
    decode_command,
    encode_acknowledgement,
    encode_command,
    evaluate_delivery,
)
from .runner_enrollment import (
    AuthenticatedRunner,
    EnrollmentAuthority,
    TransportBinding,
)


_MAX_SIGNED_64 = 2**63 - 1
_MAX_HEARTBEAT_TIMEOUT_SECONDS = 300


class TeamRoutingError(ValueError):
    """Authenticated routing or persistence failed closed."""


class AcknowledgementDecision(Enum):
    RECORDED = "RECORDED"
    DUPLICATE = "DUPLICATE"


_Result = TypeVar("_Result")


def _sanitized(operation: Callable[[], _Result], message: str) -> _Result:
    try:
        return operation()
    except Exception:
        pass
    raise TeamRoutingError(message)


def _valid_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SIGNED_64


def _copy_team(value: object) -> TeamKey:
    def copy() -> TeamKey:
        if type(value) is not TeamKey or type(value.value) is not str:
            raise ValueError
        return TeamKey(value.value)

    return _sanitized(copy, "Team route is invalid")


def _copy_generation(value: object) -> ConfigurationGeneration:
    def copy() -> ConfigurationGeneration:
        if type(value) is not ConfigurationGeneration or type(value.value) is not str:
            raise ValueError
        return ConfigurationGeneration(value.value)

    return _sanitized(copy, "configuration generation is invalid")


@dataclass(frozen=True, slots=True)
class TeamRoute:
    team_key: TeamKey
    configuration_generation: ConfigurationGeneration

    def __post_init__(self) -> None:
        _copy_team(self.team_key)
        _copy_generation(self.configuration_generation)


@dataclass(frozen=True, slots=True)
class ConnectionHeartbeat:
    configuration_generation: ConfigurationGeneration
    protocol_version: int = 1

    def __post_init__(self) -> None:
        _copy_generation(self.configuration_generation)
        if (
            type(self.protocol_version) is not int
            or not 0 <= self.protocol_version <= _MAX_SIGNED_64
        ):
            raise TeamRoutingError("heartbeat protocol version is invalid")


@dataclass(slots=True)
class _ConnectionRecord:
    runner: AuthenticatedRunner
    team_key: TeamKey
    connection_epoch: int
    heartbeat_received_at: int | None = None
    heartbeat_generation: ConfigurationGeneration | None = None


@dataclass(slots=True)
class _CentralCommandRecord:
    command: ControlCommand
    payload: bytes
    terminal_acknowledgement: bytes | None = None


def _copy_route(value: object) -> TeamRoute:
    def copy() -> TeamRoute:
        if type(value) is not TeamRoute:
            raise ValueError
        return TeamRoute(
            _copy_team(value.team_key),
            _copy_generation(value.configuration_generation),
        )

    return _sanitized(copy, "Team route is invalid")


def _copy_heartbeat(value: object) -> ConnectionHeartbeat:
    def copy() -> ConnectionHeartbeat:
        if type(value) is not ConnectionHeartbeat:
            raise ValueError
        return ConnectionHeartbeat(
            _copy_generation(value.configuration_generation),
            value.protocol_version,
        )

    return _sanitized(copy, "heartbeat is invalid")


def _decode_command(payload: object) -> tuple[ControlCommand, bytes]:
    def decode() -> tuple[ControlCommand, bytes]:
        if type(payload) is not bytes:
            raise ValueError
        command = decode_command(payload)
        canonical = encode_command(command)
        if canonical != payload:
            raise ValueError
        return command, bytes(canonical)

    return _sanitized(decode, "command payload is invalid")


def _decode_acknowledgement(payload: object) -> tuple[CommandAcknowledgement, bytes]:
    def decode() -> tuple[CommandAcknowledgement, bytes]:
        if type(payload) is not bytes:
            raise ValueError
        acknowledgement = decode_acknowledgement(payload)
        canonical = encode_acknowledgement(acknowledgement)
        if canonical != payload:
            raise ValueError
        return acknowledgement, bytes(canonical)

    return _sanitized(decode, "acknowledgement payload is invalid")


class CentralTeamRouter:
    """Central reference core with service-owned authenticated routing identity."""

    def __init__(
        self,
        authority: EnrollmentAuthority,
        *,
        routes: tuple[TeamRoute, ...],
        heartbeat_timeout_seconds: int,
    ) -> None:
        if type(authority) is not EnrollmentAuthority:
            raise TeamRoutingError("enrollment authority is invalid")
        if type(routes) is not tuple or not routes:
            raise TeamRoutingError("Team routes are invalid")
        if (
            type(heartbeat_timeout_seconds) is not int
            or not 1
            <= heartbeat_timeout_seconds
            <= _MAX_HEARTBEAT_TIMEOUT_SECONDS
        ):
            raise TeamRoutingError("heartbeat timeout is invalid")
        route_records: dict[str, ConfigurationGeneration] = {}
        for supplied in routes:
            route = _copy_route(supplied)
            if route.team_key.value in route_records:
                raise TeamRoutingError("Team routes are invalid")
            route_records[route.team_key.value] = ConfigurationGeneration(
                route.configuration_generation.value
            )
        self._authority = authority
        self._routes = route_records
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._connections: dict[str, _ConnectionRecord] = {}
        self._connection_epochs: dict[str, int] = {
            team: 0 for team in route_records
        }
        self._commands: dict[str, _CentralCommandRecord] = {}
        self._team_commands: dict[str, list[str]] = {
            team: [] for team in route_records
        }
        self._team_max_epochs: dict[str, int] = {team: 0 for team in route_records}
        self._inflight: dict[str, str] = {}

    def _authenticated_team(
        self, runner: AuthenticatedRunner, transport: TransportBinding
    ) -> TeamKey:
        def validate() -> TeamKey:
            return EnrollmentAuthority.validate_session(
                self._authority,
                runner,
                transport=transport,
            )

        try:
            team = validate()
            team_copy = _copy_team(team)
            if team_copy.value not in self._routes:
                raise ValueError
            return team_copy
        except Exception:
            pass
        raise TeamRoutingError("authenticated connection is inactive")

    def _current_connection(
        self, runner: AuthenticatedRunner, transport: TransportBinding
    ) -> _ConnectionRecord:
        team = CentralTeamRouter._authenticated_team(self, runner, transport)
        try:
            record = self._connections[team.value]
            if record.runner is not runner or record.team_key != team:
                raise ValueError
            return record
        except Exception:
            pass
        raise TeamRoutingError("authenticated connection is inactive")

    def _fresh_connection(
        self,
        runner: AuthenticatedRunner,
        transport: TransportBinding,
        now: int,
    ) -> _ConnectionRecord:
        if not _valid_time(now):
            raise TeamRoutingError("current time is invalid")
        record = CentralTeamRouter._current_connection(self, runner, transport)
        active_generation = self._routes[record.team_key.value]
        if (
            record.heartbeat_received_at is None
            or record.heartbeat_generation != active_generation
            or now < record.heartbeat_received_at
            or now - record.heartbeat_received_at
            >= self._heartbeat_timeout_seconds
        ):
            raise TeamRoutingError("connection heartbeat is stale")
        return record

    def attach(
        self, runner: AuthenticatedRunner, *, transport: TransportBinding
    ) -> int:
        team = CentralTeamRouter._authenticated_team(self, runner, transport)
        previous = self._connection_epochs[team.value]
        if previous >= _MAX_SIGNED_64:
            raise TeamRoutingError("connection epoch is exhausted")
        epoch = previous + 1
        self._connection_epochs[team.value] = epoch
        self._connections[team.value] = _ConnectionRecord(
            runner=runner,
            team_key=TeamKey(team.value),
            connection_epoch=epoch,
        )
        return epoch

    def activate_route(self, route: TeamRoute) -> None:
        route_copy = _copy_route(route)
        if route_copy.team_key.value not in self._routes:
            raise TeamRoutingError("Team route is inactive")
        self._routes[route_copy.team_key.value] = ConfigurationGeneration(
            route_copy.configuration_generation.value
        )
        connection = self._connections.get(route_copy.team_key.value)
        if connection is not None:
            connection.heartbeat_received_at = None
            connection.heartbeat_generation = None

    def record_heartbeat(
        self,
        runner: AuthenticatedRunner,
        *,
        transport: TransportBinding,
        heartbeat: ConnectionHeartbeat,
        now: int,
    ) -> int:
        if not _valid_time(now):
            raise TeamRoutingError("current time is invalid")
        record = CentralTeamRouter._current_connection(self, runner, transport)
        heartbeat_copy = _copy_heartbeat(heartbeat)
        active_generation = self._routes[record.team_key.value]
        if (
            heartbeat_copy.protocol_version != 1
            or heartbeat_copy.configuration_generation != active_generation
        ):
            raise TeamRoutingError("heartbeat is invalid")
        if record.heartbeat_received_at is not None and now < record.heartbeat_received_at:
            raise TeamRoutingError("heartbeat is invalid")
        record.heartbeat_received_at = now
        record.heartbeat_generation = ConfigurationGeneration(active_generation.value)
        return record.connection_epoch

    def register_command(self, payload: bytes) -> RegistryDecision:
        candidate, canonical = _decode_command(payload)
        team_value = candidate.team_key.value
        active_generation = self._routes.get(team_value)
        if active_generation is None or candidate.configuration_generation != active_generation:
            raise TeamRoutingError("command configuration generation is inactive")

        existing = self._commands.get(candidate.command_id.value)
        if existing is not None:
            if existing.payload == canonical:
                return RegistryDecision.DUPLICATE
            raise TeamRoutingError("immutable command ID was reused")

        epoch = candidate.control_epoch.value
        if epoch <= self._team_max_epochs[team_value]:
            raise TeamRoutingError("command epoch is not monotonic")
        if any(
            self._commands[command_id].command.control_epoch.value == epoch
            for command_id in self._team_commands[team_value]
        ):
            raise TeamRoutingError("command epoch is not monotonic")

        stored = decode_command(canonical)
        self._commands[stored.command_id.value] = _CentralCommandRecord(
            command=stored,
            payload=bytes(canonical),
        )
        self._team_commands[team_value].append(stored.command_id.value)
        self._team_max_epochs[team_value] = epoch
        return RegistryDecision.NEW

    def pending_command_count(self, team_key: TeamKey) -> int:
        team = _copy_team(team_key)
        if team.value not in self._routes:
            raise TeamRoutingError("Team route is inactive")
        return sum(
            self._commands[command_id].terminal_acknowledgement is None
            for command_id in self._team_commands[team.value]
        )

    def deliver(
        self,
        runner: AuthenticatedRunner,
        *,
        transport: TransportBinding,
        now: int,
    ) -> bytes | None:
        connection = CentralTeamRouter._fresh_connection(
            self, runner, transport, now
        )
        team = connection.team_key.value
        command_id = self._inflight.get(team)
        if command_id is None:
            pending = (
                value
                for value in self._team_commands[team]
                if self._commands[value].terminal_acknowledgement is None
            )
            command_id = next(pending, None)
            if command_id is None:
                return None
            self._inflight[team] = command_id
        record = self._commands[command_id]
        if record.command.configuration_generation != self._routes[team]:
            raise TeamRoutingError("command configuration generation is inactive")
        return bytes(record.payload)

    @staticmethod
    def _ack_matches_command(
        acknowledgement: CommandAcknowledgement, command: ControlCommand
    ) -> bool:
        return (
            acknowledgement.command_id == command.command_id
            and acknowledgement.team_key == command.team_key
            and acknowledgement.issuer == command.issuer
            and acknowledgement.desired_state is command.desired_state
            and acknowledgement.issued_at == command.issued_at
            and acknowledgement.expires_at == command.expires_at
            and acknowledgement.control_epoch == command.control_epoch
            and acknowledgement.configuration_generation
            == command.configuration_generation
        )

    def accept_acknowledgement(
        self,
        runner: AuthenticatedRunner,
        *,
        transport: TransportBinding,
        payload: bytes,
        now: int,
    ) -> AcknowledgementDecision:
        connection = CentralTeamRouter._fresh_connection(
            self, runner, transport, now
        )
        acknowledgement, canonical = _decode_acknowledgement(payload)
        team = connection.team_key.value
        if acknowledgement.team_key.value != team:
            raise TeamRoutingError("acknowledgement does not match in-flight command")

        record = self._commands.get(acknowledgement.command_id.value)
        if record is None or not CentralTeamRouter._ack_matches_command(
            acknowledgement, record.command
        ):
            raise TeamRoutingError("acknowledgement does not match in-flight command")
        if record.terminal_acknowledgement is not None:
            if record.terminal_acknowledgement == canonical:
                return AcknowledgementDecision.DUPLICATE
            raise TeamRoutingError("acknowledgement conflicts with terminal record")
        if self._inflight.get(team) != acknowledgement.command_id.value:
            raise TeamRoutingError("acknowledgement does not match in-flight command")
        if record.command.configuration_generation != self._routes[team]:
            raise TeamRoutingError("command configuration generation is inactive")

        record.terminal_acknowledgement = bytes(canonical)
        del self._inflight[team]
        return AcknowledgementDecision.RECORDED

    def terminal_acknowledgement(self, command_id: CommandId) -> bytes | None:
        def lookup() -> bytes | None:
            if type(command_id) is not CommandId or type(command_id.value) is not str:
                raise ValueError
            exact_id = CommandId(command_id.value)
            record = self._commands.get(exact_id.value)
            if record is None or record.terminal_acknowledgement is None:
                return None
            return bytes(record.terminal_acknowledgement)

        return _sanitized(lookup, "command acknowledgement lookup failed")


class RunnerCommandPersistence:
    """Runner-side generation-bound desired-control persistence reference core."""

    def __init__(
        self,
        active_team: TeamKey,
        active_generation: ConfigurationGeneration,
    ) -> None:
        self._active_team = _copy_team(active_team)
        self._active_generation = _copy_generation(active_generation)
        self._current: AppliedControl | None = None

    def accept_delivery(self, payload: bytes, *, now: int) -> bytes:
        command, _ = _decode_command(payload)

        def evaluate() -> bytes:
            result = evaluate_delivery(
                command,
                current=self._current,
                active_team=self._active_team,
                active_generation=self._active_generation,
                now=now,
            )
            if result.action is DeliveryAction.PERSIST_AND_ACK:
                if result.replacement is None:
                    raise ValueError
                persisted_command = decode_command(
                    encode_command(result.replacement.command)
                )
                persisted_acknowledgement = decode_acknowledgement(
                    encode_acknowledgement(result.replacement.acknowledgement)
                )
                self._current = AppliedControl(
                    persisted_command,
                    persisted_acknowledgement,
                )
            return encode_acknowledgement(result.acknowledgement)

        return _sanitized(evaluate, "command delivery is invalid")

    def applied_command_bytes(self) -> bytes | None:
        if self._current is None:
            return None
        return encode_command(self._current.command)
