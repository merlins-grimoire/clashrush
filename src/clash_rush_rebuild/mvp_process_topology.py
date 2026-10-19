"""Explicit P-owned topology for OWNED_RELEASE_NO_PLAYER_KILL_V1.

This is NOT admission, an IPC authenticator, or player retirement authority.
P must hold the durable admission barrier before prepare(), authenticate W's
messages, and keep that barrier on every failure/dispose. Je is always non-killing.
Gameplay Jw is non-killing; closed calibration Jw uses worker-only kill-on-close.
Ambient Jobs are neither changed nor escaped.

The existing native suspended CreateProcess primitive is retained. The generic
Popen/diagnostic donor wrapper remains unchanged and is not a policy fallback.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import secrets
import threading

from .lifecycle import CreatedProcess, MemberSnapshot, ProcessIdentity
from .win32_lifecycle_host import Win32LifecycleHost


class ProcessTopologyError(RuntimeError):
    """Uncertainty blocks the run, never grants a kill or player resume."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProcessTopologyError(message)


def _nonce(value: object) -> bool:
    return (type(value) is str and len(value) == 32
            and all(c in '0123456789abcdef' for c in value))


@dataclass(frozen=True, slots=True)
class PlayerHandoff:
    run_nonce: str
    generation: int
    challenge: str
    parent: ProcessIdentity
    worker: ProcessIdentity
    player: ProcessIdentity
    remote_player_handle: int
    remote_player_job_query: int

    def __post_init__(self) -> None:
        _require(_nonce(self.run_nonce) and _nonce(self.challenge)
                 and type(self.generation) is int and self.generation > 0
                 and all(type(i) is ProcessIdentity for i in
                         (self.parent, self.worker, self.player))
                 and len({self.parent.pid, self.worker.pid, self.player.pid}) == 3
                 and all(type(h) is int and h>0 for h in
                         (self.remote_player_handle,self.remote_player_job_query))
                 and self.remote_player_handle!=self.remote_player_job_query,
                 'invalid exact-generation player handoff')


@dataclass(frozen=True, slots=True)
class HandoffAck:
    offer: PlayerHandoff
    accepted: bool
    noninheritable: bool


@dataclass(frozen=True, slots=True)
class ReleaseStageReceipt:
    """Authenticated W statement, not input-clear or successful-run evidence.

    W must stop ALL descendant spawning before publishing and stay stopped.
    worker_members includes W and each still-live descendant, with FILETIME.
    ATTEMPTED/NO_AUTHORIZED_RELEASE never imply that physical input is clear.
    """
    offer: PlayerHandoff
    revoke_token: str
    stage: str
    worker_members: tuple[ProcessIdentity, ...]
    spawning_stopped: bool


def acknowledge_player_handoff(host: Win32LifecycleHost, offer: PlayerHandoff) -> HandoffAck:
    """W validates its received query/wait handle; never creates/resumes E.

    Call only after authenticating P and the exact reservation. The handle stays
    owned by W for query/wait use; W closes it normally (or process exit does).
    Je has JOB_OBJECT_QUERY only so W can reuse the exact family/window validator.
    No Job mutation, thread, termination or duplicate-handle authority reaches W.
    W closes both local duplicates; P retains the original non-killing Je.
    """
    _require(type(offer) is PlayerHandoff, 'typed player handoff required')
    current = host.current_process_handle()
    _require(host.identity_from_handle(current, host.current_process_id()) == offer.worker,
             'handoff delivered to wrong worker')
    handle = offer.remote_player_handle
    _require(host.identity_from_handle(handle, offer.player.pid) == offer.player,
             'handoff player identity mismatch')
    _require(host.handle_is_inheritable(handle) is False, 'inheritable player handle denied')
    _require(host.wait_process(handle, 0) is False, 'handoff player already exited')
    job=offer.remote_player_job_query
    _require(host.handle_is_inheritable(job) is False and host.job_limit_flags(job)==0
             and host.is_process_in_job(handle,job) is True
             and host.is_process_in_job(current,job) is False,
             'handoff query-only player family unproved')
    return HandoffAck(offer, True, True)


class ParentProcessTopology:
    """Single-thread-owned one-shot controller; no destructor or timeout kill.

    prepare -> publish offer -> start_worker -> authenticate/accept_worker_ack.
    revoke -> W release/spawn-freeze receipt -> optional terminate_worker.
    dispose closes only mode-proved resources; it is never player retirement.
    Partial bootstrap handles remain retained on failure for explicit disposal.
    """
    def __init__(self, host: Win32LifecycleHost, *, run_nonce: str, generation: int,
                 purpose: str = 'GAMEPLAY'):
        _require(type(purpose) is str and purpose in ('GAMEPLAY', 'CALIBRATION_READ_ONLY'),
                 'closed topology purpose required')
        self.purpose = purpose
        _require(_nonce(run_nonce) and type(generation) is int and generation > 0,
                 'exact run nonce and positive generation required')
        self._host = host
        self._thread = threading.get_ident()
        self._parent = host.identity_from_handle(host.current_process_handle(), host.current_process_id())
        self._run_nonce, self._generation = run_nonce, generation
        self._state = 'NEW'
        self._blocked = False
        self._revoke_token: str | None = None
        self._handles: list[tuple[object, bool]] = []
        self.worker_job: object | None = None
        self.player_job: object | None = None
        self.worker: CreatedProcess | None = None
        self.player: CreatedProcess | None = None
        self.offer: PlayerHandoff | None = None
        self._worker_identity: ProcessIdentity | None = None
        self._player_identity: ProcessIdentity | None = None
        self._capture_members = None
        self._captured_window = None

    @property
    def blocked(self) -> bool:
        return self._blocked

    def _parent_only(self) -> None:
        _require(threading.get_ident() == self._thread, 'topology owner thread required')
        _require(self._host.identity_from_handle(self._host.current_process_handle(),
                 self._host.current_process_id()) == self._parent, 'topology owner process required')

    def _block(self) -> str:
        self._blocked = True
        if self._revoke_token is None:
            self._revoke_token = secrets.token_hex(16)
        if self._state not in ('TERMINATION_REQUESTED', 'DISPOSED'):
            self._state = 'REVOKED'
        return self._revoke_token

    @contextmanager
    def _operation(self):
        try:
            self._parent_only()
            yield
        except BaseException:
            self._block()
            raise

    def _new_job(self, *, worker=False) -> object:
        job = self._host.create_job()
        self._handles.append((job, True))
        # Only a newly created, empty, unnamed P-owned Job is configured.
        flags = 0x2000 if worker and self.purpose == 'CALIBRATION_READ_ONLY' else 0
        if flags:
            self._host.set_kill_on_close(job)
        else:
            self._host.set_non_killing_job(job)
        _require(type(self._host.job_limit_flags(job)) is int
                 and self._host.job_limit_flags(job) == flags, 'private Job flags unproved')
        with ExitStack() as stack:
            _require(not self._members(job, stack), 'new Job is not empty')
        return job

    def _create(self, executable: str, command_line: str) -> CreatedProcess:
        process = self._host.create_policy_suspended(executable, command_line)
        self._handles.extend(((process.thread_handle, False), (process.process_handle, False)))
        return process

    def _members(self, job: object, stack: ExitStack) -> frozenset[ProcessIdentity]:
        snapshot = self._host.stable_job_members(job)
        _require(type(snapshot) is MemberSnapshot, 'complete retained member snapshot required')
        stack.callback(snapshot.close)
        values = snapshot.identities
        _require(len(set(values)) == len(values), 'duplicate member identity')
        return frozenset(values)

    @contextmanager
    def _proof(self, expected_workers: tuple[ProcessIdentity, ...], *, player_suspended: bool):
        """Retain the snapshot handles until the adjacent native leaf returns."""
        h = self._host
        _require(self.worker is not None and self.player is not None, 'both P-created actors required')
        _require(h.job_limit_flags(self.worker_job) == (0x2000 if self.purpose == 'CALIBRATION_READ_ONLY' else 0)
                 and h.job_limit_flags(self.player_job) == 0, 'private Job limits changed')
        _require(h.identity_from_handle(self.worker.process_handle, self.worker.pid) == self._worker_identity
                 and h.identity_from_handle(self.player.process_handle, self.player.pid) == self._player_identity,
                 'retained process identity changed')
        for job in (self.worker_job, self.player_job):
            _require(h.is_process_in_job(h.current_process_handle(), job) is False,
                     'parent in private Job')
        _require(h.is_process_in_job(self.worker.process_handle, self.worker_job) is True
                 and h.is_process_in_job(self.player.process_handle, self.player_job) is True
                 and h.is_process_in_job(self.worker.process_handle, self.player_job) is False
                 and h.is_process_in_job(self.player.process_handle, self.worker_job) is False,
                 'worker/player Job separation unproved')
        with ExitStack() as stack:
            workers = self._members(self.worker_job, stack)
            players = self._members(self.player_job, stack)
            _require(workers == frozenset(expected_workers) and self._worker_identity in workers,
                     'worker Job differs from exact declared members')
            _require(self._player_identity in players and not workers.intersection(players),
                     'player members overlap worker Job')
            if player_suspended:
                _require(players == frozenset((self._player_identity,)), 'suspended player has unexpected members')
            _require(h.wait_process(self.worker.process_handle, 0) is False
                     and h.wait_process(self.player.process_handle, 0) is False, 'actor exited during proof')
            yield

    def prepare(self, worker_executable: str, worker_command_line: str,
                player_executable: str, player_command_line: str, *, before_create=None,
                admission=None, mutex=None) -> PlayerHandoff:
        """P creates both actors suspended; assignment/readback precede any resume."""
        with self._operation():
            _require(self._state == 'NEW', 'topology preparation is one-shot')
            self._state = 'PREPARING'
            self.worker_job = self._new_job(worker=True)
            self.player_job = self._new_job()
            if before_create is not None:before_create()
            from contextlib import nullcontext
            with (admission.creating('WORKER', mutex, self) if admission is not None else nullcontext()):
                self.worker = self._create(worker_executable, worker_command_line)
            self._worker_identity = self._host.identity_from_handle(self.worker.process_handle, self.worker.pid)
            self._host.assign_to_job(self.worker_job, self.worker.process_handle)
            if before_create is not None:before_create()
            with (admission.creating('PLAYER', mutex, self) if admission is not None else nullcontext()):
                self.player = self._create(player_executable, player_command_line)
            self._player_identity = self._host.identity_from_handle(self.player.process_handle, self.player.pid)
            _require(self._host.is_process_in_job(self.player.process_handle, self.worker_job) is False,
                     'suspended player already in worker containment')
            self._host.assign_to_job(self.player_job, self.player.process_handle)
            with self._proof((self._worker_identity,), player_suspended=True):
                remote = self._host.duplicate_query_wait_to_worker(self.player.process_handle, self.worker.process_handle)
                remote_job = self._host.duplicate_query_job_to_worker(self.player_job,self.worker.process_handle)
            self.offer = PlayerHandoff(self._run_nonce, self._generation, secrets.token_hex(16),
                                       self._parent, self._worker_identity, self._player_identity, remote,remote_job)
            if admission is not None:
                admission.published(self.offer, mutex)
            self._state = 'PREPARED'
            return self.offer

    def start_worker(self, *, before_resume=None) -> None:
        """Call after reliable offer publication. No broad handle inheritance."""
        with self._operation():
            _require(self._state == 'PREPARED', 'prepared worker required')
            with self._proof((self._worker_identity,), player_suspended=True):
                if before_resume is not None:before_resume()
                _require(self._host.resume_thread(self.worker.thread_handle) == 1, 'worker resume count mismatch')
            self._state = 'WORKER_STARTED'

    def accept_worker_ack(self, ack: HandoffAck, *, before_resume=None) -> None:
        """Exact authenticated acknowledgment and fresh proof, then resume E once."""
        with self._operation():
            _require(self._state == 'WORKER_STARTED' and not self._blocked, 'worker handoff not open')
            _require(type(ack) is HandoffAck and type(ack.offer) is PlayerHandoff
                     and ack.offer == self.offer and ack.accepted is True and ack.noninheritable is True,
                     'missing or mismatched worker acknowledgment')
            with self._proof((self._worker_identity,), player_suspended=True):
                if before_resume is not None:before_resume()
                _require(self._host.resume_thread(self.player.thread_handle) == 1, 'player resume count mismatch')
            self._state = 'RUNNING'

    def revoke(self, reason: str) -> str:
        """Latch no-more-gameplay locally; caller must send this token to W.

        No message/ack, timeout or process exit here ever authorizes a kill.
        This is not itself an IPC delivery or durable barrier write.
        """
        self._parent_only()
        _require(type(reason) is str and bool(reason.strip()), 'revocation reason required')
        return self._block()

    def terminate_worker(self, receipt: ReleaseStageReceipt) -> bool:
        """Only Jw, after release stage + spawn freeze + exact retained members.

        Returns False if W already exited; never uses a PID/process-tree fallback.
        A receipt can be uncertain about release while still proving stage ran.
        This call does not prove exit/Job-empty; consumers must wait/read back.
        """
        with self._operation():
            _require(self._state == 'REVOKED' and self.offer is not None, 'revoke before worker termination')
            _require(type(receipt) is ReleaseStageReceipt
                     and type(receipt.offer) is PlayerHandoff and receipt.offer == self.offer
                     and type(receipt.revoke_token) is str
                     and receipt.revoke_token == self._revoke_token
                     and type(receipt.stage) is str
                     and receipt.stage in ('NOT_REQUIRED', 'ATTEMPTED', 'NO_AUTHORIZED_RELEASE')
                     and receipt.spawning_stopped is True
                     and type(receipt.worker_members) is tuple
                     and all(type(i) is ProcessIdentity for i in receipt.worker_members)
                     and len(set(receipt.worker_members)) == len(receipt.worker_members),
                     'missing or mismatched release-stage/spawn-freeze receipt')
            if self._host.wait_process(self.worker.process_handle, 0):
                return False
            with self._proof(receipt.worker_members, player_suspended=False):
                self._host.terminate_job(self.worker_job)
            self._state = 'TERMINATION_REQUESTED'
            return True

    def pin_capture(self, window, expected_title):
        from .mvp_calibration import WindowObservation
        self._parent_only()
        _require(type(window) is WindowObservation and self._capture_members is None
                 and self._state == 'RUNNING', 'captured render pin denied')
        window.require_v2()
        h = self._host
        _require(h.wait_process(self.worker.process_handle, 0) is False,
                 'worker must retain capture until parent pin')
        members = h.stable_job_members(self.player_job)
        try:
            binding = h.bind_exact(self.offer.player, expected_title, members)
            _require(window.player == binding.identity and window.render == binding.render_identity
                     and window.render in members.identities
                     and (window.root_hwnd, window.render_hwnd, window.width, window.height)
                     == (binding.root_hwnd, binding.render_hwnd, binding.width, binding.height),
                     'captured render changed')
            h._require_capture_binding(binding, self.player_job)
            self._captured_window = window
            self._capture_members = members
        except BaseException:
            members.close()
            raise

    def retire_normal_player(self, *, expected_title, deadline, before_retire, clock=None):
        """Explicit normal-success stop, called only after durable result proof.

        This is not used by dispose/failure/recovery. Je remains non-killing on
        close; only its exact retained handle is stopped. No post-deadline kill,
        numeric PID fallback, WM_CLOSE or gameplay cleanup exists here.
        """
        import math
        from .lifecycle import PlayerBinding
        from . import mvp_frozen_stop as stop
        _require(clock is None, 'effectful retirement clock denied')
        with self._operation():
            _require(self._state in ('REVOKED', 'TERMINATION_REQUESTED')
                     and self.offer is not None, 'normal retirement requires frozen worker')
            h = self._host
            def remaining():
                now = stop._monotonic_ns() / 1e9
                _require(type(now) in (int, float) and math.isfinite(now)
                         and type(deadline) in (int, float) and math.isfinite(deadline)
                         and 0 <= now < deadline, 'normal retirement deadline expired')
                return deadline - now
            def identities():
                _require(h.wait_process(self.worker.process_handle, 0) is True
                         and h.job_active_count(self.worker_job) == 0,
                         'worker residue forbids normal retirement')
                _require(h.job_limit_flags(self.player_job) == 0
                         and h.identity_from_handle(self.player.process_handle, self.player.pid) == self.offer.player
                         and h.is_process_in_job(self.player.process_handle, self.player_job) is True
                         and h.is_process_in_job(h.current_process_handle(), self.player_job) is False,
                         'exact retained player ownership required')
            remaining()
            identities()
            with ExitStack() as stack:
                members = h.stable_job_members(self.player_job)
                _require(type(members) is MemberSnapshot, 'retained player family required')
                stack.callback(members.close)
                _require(self.offer.player in members.identities
                         and self.offer.worker not in members.identities
                         and self.offer.parent not in members.identities,
                         'player family ownership changed')
                binding = h.bind_exact(self.offer.player, expected_title, members)
                _require(type(binding) is PlayerBinding and binding.identity == self.offer.player,
                         'exact owned window binding required')
                identities()
                h._require_capture_binding(binding, self.player_job)
                if self.purpose == 'CALIBRATION_READ_ONLY':
                    captured = self._captured_window
                    _require(captured is not None and self._capture_members is not None
                             and captured.render == binding.render_identity
                             and captured.player == binding.identity
                             and (captured.root_hwnd, captured.render_hwnd, captured.width, captured.height)
                             == (binding.root_hwnd, binding.render_hwnd, binding.width, binding.height),
                             'captured render continuity unproved')
                budget = min(5000, int(remaining() * 1000))
                _require(budget > 0, 'normal retirement budget exhausted')
                # Preparation can perform IO; only the frozen grant reaches the
                # trusted physical leaf, with no callback after its time sample.
                target = self.player_job
                frozen_owners = (self.offer, self._parent, self._player_identity,
                                 self._captured_window, self._capture_members, self.player, self.worker)
                leaf = stop.physical_leaf(h)
                facts = before_retire(binding)
                _require(self.player_job is target and self._host is h
                         and frozen_owners == (self.offer, self._parent, self._player_identity,
                             self._captured_window, self._capture_members, self.player, self.worker),
                         'retirement target changed during preparation')
                grant = stop.FrozenStop(self, facts)
                self._state = 'PLAYER_RETIREMENT_REQUESTED'
                grant.consume(self, leaf, target)
                _require(h.wait_process(self.player.process_handle, budget) is True
                         and h.job_active_count(self.player_job) == 0
                         and h.is_window(binding.root_hwnd) is False
                         and h.is_window(binding.render_hwnd) is False,
                         'player retirement residue')
                snapshot = h.complete_player_snapshot()
                stack.callback(snapshot.close)
                _require(not snapshot.identities, 'host player residue')
            remaining()  # includes snapshot-handle cleanup, never late absence proof
            self._state = 'PLAYER_RETIRED'
            return {'process_signaled': True, 'job_active': 0, 'windows_invalid': True,
                    'host_players': 0, 'snapshot_handles_closed': True}

    def dispose(self) -> None:
        """Scoped handle disposal, never READY/player absence/retirement proof.

        Unknown or changed Job flags deliberately retain that handle. All other
        handles are attempted; failed closes remain tracked for reconciliation.
        Calibration Jw close contains W only; gameplay release rules are unchanged.
        W's remote query handle is never closed in P's handle namespace.
        """
        self._parent_only()
        self._block()
        errors = []
        if self._capture_members is not None:
            try:
                self._capture_members.close()
                self._capture_members = None
            except BaseException as error:
                errors.append(error)
        for handle, is_job in tuple(self._handles):
            try:
                if is_job:
                    expected = 0x2000 if handle == self.worker_job and self.purpose == 'CALIBRATION_READ_ONLY' else 0
                    _require(self._host.job_limit_flags(handle) == expected, 'cannot prove scoped Job close')
                self._host.close_handle(handle)
                self._handles.remove((handle, is_job))
            except BaseException as error:
                errors.append(error)
        if errors:
            raise ProcessTopologyError('non-killing disposal incomplete; retained resources require reconciliation') from errors[0]
        self._state = 'DISPOSED'
