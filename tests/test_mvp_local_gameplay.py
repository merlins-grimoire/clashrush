from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from clash_rush_rebuild.mvp_local_gameplay import (
    AttackObservation,
    LocalBotMode,
    LocalMvpBot,
    MvpConfiguration,
    MvpGameplayError,
    Screen,
    VisitResult,
)


ACCOUNT = "account-synthetic-a"
TEAM = "team-synthetic-a"
INSTANCE = "instance-synthetic-a"


@dataclass
class FakeAttackPorts:
    observations: list[AttackObservation]
    kill_switch_values: list[bool] = field(default_factory=lambda: [True, True])
    events: list[str] = field(default_factory=list)
    execute_result: tuple[bool, str] = (True, "ATTACK_COMPLETED")

    def observe(self) -> AttackObservation:
        self.events.append("observe")
        return self.observations.pop(0)

    def kill_switch_enabled(self) -> bool:
        self.events.append("kill-switch")
        return self.kill_switch_values.pop(0)

    def record_intent(self, account_ref: str, transaction_ref: str) -> None:
        self.events.append("intent")

    def execute_attack(self) -> tuple[bool, str]:
        self.events.append("attack")
        return self.execute_result

    def cleanup(self) -> None:
        self.events.append("cleanup")

    def record_outcome(self, account_ref: str, transaction_ref: str, confirmed: bool) -> None:
        self.events.append(f"outcome:{confirmed}")


def observation(
    *,
    screen: Screen = Screen.HOME,
    account_ref: str | None = ACCOUNT,
    window_bound: bool = True,
    army_ready: bool | None = True,
) -> AttackObservation:
    return AttackObservation(
        screen=screen,
        account_ref=account_ref,
        window_bound=window_bound,
        army_ready=army_ready,
    )


def configured_bot(ports: FakeAttackPorts) -> LocalMvpBot:
    bot = LocalMvpBot(ports)
    bot.setup(MvpConfiguration(TEAM, ACCOUNT, INSTANCE))
    bot.run()
    return bot


def test_attack_visit_preserves_proven_transaction_order_and_returns_home() -> None:
    ports = FakeAttackPorts([observation(), observation()])
    bot = configured_bot(ports)

    result = bot.visit_once("tx-synthetic-0001")

    assert result == VisitResult("completed", "RETURNED_HOME", True, True)
    assert ports.events == [
        "kill-switch",
        "observe",
        "intent",
        "kill-switch",
        "attack",
        "cleanup",
        "observe",
        "outcome:True",
    ]


@pytest.mark.parametrize(
    ("unsafe", "reason"),
    [
        (observation(window_bound=False), "WINDOW_UNBOUND"),
        (observation(screen=Screen.UNKNOWN), "SCREEN_UNKNOWN"),
        (observation(screen=Screen.ATTACK), "NOT_HOME"),
        (observation(account_ref=None), "ACCOUNT_UNKNOWN"),
        (observation(account_ref="account-synthetic-other"), "ACCOUNT_MISMATCH"),
        (observation(army_ready=None), "ARMY_READINESS_UNKNOWN"),
        (observation(army_ready=False), "ARMY_NOT_READY"),
    ],
)
def test_unknown_or_mismatched_preconditions_emit_no_attack(
    unsafe: AttackObservation, reason: str
) -> None:
    ports = FakeAttackPorts([unsafe])
    result = configured_bot(ports).visit_once("tx-synthetic-0002")

    assert (result.status, result.reason_code, result.executed) == (
        "stopped",
        reason,
        False,
    )
    assert "intent" not in ports.events
    assert "attack" not in ports.events


def test_kill_switch_is_rechecked_immediately_before_attack() -> None:
    ports = FakeAttackPorts(
        [observation()], kill_switch_values=[True, False]
    )

    result = configured_bot(ports).visit_once("tx-synthetic-0003")

    assert (result.status, result.reason_code, result.executed) == (
        "stopped",
        "KILL_SWITCH",
        False,
    )
    assert ports.events == ["kill-switch", "observe", "intent", "kill-switch"]


def test_unknown_post_attack_state_is_audited_unconfirmed_and_fails_closed() -> None:
    ports = FakeAttackPorts([observation(), observation(screen=Screen.UNKNOWN)])

    result = configured_bot(ports).visit_once("tx-synthetic-0004")

    assert result == VisitResult("failed", "POST_SCREEN_UNKNOWN", True, False)
    assert ports.events[-1] == "outcome:False"


def test_setup_run_pause_stop_status_are_local_and_redacted() -> None:
    ports = FakeAttackPorts([observation(), observation()])
    bot = LocalMvpBot(ports)

    initial = bot.status()
    assert initial.mode is LocalBotMode.UNCONFIGURED
    assert initial.configured is False
    assert repr(initial).find(ACCOUNT) == -1
    assert repr(initial).find(INSTANCE) == -1

    bot.setup(MvpConfiguration(TEAM, ACCOUNT, INSTANCE))
    assert bot.status().mode is LocalBotMode.STOPPED
    bot.run()
    assert bot.status().mode is LocalBotMode.RUNNING
    bot.pause()
    assert bot.status().mode is LocalBotMode.PAUSED
    with pytest.raises(MvpGameplayError, match="running"):
        bot.visit_once("tx-synthetic-paused")
    bot.run()
    bot.stop()
    assert bot.status().mode is LocalBotMode.STOPPED


def test_setup_accepts_exactly_one_local_team_account_and_instance() -> None:
    ports = FakeAttackPorts([])
    bot = LocalMvpBot(ports)

    with pytest.raises(MvpGameplayError):
        bot.setup(MvpConfiguration("", ACCOUNT, INSTANCE))
    with pytest.raises(MvpGameplayError):
        bot.setup(MvpConfiguration(TEAM, "", INSTANCE))
    with pytest.raises(MvpGameplayError):
        bot.setup(MvpConfiguration(TEAM, ACCOUNT, ""))

    bot.setup(MvpConfiguration(TEAM, ACCOUNT, INSTANCE))
    with pytest.raises(MvpGameplayError, match="stopped"):
        bot.setup(MvpConfiguration(TEAM, ACCOUNT, "instance-synthetic-b"))


def test_attack_only_surface_has_no_upgrade_reward_or_purchase_executor() -> None:
    public = {name for name in dir(LocalMvpBot) if not name.startswith("_")}

    assert public == {"pause", "run", "setup", "status", "stop", "visit_once"}
