from __future__ import annotations

from dataclasses import dataclass

import pytest

import clash_rush_rebuild.cli as cli_module
from clash_rush_rebuild.lifecycle import (
    AcquiredMutexLease,
    CreatedProcess,
    LifecycleError,
    LifecycleSupervisor,
    MemberSnapshot,
    PlayerBinding,
    PlayerSnapshot,
    ProcessIdentity,
    StopRecord,
)
from clash_rush_rebuild.lifecycle_state import Active, BlockReason, Ready
from clash_rush_rebuild.registry import Slot


@dataclass
class FakeStateStore:
    state: Ready | Active
    events: list[str]
    fail_ready_commit: str | None = None

    def load(self) -> Ready | Active:
        self.events.append("state:load")
        return self.state

    def commit(self, state: Ready | Active) -> Ready | Active:
        self.events.append(f"state:commit:{state.state_name}")
        if type(state) is Ready and self.fail_ready_commit is not None:
            raise OSError(self.fail_ready_commit)
        self.state = state
        return state


class FakeHost:
    def __init__(
        self,
        events: list[str],
        *,
        fail_capture_ready: bool = False,
        fail_job_create: bool = False,
        fail_stage: str | None = None,
        bind_missing_reads: int = 0,
        fail_job_termination: bool = False,
        post_launch_empty_reads: int = 0,
        surviving_hwnd: int | None = None,
    ) -> None:
        self.events = events
        self.identity = ProcessIdentity(100, 9001)
        self.running = False
        self.fail_capture_ready = fail_capture_ready
        self.fail_job_create = fail_job_create
        self.fail_stage = fail_stage
        self.failed_once = False
        self.bind_missing_reads = bind_missing_reads
        self.fail_job_termination = fail_job_termination
        self.post_launch_empty_reads = post_launch_empty_reads
        self.surviving_hwnd = surviving_hwnd
        self.stop_failure: str | None = None
        self.membership_query_count = 0

    def complete_player_snapshot(self) -> PlayerSnapshot:
        self.events.append("players:snapshot")
        if self.stop_failure == "player_snapshot_query":
            raise RuntimeError("synthetic post-stop player query failure")
        if self.stop_failure == "player_absence":
            identities = (self.identity,)
        elif self.running and self.post_launch_empty_reads > 0:
            self.post_launch_empty_reads -= 1
            identities = ()
        else:
            identities = (self.identity,) if self.running else ()
        return PlayerSnapshot(identities, self._close_player_snapshot)

    def _close_player_snapshot(self) -> None:
        self.events.append("players:close")
        if self.stop_failure == "player_snapshot_close":
            raise RuntimeError("synthetic post-stop player snapshot close failure")

    def create_job(self) -> object:
        self.events.append("job:create")
        if self.fail_job_create:
            raise RuntimeError("synthetic Job creation failure")
        return "job"

    def set_kill_on_close(self, job: object) -> None:
        self.events.append("job:configure")
        self._fail_once("job:configure")

    def create_suspended(self, internal_name: str) -> CreatedProcess:
        self.events.append(f"process:create-suspended:{internal_name}")
        self._fail_once("process:create")
        self.running = True
        return CreatedProcess("process", "thread", 100)

    def identity_from_handle(
        self, process: object, expected_pid: int
    ) -> ProcessIdentity:
        self.events.append("process:identity")
        self._fail_once("process:identity")
        return self.identity

    def assign_to_job(self, job: object, process: object) -> None:
        self.events.append("job:assign")
        self._fail_once("job:assign")

    def is_process_in_job(self, process: object, job: object) -> bool:
        self.events.append("job:confirm")
        return True

    def resume_thread(self, thread: object) -> int:
        self.events.append("thread:resume")
        self._fail_once("thread:resume")
        return 1

    def close_thread(self, thread: object) -> None:
        self.events.append("thread:close")
        self._fail_once("thread:close")

    def stable_job_members(self, job: object) -> MemberSnapshot:
        self.events.append("job:members")
        self.membership_query_count += 1
        query = self.membership_query_count
        if self.stop_failure == "membership_query":
            raise RuntimeError("synthetic post-stop membership query failure")
        self._fail_once("job:members")
        return MemberSnapshot(
            (self.identity,), lambda: self._close_members(query)
        )

    def _close_members(self, query: int) -> None:
        self.events.append("members:close")
        if (
            self.stop_failure == "fresh_members_close" and query == 2
        ) or (
            self.stop_failure == "launch_members_close" and query == 1
        ):
            raise RuntimeError("synthetic member snapshot close failure")

    def bind_exact(
        self,
        identity: ProcessIdentity,
        expected_title: str,
        members: MemberSnapshot,
    ) -> PlayerBinding | None:
        self.events.append("window:bind")
        if self.bind_missing_reads > 0:
            self.bind_missing_reads -= 1
            return None
        return PlayerBinding(identity, identity, 101, 102, 1280, 720, "a" * 32)

    def capture_ready(self, binding: PlayerBinding, job: object) -> None:
        self.events.append("capture:ready")
        if self.fail_capture_ready:
            raise RuntimeError("synthetic capture readiness failure")

    def capture_bgra(self, binding: PlayerBinding) -> tuple[int, int, bytes]:
        self.events.append("capture:bgra")
        return binding.width, binding.height, b"x" * (binding.width * binding.height * 4)

    def terminate_job(self, job_handle: object) -> None:
        self.events.append("job:terminate")
        if self.fail_job_termination or self.stop_failure == "terminate_job":
            raise OSError("synthetic Job termination failure")
        self.running = False

    def terminate_retained_process(self, process: object) -> None:
        self.events.append("process:terminate-retained")
        self.running = False

    def wait_process(self, process: object, milliseconds: int) -> bool:
        self.events.append("process:wait")
        return self.stop_failure != "process_wait"

    def job_active_count(self, job: object) -> int:
        self.events.append("job:active-count")
        return 1 if self.stop_failure == "job_not_empty" else 0

    def is_window(self, hwnd: int) -> bool:
        self.events.append(f"window:is:{hwnd}")
        return self.stop_failure == "window_valid" or hwnd == self.surviving_hwnd

    def close_handle(self, handle: object) -> None:
        self.events.append(f"handle:close:{handle}")
        if self.stop_failure == f"close_{handle}_handle":
            raise RuntimeError("synthetic retained handle close failure")

    def _fail_once(self, stage: str) -> None:
        if self.fail_stage == stage and not self.failed_once:
            self.failed_once = True
            raise RuntimeError(f"synthetic {stage} failure")


@pytest.mark.parametrize("initial_slot", [0, 3, 4])
@pytest.mark.parametrize("preserve_cursor", [False, True])
def test_composed_visit_one_selects_rotation_or_diagnostic_cursor_preservation(
    monkeypatch,
    initial_slot: int,
    preserve_cursor: bool,
) -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(initial_slot), events)
    host = FakeHost(events)
    slots = tuple(
        Slot(
            index,
            "Pie64" if index == 0 else f"Pie64_{index + 1}",
            f"Example Slot {index}",
            1280,
            720,
            240,
        )
        for index in range(5)
    )

    class SyntheticLease:
        abandoned = False

        @staticmethod
        def require_usable() -> None:
            events.append("mutex:usable")

        @staticmethod
        def release() -> None:
            events.append("mutex:release")

    class SyntheticRuntime:
        @staticmethod
        def acquire_mutex() -> SyntheticLease:
            events.append("mutex:acquire")
            return SyntheticLease()

    monkeypatch.setattr(cli_module, "NativeLifecycleApi", object)
    monkeypatch.setattr(cli_module, "Win32Runtime", lambda _native: SyntheticRuntime())
    monkeypatch.setattr(
        cli_module,
        "Win32LifecycleHost",
        lambda _native, nonce_factory: host,
    )
    monkeypatch.setattr(cli_module, "build_native_state_store", lambda _root: state)
    monkeypatch.setattr(cli_module, "load_private_registry", lambda *_args: slots)

    command = [
        "visit-one",
        "--project-root",
        "synthetic-project",
        "--slots",
        "synthetic-slots.toml",
        "--owner-approved",
    ]
    if preserve_cursor:
        command.append("--diagnostic-preserve-cursor")

    status = cli_module.main(command)

    assert status == 0
    expected_slot = initial_slot if preserve_cursor else (initial_slot + 1) % 5
    assert state.state == Ready(expected_slot)
    assert events[-1] == "mutex:release"


def test_start_commits_active_before_creating_native_objects() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    slot = Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240)

    binding = supervisor.start(slot)

    assert binding.identity == ProcessIdentity(100, 9001)
    assert events == [
        "state:load",
        "players:snapshot",
        "players:close",
        "state:commit:ACTIVE",
        "job:create",
        "job:configure",
        "process:create-suspended:Pie64",
        "process:identity",
        "job:assign",
        "job:confirm",
        "thread:resume",
        "thread:close",
        "players:snapshot",
        "players:close",
        "job:members",
        "window:bind",
        "capture:ready",
    ]


def test_owned_capture_revalidates_exact_binding_and_job_before_pixels() -> None:
    events: list[str] = []
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        FakeStateStore(Ready(0), events),
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))
    events.clear()

    width, height, pixels = supervisor.capture_owned(binding)

    assert (width, height, len(pixels)) == (1280, 720, 1280 * 720 * 4)
    assert events == ["capture:ready", "capture:bgra"]


def test_start_waits_boundedly_for_the_exact_post_resume_identity() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, post_launch_empty_reads=2),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        readiness_wait=lambda milliseconds: events.append(f"wait:{milliseconds}"),
        readiness_attempts=3,
    )

    supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert events.count("wait:500") == 2
    assert events.count("players:snapshot") == 4


def test_start_waits_boundedly_for_exact_window_and_capture_readiness() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, bind_missing_reads=2),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        readiness_wait=lambda milliseconds: events.append(f"wait:{milliseconds}"),
        readiness_attempts=3,
    )

    supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert events.count("window:bind") == 3
    assert events.count("wait:500") == 2
    assert events.count("members:close") == 2


def test_active_restart_state_blocks_before_process_discovery() -> None:
    events: list[str] = []
    state = FakeStateStore(Active(0, "0123456789abcdef0123456789abcdef", None), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "fedcba9876543210fedcba9876543210",
    )

    with pytest.raises(Exception, match="does not authorize"):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert events == ["state:load"]


def test_capture_readiness_failure_terminates_owned_job_and_blocks_same_slot() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, fail_capture_ready=True),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        readiness_wait=lambda _milliseconds: None,
        readiness_attempts=1,
    )

    with pytest.raises(LifecycleError, match="window and capture"):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.CAPTURE_READINESS,
    )
    rollback_start = events.index("job:terminate")
    assert events[rollback_start:] == [
        "job:terminate",
        "process:wait",
        "job:active-count",
        "window:is:101",
        "window:is:102",
        "players:snapshot",
        "players:close",
        "handle:close:process",
        "handle:close:job",
        "state:commit:ACTIVE",
    ]
    assert events.index("members:close") < rollback_start


def test_forced_stop_proves_job_windows_and_players_before_ready() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    record = supervisor.stop(binding, None)

    assert record == StopRecord(0, "0123456789abcdef0123456789abcdef", 1, 2, True)
    assert state.state == Ready(1)


@pytest.mark.parametrize("initial_slot", [0, 3])
def test_diagnostic_stop_preserves_exact_pre_admission_ready_cursor(
    initial_slot: int,
) -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(initial_slot), events)
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        preserve_ready_cursor=True,
    )
    slot = Slot(
        initial_slot,
        "Pie64" if initial_slot == 0 else f"Pie64_{initial_slot + 1}",
        f"Example Slot {initial_slot}",
        1280,
        720,
        240,
    )
    binding = supervisor.start(slot)

    supervisor.stop(binding, None)

    assert state.state == Ready(initial_slot)
    ready_commit = events.index("state:commit:READY")
    assert events.index("job:terminate") < ready_commit
    assert events.index("job:active-count") < ready_commit
    assert events.index(f"window:is:{binding.root_hwnd}") < ready_commit
    assert events.index(f"window:is:{binding.render_hwnd}") < ready_commit
    assert events.index("players:close", events.index("job:terminate")) < ready_commit
    assert events.index("handle:close:process") < ready_commit
    assert events.index("handle:close:job") < ready_commit


@pytest.mark.parametrize("failure", ["write failure", "read-back failure"])
def test_diagnostic_ready_commit_failure_leaves_active_state_blocked(
    failure: str,
) -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events, fail_ready_commit=failure)
    supervisor = LifecycleSupervisor(
        FakeHost(events),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        preserve_ready_cursor=True,
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    with pytest.raises(LifecycleError, match="READY lifecycle state"):
        supervisor.stop(binding, None)

    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        None,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "membership_query",
        "terminate_job",
        "process_wait",
        "job_not_empty",
        "window_valid",
        "player_snapshot_query",
        "player_absence",
        "player_snapshot_close",
        "fresh_members_close",
        "launch_members_close",
        "close_process_handle",
        "close_job_handle",
    ],
)
def test_every_diagnostic_pre_ready_proof_or_cleanup_failure_remains_active(
    failure: str,
) -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
        preserve_ready_cursor=True,
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))
    host.stop_failure = failure

    with pytest.raises(LifecycleError):
        supervisor.stop(binding, None)

    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.STOP_PROOF,
    )
    assert "state:commit:READY" not in events
    assert "job:terminate" in events
    assert "process:wait" in events
    assert "job:active-count" in events
    assert f"window:is:{binding.root_hwnd}" in events
    assert f"window:is:{binding.render_hwnd}" in events
    assert "players:snapshot" in events
    assert "handle:close:process" in events
    assert "handle:close:job" in events


def test_stop_fresh_membership_failure_still_terminates_owned_job() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    host = FakeHost(events)
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))
    host.fail_stage = "job:members"
    host.failed_once = False

    with pytest.raises(LifecycleError):
        supervisor.stop(binding, None)

    assert events.count("job:members") == 2
    assert "job:terminate" in events
    assert "process:wait" in events
    assert "state:commit:READY" not in events
    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.STOP_PROOF,
    )


def test_surviving_hwnd_keeps_active_slot_blocked() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, surviving_hwnd=101),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    binding = supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    with pytest.raises(LifecycleError, match="HWND"):
        supervisor.stop(binding, None)

    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.STOP_PROOF,
    )
    assert "state:commit:READY" not in events


def test_job_creation_failure_preserves_active_slot_and_closed_reason() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, fail_job_create=True),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )

    with pytest.raises(LifecycleError, match="job setup"):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.JOB_SETUP,
    )
    assert events[-2:] == ["job:create", "state:commit:ACTIVE"]


@pytest.mark.parametrize(
    ("stage", "reason", "required_events", "forbidden_events"),
    [
        ("job:configure", BlockReason.JOB_SETUP, (), ("job:terminate",)),
        ("process:create", BlockReason.PROCESS_CREATE, (), ("job:terminate",)),
        (
            "process:identity",
            BlockReason.IDENTITY,
            ("process:terminate-retained",),
            ("job:terminate",),
        ),
        (
            "job:assign",
            BlockReason.ASSIGNMENT,
            ("job:terminate", "process:terminate-retained"),
            (),
        ),
        ("thread:resume", BlockReason.RESUME, ("job:terminate",), ()),
        ("thread:close", BlockReason.THREAD_CLOSE, ("job:terminate",), ()),
    ],
)
def test_every_early_start_failure_uses_its_owned_rollback_boundary(
    stage: str,
    reason: BlockReason,
    required_events: tuple[str, ...],
    forbidden_events: tuple[str, ...],
) -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(events, fail_stage=stage),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )

    with pytest.raises(LifecycleError):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert state.state == Active(0, "0123456789abcdef0123456789abcdef", reason)
    for event in required_events:
        assert event in events
    for event in forbidden_events:
        assert event not in events
    assert "state:commit:READY" not in events


def test_uncertain_assignment_attempts_retained_termination_when_job_termination_raises() -> (
    None
):
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    supervisor = LifecycleSupervisor(
        FakeHost(
            events,
            fail_stage="job:assign",
            fail_job_termination=True,
        ),
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )

    with pytest.raises(LifecycleError):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert events.index("job:terminate") < events.index("process:terminate-retained")
    assert "process:wait" in events
    assert "state:commit:READY" not in events


def test_confirmed_assignment_job_termination_failure_uses_retained_fallback_and_cleans_up() -> None:
    events: list[str] = []
    state = FakeStateStore(Ready(0), events)
    host = FakeHost(
        events,
        fail_stage="thread:resume",
        fail_job_termination=True,
    )
    supervisor = LifecycleSupervisor(
        host,
        state,
        AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )

    with pytest.raises(LifecycleError, match="rollback proof failed"):
        supervisor.start(Slot(0, "Pie64", "Example Slot 0", 1280, 720, 240))

    assert events.index("job:terminate") < events.index("process:terminate-retained")
    assert "process:wait" in events
    assert "job:active-count" in events
    assert "handle:close:process" in events
    assert "handle:close:job" in events
    assert host.running is False
    assert state.state == Active(
        0,
        "0123456789abcdef0123456789abcdef",
        BlockReason.ROLLBACK_UNPROVED,
    )
    assert "state:commit:READY" not in events
