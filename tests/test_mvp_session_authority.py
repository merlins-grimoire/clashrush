from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from clash_rush_rebuild.mvp_local_native import DurableSessionAudit
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_session_authority import (
    ActionPhase,
    ControlMode,
    DurableSessionAuthority,
    SessionAuthorityError,
    run_reversible_transaction,
)


CONFIG = MvpConfiguration("team-synthetic", "account-synthetic", "slot-2", "a" * 64)
TREE = "b" * 40
READY = b'{"schema":1,"state":"READY","next_slot":2}\n'
PURPOSE = "MVP_SINGLE_ACCOUNT_ATTACK"


def authority(tmp_path: Path) -> DurableSessionAuthority:
    store = DurableSessionAuthority(tmp_path / "session.sqlite3", nonce_factory=lambda: "1" * 32)
    store.setup(CONFIG)
    return store


def start(store: DurableSessionAuthority, *, nonce: str | None = None):
    return store.run(
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        deadline=200,
        now=100,
        nonce=nonce,
    )


def test_run_admission_is_atomic_one_use_and_exactly_bound(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)

    transaction = store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )

    assert transaction.phase is ActionPhase.PLANNED
    with pytest.raises(SessionAuthorityError, match="consumed"):
        store.admit(
            run_nonce=session.run_nonce,
            transaction_ref="tx-two",
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            configuration_revision=session.configuration_revision,
            control_revision=session.control_revision,
            now=102,
        )


def test_mismatched_selection_never_consumes_admission(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)

    with pytest.raises(SessionAuthorityError, match="READY"):
        store.admit(
            run_nonce=session.run_nonce,
            transaction_ref="tx-one",
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=b'{"schema":1,"state":"READY","next_slot":1}\n',
            configuration_revision=session.configuration_revision,
            control_revision=session.control_revision,
            now=101,
        )

    assert store.status().admission_consumed is False


def test_concurrent_pause_and_stop_cannot_lose_stop(tmp_path: Path) -> None:
    path = tmp_path / "session.sqlite3"
    store = authority(tmp_path)
    start(store)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def command(target: ControlMode, command_id: str) -> None:
        try:
            other = DurableSessionAuthority(path)
            barrier.wait()
            other.command(target, command_id=command_id)
            other.close()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=command, args=(ControlMode.PAUSED, "pause-one")),
        threading.Thread(target=command, args=(ControlMode.STOPPED, "stop-one")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    assert store.status().mode is ControlMode.STOPPED


def test_pause_is_irreversible_for_nonce_and_resume_creates_new_identity(tmp_path: Path) -> None:
    store = authority(tmp_path)
    first = start(store)
    store.command(ControlMode.PAUSED, command_id="pause-one")

    with pytest.raises(SessionAuthorityError, match="not current"):
        store.admit(
            run_nonce=first.run_nonce,
            transaction_ref="tx-old",
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            configuration_revision=first.configuration_revision,
            control_revision=first.control_revision,
            now=101,
        )

    resumed = store.resume(
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        deadline=220,
        now=110,
        nonce="2" * 32,
    )
    assert resumed.run_nonce != first.run_nonce


def test_duplicate_transaction_and_input_cannot_execute_twice(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    calls: list[str] = []

    result = run_reversible_transaction(
        store,
        "tx-one",
        execute=lambda: calls.append("input") or True,
        confirm=lambda: True,
    )
    assert result.phase is ActionPhase.CONFIRMED
    with pytest.raises(SessionAuthorityError, match="PLANNED"):
        run_reversible_transaction(
            store,
            "tx-one",
            execute=lambda: calls.append("duplicate") or True,
            confirm=lambda: True,
        )
    assert calls == ["input"]


def test_exception_after_input_started_is_durably_uncertain(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )

    def raises_after_input() -> bool:
        raise RuntimeError("synthetic executor fault")

    with pytest.raises(RuntimeError, match="synthetic"):
        run_reversible_transaction(store, "tx-one", execute=raises_after_input, confirm=lambda: True)

    assert store.transaction("tx-one").phase is ActionPhase.UNCERTAIN
    with pytest.raises(SessionAuthorityError, match="unresolved"):
        store.run(
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            deadline=300,
            now=120,
            nonce="3" * 32,
        )


def test_game_and_retirement_outcomes_remain_independent_until_final_ack(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    run_reversible_transaction(store, "tx-one", execute=lambda: True, confirm=lambda: True)

    before = store.status()
    assert before.game_outcome == "CONFIRMED"
    assert before.retirement_outcome is None
    assert before.final_acknowledged is False

    store.record_retirement(session.run_nonce, succeeded=False)
    after = store.status()
    assert after.game_outcome == "CONFIRMED"
    assert after.retirement_outcome == "FAILED"
    assert after.final_acknowledged is True


def test_explicit_stopped_import_preserves_legacy_file(tmp_path: Path) -> None:
    legacy = tmp_path / "mvp-local-control.json"
    legacy.write_bytes(
        (
            json.dumps(
            {
                "schema": 2,
                "mode": "STOPPED",
                "configuration": {
                    "team_ref": CONFIG.team_ref,
                    "account_ref": CONFIG.account_ref,
                    "instance_ref": CONFIG.instance_ref,
                    "player_tag_sha256": CONFIG.player_tag_sha256,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    )
    before = legacy.read_bytes()
    store = DurableSessionAuthority(tmp_path / "session.sqlite3")

    store.import_stopped_legacy(legacy)

    assert store.status().mode is ControlMode.STOPPED
    assert legacy.read_bytes() == before


@pytest.mark.parametrize(
    "phases",
    [
        (),
        ((ActionPhase.PLANNED, ActionPhase.INTENT_RECORDED),),
        (
            (ActionPhase.PLANNED, ActionPhase.INTENT_RECORDED),
            (ActionPhase.INTENT_RECORDED, ActionPhase.INPUT_STARTED),
        ),
        (
            (ActionPhase.PLANNED, ActionPhase.INTENT_RECORDED),
            (ActionPhase.INTENT_RECORDED, ActionPhase.INPUT_STARTED),
            (ActionPhase.INPUT_STARTED, ActionPhase.INPUT_COMPLETED),
        ),
    ],
)
def test_every_preterminal_crash_cut_reopens_truthfully_and_blocks_replay(
    tmp_path: Path, phases: tuple[tuple[ActionPhase, ActionPhase], ...]
) -> None:
    path = tmp_path / "session.sqlite3"
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    for expected, target in phases:
        store.transition_action("tx-one", expected=expected, target=target)
    expected_phase = phases[-1][1] if phases else ActionPhase.PLANNED
    store.close()

    reopened = DurableSessionAuthority(path)
    assert reopened.transaction("tx-one").phase is expected_phase
    reopened.command(ControlMode.STOPPED, command_id="stop-after-crash")
    with pytest.raises(SessionAuthorityError, match="unresolved"):
        reopened.run(
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            deadline=250,
            now=110,
            nonce="4" * 32,
        )


def test_final_ack_survives_reopen_only_after_both_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "session.sqlite3"
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    run_reversible_transaction(store, "tx-one", execute=lambda: True, confirm=lambda: True)
    store.record_retirement(session.run_nonce, succeeded=True)
    store.close()

    receipt = DurableSessionAuthority(path).status()
    assert receipt.final_acknowledged is True
    assert (receipt.game_outcome, receipt.retirement_outcome) == ("CONFIRMED", "SUCCEEDED")


def test_expired_or_substituted_admission_does_not_consume_run(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)

    with pytest.raises(SessionAuthorityError, match="deadline"):
        store.admit(
            run_nonce=session.run_nonce,
            transaction_ref="tx-one",
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            configuration_revision=session.configuration_revision,
            control_revision=session.control_revision,
            now=200,
        )

    assert store.status().admission_consumed is False


def test_completed_input_with_unavailable_confirmation_is_uncertain(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    store.transition_action(
        "tx-one", expected=ActionPhase.PLANNED, target=ActionPhase.INTENT_RECORDED
    )
    store.transition_action(
        "tx-one", expected=ActionPhase.INTENT_RECORDED, target=ActionPhase.INPUT_STARTED
    )
    store.transition_action(
        "tx-one", expected=ActionPhase.INPUT_STARTED, target=ActionPhase.INPUT_COMPLETED
    )
    DurableSessionAudit(store, "tx-one").outcome("tx-one", False)
    assert store.transaction("tx-one").phase is ActionPhase.UNCERTAIN


def test_missing_retirement_outcome_blocks_new_run(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )
    run_reversible_transaction(store, "tx-one", execute=lambda: True, confirm=lambda: True)
    store.command(ControlMode.STOPPED, command_id="stop-one")
    with pytest.raises(SessionAuthorityError, match="unresolved"):
        store.run(
            purpose=PURPOSE,
            candidate_tree=TREE,
            ready_bytes=READY,
            deadline=250,
            now=110,
            nonce="5" * 32,
        )


def test_control_command_requires_exact_mode_type(tmp_path: Path) -> None:
    store = authority(tmp_path)
    start(store)
    with pytest.raises(SessionAuthorityError, match="invalid control"):
        store.command("PAUSED", command_id="bad-type")  # type: ignore[arg-type]


def test_reversible_executor_negative_postcondition_is_uncertain(tmp_path: Path) -> None:
    store = authority(tmp_path)
    session = start(store)
    store.admit(
        run_nonce=session.run_nonce,
        transaction_ref="tx-one",
        purpose=PURPOSE,
        candidate_tree=TREE,
        ready_bytes=READY,
        configuration_revision=session.configuration_revision,
        control_revision=session.control_revision,
        now=101,
    )

    result = run_reversible_transaction(
        store, "tx-one", execute=lambda: True, confirm=lambda: False
    )

    assert result.phase is ActionPhase.UNCERTAIN
