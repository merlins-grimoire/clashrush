from __future__ import annotations

from dataclasses import dataclass

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
    LifecycleError,
    MemberSnapshot,
    PlayerBinding,
    PlayerSnapshot,
    ProcessIdentity,
    StopRecord,
)
from clash_rush_rebuild.lifecycle_state import BlockReason
from clash_rush_rebuild.lifecycle_state_v2 import ActiveV2, ReadyV2
from clash_rush_rebuild.lifecycle_v2 import SchemaV2LifecycleSupervisor
from clash_rush_rebuild.registry import Slot


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")
VISIT_NONCE = "fedcba9876543210fedcba9876543210"
ACCOUNT_A = AccountKey("account-a")
ACCOUNT_B = AccountKey("account-b")
CONFIGURATION = SchemaV2Configuration(
    GENERATION,
    (AccountRecord(ACCOUNT_A), AccountRecord(ACCOUNT_B)),
)
SLOTS = (
    Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240),
    Slot(1, "Pie64_1", "Example Slot 1", 1280, 720, 240),
)


@dataclass
class FakeStateStore:
    state: ReadyV2 | ActiveV2
    events: list[str]

    def load(self) -> ReadyV2 | ActiveV2:
        self.events.append("state:load")
        return self.state

    def commit(self, state: ReadyV2 | ActiveV2) -> ReadyV2 | ActiveV2:
        self.events.append(f"state:commit:{state.state_name}")
        self.state = state
        return state


class FakeHost:
    def __init__(self, events: list[str], *, fail_job_create: bool = False) -> None:
        self.events = events
        self.identity = ProcessIdentity(100, 9001)
        self.running = False
        self.fail_job_create = fail_job_create

    def complete_player_snapshot(self) -> PlayerSnapshot:
        self.events.append("players:snapshot")
        identities = (self.identity,) if self.running else ()
        return PlayerSnapshot(identities, lambda: self.events.append("players:close"))

    def create_job(self) -> object:
        self.events.append("job:create")
        if self.fail_job_create:
            raise RuntimeError("synthetic Job creation failure")
        return "job"

    def set_kill_on_close(self, job: object) -> None:
        self.events.append("job:configure")

    def create_suspended(self, internal_name: str) -> CreatedProcess:
        self.events.append(f"process:create-suspended:{internal_name}")
        self.running = True
        return CreatedProcess("process", "thread", 100)

    def identity_from_handle(
        self, process: object, expected_pid: int
    ) -> ProcessIdentity:
        self.events.append("process:identity")
        return self.identity

    def assign_to_job(self, job: object, process: object) -> None:
        self.events.append("job:assign")

    def is_process_in_job(self, process: object, job: object) -> bool:
        self.events.append("job:confirm")
        return True

    def resume_thread(self, thread: object) -> int:
        self.events.append("thread:resume")
        return 1

    def close_thread(self, thread: object) -> None:
        self.events.append("thread:close")

    def stable_job_members(self, job: object) -> MemberSnapshot:
        self.events.append("job:members")
        return MemberSnapshot(
            (self.identity,), lambda: self.events.append("members:close")
        )

    def bind_exact(
        self,
        identity: ProcessIdentity,
        expected_title: str,
        members: MemberSnapshot,
    ) -> PlayerBinding | None:
        self.events.append(f"window:bind:{expected_title}")
        return PlayerBinding(identity, identity, 101, 102, 1280, 720, "a" * 32)

    def capture_ready(self, binding: PlayerBinding, job: object) -> None:
        self.events.append("capture:ready")

    def terminate_job(self, job: object) -> None:
        self.events.append("job:terminate")
        self.running = False

    def terminate_retained_process(self, process: object) -> None:
        self.events.append("process:terminate-retained")
        self.running = False

    def wait_process(self, process: object, milliseconds: int) -> bool:
        self.events.append("process:wait")
        return True

    def job_active_count(self, job: object) -> int:
        self.events.append("job:active-count")
        return 0

    def is_window(self, hwnd: int) -> bool:
        self.events.append(f"window:is:{hwnd}")
        return False

    def close_handle(self, handle: object) -> None:
        self.events.append(f"handle:close:{handle}")


def _supervisor(
    events: list[str],
    state: FakeStateStore,
    *,
    host: FakeHost | None = None,
) -> SchemaV2LifecycleSupervisor:
    return SchemaV2LifecycleSupervisor(
        host if host is not None else FakeHost(events),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        configuration=CONFIGURATION,
        slots=SLOTS,
    )


def test_start_admitted_ignores_tie_cursor_and_commits_exact_admitted_nonce() -> None:
    events: list[str] = []
    state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_A), events)

    binding = _supervisor(events, state).start_admitted(
        ACCOUNT_B, GENERATION, VISIT_NONCE
    )

    assert binding.identity == ProcessIdentity(100, 9001)
    assert state.state == ActiveV2(GENERATION, ACCOUNT_B, 1, VISIT_NONCE, None)
    assert events[:4] == [
        "state:load",
        "players:snapshot",
        "players:close",
        "state:commit:ACTIVE",
    ]
    assert events.index("state:commit:ACTIVE") < events.index("job:create")
    assert "process:create-suspended:Pie64_1" in events
    assert "window:bind:Example Slot 1" in events


def test_start_admitted_rejects_identity_mismatches_before_host_activity() -> None:
    wrong_generation = ConfigurationGeneration("1" * 32)
    cases = (
        (AccountKey("not-configured"), GENERATION, VISIT_NONCE),
        (ACCOUNT_A, wrong_generation, VISIT_NONCE),
        (ACCOUNT_A, GENERATION, "A" * 32),
    )

    for account_key, generation, visit_nonce in cases:
        events: list[str] = []
        state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_B), events)
        with pytest.raises(LifecycleError):
            _supervisor(events, state).start_admitted(
                account_key, generation, visit_nonce
            )
        assert events == []
        assert state.state == ReadyV2(GENERATION, ACCOUNT_B)


def test_start_admitted_active_state_blocks_duplicate_before_process_discovery() -> None:
    events: list[str] = []
    state = FakeStateStore(
        ActiveV2(GENERATION, ACCOUNT_A, 0, VISIT_NONCE, None), events
    )

    with pytest.raises(LifecycleError, match="does not authorize"):
        _supervisor(events, state).start_admitted(ACCOUNT_B, GENERATION, "1" * 32)

    assert events == ["state:load"]


def test_start_admitted_failure_preserves_exact_nonce_and_closed_reason() -> None:
    events: list[str] = []
    state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_A), events)
    host = FakeHost(events, fail_job_create=True)

    with pytest.raises(LifecycleError, match="job setup"):
        _supervisor(events, state, host=host).start_admitted(
            ACCOUNT_B, GENERATION, VISIT_NONCE
        )

    assert state.state == ActiveV2(
        GENERATION, ACCOUNT_B, 1, VISIT_NONCE, BlockReason.JOB_SETUP
    )
    assert events[-2:] == ["job:create", "state:commit:ACTIVE"]


def test_stop_admitted_proves_absence_then_commits_stable_account_cursor() -> None:
    events: list[str] = []
    state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_A), events)
    supervisor = _supervisor(events, state)
    binding = supervisor.start_admitted(ACCOUNT_B, GENERATION, VISIT_NONCE)

    record = supervisor.stop_admitted(binding, None)

    assert record == StopRecord(1, VISIT_NONCE, 1, 2, True)
    assert state.state == ReadyV2(GENERATION, ACCOUNT_B)
    assert events.index("job:terminate") < events.index("state:commit:READY")
    assert events[-1] == "state:commit:READY"


def test_unproved_stop_terminates_owned_job_but_halts_in_blocked_active() -> None:
    events: list[str] = []
    state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_A), events)

    class UnprovedStopHost(FakeHost):
        def __init__(self, host_events: list[str]) -> None:
            super().__init__(host_events)
            self.member_queries = 0

        def stable_job_members(self, job: object) -> MemberSnapshot:
            self.member_queries += 1
            if self.member_queries == 2:
                raise RuntimeError("synthetic fresh membership failure")
            return super().stable_job_members(job)

    host = UnprovedStopHost(events)
    supervisor = _supervisor(events, state, host=host)
    binding = supervisor.start_admitted(ACCOUNT_B, GENERATION, VISIT_NONCE)

    with pytest.raises(LifecycleError, match="membership was not proved"):
        supervisor.stop_admitted(binding, None)

    assert "job:terminate" in events
    assert state.state == ActiveV2(
        GENERATION, ACCOUNT_B, 1, VISIT_NONCE, BlockReason.STOP_PROOF
    )
    assert "state:commit:READY" not in events


def test_instance_shadow_cannot_bypass_admitted_stop_proof() -> None:
    events: list[str] = []
    state = FakeStateStore(ReadyV2(GENERATION, ACCOUNT_A), events)
    supervisor = _supervisor(events, state)
    binding = supervisor.start_admitted(ACCOUNT_B, GENERATION, VISIT_NONCE)
    supervisor._prove_stopped_and_close_owned = lambda owned, candidate: 99  # type: ignore[method-assign]

    record = SchemaV2LifecycleSupervisor.stop_admitted(supervisor, binding, None)

    assert record.observed_member_count == 1
    assert "job:terminate" in events
