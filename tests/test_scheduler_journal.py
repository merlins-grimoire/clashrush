from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
)
from clash_rush_rebuild.scheduler_journal import (
    ActionState,
    SchedulerJournalError,
    transition_action,
)
from clash_rush_rebuild.scheduler_store import (
    _CREATE_JOBS_SQL,
    _CREATE_VISITS_SQL,
    SQLiteJobStore,
    SchedulerStoreError,
)


CONFIGURATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNT = AccountKey("account-a")
NONCE = "d" * 32
ACTION_NONCE = "e" * 32
INTENT_FINGERPRINT = "f" * 64


def eligible_job() -> QueueJob:
    return QueueJob(
        account_key=ACCOUNT,
        configuration_generation=CONFIGURATION,
        job_generation=JobGeneration(1),
        availability_event=AvailabilityEvent("a" * 32),
        available_at=50.0,
        due_at=100.0,
        pending_lane=Lane.HOME,
        state=QueueState.ELIGIBLE,
    )


def admit(store: SQLiteJobStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: NONCE,
    )
    store.insert(eligible_job())
    admitted = store.admit_oldest(
        ready=ReadyV2(CONFIGURATION, ACCOUNT),
        now=100.0,
        account_order=(ACCOUNT,),
    )
    assert admitted is not None
    return admitted


def test_input_started_action_cannot_be_claimed_again_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        planned = store.plan_action(
            admitted, lane=Lane.HOME, intent_fingerprint=INTENT_FINGERPRINT
        )
        recorded = store.record_action_intent(planned)
        started = store.mark_input_started(recorded)
        assert started.state is ActionState.INPUT_STARTED

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_actions() == (started,)
        assert restarted.load_replayable_actions() == ()
        with pytest.raises(SchedulerStoreError, match="INPUT_STARTED"):
            restarted.mark_input_started(started)
        assert restarted.load_actions() == (started,)


def test_input_completed_action_is_not_replayable_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        planned = store.plan_action(
            admitted, lane=Lane.HOME, intent_fingerprint=INTENT_FINGERPRINT
        )
        recorded = store.record_action_intent(planned)
        completed = store.mark_input_completed(store.mark_input_started(recorded))

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_actions() == (completed,)
        assert restarted.load_replayable_actions() == ()
        with pytest.raises(SchedulerStoreError, match="INPUT_COMPLETED"):
            restarted.mark_input_started(completed)


def test_terminal_outcome_and_pending_lane_commit_atomically_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        action = store.mark_input_completed(
            store.mark_input_started(
                store.record_action_intent(
                    store.plan_action(
                        admitted,
                        lane=Lane.HOME,
                        intent_fingerprint=INTENT_FINGERPRINT,
                    )
                )
            )
        )
        finalized = store.finalize_action(
            action,
            outcome=ActionState.CONFIRMED,
            pending_lane=Lane.BUILDER,
        )

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_actions() == (finalized,)
        assert restarted.load_replayable_actions() == ()
        assert restarted.load_all()[0].pending_lane is Lane.BUILDER
        assert restarted.load_admitted_visits()[0].job.pending_lane is Lane.BUILDER


def test_terminal_update_failure_rolls_back_outcome_and_pending_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        action = store.mark_input_completed(
            store.mark_input_started(
                store.record_action_intent(
                    store.plan_action(
                        admitted,
                        lane=Lane.HOME,
                        intent_fingerprint=INTENT_FINGERPRINT,
                    )
                )
            )
        )
        connection = store._connection
        assert connection is not None
        connection.execute(
            """CREATE TRIGGER reject_pending_lane
               BEFORE UPDATE OF pending_lane ON scheduler_jobs
               BEGIN SELECT RAISE(ABORT, 'synthetic'); END"""
        )

        with pytest.raises(SchedulerStoreError, match="finalization failed"):
            store.finalize_action(
                action,
                outcome=ActionState.CONFIRMED,
                pending_lane=Lane.BUILDER,
            )

        assert store.load_actions() == (action,)
        assert store.load_all()[0].pending_lane is Lane.HOME
        assert store.load_admitted_visits()[0].job.pending_lane is Lane.HOME


def test_terminal_intent_fingerprint_cannot_be_planned_again_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        started = store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )
        store.finalize_action(
            started,
            outcome=ActionState.UNCERTAIN,
            pending_lane=Lane.HOME,
        )

    with SQLiteJobStore(path) as restarted:
        with pytest.raises(SchedulerStoreError, match="already journaled"):
            restarted.plan_action(
                restarted.load_admitted_visits()[0],
                lane=Lane.HOME,
                intent_fingerprint=INTENT_FINGERPRINT,
            )


def test_nonterminal_action_blocks_a_second_plan_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        started = store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )

    with SQLiteJobStore(path) as restarted:
        with pytest.raises(SchedulerStoreError, match="nonterminal action"):
            restarted.plan_action(
                restarted.load_admitted_visits()[0],
                lane=Lane.BUILDER,
                intent_fingerprint="1" * 64,
            )
        assert restarted.load_actions() == (started,)


def test_known_version_two_database_migrates_with_an_empty_action_journal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        f"{_CREATE_JOBS_SQL}; {_CREATE_VISITS_SQL}; PRAGMA user_version = 2;"
    )
    connection.close()

    with SQLiteJobStore(path) as store:
        assert store.load_all() == ()
        assert store.load_admitted_visits() == ()
        assert store.load_actions() == ()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    connection.close()


def test_terminal_state_with_nonterminal_marker_blocks_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE scheduler_actions SET state = 'CONFIRMED'")
    connection.rollback()
    connection.close()

    with SQLiteJobStore(path) as restarted:
        assert restarted.load_actions()[0].state is ActionState.INPUT_STARTED


def test_internal_transition_helper_cannot_skip_or_reverse_the_state_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        planned = store.plan_action(
            admitted,
            lane=Lane.HOME,
            intent_fingerprint=INTENT_FINGERPRINT,
        )
        with pytest.raises(SchedulerStoreError, match="transition"):
            store._transition_action(
                planned,
                required_state=ActionState.PLANNED,
                next_state=ActionState.INPUT_STARTED,
            )
        assert store.load_actions() == (planned,)

        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(planned, "state", ActionState.INPUT_STARTED)
        with pytest.raises(SchedulerJournalError, match="forbidden"):
            transition_action(planned, next_state=ActionState.INPUT_COMPLETED)


def test_invalid_version_two_admission_is_rejected_without_schema_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    orphan = eligible_job()
    connection = sqlite3.connect(path)
    connection.executescript(
        f"{_CREATE_JOBS_SQL}; {_CREATE_VISITS_SQL}; PRAGMA user_version = 2;"
    )
    values = SQLiteJobStore._values(
        QueueJob(
            account_key=orphan.account_key,
            configuration_generation=orphan.configuration_generation,
            job_generation=orphan.job_generation,
            availability_event=orphan.availability_event,
            available_at=orphan.available_at,
            due_at=orphan.due_at,
            pending_lane=orphan.pending_lane,
            state=QueueState.ADMITTED,
        )
    )
    connection.execute(
        """INSERT INTO scheduler_jobs (
               account_key, configuration_generation, job_generation,
               availability_event, available_at, due_at, pending_lane, state,
               reconcile_at, yield_set, prior_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )
    connection.commit()
    before = connection.execute(
        "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="admission integrity"):
        SQLiteJobStore(path)

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert connection.execute(
        "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall() == before
    connection.close()


def test_terminal_action_with_changed_visit_binding_blocks_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        started = store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )
        store.finalize_action(
            started,
            outcome=ActionState.UNCERTAIN,
            pending_lane=Lane.HOME,
        )

    connection = sqlite3.connect(path)
    connection.execute(
        """UPDATE scheduler_actions
           SET visit_generation = 999, visit_nonce = ?""",
        ("1" * 32,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="journal integrity"):
        SQLiteJobStore(path)


def test_terminal_action_lane_and_snapshot_change_blocks_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        started = store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )
        store.finalize_action(
            started,
            outcome=ActionState.CONFIRMED,
            pending_lane=Lane.HOME,
        )

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE scheduler_actions SET lane = 'BUILDER', lane_snapshot = 'BUILDER'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="journal integrity"):
        SQLiteJobStore(path)


def test_coherent_terminal_to_replayable_rewrite_blocks_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    with SQLiteJobStore(path) as store:
        admitted = admit(store, monkeypatch)
        monkeypatch.setattr(
            "clash_rush_rebuild.scheduler_store.secrets.token_hex",
            lambda size: ACTION_NONCE,
        )
        started = store.mark_input_started(
            store.record_action_intent(
                store.plan_action(
                    admitted,
                    lane=Lane.HOME,
                    intent_fingerprint=INTENT_FINGERPRINT,
                )
            )
        )
        store.finalize_action(
            started,
            outcome=ActionState.UNCERTAIN,
            pending_lane=Lane.HOME,
        )

    connection = sqlite3.connect(path)
    connection.execute(
        """UPDATE scheduler_actions
           SET state = 'PLANNED', open_singleton = 1,
               input_started = 0, input_completed = 0"""
    )
    connection.commit()
    connection.close()

    with pytest.raises(SchedulerStoreError, match="journal integrity"):
        SQLiteJobStore(path)
