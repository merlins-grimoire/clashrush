"""Pure fail-closed lifecycle domain for one owned BlueStacks run."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .lifecycle_state import Active, BlockReason, LifecycleStateStore, Ready
from .registry import Slot

_MUTEX_NAME = "Global\\ClashRushRebuildLifecycle-v1"


class LifecycleError(RuntimeError):
    """The exact owned lifecycle could not be established or proved stopped."""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    creation_time_100ns: int

    def __post_init__(self) -> None:
        if (
            type(self.pid) is not int
            or self.pid <= 0
            or type(self.creation_time_100ns) is not int
            or self.creation_time_100ns <= 0
        ):
            raise LifecycleError("invalid process identity")


@dataclass(frozen=True, slots=True)
class AcquiredMutexLease:
    name: str
    abandoned: bool = False
    held: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or self.name != _MUTEX_NAME
            or type(self.abandoned) is not bool
            or type(self.held) is not bool
            or not self.held
        ):
            raise LifecycleError("valid acquired lifecycle mutex required")


@dataclass(frozen=True, slots=True)
class CreatedProcess:
    process_handle: object
    thread_handle: object
    pid: int

    def __post_init__(self) -> None:
        if (
            self.process_handle is None
            or self.thread_handle is None
            or type(self.pid) is not int
            or self.pid <= 0
        ):
            raise LifecycleError("invalid suspended process result")


class _OwnedIdentitySnapshot:
    def __init__(
        self,
        identities: tuple[ProcessIdentity, ...],
        closer: Callable[[], None],
    ) -> None:
        if type(identities) is not tuple or any(
            type(identity) is not ProcessIdentity for identity in identities
        ):
            raise LifecycleError("invalid identity snapshot")
        if not callable(closer):
            raise LifecycleError("snapshot closer required")
        self.identities = identities
        self._closer = closer
        self._closed = False

    def close(self) -> None:
        if self._closed:
            raise LifecycleError("identity snapshot already closed")
        self._closer()
        self._closed = True


class PlayerSnapshot(_OwnedIdentitySnapshot):
    pass


class MemberSnapshot(_OwnedIdentitySnapshot):
    pass


@dataclass(frozen=True, slots=True)
class PlayerBinding:
    identity: ProcessIdentity
    render_identity: ProcessIdentity
    root_hwnd: int
    render_hwnd: int
    width: int
    height: int
    capture_nonce: str

    def __post_init__(self) -> None:
        integers = (self.root_hwnd, self.render_hwnd, self.width, self.height)
        if (
            type(self.identity) is not ProcessIdentity
            or type(self.render_identity) is not ProcessIdentity
            or any(
                type(value) is not int or value <= 0 for value in integers
            )
        ):
            raise LifecycleError("invalid player binding")
        if self.root_hwnd == self.render_hwnd or self.width < 640 or self.height < 360:
            raise LifecycleError("invalid player window geometry")
        if (
            type(self.capture_nonce) is not str
            or len(self.capture_nonce) != 32
            or any(
                character not in "0123456789abcdef" for character in self.capture_nonce
            )
        ):
            raise LifecycleError("invalid capture nonce")


@dataclass(frozen=True, slots=True)
class StopRecord:
    slot: int
    run_nonce: str
    observed_member_count: int
    observed_hwnd_count: int
    forced: bool

    def __post_init__(self) -> None:
        if (
            type(self.slot) is not int
            or not 0 <= self.slot < 5
            or type(self.run_nonce) is not str
            or len(self.run_nonce) != 32
            or type(self.observed_member_count) is not int
            or self.observed_member_count < 1
            or type(self.observed_hwnd_count) is not int
            or self.observed_hwnd_count != 2
            or type(self.forced) is not bool
        ):
            raise LifecycleError("invalid sanitized stop record")


@dataclass(slots=True)
class _OwnedRun:
    slot: int
    run_nonce: str
    job: object
    process: object
    members: MemberSnapshot
    binding: PlayerBinding


class StatePort(Protocol):
    def load(self) -> Ready | Active: ...
    def commit(self, state: Ready | Active) -> Ready | Active: ...


class LifecycleHostPort(Protocol):
    def complete_player_snapshot(self) -> PlayerSnapshot: ...
    def create_job(self) -> object: ...
    def set_kill_on_close(self, job: object) -> None: ...
    def create_suspended(self, internal_name: str) -> CreatedProcess: ...
    def create_suspended_clash(self, internal_name: str) -> CreatedProcess: ...
    def identity_from_handle(
        self, process: object, expected_pid: int
    ) -> ProcessIdentity: ...
    def assign_to_job(self, job: object, process: object) -> None: ...
    def is_process_in_job(self, process: object, job: object) -> bool: ...
    def resume_thread(self, thread: object) -> int: ...
    def close_thread(self, thread: object) -> None: ...
    def stable_job_members(self, job: object) -> MemberSnapshot: ...
    def bind_exact(
        self,
        identity: ProcessIdentity,
        expected_title: str,
        members: MemberSnapshot,
    ) -> PlayerBinding | None: ...
    def capture_ready(self, binding: PlayerBinding, job: object) -> None: ...
    def capture_bgra(self, binding: PlayerBinding) -> tuple[int, int, bytes]: ...
    def terminate_job(self, job: object) -> None: ...
    def terminate_retained_process(self, process: object) -> None: ...
    def wait_process(self, process: object, milliseconds: int) -> bool: ...
    def job_active_count(self, job: object) -> int: ...
    def is_window(self, hwnd: int) -> bool: ...
    def close_handle(self, handle: object) -> None: ...


class LifecycleSupervisor:
    def __init__(
        self,
        host: LifecycleHostPort,
        state: StatePort | LifecycleStateStore,
        mutex: AcquiredMutexLease,
        *,
        nonce_factory: Callable[[], str],
        readiness_wait: Callable[[int], None] | None = None,
        readiness_attempts: int = 180,
        preserve_ready_cursor: bool = False,
    ) -> None:
        if type(mutex) is not AcquiredMutexLease or mutex.abandoned:
            raise LifecycleError("non-abandoned acquired lifecycle mutex required")
        if not callable(nonce_factory):
            raise LifecycleError("nonce factory required")
        if (
            (readiness_wait is not None and not callable(readiness_wait))
            or type(readiness_attempts) is not int
            or not 1 <= readiness_attempts <= 240
        ):
            raise LifecycleError("bounded readiness policy required")
        if type(preserve_ready_cursor) is not bool:
            raise LifecycleError("exact READY cursor policy required")
        self._host = host
        self._state = state
        self._mutex = mutex
        self._nonce_factory = nonce_factory
        self._readiness_wait = (
            readiness_wait
            if readiness_wait is not None
            else lambda milliseconds: time.sleep(milliseconds / 1000)
        )
        self._readiness_attempts = readiness_attempts
        self._preserve_ready_cursor = preserve_ready_cursor
        self._owned: _OwnedRun | None = None

    def start(self, slot: Slot) -> PlayerBinding:
        return self._start(slot, direct_clash=False)

    def start_clash(self, slot: Slot) -> PlayerBinding:
        """Start the one hardcoded Clash package inside the owned player process."""
        return self._start(slot, direct_clash=True)

    def _start(self, slot: Slot, *, direct_clash: bool) -> PlayerBinding:
        if type(direct_clash) is not bool:
            raise LifecycleError("exact launch mode required")
        if type(slot) is not Slot:
            raise LifecycleError("exact selected slot required")
        if self._owned is not None:
            raise LifecycleError("owned run must stop before another launch")
        current = self._state.load()
        if type(current) is not Ready or current.next_slot != slot.index:
            raise LifecycleError("lifecycle state does not authorize selected slot")

        self._require_player_absence()

        run_nonce = self._nonce_factory()
        active = Active(slot.index, run_nonce, None)
        committed = self._state.commit(active)
        if type(committed) is not Active or committed != active:
            raise LifecycleError("ACTIVE lifecycle state was not durably committed")

        job: object | None = None
        created: CreatedProcess | None = None
        identity: ProcessIdentity | None = None
        members: MemberSnapshot | None = None
        binding: PlayerBinding | None = None
        assignment_uncertain = False
        assigned = False
        thread_closed = False
        reason = BlockReason.JOB_SETUP
        try:
            job = self._host.create_job()
            self._host.set_kill_on_close(job)
            reason = BlockReason.PROCESS_CREATE
            created = (
                self._host.create_suspended_clash(slot.internal_name)
                if direct_clash
                else self._host.create_suspended(slot.internal_name)
            )
            reason = BlockReason.IDENTITY
            identity = self._host.identity_from_handle(
                created.process_handle, created.pid
            )
            reason = BlockReason.ASSIGNMENT
            assignment_uncertain = True
            self._host.assign_to_job(job, created.process_handle)
            if self._host.is_process_in_job(created.process_handle, job) is not True:
                raise LifecycleError("created process Job membership was not proved")
            assigned = True
            assignment_uncertain = False
            reason = BlockReason.RESUME
            if self._host.resume_thread(created.thread_handle) != 1:
                raise LifecycleError("unexpected suspended-thread resume count")
            reason = BlockReason.THREAD_CLOSE
            self._host.close_thread(created.thread_handle)
            thread_closed = True

            reason = BlockReason.PLAYER_ENUMERATION
            exact_player_seen = False
            for attempt in range(self._readiness_attempts):
                post_launch = self._host.complete_player_snapshot()
                try:
                    observed = post_launch.identities
                finally:
                    post_launch.close()
                if observed == (identity,):
                    exact_player_seen = True
                    break
                if observed:
                    raise LifecycleError("post-launch player identity is not exact")
                if attempt + 1 < self._readiness_attempts:
                    self._readiness_wait(500)
            if not exact_player_seen:
                raise LifecycleError("exact post-launch player did not become ready")
            if direct_clash:
                # ClashAutomation waits after direct launch before inspecting
                # the game. Bind only after that transition so the retained
                # render geometry belongs to Clash rather than the launcher.
                self._readiness_wait(30_000)
            readiness_complete = False
            for attempt in range(self._readiness_attempts):
                candidate_members: MemberSnapshot | None = None
                candidate_binding: PlayerBinding | None = None
                attempt_failed = False
                try:
                    reason = BlockReason.MEMBERSHIP
                    candidate_members = self._host.stable_job_members(job)
                    if identity not in candidate_members.identities:
                        attempt_failed = True
                    else:
                        reason = BlockReason.WINDOW_BINDING
                        candidate_binding = self._host.bind_exact(
                            identity, slot.display_name, candidate_members
                        )
                        if (
                            type(candidate_binding) is not PlayerBinding
                            or candidate_binding.identity != identity
                        ):
                            attempt_failed = True
                        else:
                            binding = candidate_binding
                            reason = BlockReason.CAPTURE_READINESS
                            self._host.capture_ready(candidate_binding, job)
                except BaseException:  # noqa: BLE001 - bounded transient readiness retry
                    attempt_failed = True
                if not attempt_failed:
                    members = candidate_members
                    binding = candidate_binding
                    readiness_complete = True
                    break
                if candidate_members is not None:
                    candidate_members.close()
                if attempt + 1 < self._readiness_attempts:
                    self._readiness_wait(500)
            if not readiness_complete:
                raise LifecycleError("exact window and capture did not become ready")

        except BaseException as exc:
            self._rollback_start(
                active=active,
                reason=reason,
                job=job,
                created=created,
                assignment_uncertain=assignment_uncertain,
                assigned=assigned,
                thread_closed=thread_closed,
                members=members,
                binding=binding,
            )
            if isinstance(exc, LifecycleError):
                raise
            message = reason.value.replace("_", " ").lower()
            raise LifecycleError(f"{message} failed") from exc

        if job is None or created is None or members is None or binding is None:
            raise LifecycleError("owned launch result is incomplete")
        self._owned = _OwnedRun(
            slot.index,
            run_nonce,
            job,
            created.process_handle,
            members,
            binding,
        )
        return binding

    def _require_player_absence(self) -> None:
        """Prelaunch enumeration seam, including snapshot cleanup."""
        players = self._host.complete_player_snapshot()
        try:
            if players.identities:
                raise LifecycleError("another BlueStacks player is already running")
        finally:
            players.close()

    def capture_owned(self, binding: PlayerBinding) -> tuple[int, int, bytes]:
        """Revalidate and capture only the currently owned exact binding."""
        owned = self._owned
        if (
            owned is None
            or type(binding) is not PlayerBinding
            or binding != owned.binding
        ):
            raise LifecycleError("capture binding does not match the owned run")
        self._host.capture_ready(binding, owned.job)
        frame = self._host.capture_bgra(binding)
        if (
            type(frame) is not tuple
            or len(frame) != 3
            or type(frame[0]) is not int
            or type(frame[1]) is not int
            or (frame[0], frame[1]) != (binding.width, binding.height)
            or type(frame[2]) is not bytes
            or len(frame[2]) != binding.width * binding.height * 4
        ):
            raise LifecycleError("owned capture result is malformed")
        return frame

    def _commit_blocked(self, active: Active, reason: BlockReason) -> None:
        blocked = Active(active.slot, active.run_nonce, reason)
        try:
            committed = self._state.commit(blocked)
        except BaseException as exc:
            raise LifecycleError(
                "blocked lifecycle state could not be committed"
            ) from exc
        if type(committed) is not Active or committed != blocked:
            raise LifecycleError("blocked lifecycle state was not committed exactly")

    def stop(self, binding: PlayerBinding, proof: None) -> StopRecord:
        if proof is not None:
            raise LifecycleError("Slice 1 accepts no graceful-close proof")
        owned = self._owned
        if (
            owned is None
            or type(binding) is not PlayerBinding
            or binding != owned.binding
        ):
            raise LifecycleError("stop binding does not match the owned run")

        try:
            member_count = self._prove_stopped_and_close_owned(owned, binding)
        except BaseException as exc:
            blocked = Active(owned.slot, owned.run_nonce, BlockReason.STOP_PROOF)
            try:
                self._state.commit(blocked)
            except BaseException:  # noqa: BLE001, S110 - previous ACTIVE remains fail-closed
                pass
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError("owned stop proof failed") from exc

        ready = Ready(
            owned.slot if self._preserve_ready_cursor else (owned.slot + 1) % 5
        )
        try:
            committed = self._state.commit(ready)
        except BaseException as exc:
            raise LifecycleError(
                "READY lifecycle state was not durably committed"
            ) from exc
        if type(committed) is not Ready or committed != ready:
            raise LifecycleError("READY lifecycle state was not durably committed")
        self._owned = None
        return StopRecord(owned.slot, owned.run_nonce, member_count, 2, True)

    def _prove_stopped_and_close_owned(
        self, owned: _OwnedRun, binding: PlayerBinding
    ) -> int:
        members: MemberSnapshot | None = None
        membership_proved = False
        member_count = 0
        problem: str | None = None

        def attempt(message: str, operation):
            nonlocal problem
            try:
                return operation()
            except BaseException:  # noqa: BLE001 - every authority must be attempted
                if problem is None:
                    problem = message
                return None

        try:
            members = self._host.stable_job_members(owned.job)
            member_count = len(members.identities)
            membership_proved = (
                member_count >= 1 and owned.binding.identity in members.identities
            )
        except BaseException:  # noqa: BLE001 - the owned Job must still be terminated
            membership_proved = False

        attempt("owned Job termination failed", lambda: self._host.terminate_job(owned.job))
        if attempt("owned process wait failed", lambda: self._host.wait_process(owned.process, 30_000)) is not True and problem is None:
            problem = "owned process did not signal"
        if attempt("owned Job empty proof failed", lambda: self._host.job_active_count(owned.job)) != 0 and problem is None:
            problem = "owned Job did not become empty"
        root_valid = attempt("root HWND invalidation proof failed", lambda: self._host.is_window(binding.root_hwnd))
        render_valid = attempt("render HWND invalidation proof failed", lambda: self._host.is_window(binding.render_hwnd))
        if (root_valid is not False or render_valid is not False) and problem is None:
            problem = "bound HWND remained valid after Job termination"
        players = attempt("post-stop player snapshot failed", self._host.complete_player_snapshot)
        if players is not None:
            if players.identities and problem is None:
                problem = "a BlueStacks player remained after owned stop"
            attempt("post-stop player snapshot close failed", players.close)
        if members is not None:
            attempt("fresh member snapshot close failed", members.close)
        attempt("launch member snapshot close failed", owned.members.close)
        attempt("owned process handle close failed", lambda: self._host.close_handle(owned.process))
        attempt("owned Job handle close failed", lambda: self._host.close_handle(owned.job))
        if not membership_proved and problem is None:
            problem = "fresh owned Job membership was not proved"
        if problem is not None:
            raise LifecycleError(problem)
        return member_count

    def _rollback_start(
        self,
        *,
        active: Active,
        reason: BlockReason,
        job: object | None,
        created: CreatedProcess | None,
        assignment_uncertain: bool,
        assigned: bool,
        thread_closed: bool,
        members: MemberSnapshot | None,
        binding: PlayerBinding | None,
    ) -> None:
        problem: str | None = None

        def attempt(message: str, operation):
            nonlocal problem
            try:
                return True, operation()
            except BaseException:  # noqa: BLE001 - every retirement authority must run
                if problem is None:
                    problem = message
                return False, None

        if created is not None:
            if assigned:
                if job is None:
                    problem = "assigned rollback lost its Job handle"
                    job_terminated = False
                else:
                    job_terminated, _ = attempt(
                        "rollback Job termination failed",
                        lambda: self._host.terminate_job(job),
                    )
                if not job_terminated:
                    attempt(
                        "rollback retained-process termination failed",
                        lambda: self._host.terminate_retained_process(
                            created.process_handle
                        ),
                    )
            elif assignment_uncertain:
                if job is None:
                    if problem is None:
                        problem = "uncertain rollback lost its Job handle"
                else:
                    attempt(
                        "rollback Job termination failed",
                        lambda: self._host.terminate_job(job),
                    )
                attempt(
                    "rollback retained-process termination failed",
                    lambda: self._host.terminate_retained_process(
                        created.process_handle
                    ),
                )
            else:
                attempt(
                    "rollback retained-process termination failed",
                    lambda: self._host.terminate_retained_process(
                        created.process_handle
                    ),
                )

            wait_ok, waited = attempt(
                "rollback process wait failed",
                lambda: self._host.wait_process(created.process_handle, 30_000),
            )
            if wait_ok and waited is not True and problem is None:
                problem = "rollback process wait failed"

            if assigned or assignment_uncertain:
                if job is None:
                    if problem is None:
                        problem = "rollback Job handle unavailable"
                else:
                    count_ok, active_count = attempt(
                        "rollback Job empty proof failed",
                        lambda: self._host.job_active_count(job),
                    )
                    if count_ok and active_count != 0 and problem is None:
                        problem = "rollback Job did not become empty"

            if binding is not None:
                root_ok, root_valid = attempt(
                    "rollback root HWND invalidation proof failed",
                    lambda: self._host.is_window(binding.root_hwnd),
                )
                render_ok, render_valid = attempt(
                    "rollback render HWND invalidation proof failed",
                    lambda: self._host.is_window(binding.render_hwnd),
                )
                if (
                    root_ok
                    and render_ok
                    and (root_valid is not False or render_valid is not False)
                    and problem is None
                ):
                    problem = "rollback HWND invalidation failed"

            snapshot_ok, players = attempt(
                "rollback player snapshot failed",
                self._host.complete_player_snapshot,
            )
            if snapshot_ok and players is not None:
                if players.identities and problem is None:
                    problem = "rollback player absence was not proved"
                attempt("rollback player snapshot close failed", players.close)

            if not thread_closed:
                attempt(
                    "rollback thread handle close failed",
                    lambda: self._host.close_thread(created.thread_handle),
                )

        if members is not None:
            attempt("rollback member snapshot close failed", members.close)
        if created is not None:
            attempt(
                "rollback process handle close failed",
                lambda: self._host.close_handle(created.process_handle),
            )
        if job is not None:
            attempt(
                "rollback Job handle close failed",
                lambda: self._host.close_handle(job),
            )

        if problem is None:
            committed, _ = attempt(
                "rollback blocked-state commit failed",
                lambda: self._commit_blocked(active, reason),
            )
            if committed:
                return

        try:
            self._state.commit(
                Active(active.slot, active.run_nonce, BlockReason.ROLLBACK_UNPROVED)
            )
        except BaseException:  # noqa: BLE001, S110 - previous ACTIVE remains fail-closed
            pass
        raise LifecycleError("rollback proof failed")
