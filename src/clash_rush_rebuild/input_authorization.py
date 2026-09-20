"""Closed physical-input authority for the bounded MVP.

Only the monitored attack composition can receive physical-input authority.  The
no-input diagnostic receives an explicit empty capability, and every physical
input rechecks this boundary immediately before delivery.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum


PLACEMENT_ENABLED = False


class InputAuthorizationError(RuntimeError):
    """Physical input is outside the exact authorized run purpose."""


class InputPurpose(StrEnum):
    NO_INPUT_DIAGNOSTIC = "NO_INPUT_DIAGNOSTIC"
    STARTUP_CONTINUE_ONLY = "STARTUP_CONTINUE_ONLY"
    STARTUP_DEBUG = "STARTUP_DEBUG"
    MONITORED_ATTACK = "MONITORED_ATTACK"


class InputAction(StrEnum):
    """Closed action vocabulary needed by the monitored attack path."""

    STARTUP_CONTINUE = "STARTUP_CONTINUE"
    STARTUP_LAUNCH_GAME = "STARTUP_LAUNCH_GAME"
    STARTUP_CLOSE_PROMO = "STARTUP_CLOSE_PROMO"
    STARTUP_OKAY = "STARTUP_OKAY"
    ACCOUNT_EXPORT_NAVIGATION = "ACCOUNT_EXPORT_NAVIGATION"
    ATTACK_NAVIGATION = "ATTACK_NAVIGATION"
    TROOP_DEPLOYMENT = "TROOP_DEPLOYMENT"
    RETURN_HOME = "RETURN_HOME"
    CLEANUP_RELEASE = "CLEANUP_RELEASE"


class InputAuthorization:
    """Exact run-purpose capability checked at the native input boundary."""

    __slots__ = ("_live_gate", "_purpose")

    def __init__(
        self,
        purpose: InputPurpose,
        live_gate: Callable[[], bool] | None,
    ) -> None:
        if type(purpose) is not InputPurpose or (
            purpose is InputPurpose.NO_INPUT_DIAGNOSTIC
            and live_gate is not None
        ) or (
            purpose in {
                InputPurpose.STARTUP_CONTINUE_ONLY,
                InputPurpose.STARTUP_DEBUG,
                InputPurpose.MONITORED_ATTACK,
            }
            and not callable(live_gate)
        ):
            raise InputAuthorizationError("input authorization is malformed")
        self._purpose = purpose
        self._live_gate = live_gate

    @classmethod
    def no_input_diagnostic(cls) -> InputAuthorization:
        return cls(InputPurpose.NO_INPUT_DIAGNOSTIC, None)

    @classmethod
    def monitored_attack(
        cls, live_gate: Callable[[], bool]
    ) -> InputAuthorization:
        return cls(InputPurpose.MONITORED_ATTACK, live_gate)

    @classmethod
    def startup_continue_only(
        cls, live_gate: Callable[[], bool]
    ) -> InputAuthorization:
        return cls(InputPurpose.STARTUP_CONTINUE_ONLY, live_gate)

    @property
    def purpose(self) -> InputPurpose:
        return self._purpose

    @classmethod
    def startup_debug(cls, live_gate: Callable[[], bool]) -> InputAuthorization:
        return cls(InputPurpose.STARTUP_DEBUG, live_gate)

    def require(self, action: InputAction) -> None:
        if type(action) is not InputAction:
            raise InputAuthorizationError("exact action is required")
        allowed_action = (
            self._purpose is InputPurpose.STARTUP_CONTINUE_ONLY
            and action is InputAction.STARTUP_CONTINUE
        ) or (
            self._purpose is InputPurpose.STARTUP_DEBUG
            and action in {
                InputAction.STARTUP_LAUNCH_GAME, InputAction.STARTUP_CLOSE_PROMO,
                InputAction.STARTUP_OKAY, InputAction.STARTUP_CONTINUE,
            }
        ) or (
            self._purpose is InputPurpose.MONITORED_ATTACK
            and action in {
                InputAction.ACCOUNT_EXPORT_NAVIGATION, InputAction.ATTACK_NAVIGATION,
                InputAction.TROOP_DEPLOYMENT, InputAction.RETURN_HOME,
                InputAction.CLEANUP_RELEASE,
            }
        )
        if not allowed_action:
            raise InputAuthorizationError("physical input is not authorized")
        try:
            allowed = self._live_gate is not None and self._live_gate() is True
        except BaseException:
            allowed = False
        if not allowed:
            raise InputAuthorizationError("physical input is not authorized")


__all__ = [
    "PLACEMENT_ENABLED",
    "InputAction",
    "InputAuthorization",
    "InputAuthorizationError",
    "InputPurpose",
]
