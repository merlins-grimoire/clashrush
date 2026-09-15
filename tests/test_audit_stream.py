from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

import clash_rush_rebuild.audit_stream as audit_stream_module
from clash_rush_rebuild.audit_event import (
    AccountRef,
    AuditEventKind,
    AuditOutcome,
    ReasonCode,
    TeamRef,
    decode_audit_event,
)
from clash_rush_rebuild.audit_stream import (
    AuditedInputStatus,
    DurableAuditStream,
    InputResult,
    execute_audited_input,
)
from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state_v2 import ReadyV2
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
    VisitGeneration,
)
from clash_rush_rebuild.scheduler_journal import ActionState, transition_action
from clash_rush_rebuild.scheduler_store import SQLiteJobStore

TEAM = TeamRef("1" * 32)
ACCOUNT_REF = AccountRef("2" * 32)
CONFIGURATION = ConfigurationGeneration("3" * 32)
ACCOUNT_KEY = AccountKey("synthetic-account")
VISIT_NONCE = "4" * 32
ACTION_NONCE = "5" * 32
INTENT_FINGERPRINT = "6" * 64


def make_planned_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SQLiteJobStore, object]:
    nonces = iter((VISIT_NONCE, ACTION_NONCE))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    store = SQLiteJobStore(tmp_path / "scheduler.sqlite3")
    store.insert(
        QueueJob(
            account_key=ACCOUNT_KEY,
            configuration_generation=CONFIGURATION,
            job_generation=JobGeneration(1),
            availability_event=AvailabilityEvent("7" * 32),
            available_at=10.0,
            due_at=20.0,
            pending_lane=Lane.HOME,
            state=QueueState.ELIGIBLE,
        )
    )
    admitted = store.admit_oldest(
        ready=ReadyV2(CONFIGURATION, ACCOUNT_KEY),
        now=20.0,
        account_order=(ACCOUNT_KEY,),
    )
    assert admitted is not None
    return store, store.plan_action(
        admitted,
        lane=Lane.HOME,
        intent_fingerprint=INTENT_FINGERPRINT,
    )


def test_durable_stream_appends_canonical_generations_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    clock_values = iter((101, 102))
    wall_values = iter(
        ("2030-01-02T03:04:05.000006Z", "2030-01-02T03:04:06.000007Z")
    )

    first = DurableAuditStream(
        path,
        monotonic_clock=lambda: next(clock_values),
        wall_clock=lambda: next(wall_values),
    ).append(
        team=TEAM,
        account_ref=ACCOUNT_REF,
        visit_generation=VisitGeneration(9),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_INTENT,
        reason_code=ReasonCode.ACTION_AUTHORIZED,
        outcome=AuditOutcome.PENDING,
    )
    second = DurableAuditStream(
        path,
        monotonic_clock=lambda: next(clock_values),
        wall_clock=lambda: next(wall_values),
    ).append(
        team=TEAM,
        account_ref=ACCOUNT_REF,
        visit_generation=VisitGeneration(9),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_OUTCOME,
        reason_code=ReasonCode.POSTCONDITION_CONFIRMED,
        outcome=AuditOutcome.SUCCEEDED,
    )

    records = tuple(decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines())
    assert records == (first, second)
    assert (first.event_generation, second.event_generation) == (1, 2)


def test_open_streams_resynchronize_generation_before_each_append(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first_stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    second_stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 102,
        wall_clock=lambda: "2030-01-02T03:04:06.000007Z",
    )

    first = first_stream.append(
        team=TEAM,
        account_ref=ACCOUNT_REF,
        visit_generation=VisitGeneration(9),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_INTENT,
        reason_code=ReasonCode.ACTION_AUTHORIZED,
        outcome=AuditOutcome.PENDING,
    )
    second = second_stream.append(
        team=TEAM,
        account_ref=ACCOUNT_REF,
        visit_generation=VisitGeneration(9),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_OUTCOME,
        reason_code=ReasonCode.POSTCONDITION_CONFIRMED,
        outcome=AuditOutcome.SUCCEEDED,
    )

    assert (first.event_generation, second.event_generation) == (1, 2)
    assert [
        decode_audit_event(line + b"\n").event_generation
        for line in path.read_bytes().splitlines()
    ] == [1, 2]


def test_pre_input_audit_failure_suppresses_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    calls: list[str] = []
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    monkeypatch.setattr("clash_rush_rebuild.audit_stream.os.write", lambda fd, data: (_ for _ in ()).throw(OSError("synthetic failure")))

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=lambda: calls.append("input") or InputResult.SUCCEEDED,
    )

    assert report.status is AuditedInputStatus.SUPPRESSED
    assert report.action.state is ActionState.INTENT_RECORDED
    assert calls == []
    assert store.load_actions() == (report.action,)
    store.close()


def test_successful_input_has_durable_intent_and_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    monotonic_values = iter((101, 102))
    wall_values = iter(
        ("2030-01-02T03:04:05.000006Z", "2030-01-02T03:04:06.000007Z")
    )
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: next(monotonic_values),
        wall_clock=lambda: next(wall_values),
    )

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.BUILDER,
        input_operation=lambda: InputResult.SUCCEEDED,
    )

    events = tuple(decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines())
    assert report.status is AuditedInputStatus.CONFIRMED
    assert report.action.state is ActionState.CONFIRMED
    assert tuple(event.kind for event in events) == (
        AuditEventKind.ACTION_INTENT,
        AuditEventKind.ACTION_OUTCOME,
    )
    assert tuple(event.outcome for event in events) == (
        AuditOutcome.PENDING,
        AuditOutcome.SUCCEEDED,
    )
    assert store.load_actions() == (report.action,)
    store.close()


def test_post_input_audit_failure_marks_action_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    real_write = __import__("os").write
    writes = 0

    def fail_second_write(descriptor: int, record: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic post-input failure")
        return real_write(descriptor, record)

    monkeypatch.setattr("clash_rush_rebuild.audit_stream.os.write", fail_second_write)
    input_calls: list[str] = []

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=lambda: input_calls.append("input") or InputResult.SUCCEEDED,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert input_calls == ["input"]
    assert store.load_replayable_actions() == ()
    assert [
        decode_audit_event(line + b"\n").kind for line in path.read_bytes().splitlines()
    ] == [AuditEventKind.ACTION_INTENT]
    store.close()


def test_input_cannot_replace_audit_syscalls_to_fake_outcome_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    captured: dict[str, bytes] = {}

    def replace_outcome_syscalls() -> InputResult:
        def fake_write(descriptor: int, record: bytes) -> int:
            captured["record"] = record
            return len(record)

        monkeypatch.setattr(audit_stream_module.os, "write", fake_write)
        monkeypatch.setattr(audit_stream_module.os, "fsync", lambda descriptor: None)
        monkeypatch.setattr(
            audit_stream_module.os,
            "lseek",
            lambda descriptor, offset, whence: path.stat().st_size
            + len(captured["record"]),
        )
        monkeypatch.setattr(
            audit_stream_module.os,
            "read",
            lambda descriptor, size: captured["record"],
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=replace_outcome_syscalls,
    )

    events = tuple(
        decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines()
    )
    assert [event.kind for event in events] == [AuditEventKind.ACTION_INTENT]
    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


@pytest.mark.parametrize("target", ("append", "encode", "generation"))
def test_input_cannot_mutate_audit_function_code_to_fake_outcome_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def replace_function_code() -> InputResult:
        if target == "append":
            function = audit_stream_module._PRISTINE_AUDIT_APPEND

            def replacement(self: object, **kwargs: object) -> None:
                return None

        elif target == "encode":
            function = audit_stream_module.encode_audit_event
            intent_record = path.read_bytes()

            def replacement(event: object, record: bytes = intent_record) -> bytes:
                return record

        else:
            function = audit_stream_module._PRISTINE_LOAD_NEXT_GENERATION

            def replacement(stream_path: Path) -> int:
                return 1

        monkeypatch.setattr(function, "__code__", replacement.__code__)
        monkeypatch.setattr(function, "__defaults__", replacement.__defaults__)
        monkeypatch.setattr(function, "__kwdefaults__", replacement.__kwdefaults__)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=replace_function_code,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_mutate_atomic_finalizer_code_to_fake_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def replace_finalizer_code() -> InputResult:
        function = audit_stream_module._PRISTINE_ATOMIC_FINALIZE

        def make_replacement(
            binding_seal: object,
            decode_action: object,
            require_action: object,
            require_open: object,
            terminal_states: object,
            transition: object,
        ) -> object:
            def replacement(
                journal: object,
                action: object,
                outcome: object,
                pending_lane: object,
            ) -> object:
                _ = (
                    binding_seal,
                    decode_action,
                    require_action,
                    require_open,
                    terminal_states,
                )
                return transition(action, next_state=ActionState.CONFIRMED)

            return replacement

        replacement = make_replacement(None, None, None, None, None, None)
        monkeypatch.setattr(function, "__code__", replacement.__code__)
        monkeypatch.setattr(function, "__defaults__", replacement.__defaults__)
        monkeypatch.setattr(function, "__kwdefaults__", replacement.__kwdefaults__)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=replace_finalizer_code,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_actions() == (report.action,)
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_rebind_input_completion_to_skip_durable_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    store_path = tmp_path / "scheduler.sqlite3"
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def replace_completion_dispatch() -> InputResult:
        monkeypatch.setattr(
            SQLiteJobStore,
            "mark_input_completed",
            lambda _store, action: transition_action(
                action, next_state=ActionState.INPUT_COMPLETED
            ),
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=replace_completion_dispatch,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    store.close()
    with SQLiteJobStore(store_path) as reopened:
        assert reopened.load_actions() == (report.action,)
        assert reopened.load_replayable_actions() == ()


def test_input_cannot_mutate_finalizer_transition_code_to_corrupt_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    store_path = tmp_path / "scheduler.sqlite3"
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def replace_transition_code() -> InputResult:
        function = transition_action

        def replacement(action: object, *, next_state: object) -> object:
            _ = next_state
            return action

        monkeypatch.setattr(function, "__code__", replacement.__code__)
        monkeypatch.setattr(function, "__defaults__", replacement.__defaults__)
        monkeypatch.setattr(function, "__kwdefaults__", replacement.__kwdefaults__)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=replace_transition_code,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    store.close()
    with SQLiteJobStore(store_path) as reopened:
        assert reopened.load_actions() == (report.action,)
        assert reopened.load_replayable_actions() == ()


def test_input_cannot_empty_finalizer_closure_cell_to_strand_started_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    store_path = tmp_path / "scheduler.sqlite3"
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def empty_transition_cell() -> InputResult:
        finalizer = audit_stream_module._PRISTINE_ATOMIC_FINALIZE
        transition_cell = next(
            cell
            for cell in (finalizer.__closure__ or ())
            if cell.cell_contents is transition_action
        )
        monkeypatch.delattr(transition_cell, "cell_contents")
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=empty_transition_cell,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    store.close()
    with SQLiteJobStore(store_path) as reopened:
        assert reopened.load_actions() == (report.action,)
        assert reopened.load_replayable_actions() == ()


def test_base_exception_from_input_is_durably_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def interrupted_input() -> InputResult:
        raise KeyboardInterrupt("synthetic interruption after input admission")

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=interrupted_input,
    )

    events = tuple(decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines())
    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert events[-1].outcome is AuditOutcome.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_stream_state_cannot_be_instance_shadowed_to_duplicate_generation(
    tmp_path: Path,
) -> None:
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    stream.append(
        team=TEAM,
        account_ref=ACCOUNT_REF,
        visit_generation=VisitGeneration(9),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_INTENT,
        reason_code=ReasonCode.ACTION_AUTHORIZED,
        outcome=AuditOutcome.PENDING,
    )

    with pytest.raises(AttributeError):
        stream._load_next_generation = lambda: 1  # type: ignore[method-assign]


def test_input_cannot_redirect_outcome_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    original_path = tmp_path / "audit.jsonl"
    redirected_path = tmp_path / "redirected.jsonl"
    stream = DurableAuditStream(
        original_path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def redirect_then_succeed() -> InputResult:
        stream._path = redirected_path  # type: ignore[attr-defined]
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=redirect_then_succeed,
    )

    events = tuple(
        decode_audit_event(line + b"\n") for line in original_path.read_bytes().splitlines()
    )
    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert events[-1].outcome is AuditOutcome.UNCERTAIN
    assert not redirected_path.exists()
    store.close()


def test_low_level_stream_mutation_after_input_forces_original_path_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    original_path = tmp_path / "audit.jsonl"
    redirected_path = tmp_path / "redirected.jsonl"
    stream = DurableAuditStream(
        original_path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def corrupt_capability_then_succeed() -> InputResult:
        object.__setattr__(
            stream, "_DurableAuditStream__path", redirected_path
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=corrupt_capability_then_succeed,
    )

    events = tuple(
        decode_audit_event(line + b"\n") for line in original_path.read_bytes().splitlines()
    )
    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert [event.outcome for event in events] == [
        AuditOutcome.PENDING,
        AuditOutcome.UNCERTAIN,
    ]
    assert not redirected_path.exists()
    store.close()


def test_post_input_close_base_exception_marks_action_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )
    real_close = os.close
    closes = 0

    def fail_second_close(descriptor: int) -> None:
        nonlocal closes
        closes += 1
        real_close(descriptor)
        if closes == 2:
            raise KeyboardInterrupt("synthetic post-input close failure")

    monkeypatch.setattr("clash_rush_rebuild.audit_stream.os.close", fail_second_close)

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=lambda: InputResult.SUCCEEDED,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


@pytest.mark.parametrize("mutation", ("unlink", "truncate", "rename"))
def test_input_cannot_discard_intent_stream_and_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    moved = tmp_path / "moved.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def mutate_stream_path() -> InputResult:
        if mutation == "unlink":
            path.unlink()
        elif mutation == "truncate":
            path.write_bytes(b"")
        else:
            path.rename(moved)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=mutate_stream_path,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_transiently_truncate_and_restore_intent_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def truncate_and_restore() -> InputResult:
        intent_record = path.read_bytes()
        path.write_bytes(b"")
        path.write_bytes(intent_record)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=truncate_and_restore,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_rename_away_and_restore_intent_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    moved = tmp_path / "moved.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def rename_away_and_restore() -> InputResult:
        path.rename(moved)
        moved.rename(path)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=rename_away_and_restore,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_rewrite_intent_to_same_size_canonical_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def rewrite_canonical_record() -> InputResult:
        intent_record = path.read_bytes()
        rewritten = intent_record.replace(ACTION_NONCE.encode(), b"8" * 32)
        assert rewritten != intent_record
        assert len(rewritten) == len(intent_record)
        assert decode_audit_event(rewritten).action_nonce == "8" * 32
        path.write_bytes(rewritten)
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=rewrite_canonical_record,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


@pytest.mark.parametrize(
    "mutation", ("truncate_restore", "rename_restore", "rewrite_restore")
)
def test_input_cannot_hide_transient_stream_mutation_by_restoring_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    moved = tmp_path / "moved.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def mutate_restore_and_reset_timestamps() -> InputResult:
        intent_record = path.read_bytes()
        file_metadata = path.stat()
        parent_metadata = path.parent.stat()
        if mutation == "truncate_restore":
            path.write_bytes(b"")
            path.write_bytes(intent_record)
        elif mutation == "rename_restore":
            path.rename(moved)
            moved.rename(path)
        else:
            rewritten = intent_record.replace(ACTION_NONCE.encode(), b"8" * 32)
            assert rewritten != intent_record
            assert len(rewritten) == len(intent_record)
            path.write_bytes(rewritten)
            path.write_bytes(intent_record)
        os.utime(
            path,
            ns=(file_metadata.st_atime_ns, file_metadata.st_mtime_ns),
        )
        os.utime(
            path.parent,
            ns=(parent_metadata.st_atime_ns, parent_metadata.st_mtime_ns),
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=mutate_restore_and_reset_timestamps,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_replace_change_time_reader_to_hide_stream_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def mutate_and_replace_change_time_reader() -> InputResult:
        intent_record = path.read_bytes()
        file_metadata = path.stat()
        parent_metadata = path.parent.stat()
        original_change_time = audit_stream_module._stream_file_seal(path)[5]
        path.write_bytes(b"")
        path.write_bytes(intent_record)
        os.utime(
            path,
            ns=(file_metadata.st_atime_ns, file_metadata.st_mtime_ns),
        )
        os.utime(
            path.parent,
            ns=(parent_metadata.st_atime_ns, parent_metadata.st_mtime_ns),
        )
        monkeypatch.setattr(
            audit_stream_module,
            "_stream_change_time",
            lambda descriptor: original_change_time,
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=mutate_and_replace_change_time_reader,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_cannot_mutate_change_time_defaults_to_hide_stream_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def mutate_and_replace_query_default() -> InputResult:
        intent_record = path.read_bytes()
        file_metadata = path.stat()
        parent_metadata = path.parent.stat()
        original_change_time = audit_stream_module._stream_file_seal(path)[5]
        original_defaults = audit_stream_module._stream_change_time.__defaults__
        assert original_defaults is not None
        path.write_bytes(b"")
        path.write_bytes(intent_record)
        os.utime(
            path,
            ns=(file_metadata.st_atime_ns, file_metadata.st_mtime_ns),
        )
        os.utime(
            path.parent,
            ns=(parent_metadata.st_atime_ns, parent_metadata.st_mtime_ns),
        )

        def fake_query(handle: int, info_class: int, buffer: object, size: int) -> int:
            information = ctypes.cast(
                buffer,
                ctypes.POINTER(audit_stream_module._FileBasicInfo),
            ).contents
            information.change_time = original_change_time
            return 1

        changed_defaults = list(original_defaults)
        changed_defaults[5] = fake_query
        monkeypatch.setattr(
            audit_stream_module._stream_change_time,
            "__defaults__",
            tuple(changed_defaults),
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=mutate_and_replace_query_default,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()


def test_input_closing_scheduler_is_recovered_as_terminal_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    database_path = store.path
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=lambda: store.close() or InputResult.SUCCEEDED,
    )

    with SQLiteJobStore(database_path) as reopened:
        assert report.status is AuditedInputStatus.UNCERTAIN
        assert report.action.state is ActionState.UNCERTAIN
        assert reopened.load_actions() == (report.action,)
        assert reopened.load_replayable_actions() == ()


def test_input_cannot_class_shadow_outcome_append_and_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def shadow_append() -> InputResult:
        monkeypatch.setattr(
            DurableAuditStream, "append", lambda self, **kwargs: None
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=shadow_append,
    )

    events = tuple(decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines())
    assert [event.kind for event in events] == [AuditEventKind.ACTION_INTENT]
    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    store.close()


def test_input_cannot_class_shadow_generation_allocator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    path = tmp_path / "audit.jsonl"
    stream = DurableAuditStream(
        path,
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def shadow_generation() -> InputResult:
        monkeypatch.setattr(
            DurableAuditStream,
            "_load_next_generation",
            staticmethod(lambda path: 1),
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=shadow_generation,
    )

    events = tuple(decode_audit_event(line + b"\n") for line in path.read_bytes().splitlines())
    assert [event.event_generation for event in events] == [1, 2]
    assert report.status is AuditedInputStatus.CONFIRMED
    assert report.action.state is ActionState.CONFIRMED
    store.close()


@pytest.mark.parametrize(
    "helper_name",
    (
        "_require_action",
        "_require_open",
        "_decode_action",
        "_action_binding_seal",
        "finalize_action",
    ),
)
def test_input_cannot_class_shadow_scheduler_finalization_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_name: str
) -> None:
    store, planned = make_planned_action(tmp_path, monkeypatch)
    stream = DurableAuditStream(
        tmp_path / "audit.jsonl",
        monotonic_clock=lambda: 101,
        wall_clock=lambda: "2030-01-02T03:04:05.000006Z",
    )

    def shadow_finalization() -> InputResult:
        monkeypatch.setattr(
            SQLiteJobStore,
            helper_name,
            lambda self, action, **kwargs: action,
        )
        return InputResult.SUCCEEDED

    report = execute_audited_input(
        stream,
        store,
        team=TEAM,
        account_ref=ACCOUNT_REF,
        action=planned,
        pending_lane=Lane.HOME,
        input_operation=shadow_finalization,
    )

    assert report.status is AuditedInputStatus.UNCERTAIN
    assert report.action.state is ActionState.UNCERTAIN
    assert store.load_replayable_actions() == ()
    store.close()
