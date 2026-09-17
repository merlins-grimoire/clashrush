"""Static-only local MVP adapter for one attack/farm account visit.

The visit transaction is adapted from the first-party M6/M9 transaction seam in
``clash-rush-automation`` at commit 949497bf0a543a43ec6ef39a8c897e366bc10362.
It preserves observe -> validate -> intent -> final gate -> execute -> cleanup ->
observe -> confirm -> outcome ordering while narrowing authority to attack/farm.
All native, capture, recognition, and audit behavior remains behind injected
ports so this module can be proved with synthetic tests and cannot act alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Protocol


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_TRANSACTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class MvpGameplayError(RuntimeError):
    """The local MVP control or visit request failed closed."""


class Screen(StrEnum):
    HOME = "HOME"
    ATTACK = "ATTACK"
    UNKNOWN = "UNKNOWN"


class LocalBotMode(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


@dataclass(frozen=True, slots=True)
class MvpConfiguration:
    team_ref: str
    account_ref: str
    instance_ref: str


@dataclass(frozen=True, slots=True)
class AttackObservation:
    screen: Screen
    account_ref: str | None
    window_bound: bool
    army_ready: bool | None


@dataclass(frozen=True, slots=True)
class VisitResult:
    status: str
    reason_code: str
    executed: bool
    confirmed: bool


@dataclass(frozen=True, slots=True)
class LocalBotStatus:
    mode: LocalBotMode
    configured: bool


class AttackVisitPorts(Protocol):
    """Injected authority; implementations own all observation and mutation."""

    def observe(self) -> AttackObservation: ...

    def kill_switch_enabled(self) -> bool: ...

    def record_intent(self, account_ref: str, transaction_ref: str) -> None: ...

    def execute_attack(self) -> tuple[bool, str]: ...

    def cleanup(self) -> None: ...

    def record_outcome(
        self, account_ref: str, transaction_ref: str, confirmed: bool
    ) -> None: ...


def _valid_reference(value: object) -> bool:
    return type(value) is str and _REFERENCE.fullmatch(value) is not None


def _stopped(reason: str) -> VisitResult:
    return VisitResult("stopped", reason, False, False)


def _failed(reason: str, *, executed: bool = False) -> VisitResult:
    return VisitResult("failed", reason, executed, False)


class LocalMvpBot:
    """One-Team, one-account, one-instance attack-only local control surface."""

    __slots__ = ("_ports", "_configuration", "_mode")

    def __init__(self, ports: AttackVisitPorts) -> None:
        self._ports = ports
        self._configuration: MvpConfiguration | None = None
        self._mode = LocalBotMode.UNCONFIGURED

    def setup(self, configuration: MvpConfiguration) -> None:
        if self._mode is not LocalBotMode.UNCONFIGURED:
            raise MvpGameplayError("setup is frozen after entering the stopped state")
        if (
            type(configuration) is not MvpConfiguration
            or not _valid_reference(configuration.team_ref)
            or not _valid_reference(configuration.account_ref)
            or not _valid_reference(configuration.instance_ref)
        ):
            raise MvpGameplayError(
                "setup requires one exact local team, account, and instance"
            )
        self._configuration = MvpConfiguration(
            configuration.team_ref,
            configuration.account_ref,
            configuration.instance_ref,
        )
        self._mode = LocalBotMode.STOPPED

    def run(self) -> None:
        if self._mode not in {LocalBotMode.STOPPED, LocalBotMode.PAUSED}:
            raise MvpGameplayError("run requires a stopped or paused local bot")
        self._mode = LocalBotMode.RUNNING

    def pause(self) -> None:
        if self._mode is not LocalBotMode.RUNNING:
            raise MvpGameplayError("pause requires a running local bot")
        self._mode = LocalBotMode.PAUSED

    def stop(self) -> None:
        if self._mode not in {LocalBotMode.RUNNING, LocalBotMode.PAUSED}:
            raise MvpGameplayError("stop requires a running or paused local bot")
        self._mode = LocalBotMode.STOPPED

    def status(self) -> LocalBotStatus:
        return LocalBotStatus(self._mode, self._configuration is not None)

    def visit_once(self, transaction_ref: str) -> VisitResult:
        """Run one injected attack transaction; never launches or captures itself."""
        if self._mode is not LocalBotMode.RUNNING:
            raise MvpGameplayError("visit requires a running local bot")
        configuration = self._configuration
        if configuration is None or not _valid_reference(transaction_ref):
            raise MvpGameplayError("visit requires configured exact references")

        gate = self._kill_switch_gate()
        if gate is not None:
            return gate

        try:
            before = self._ports.observe()
        except BaseException:
            return _failed("OBSERVATION_UNAVAILABLE")
        reason = self._validate_observation(
            before, configuration.account_ref, post_attack=False
        )
        if reason is not None:
            return _stopped(reason)

        try:
            intent_result = self._ports.record_intent(
                configuration.account_ref, transaction_ref
            )
        except BaseException:
            return _failed("INTENT_AUDIT_UNAVAILABLE")
        if intent_result is not None:
            return _failed("INTENT_AUDIT_MALFORMED")

        gate = self._kill_switch_gate()
        if gate is not None:
            return gate

        executed = False
        execute_reason = "ATTACK_EXECUTION_FAILED"
        cleanup_failed = False
        try:
            execution = self._ports.execute_attack()
            if (
                type(execution) is tuple
                and len(execution) == 2
                and type(execution[0]) is bool
                and type(execution[1]) is str
                and bool(execution[1])
            ):
                executed, execute_reason = execution
            else:
                execute_reason = "ATTACK_EXECUTION_MALFORMED"
        except BaseException:
            execute_reason = "ATTACK_EXECUTION_UNAVAILABLE"
        finally:
            try:
                cleanup_result = self._ports.cleanup()
                cleanup_failed = cleanup_result is not None
            except BaseException:
                cleanup_failed = True

        if cleanup_failed:
            self._record_outcome(configuration, transaction_ref, False)
            return _failed("CLEANUP_FAILED", executed=executed)
        if executed is not True:
            self._record_outcome(configuration, transaction_ref, False)
            return _failed(execute_reason)

        try:
            after = self._ports.observe()
        except BaseException:
            self._record_outcome(configuration, transaction_ref, False)
            return _failed("POST_OBSERVATION_UNAVAILABLE", executed=True)
        reason = self._validate_observation(
            after, configuration.account_ref, post_attack=True
        )
        confirmed = reason is None
        if not self._record_outcome(configuration, transaction_ref, confirmed):
            return _failed("OUTCOME_AUDIT_UNAVAILABLE", executed=True)
        if not confirmed:
            return _failed(reason, executed=True)
        return VisitResult("completed", "RETURNED_HOME", True, True)

    def _kill_switch_gate(self) -> VisitResult | None:
        try:
            enabled = self._ports.kill_switch_enabled()
        except BaseException:
            return _stopped("KILL_SWITCH_UNAVAILABLE")
        if enabled is not True:
            reason = "KILL_SWITCH" if type(enabled) is bool else "KILL_SWITCH_UNAVAILABLE"
            return _stopped(reason)
        return None

    @staticmethod
    def _validate_observation(
        observation: object, expected_account: str, *, post_attack: bool
    ) -> str | None:
        prefix = "POST_" if post_attack else ""
        if type(observation) is not AttackObservation:
            return f"{prefix}OBSERVATION_MALFORMED"
        if observation.window_bound is not True:
            return f"{prefix}WINDOW_UNBOUND"
        if type(observation.screen) is not Screen:
            return f"{prefix}SCREEN_UNKNOWN"
        if observation.screen is Screen.UNKNOWN:
            return f"{prefix}SCREEN_UNKNOWN"
        if observation.screen is not Screen.HOME:
            return f"{prefix}NOT_HOME"
        if observation.account_ref is None:
            return f"{prefix}ACCOUNT_UNKNOWN"
        if (
            not _valid_reference(observation.account_ref)
            or observation.account_ref != expected_account
        ):
            return f"{prefix}ACCOUNT_MISMATCH"
        if post_attack:
            return None
        if type(observation.army_ready) is not bool:
            return "ARMY_READINESS_UNKNOWN"
        if observation.army_ready is False:
            return "ARMY_NOT_READY"
        return None

    def _record_outcome(
        self, configuration: MvpConfiguration, transaction_ref: str, confirmed: bool
    ) -> bool:
        try:
            result = self._ports.record_outcome(
                configuration.account_ref, transaction_ref, confirmed
            )
        except BaseException:
            return False
        return result is None
