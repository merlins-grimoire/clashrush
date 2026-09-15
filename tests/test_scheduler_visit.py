from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationGeneration,
    SchemaV2Configuration,
)
from clash_rush_rebuild.lifecycle import (
    AcquiredMutexLease,
    CreatedProcess,
    MemberSnapshot,
    PlayerBinding,
    PlayerSnapshot,
    ProcessIdentity,
)
from clash_rush_rebuild.lifecycle_state import BlockReason
from clash_rush_rebuild.lifecycle_state_v2 import ActiveV2, ReadyV2, decode_state_v2, encode_state_v2
from clash_rush_rebuild.lifecycle_v2 import SchemaV2LifecycleSupervisor
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.scheduler_admission import admit_and_start_oldest
from clash_rush_rebuild.scheduler_contract import (
    AvailabilityEvent,
    JobGeneration,
    Lane,
    QueueJob,
    QueueState,
)
from clash_rush_rebuild.scheduler_store import SQLiteJobStore, SchedulerStoreError
from clash_rush_rebuild.scheduler_successor import SuccessorKind
from clash_rush_rebuild.scheduler_visit import (
    RecoveryOutcome,
    SchedulerVisitError,
    SuccessorRequest,
    finish_started_visit,
    reconcile_stopped_visit,
)


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
ACCOUNTS = tuple(AccountKey(f"account-{index}") for index in range(5))
CONFIGURATION = SchemaV2Configuration(
    GENERATION,
    tuple(AccountRecord(account) for account in ACCOUNTS),
)
SLOTS = tuple(
    Slot(index, f"Pie64_{index}", f"Synthetic Slot {index}", 1280, 720, 240)
    for index in range(5)
)


@dataclass
class DurableState:
    state: ReadyV2 | ActiveV2
    fail_ready_commit: bool = False

    def load(self) -> ReadyV2 | ActiveV2:
        return self.state

    def commit(self, state: ReadyV2 | ActiveV2) -> ReadyV2 | ActiveV2:
        if self.fail_ready_commit and type(state) is ReadyV2:
            raise RuntimeError("synthetic READY commit interruption")
        self.state = state
        return state


class InertLifecycleHost:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.running = False
        self.running_count = 0
        self.maximum_overlap = 0
        self.current_identity: ProcessIdentity | None = None
        self.forbidden_native_input_calls = 0
        self.member_queries = 0
        self.fail_stop_membership = False

    def complete_player_snapshot(self) -> PlayerSnapshot:
        identities = () if self.current_identity is None else (self.current_identity,)
        return PlayerSnapshot(identities, lambda: None)

    def create_job(self) -> object:
        return object()

    def set_kill_on_close(self, job: object) -> None:
        del job

    def create_suspended(self, internal_name: str) -> CreatedProcess:
        index = int(internal_name.rsplit("_", 1)[1])
        assert not self.running
        self.running = True
        self.running_count += 1
        self.maximum_overlap = max(self.maximum_overlap, self.running_count)
        self.current_identity = ProcessIdentity(100 + index, 9000 + index)
        self.trace.append(f"START{index}")
        return CreatedProcess(f"process-{index}", f"thread-{index}", 100 + index)

    def identity_from_handle(self, process: object, expected_pid: int) -> ProcessIdentity:
        del process, expected_pid
        assert self.current_identity is not None
        return self.current_identity

    def assign_to_job(self, job: object, process: object) -> None:
        del job, process

    def is_process_in_job(self, process: object, job: object) -> bool:
        del process, job
        return True

    def resume_thread(self, thread: object) -> int:
        del thread
        return 1

    def close_thread(self, thread: object) -> None:
        del thread

    def stable_job_members(self, job: object) -> MemberSnapshot:
        del job
        self.member_queries += 1
        if self.fail_stop_membership and self.member_queries % 2 == 0:
            raise RuntimeError("synthetic stop membership failure")
        assert self.current_identity is not None
        return MemberSnapshot((self.current_identity,), lambda: None)

    def bind_exact(
        self,
        identity: ProcessIdentity,
        expected_title: str,
        members: MemberSnapshot,
    ) -> PlayerBinding:
        del expected_title, members
        return PlayerBinding(identity, identity, 101, 102, 1280, 720, "a" * 32)

    def capture_ready(self, binding: PlayerBinding, job: object) -> None:
        del binding, job

    def terminate_job(self, job: object) -> None:
        del job
        assert self.running
        assert self.current_identity is not None
        index = self.current_identity.pid - 100
        self.trace.append(f"STOP{index}")
        self.running = False
        self.running_count -= 1
        self.current_identity = None

    def terminate_retained_process(self, process: object) -> None:
        del process
        self.running = False
        self.running_count = 0
        self.current_identity = None

    def wait_process(self, process: object, milliseconds: int) -> bool:
        del process, milliseconds
        return True

    def job_active_count(self, job: object) -> int:
        del job
        return 0

    def is_window(self, hwnd: int) -> bool:
        del hwnd
        return False

    def close_handle(self, handle: object) -> None:
        del handle


def make_supervisor(
    state: DurableState, host: InertLifecycleHost
) -> SchemaV2LifecycleSupervisor:
    return SchemaV2LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        configuration=CONFIGURATION,
        slots=SLOTS,
        readiness_attempts=1,
    )


def make_job(account: AccountKey, *, generation: int = 1, due_at: float = 100.0) -> QueueJob:
    return QueueJob(
        account_key=account,
        configuration_generation=GENERATION,
        job_generation=JobGeneration(generation),
        availability_event=AvailabilityEvent(f"{generation:x}" * 32),
        available_at=due_at,
        due_at=due_at,
        pending_lane=Lane.HOME,
        state=QueueState.ELIGIBLE,
    )


def test_five_account_dual_lane_trace_runs_twice_across_restart_without_native_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    nonces = (f"{value:032x}" for value in range(1, 100))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    trace: list[str] = []
    host = InertLifecycleHost(trace)
    state = DurableState(ReadyV2(GENERATION, ACCOUNTS[-1]))
    lifecycle = make_supervisor(state, host)

    with SQLiteJobStore(path) as store:
        for account in ACCOUNTS:
            store.insert(make_job(account))
        for index in range(5):
            started = admit_and_start_oldest(
                store,
                lifecycle,
                ready=state.state,
                now=100.0,
                account_order=ACCOUNTS,
            )
            assert started is not None
            assert started.admitted_visit.admission.account_key == ACCOUNTS[index]
            assert isinstance(state.state, ActiveV2)
            assert state.state.run_nonce == started.admitted_visit.visit_nonce
            trace.extend((f"H{index}", f"B{index}", f"SAFE_HOME{index}"))
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 200.0, Lane.HOME),
            )
            assert state.state == ReadyV2(GENERATION, ACCOUNTS[index])

    restarted = decode_state_v2(encode_state_v2(state.state))
    assert type(restarted) is ReadyV2
    state.state = restarted
    lifecycle = make_supervisor(state, host)
    with SQLiteJobStore(path) as store:
        store.refresh_due(GENERATION, now=200.0)
        for index in range(5):
            started = admit_and_start_oldest(
                store,
                lifecycle,
                ready=state.state,
                now=200.0,
                account_order=ACCOUNTS,
            )
            assert started is not None
            assert started.admitted_visit.admission.account_key == ACCOUNTS[index]
            trace.extend((f"H{index}", f"B{index}", f"SAFE_HOME{index}"))
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 300.0, Lane.HOME),
            )
        assert len(store.load(GENERATION)) == 5
        assert all(job.pending_lane is Lane.HOME for job in store.load(GENERATION))

    expected_cycle = [
        event
        for index in range(5)
        for event in (f"START{index}", f"H{index}", f"B{index}", f"SAFE_HOME{index}", f"STOP{index}")
    ]
    assert trace == expected_cycle * 2
    assert host.maximum_overlap == 1
    assert host.running_count == 0
    assert host.forbidden_native_input_calls == 0


def test_successor_plan_failure_emergency_stops_and_keeps_exact_blocked_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex", lambda size: "d" * 32
    )
    trace: list[str] = []
    host = InertLifecycleHost(trace)
    state = DurableState(ReadyV2(GENERATION, ACCOUNTS[-1]))
    lifecycle = make_supervisor(state, host)

    with SQLiteJobStore(path) as store:
        store.insert(make_job(ACCOUNTS[0]))
        started = admit_and_start_oldest(
            store,
            lifecycle,
            ready=state.state,
            now=100.0,
            account_order=ACCOUNTS,
        )
        assert started is not None

        def fail_plan(*args: object, **kwargs: object) -> object:
            raise SchedulerStoreError("synthetic successor persistence failure")

        monkeypatch.setattr(SQLiteJobStore, "plan_successor", fail_plan)
        with pytest.raises(SchedulerVisitError, match="successor plan"):
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 200.0, Lane.HOME),
            )

        assert state.state == ActiveV2(
            GENERATION,
            ACCOUNTS[0],
            0,
            started.admitted_visit.visit_nonce,
            BlockReason.SUCCESSOR_PLAN,
        )
        assert host.running_count == 0
        assert store.load_admitted_visits() == (started.admitted_visit,)
        assert store.load_successor_plans() == ()
        with pytest.raises(SchedulerStoreError, match="open admission"):
            store.admit_oldest(
                ready=ReadyV2(GENERATION, ACCOUNTS[-1]),
                now=100.0,
                account_order=ACCOUNTS,
            )


def test_cross_store_crash_seams_release_block_or_finalize_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    nonces = (f"{value:032x}" for value in range(1, 20))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    ready = ReadyV2(GENERATION, ACCOUNTS[-1])

    with SQLiteJobStore(path) as store:
        store.insert(make_job(ACCOUNTS[0]))
        assert reconcile_stopped_visit(store, lifecycle_state=ready, player_count=0) is RecoveryOutcome.NORMAL
        admitted = store.admit_oldest(ready=ready, now=100.0, account_order=ACCOUNTS)
        assert admitted is not None

    with SQLiteJobStore(path) as restarted:
        assert reconcile_stopped_visit(
            restarted, lifecycle_state=ready, player_count=0
        ) is RecoveryOutcome.RELEASED_UNSTARTED
        assert restarted.load_admitted_visits() == ()
        assert restarted.load(GENERATION) == (make_job(ACCOUNTS[0]),)
        admitted = restarted.admit_oldest(ready=ready, now=100.0, account_order=ACCOUNTS)
        assert admitted is not None
        active = ActiveV2(GENERATION, ACCOUNTS[0], 0, admitted.visit_nonce, None)
        with pytest.raises(SchedulerVisitError, match="ACTIVE"):
            reconcile_stopped_visit(restarted, lifecycle_state=active, player_count=0)
        assert restarted.load_admitted_visits() == (admitted,)
        plan = restarted.plan_successor(
            admitted,
            kind=SuccessorKind.IMMEDIATE_FREE,
            observed_at=200.0,
            pending_lane=Lane.HOME,
        )
        with pytest.raises(SchedulerVisitError, match="ACTIVE"):
            reconcile_stopped_visit(restarted, lifecycle_state=active, player_count=0)
        assert restarted.load_successor_plan(admitted) == plan

    stopped = ReadyV2(GENERATION, ACCOUNTS[0])
    with SQLiteJobStore(path) as restarted_after_stop:
        with pytest.raises(SchedulerVisitError, match="cursor mismatch"):
            reconcile_stopped_visit(
                restarted_after_stop,
                lifecycle_state=ReadyV2(GENERATION, ACCOUNTS[1]),
                player_count=0,
            )
        assert restarted_after_stop.load_admitted_visits() == (admitted,)
        assert reconcile_stopped_visit(
            restarted_after_stop, lifecycle_state=stopped, player_count=0
        ) is RecoveryOutcome.FINALIZED_SUCCESSOR
        assert restarted_after_stop.load_admitted_visits() == ()
        assert restarted_after_stop.load(GENERATION) == (plan.successor,)
        assert reconcile_stopped_visit(
            restarted_after_stop, lifecycle_state=stopped, player_count=0
        ) is RecoveryOutcome.NORMAL
        assert restarted_after_stop.load(GENERATION) == (plan.successor,)


def test_stop_proof_failure_retains_the_plan_and_blocks_the_exact_active_visit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    nonces = (f"{value:032x}" for value in range(1, 10))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    host = InertLifecycleHost([])
    state = DurableState(ReadyV2(GENERATION, ACCOUNTS[-1]))
    lifecycle = make_supervisor(state, host)

    with SQLiteJobStore(path) as store:
        store.insert(make_job(ACCOUNTS[0]))
        started = admit_and_start_oldest(
            store,
            lifecycle,
            ready=state.state,
            now=100.0,
            account_order=ACCOUNTS,
        )
        assert started is not None
        host.fail_stop_membership = True
        with pytest.raises(SchedulerVisitError, match="stop was unproved"):
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 200.0, Lane.HOME),
            )

        assert state.state == ActiveV2(
            GENERATION,
            ACCOUNTS[0],
            0,
            started.admitted_visit.visit_nonce,
            BlockReason.STOP_PROOF,
        )
        assert host.running_count == 0
        assert store.load_successor_plan(started.admitted_visit) is not None
        assert store.load_admitted_visits() == (started.admitted_visit,)


def test_ready_commit_interruption_leaves_active_and_durable_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    nonces = (f"{value:032x}" for value in range(1, 10))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    host = InertLifecycleHost([])
    state = DurableState(ReadyV2(GENERATION, ACCOUNTS[-1]))
    lifecycle = make_supervisor(state, host)

    with SQLiteJobStore(path) as store:
        store.insert(make_job(ACCOUNTS[0]))
        started = admit_and_start_oldest(
            store,
            lifecycle,
            ready=state.state,
            now=100.0,
            account_order=ACCOUNTS,
        )
        assert started is not None
        state.fail_ready_commit = True
        with pytest.raises(SchedulerVisitError, match="stop was unproved"):
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 200.0, Lane.HOME),
            )

        assert state.state == ActiveV2(
            GENERATION, ACCOUNTS[0], 0, started.admitted_visit.visit_nonce, None
        )
        assert host.running_count == 0
        assert store.load_successor_plan(started.admitted_visit) is not None
        assert store.load_admitted_visits() == (started.admitted_visit,)


def test_finalization_interruption_recovers_once_from_ready_and_persisted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    nonces = (f"{value:032x}" for value in range(1, 10))
    monkeypatch.setattr(
        "clash_rush_rebuild.scheduler_store.secrets.token_hex",
        lambda size: next(nonces),
    )
    host = InertLifecycleHost([])
    state = DurableState(ReadyV2(GENERATION, ACCOUNTS[-1]))
    lifecycle = make_supervisor(state, host)

    with SQLiteJobStore(path) as store:
        store.insert(make_job(ACCOUNTS[0]))
        started = admit_and_start_oldest(
            store,
            lifecycle,
            ready=state.state,
            now=100.0,
            account_order=ACCOUNTS,
        )
        assert started is not None
        connection = store._require_open()
        connection.execute(
            """CREATE TEMP TRIGGER fail_finalization
               BEFORE UPDATE OF state ON scheduler_jobs
               BEGIN SELECT RAISE(ABORT, 'synthetic finalization interruption'); END"""
        )
        with pytest.raises(SchedulerVisitError, match="finalization remains pending"):
            finish_started_visit(
                store,
                lifecycle,
                started,
                SuccessorRequest(SuccessorKind.IMMEDIATE_FREE, 200.0, Lane.HOME),
            )
        assert state.state == ReadyV2(GENERATION, ACCOUNTS[0])
        plan = store.load_successor_plan(started.admitted_visit)
        assert plan is not None
        assert store.load_admitted_visits() == (started.admitted_visit,)
        connection.execute("DROP TRIGGER fail_finalization")

    with SQLiteJobStore(path) as restarted:
        assert reconcile_stopped_visit(
            restarted, lifecycle_state=state.state, player_count=0
        ) is RecoveryOutcome.FINALIZED_SUCCESSOR
        assert restarted.load(GENERATION) == (plan.successor,)
        restarted._require_open().execute(
            "UPDATE scheduler_jobs SET due_at = due_at + 1"
        )
        with pytest.raises(SchedulerStoreError, match="stale scheduler successor"):
            restarted.finalize_successor(plan)
