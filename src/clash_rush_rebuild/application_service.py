"""Closed application-service boundary for local and future remote controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Callable, NamedTuple, Protocol, TypeVar


class ControlRejected(RuntimeError):
    """A control request failed closed before orchestration."""


_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z"
)
_MAX_EPOCH = 2**63 - 1
_MAX_QUEUE_DEPTH = 1_000_000
_MAX_SANITIZED_SLOT_INDEX = 1023
_MAX_DIAGNOSTIC_SECONDS = 300
_PortResult = TypeVar("_PortResult")


def _call_port(operation: Callable[[], _PortResult], failure: str) -> _PortResult:
    try:
        return operation()
    except Exception:
        pass
    raise ControlRejected(failure)


def _validate_port_result(
    operation: Callable[[], _PortResult], failure: str
) -> _PortResult:
    try:
        return operation()
    except Exception:
        pass
    raise ControlRejected(failure)


def _is_reference(value: object) -> bool:
    return type(value) is str and _REFERENCE_PATTERN.fullmatch(value) is not None


def _is_utc_timestamp(value: object) -> bool:
    if type(value) is not str or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return True


class CommandKind(StrEnum):
    SETUP = "SETUP"
    RUN = "RUN"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"
    STATUS = "STATUS"
    QUARANTINE = "QUARANTINE"
    UNQUARANTINE = "UNQUARANTINE"
    DEBUG_START = "DEBUG_START"
    DEBUG_STATUS = "DEBUG_STATUS"
    DEBUG_STOP = "DEBUG_STOP"


class ControlMode(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class Resource(StrEnum):
    HOME_GOLD = "HOME_GOLD"
    HOME_ELIXIR = "HOME_ELIXIR"
    HOME_DARK_ELIXIR = "HOME_DARK_ELIXIR"
    BUILDER_GOLD = "BUILDER_GOLD"
    BUILDER_ELIXIR = "BUILDER_ELIXIR"


class StatusBlockedReason(StrEnum):
    OFFLINE = "OFFLINE"
    NO_ELIGIBLE_ACCOUNT = "NO_ELIGIBLE_ACCOUNT"
    QUARANTINED = "QUARANTINED"
    LIFECYCLE_BLOCKED = "LIFECYCLE_BLOCKED"
    CONFIGURATION_BLOCKED = "CONFIGURATION_BLOCKED"


class StatusOutcome(StrEnum):
    IDLE = "IDLE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class Actor:
    actor_ref: str


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    command_ref: str
    team_ref: str
    account_ref: str | None = None
    debug_minutes: int | None = None
    resources: frozenset[Resource] = frozenset()


class _CommandSnapshot(NamedTuple):
    kind: CommandKind
    command_ref: str
    team_ref: str
    account_ref: str | None
    debug_minutes: int | None
    resources: frozenset[Resource]


@dataclass(frozen=True, slots=True)
class DesiredState:
    team_ref: str
    mode: ControlMode
    epoch: int


@dataclass(frozen=True, slots=True)
class DurableIntent:
    command_ref: str
    team_ref: str
    kind: CommandKind
    mode: ControlMode
    epoch: int
    account_ref: str | None = None
    debug_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    queue_depth: int
    next_due_at: str | None
    blocked_reason: StatusBlockedReason | None
    current_account_ref: str | None
    last_outcome: StatusOutcome | None


@dataclass(frozen=True, slots=True)
class LifecycleStatus:
    active: bool
    sanitized_slot_index: int | None


@dataclass(frozen=True, slots=True)
class DiagnosticStatus:
    active: bool
    remaining_seconds: int


@dataclass(frozen=True, slots=True)
class StatusView:
    mode: ControlMode
    epoch: int
    connected: bool
    lifecycle_active: bool
    sanitized_slot_index: int | None
    queue_depth: int
    next_due_at: str | None
    blocked_reason: StatusBlockedReason | None
    current_account_ref: str | None
    last_outcome: StatusOutcome | None
    resource_authority: tuple[tuple[Resource, bool], ...]


class AuthorizationPort(Protocol):
    def authorize(self, actor: Actor, team_ref: str, action: CommandKind) -> bool: ...


class DesiredStatePort(Protocol):
    def load(self, team_ref: str) -> DesiredState: ...
    def persist(self, command: Command) -> DurableIntent: ...


class SchedulerPort(Protocol):
    def is_online(self, team_ref: str) -> bool: ...
    def accept_desired(self, intent: DurableIntent) -> None: ...
    def status(self, team_ref: str) -> SchedulerStatus: ...


class LifecyclePort(Protocol):
    def status(self, team_ref: str) -> LifecycleStatus: ...


class SetupPort(Protocol):
    def configure(self, team_ref: str, resources: frozenset[Resource]) -> str: ...


class ResourceAuthorityPort(Protocol):
    def enabled_for(self, team_ref: str) -> frozenset[Resource]: ...


class DiagnosticPort(Protocol):
    def accept_desired(self, intent: DurableIntent) -> None: ...
    def status(self, team_ref: str) -> DiagnosticStatus: ...


class ApplicationService:
    """The sole adapter-facing command boundary; injected ports retain authority."""

    def __init__(
        self,
        authorization: AuthorizationPort,
        desired_states: DesiredStatePort,
        scheduler: SchedulerPort,
        lifecycle: LifecyclePort,
        setup: SetupPort,
        resources: ResourceAuthorityPort,
        diagnostics: DiagnosticPort,
    ) -> None:
        self._authorization = authorization
        self._desired_states = desired_states
        self._scheduler = scheduler
        self._lifecycle = lifecycle
        self._setup = setup
        self._resources = resources
        self._diagnostics = diagnostics

    def _scheduler_online(self, team_ref: str) -> bool:
        online = _call_port(
            lambda: self._scheduler.is_online(team_ref),
            "scheduler online status unavailable",
        )

        def validate() -> bool:
            if type(online) is not bool:
                raise ValueError
            return online

        return _validate_port_result(
            validate, "scheduler online status must be an exact bool"
        )

    def _authorize(self, actor_ref: str, command: _CommandSnapshot) -> None:
        authorized = _call_port(
            lambda: self._authorization.authorize(
                Actor(actor_ref), command.team_ref, command.kind
            ),
            "authorization unavailable",
        )

        def validate() -> None:
            if authorized is not True:
                raise ValueError

        _validate_port_result(validate, "control command is not authorized")

    def _load_desired(self, team_ref: str) -> DesiredState:
        desired = _call_port(
            lambda: self._desired_states.load(team_ref), "desired state unavailable"
        )

        def validate_and_copy() -> DesiredState:
            if (
                type(desired) is not DesiredState
                or not _is_reference(desired.team_ref)
                or desired.team_ref != team_ref
                or type(desired.mode) is not ControlMode
                or type(desired.epoch) is not int
                or not 0 <= desired.epoch <= _MAX_EPOCH
            ):
                raise ValueError
            return DesiredState(desired.team_ref, desired.mode, desired.epoch)

        return _validate_port_result(validate_and_copy, "desired state is malformed")

    def _persist(
        self,
        command: _CommandSnapshot,
        expected_mode: ControlMode,
        previous_epoch: int,
    ) -> DurableIntent:
        if previous_epoch >= _MAX_EPOCH:
            raise ControlRejected("desired state epoch is exhausted")
        callback_command = Command(
            command.kind,
            command.command_ref,
            command.team_ref,
            command.account_ref,
            command.debug_minutes,
            command.resources,
        )
        intent = _call_port(
            lambda: self._desired_states.persist(callback_command),
            "persisted intent unavailable",
        )

        def validate_and_copy() -> DurableIntent:
            if (
                type(intent) is not DurableIntent
                or not _is_reference(intent.command_ref)
                or intent.command_ref != command.command_ref
                or not _is_reference(intent.team_ref)
                or intent.team_ref != command.team_ref
                or intent.kind is not command.kind
                or intent.mode is not expected_mode
                or type(intent.epoch) is not int
                or intent.epoch != previous_epoch + 1
                or intent.epoch > _MAX_EPOCH
                or (command.account_ref is None and intent.account_ref is not None)
                or (
                    command.account_ref is not None
                    and (
                        not _is_reference(intent.account_ref)
                        or intent.account_ref != command.account_ref
                    )
                )
                or (
                    command.debug_minutes is None
                    and intent.debug_minutes is not None
                )
                or (
                    command.debug_minutes is not None
                    and (
                        type(intent.debug_minutes) is not int
                        or intent.debug_minutes != command.debug_minutes
                    )
                )
            ):
                raise ValueError
            return DurableIntent(
                intent.command_ref,
                intent.team_ref,
                intent.kind,
                intent.mode,
                intent.epoch,
                intent.account_ref,
                intent.debug_minutes,
            )

        return _validate_port_result(validate_and_copy, "persisted intent is malformed")

    def _load_scheduler_status(self, team_ref: str) -> SchedulerStatus:
        status = _call_port(
            lambda: self._scheduler.status(team_ref), "scheduler status unavailable"
        )

        def validate_and_copy() -> SchedulerStatus:
            if (
                type(status) is not SchedulerStatus
                or type(status.queue_depth) is not int
                or not 0 <= status.queue_depth <= _MAX_QUEUE_DEPTH
                or (
                    status.next_due_at is not None
                    and not _is_utc_timestamp(status.next_due_at)
                )
                or (
                    status.blocked_reason is not None
                    and type(status.blocked_reason) is not StatusBlockedReason
                )
                or (
                    status.current_account_ref is not None
                    and not _is_reference(status.current_account_ref)
                )
                or (
                    status.last_outcome is not None
                    and type(status.last_outcome) is not StatusOutcome
                )
            ):
                raise ValueError
            return SchedulerStatus(
                status.queue_depth,
                status.next_due_at,
                status.blocked_reason,
                status.current_account_ref,
                status.last_outcome,
            )

        return _validate_port_result(validate_and_copy, "scheduler status is malformed")

    def _load_lifecycle_status(self, team_ref: str) -> LifecycleStatus:
        status = _call_port(
            lambda: self._lifecycle.status(team_ref), "lifecycle status unavailable"
        )

        def validate_and_copy() -> LifecycleStatus:
            if type(status) is not LifecycleStatus:
                raise ValueError
            slot = status.sanitized_slot_index
            if type(status.active) is not bool or (
                slot is not None
                and (
                    type(slot) is not int
                    or not 0 <= slot <= _MAX_SANITIZED_SLOT_INDEX
                )
            ):
                raise ValueError
            return LifecycleStatus(status.active, slot)

        return _validate_port_result(validate_and_copy, "lifecycle status is malformed")

    def _load_resource_authority(self, team_ref: str) -> frozenset[Resource]:
        enabled = _call_port(
            lambda: self._resources.enabled_for(team_ref),
            "resource authority unavailable",
        )

        def validate() -> frozenset[Resource]:
            if type(enabled) is not frozenset or any(
                type(resource) is not Resource for resource in enabled
            ):
                raise ValueError
            return enabled

        return _validate_port_result(validate, "resource authority is malformed")

    def _load_diagnostic_status(self, team_ref: str) -> DiagnosticStatus:
        status = _call_port(
            lambda: self._diagnostics.status(team_ref),
            "diagnostic status unavailable",
        )

        def validate_and_copy() -> DiagnosticStatus:
            if (
                type(status) is not DiagnosticStatus
                or type(status.active) is not bool
                or type(status.remaining_seconds) is not int
                or not 0 <= status.remaining_seconds <= _MAX_DIAGNOSTIC_SECONDS
            ):
                raise ValueError
            return DiagnosticStatus(status.active, status.remaining_seconds)

        return _validate_port_result(validate_and_copy, "diagnostic status is malformed")

    def _configure_setup(
        self, team_ref: str, resources: frozenset[Resource]
    ) -> str:
        result = _call_port(
            lambda: self._setup.configure(team_ref, resources),
            "setup result unavailable",
        )

        def validate() -> str:
            if not _is_reference(result):
                raise ValueError
            return result

        return _validate_port_result(validate, "setup result is malformed")

    def _admit_scheduler(self, intent: DurableIntent) -> None:
        result = _call_port(
            lambda: self._scheduler.accept_desired(
                ApplicationService._copy_intent(intent)
            ),
            "scheduler admission unavailable",
        )

        def validate() -> None:
            if result is not None:
                raise ValueError

        _validate_port_result(validate, "scheduler admission result is malformed")

    def _admit_diagnostic(self, intent: DurableIntent) -> None:
        result = _call_port(
            lambda: self._diagnostics.accept_desired(
                ApplicationService._copy_intent(intent)
            ),
            "diagnostic admission unavailable",
        )

        def validate() -> None:
            if result is not None:
                raise ValueError

        _validate_port_result(validate, "diagnostic admission result is malformed")

    @staticmethod
    def _copy_intent(intent: DurableIntent) -> DurableIntent:
        return DurableIntent(
            intent.command_ref,
            intent.team_ref,
            intent.kind,
            intent.mode,
            intent.epoch,
            intent.account_ref,
            intent.debug_minutes,
        )

    @staticmethod
    def _validate_request(actor: Actor, command: Command) -> tuple[str, _CommandSnapshot]:
        if type(command) is not Command:
            raise ControlRejected("exact command and actor types required")
        if type(actor) is not Actor:
            raise ControlRejected("exact actor reference required")
        try:
            actor_ref = actor.actor_ref
            request = _CommandSnapshot(
                command.kind,
                command.command_ref,
                command.team_ref,
                command.account_ref,
                command.debug_minutes,
                command.resources,
            )
            if type(request.kind) is not CommandKind:
                raise ControlRejected("exact command and actor types required")
            if not _is_reference(actor_ref):
                raise ControlRejected("exact actor reference required")
            if not _is_reference(request.command_ref) or not _is_reference(
                request.team_ref
            ):
                raise ControlRejected("exact command and team references required")
            if request.account_ref is not None and not _is_reference(
                request.account_ref
            ):
                raise ControlRejected("exact account reference required")
            if type(request.resources) is not frozenset or any(
                type(resource) is not Resource for resource in request.resources
            ):
                if request.kind is CommandKind.SETUP:
                    raise ControlRejected("setup resource authority is invalid")
                raise ControlRejected("command fields are invalid")

            account_kinds = {CommandKind.QUARANTINE, CommandKind.UNQUARANTINE}
            if request.kind in account_kinds and request.account_ref is None:
                raise ControlRejected(
                    "account control requires an exact account reference"
                )
            if request.kind not in account_kinds and request.account_ref is not None:
                raise ControlRejected("command fields are invalid")
            if request.kind is CommandKind.DEBUG_START:
                if (
                    type(request.debug_minutes) is not int
                    or not 2 <= request.debug_minutes <= 5
                ):
                    raise ControlRejected(
                        "debug duration must be from two through five minutes"
                    )
            elif request.debug_minutes is not None:
                raise ControlRejected("command fields are invalid")
            if request.kind is not CommandKind.SETUP and request.resources:
                raise ControlRejected("command fields are invalid")
            return actor_ref, request
        except ControlRejected:
            raise
        except Exception:
            pass
        raise ControlRejected("control request is malformed")

    def execute(self, actor: Actor, command: Command) -> object:
        actor_ref, command_snapshot = ApplicationService._validate_request(actor, command)
        ApplicationService._authorize(self, actor_ref, command_snapshot)
        command = command_snapshot
        if command.kind in {
            CommandKind.RUN,
            CommandKind.PAUSE,
            CommandKind.RESUME,
            CommandKind.STOP,
        }:
            current = ApplicationService._load_desired(self, command.team_ref)
            allowed_from = {
                CommandKind.RUN: ControlMode.STOPPED,
                CommandKind.PAUSE: ControlMode.RUNNING,
                CommandKind.RESUME: ControlMode.PAUSED,
                CommandKind.STOP: (ControlMode.RUNNING, ControlMode.PAUSED),
            }[command.kind]
            if isinstance(allowed_from, tuple):
                valid = current.mode in allowed_from
            else:
                valid = current.mode is allowed_from
            if not valid:
                raise ControlRejected("invalid control-state transition")
            if (
                command.kind in {CommandKind.RUN, CommandKind.RESUME}
                and not ApplicationService._scheduler_online(self, command.team_ref)
            ):
                raise ControlRejected("run or resume rejected while scheduler is offline")
            expected_mode = {
                CommandKind.RUN: ControlMode.RUNNING,
                CommandKind.PAUSE: ControlMode.PAUSED,
                CommandKind.RESUME: ControlMode.RUNNING,
                CommandKind.STOP: ControlMode.STOPPED,
            }[command.kind]
            intent = ApplicationService._persist(
                self, command, expected_mode, current.epoch
            )
            ApplicationService._admit_scheduler(self, intent)
            return intent
        if command.kind in {CommandKind.QUARANTINE, CommandKind.UNQUARANTINE}:
            current = ApplicationService._load_desired(self, command.team_ref)
            intent = ApplicationService._persist(
                self, command, current.mode, current.epoch
            )
            ApplicationService._admit_scheduler(self, intent)
            return intent
        if command.kind is CommandKind.SETUP:
            if (
                ApplicationService._load_desired(self, command.team_ref).mode
                is not ControlMode.STOPPED
            ):
                raise ControlRejected("setup requires the stopped control state")
            return ApplicationService._configure_setup(
                self, command.team_ref, command.resources
            )
        if command.kind is CommandKind.STATUS:
            desired = ApplicationService._load_desired(self, command.team_ref)
            scheduler = ApplicationService._load_scheduler_status(
                self, command.team_ref
            )
            lifecycle = ApplicationService._load_lifecycle_status(
                self, command.team_ref
            )
            enabled = ApplicationService._load_resource_authority(
                self, command.team_ref
            )
            return StatusView(
                mode=desired.mode,
                epoch=desired.epoch,
                connected=ApplicationService._scheduler_online(self, command.team_ref),
                lifecycle_active=lifecycle.active,
                sanitized_slot_index=lifecycle.sanitized_slot_index,
                queue_depth=scheduler.queue_depth,
                next_due_at=scheduler.next_due_at,
                blocked_reason=scheduler.blocked_reason,
                current_account_ref=scheduler.current_account_ref,
                last_outcome=scheduler.last_outcome,
                resource_authority=tuple(
                    (resource, resource in enabled) for resource in Resource
                ),
            )
        if command.kind is CommandKind.DEBUG_START:
            current = ApplicationService._load_desired(self, command.team_ref)
            if current.mode is not ControlMode.RUNNING:
                raise ControlRejected("debug start requires an already-running team")
            intent = ApplicationService._persist(
                self, command, current.mode, current.epoch
            )
            ApplicationService._admit_diagnostic(self, intent)
            return intent
        if command.kind is CommandKind.DEBUG_STATUS:
            return ApplicationService._load_diagnostic_status(self, command.team_ref)
        if command.kind is CommandKind.DEBUG_STOP:
            current = ApplicationService._load_desired(self, command.team_ref)
            intent = ApplicationService._persist(
                self, command, current.mode, current.epoch
            )
            ApplicationService._admit_diagnostic(self, intent)
            return intent
        raise ControlRejected("control command is not implemented")


class _ServiceDispatch:
    """Private trusted-process binding from an adapter to one exact service."""

    __slots__ = ("__service",)

    def __init__(self, service: ApplicationService) -> None:
        self.__service = service

    def invoke(self, actor: Actor, command: Command) -> object:
        try:
            service = self.__service
            if type(service) is not ApplicationService:
                raise TypeError
        except Exception:
            pass
        else:
            return ApplicationService.execute(service, actor, command)
        raise ControlRejected("adapter dispatch is unavailable")


class LocalControlAdapter:
    """Thin trusted-process adapter whose only supported API is ``submit``."""

    __slots__ = ("__dispatch",)

    def __init__(self, service: ApplicationService) -> None:
        if type(service) is not ApplicationService:
            raise TypeError("exact ApplicationService required")
        self.__dispatch = _ServiceDispatch(service)

    def submit(self, actor: Actor, command: Command) -> object:
        try:
            dispatch = self.__dispatch
            if type(dispatch) is not _ServiceDispatch:
                raise TypeError
        except Exception:
            pass
        else:
            return _ServiceDispatch.invoke(dispatch, actor, command)
        raise ControlRejected("adapter dispatch is unavailable")
