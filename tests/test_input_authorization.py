from __future__ import annotations

import pytest

from clash_rush_rebuild.input_authorization import (
    PLACEMENT_ENABLED,
    InputAction,
    InputAuthorization,
    InputAuthorizationError,
    InputPurpose,
)


def test_no_input_diagnostic_authorization_rejects_every_physical_action() -> None:
    authorization = InputAuthorization.no_input_diagnostic()

    assert authorization.purpose is InputPurpose.NO_INPUT_DIAGNOSTIC
    for action in InputAction:
        with pytest.raises(InputAuthorizationError, match="not authorized"):
            authorization.require(action)


def test_monitored_attack_authorizes_only_closed_attack_path_vocabulary() -> None:
    authorization = InputAuthorization.monitored_attack(lambda: True)

    assert authorization.purpose is InputPurpose.MONITORED_ATTACK
    assert set(InputAction) == {
        InputAction.STARTUP_CONTINUE,
        InputAction.STARTUP_LAUNCH_GAME,
        InputAction.STARTUP_CLOSE_PROMO,
        InputAction.STARTUP_OKAY,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ATTACK_NAVIGATION,
        InputAction.TROOP_DEPLOYMENT,
        InputAction.RETURN_HOME,
        InputAction.CLEANUP_RELEASE,
    }
    startup = {InputAction.STARTUP_CONTINUE, InputAction.STARTUP_LAUNCH_GAME,
               InputAction.STARTUP_CLOSE_PROMO, InputAction.STARTUP_OKAY}
    for action in set(InputAction) - startup:
        authorization.require(action)
    for action in startup:
        with pytest.raises(InputAuthorizationError, match="not authorized"):
            authorization.require(action)
    with pytest.raises(InputAuthorizationError, match="exact action"):
        authorization.require("purchase")  # type: ignore[arg-type]


def test_monitored_attack_rechecks_live_authorization_for_each_input() -> None:
    checks = iter([True, False])
    authorization = InputAuthorization.monitored_attack(lambda: next(checks))

    authorization.require(InputAction.ATTACK_NAVIGATION)
    with pytest.raises(InputAuthorizationError, match="not authorized"):
        authorization.require(InputAction.TROOP_DEPLOYMENT)


def test_account_readiness_capability_authorizes_only_export_navigation() -> None:
    authorization = InputAuthorization.account_readiness(lambda: True)

    authorization.require(InputAction.ACCOUNT_EXPORT_NAVIGATION)
    for action in set(InputAction) - {InputAction.ACCOUNT_EXPORT_NAVIGATION}:
        with pytest.raises(InputAuthorizationError, match="not authorized"):
            authorization.require(action)


def test_placement_and_unapproved_authority_remain_unrepresentable() -> None:
    assert PLACEMENT_ENABLED is False
    names = {action.name for action in InputAction}
    forbidden = {
        "SPENDING",
        "PURCHASE",
        "GEMS",
        "MAGIC_ITEMS",
        "SEASONAL_CRAFTING",
        "REWARDS",
        "ACCOUNT_SWITCHING",
        "CREDENTIALS",
        "UNKNOWN_SCREEN",
        "RANDOM_FALLBACK",
        "PLACEMENT",
        "UPGRADE",
        "RESEARCH",
        "DONATION",
    }
    assert names.isdisjoint(forbidden)
