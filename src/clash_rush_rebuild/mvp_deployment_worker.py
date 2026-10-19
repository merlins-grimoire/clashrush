"""Parent-owned bounded attack worker, with metadata-only held-mouse lease.

Reuses the repository's suspended/Job-owned child primitive. No game frame,
private identity, credential or key history crosses the shared-memory boundary.
An abnormal child is never retried and never receives a manufactured READY.
"""
from __future__ import annotations
import argparse

from multiprocessing import shared_memory
import os
from pathlib import Path
import secrets
import struct
import sys
import time

from .mvp_deployment import DeploymentError, finite

_MAGIC=b'CRDEP001'; _HEADER=struct.Struct('<8sIId')
_HELD=32;_REVOKED=33;_SIZE=64
OWNER_TIMEOUT=270  # remaining parent cleanup is a separate <=30s reserve
STOP_RESERVE=30.0
_context=None


class MouseLease:
    """Single parent/child lease; not an equal-privilege security sandbox."""
    def __init__(self,segment,*,owned):self.segment=segment;self.owned=owned;self.closed=False
    @classmethod
    def create(cls,deadline):
        finite(deadline)
        if deadline<=time.monotonic():raise DeploymentError('DEADLINE')
        segment=shared_memory.SharedMemory(create=True,size=_SIZE)
        obj=cls(segment,owned=True);segment.buf[:]=bytes(_SIZE)
        _HEADER.pack_into(segment.buf,0,_MAGIC,os.getpid(),secrets.randbelow(2**31-1)+1,deadline)
        return obj
    @classmethod
    def open(cls,name,*,expected_parent):
        if type(name) is not str or not name or len(name)>128:raise DeploymentError('OWNER_LEASE_INVALID')
        try:
            try:segment=shared_memory.SharedMemory(name=name,create=False,track=False)
            except TypeError:segment=shared_memory.SharedMemory(name=name,create=False)
            obj=cls(segment,owned=False)
            # Windows rounds named shared-memory mappings up to the system page
            # size, so ``segment.size`` can exceed the requested metadata size.
            # Reject truncation, but accept the platform-owned padding.
            if segment.size<_SIZE or obj.parent!=expected_parent or not obj.active():
                obj.close();raise DeploymentError('OWNER_LEASE_INVALID')
            return obj
        except DeploymentError:raise
        except BaseException:raise DeploymentError('OWNER_LEASE_UNAVAILABLE') from None
    def _header(self):
        if self.closed:raise DeploymentError('OWNER_LEASE_CLOSED')
        magic,parent,marker,deadline=_HEADER.unpack_from(self.segment.buf)
        if magic!=_MAGIC or parent<1 or not 1<=marker<2**32:raise DeploymentError('OWNER_LEASE_INVALID')
        finite(deadline)
        return parent,marker,deadline
    @property
    def parent(self):return self._header()[0]
    @property
    def marker(self):return self._header()[1]
    @property
    def deadline(self):return self._header()[2]
    @property
    def name(self):return self.segment.name
    @property
    def held(self):
        self._header()
        value=self.segment.buf[_HELD]
        if value not in (0,1):raise DeploymentError('OWNER_LEASE_INVALID')
        return value==1
    def active(self):
        try:return self.segment.buf[_REVOKED]==0 and time.monotonic()<self.deadline
        except BaseException:return False
    def set_held(self,value: bool):
        if type(value) is not bool:raise DeploymentError('OWNER_LEASE_INVALID')
        self._header()
        if value and not self.active():raise DeploymentError('AUTHORIZATION_LOST')
        self.segment.buf[_HELD]=int(value)
    def revoke(self):
        self._header();self.segment.buf[_REVOKED]=1
    def close(self):
        if self.closed:return
        if self.owned:
            try:self.segment.unlink()
            except FileNotFoundError:pass
        self.segment.close();self.closed=True
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def retire_mouse(lease: MouseLease,up=None) -> bool:
    """Revoke metadata only. Historical held state never authorizes parent UP.

    The unused argument preserves legacy call compatibility, not a capability.
    Only the live worker's attributed input sink owns physical release.
    """
    try:
        lease.revoke()
        return lease.held is False
    except BaseException:return False


def require_worker_context() -> MouseLease:
    if type(_context) is not MouseLease or _context.parent!=os.getppid() or not _context.active():
        raise DeploymentError('SUPERVISED_WORKER_REQUIRED')
    return _context


def consume_policy_handoff(*, authority, run, producer_epoch, offer, host, mutex, runner_manifest_bytes,
                           clock=time.monotonic):
    """W's exact one-use ingress, with no player launch/Job/termination capability."""
    from .mvp_process_topology import PlayerHandoff,acknowledge_player_handoff
    from .mvp_session_authority import DurableSessionAuthority,RunBindingV3
    from .mvp_policy_evidence import nanoseconds
    if (type(authority) is not DurableSessionAuthority or type(run) is not RunBindingV3
            or type(offer) is not PlayerHandoff or offer.run_nonce!=run.run_nonce
            or offer.generation!=run.generation or mutex.abandoned is not False):
        raise DeploymentError('POLICY_WORKER_BINDING')
    mutex.require_usable()
    from .mvp_runner_manifest import verify_runner_manifest
    verify_runner_manifest(runner_manifest_bytes,approved_sha256=run.runner_manifest_digest,
        approved_tree=run.source_tree,package_root=Path(__file__).parent)
    ack=acknowledge_player_handoff(host,offer)
    authority.consume_v3(run,producer_epoch=producer_epoch,now_ns=nanoseconds(clock()))
    return ack


class PolicyParentSession:
    """P's schema-3 orchestration; no capture/input, only explicit exact retirement.

    Call each administrative operation under the protected lifecycle mutex.
    Publish/wait for authenticated W messages OUTSIDE that mutex: W must acquire
    it to consume the reservation before P accepts its acknowledgment. The
    composition root supplies verified approval/stopped proof, never CLI flags.
    """
    def __init__(self, *, authority, run, writer_epoch, state, topology, runner_manifest_bytes,
                 approval_current, clock=time.monotonic):
        from .mvp_session_authority import DurableSessionAuthority,RunBindingV3
        from .mvp_process_topology import ParentProcessTopology
        if (type(authority) is not DurableSessionAuthority or type(run) is not RunBindingV3
                or type(topology) is not ParentProcessTopology
                or topology._run_nonce!=run.run_nonce or topology._generation!=run.generation
                or not callable(approval_current) or not callable(clock)):
            raise DeploymentError('POLICY_PARENT_BINDING')
        self.authority=authority;self.run=run;self.epoch=writer_epoch
        self.state=state;self.topology=topology;self.clock=clock
        self.runner_manifest_bytes=runner_manifest_bytes
        self.approval_current=approval_current
        self.active=None;self.prepared=False;self.last=-1
        self.normal_closed = None
        self.retirement_epoch = run.revocation_epoch
        self.canonical = None

    def _owned(self, mutex):
        if mutex.abandoned is not False:raise DeploymentError('POLICY_MUTEX_ABANDONED')
        mutex.require_usable()
        self.topology._parent_only()

    def _before_deadline(self):
        from .mvp_policy_evidence import nanoseconds
        now=nanoseconds(self.clock())
        if now<self.last or not self.run.started_ns<=now<self.run.deadline_ns:
            raise DeploymentError('POLICY_DEADLINE')
        self.last=now

    def _authorize_native(self):
        if self.canonical is not None:
            self.canonical.current()
        self._before_deadline()
        if self.approval_current(self.run) is not True:
            raise DeploymentError('POLICY_APPROVAL_UNAVAILABLE')
        # Protected source reads can expire D; no fallible validation after this.
        self._before_deadline()

    def _retain_active(self):
        from .lifecycle_state import Active,BlockReason
        if self.active is None:return
        current=self.state.load()
        if type(current) is not Active or (current.slot,current.run_nonce)!=(self.active.slot,self.run.run_nonce):
            raise DeploymentError('POLICY_LIFECYCLE_CHANGED')
        blocked=Active(current.slot,current.run_nonce,BlockReason.STOP_PROOF)
        if self.state.commit(blocked)!=blocked or self.state.load()!=blocked:
            raise DeploymentError('POLICY_LIFECYCLE_READBACK')

    def prepare(self, *, mutex, stopped, slot, worker_executable, worker_command,
                player_executable, player_command):
        from .lifecycle_state import Active,Ready
        self._owned(mutex);self._before_deadline()
        if self.prepared:raise DeploymentError('POLICY_PARENT_REPLAY')
        from .mvp_runner_manifest import verify_runner_manifest
        verify_runner_manifest(self.runner_manifest_bytes,approved_sha256=self.run.runner_manifest_digest,
            approved_tree=self.run.source_tree,package_root=Path(__file__).parent)
        self._before_deadline()
        ready=self.state.load()
        if type(slot) is not int or type(ready) is not Ready or ready.next_slot!=slot:
            raise DeploymentError('POLICY_READY_BINDING')
        # Host write-ahead barrier precedes even local reservation.
        if self.canonical is not None:
            self.canonical.reserve(self.run, self.topology._parent,
                authority=self.authority, state=self.state, mutex=mutex,
                commands=((worker_executable, worker_command), (player_executable, player_command)))
        # Reserve/readback before any process; ACTIVE/readback before creation.
        self.authority.reserve_v3(self.run,writer_epoch=self.epoch,proof=stopped)
        self.prepared=True
        if self.topology.purpose == 'CALIBRATION_READ_ONLY':
            self.authority.record_calibration_scope_v3(self.run, self.calibration_scope,
                                                       writer_epoch=self.epoch)
        self.active=Active(slot,self.run.run_nonce,None)
        try:
            if self.state.commit(self.active)!=self.active or self.state.load()!=self.active:
                raise DeploymentError('POLICY_ACTIVE_READBACK')
            self._before_deadline()
            offer=self.topology.prepare(worker_executable,worker_command,player_executable,player_command,
                                        before_create=self._authorize_native,
                                        admission=self.canonical, mutex=mutex)
            self._before_deadline()
            return offer
        except BaseException:
            self.topology.revoke('BOOTSTRAP_UNCERTAIN')
            self._retain_active()
            raise

    def start_worker(self, *, mutex):
        self._owned(mutex)
        try:
            self._before_deadline()
            if not self.prepared:raise DeploymentError('POLICY_PARENT_NOT_PREPARED')
            self.topology.start_worker(before_resume=self._authorize_native)
        except BaseException:
            self.topology.revoke('BOOTSTRAP_UNCERTAIN')
            self._retain_active()
            raise

    def accept_worker(self, ack, *, mutex):
        self._owned(mutex)
        try:
            self._before_deadline()
            # W consumed its exact reservation under the mutex before sending ack.
            self.authority._bound_v3(self.run,producer=True)
            self._before_deadline()
            self.topology.accept_worker_ack(ack,before_resume=self._authorize_native)
        except BaseException:
            self.topology.revoke('BOOTSTRAP_UNCERTAIN')
            self._retain_active()
            raise

    def revoke(self, *, mutex, lease):
        self._owned(mutex)
        # Local no-more-gameplay is independent of SQLite writer availability.
        lease.revoke()
        token=self.topology.revoke('NO_MORE_GAMEPLAY')
        try:
            if self.authority.status_v3().revocation_epoch != self.retirement_epoch:
                raise DeploymentError('POLICY_AUTHORITY_REVOKED')
            self.retirement_epoch = self.authority.revoke_v3(writer_epoch=self.epoch)
        finally:
            self._retain_active()
        return token

    def contain_worker(self, receipt, *, mutex):
        self._owned(mutex)
        try:
            return self.topology.terminate_worker(receipt)
        finally:
            self._retain_active()

    def finalize(self, *, mutex, outcome, reason):
        """Administrative only; D does not discard genuine immutable pre-D facts.

        Worker exit/stdout is not SUCCESS. The sole authority validates the entire
        durable seal. ACTIVE/closure stay unresolved while the player survives.
        """
        self._owned(mutex)
        self._retain_active()
        return self.authority.finalize_v3(self.run,writer_epoch=self.epoch,
                                          outcome=outcome,reason=reason)

    def dispose(self, *, mutex):
        self._owned(mutex)
        try:
            if self.normal_closed is None:
                self._retain_active()
        finally:
            self.topology.dispose()

    def retire_normal_success(self, *, mutex, expected_title):
        """Close a proved gameplay success only; failures keep exact ACTIVE.

        Immutable gameplay outcome and physical retirement are separate facts.
        All physical stop/absence work must finish pre-D; durable closure may
        finish later. A crash between READY and SQLite closure still blocks
        admission on the unclosed generation, never replays gameplay.
        """
        return self._retire_exact(mutex=mutex, expected_title=expected_title, calibration=False)

    def retire_calibration(self, *, mutex, expected_title):
        """Same-parent CAPTURED cleanup; never upgrades the semantic outcome."""
        return self._retire_exact(mutex=mutex, expected_title=expected_title, calibration=True)

    def _retire_exact(self, *, mutex, expected_title, calibration):
        from contextlib import nullcontext, ExitStack
        from .mvp_frozen_stop import pinned_stop_sources
        from .win32_lifecycle_host import Win32LifecycleHost
        self._owned(mutex)
        if self.normal_closed is not None:
            self.authority._require_closed_v3(self.run.generation)
            if self.state.load() != self.normal_closed:
                raise DeploymentError('POLICY_LIFECYCLE_CHANGED')
            if self.canonical is not None:
                self.canonical.current()
                self.canonical.stopped(self.authority, self.state)
            return self.normal_closed
        sources = getattr(self, 'stop_sources', None)
        if isinstance(self.topology._host, Win32LifecycleHost) and sources is None:
            raise DeploymentError('POLICY_STOP_SOURCES_REQUIRED')
        with ExitStack() as stack:
            limits = stack.enter_context(pinned_stop_sources(sources, self.run) if sources is not None else
                nullcontext((self.run.deadline_ns, 2**63-1)))
            return self._retire_pinned(mutex=mutex, expected_title=expected_title,
                                       calibration=calibration, limits=limits, close_sources=stack.close)

    def _retire_pinned(self, *, mutex, expected_title, calibration, limits, close_sources):
        from dataclasses import asdict
        from .lifecycle_state import Ready, encode_state
        from .mvp_session_authority import RetirementV3, ClosureProofV3, canonical_v3, digest_v3
        self._owned(mutex)
        if self.normal_closed is not None:
            self.authority._require_closed_v3(self.run.generation)
            if self.state.load() != self.normal_closed:
                raise DeploymentError('POLICY_LIFECYCLE_CHANGED')
            if self.canonical is not None:
                self.canonical.current()
                self.canonical.stopped(self.authority, self.state)
            return self.normal_closed
        self._retain_active()
        outcome = self.authority.outcome_v3(self.run.generation)
        facts, inputs, _ = self.authority._evidence_v3(self.run)
        def authorize(binding=None):
            self._authorize_native()
            if calibration:
                observation = self.authority.calibration_observation_v3(self.run)
                scope = self.authority.calibration_scope_v3(self.run)
                if (self.topology.purpose != 'CALIBRATION_READ_ONLY'
                        or scope != self.calibration_scope or observation is None
                        or observation.status != 'CAPTURED'
                        or observation.offer_digest != digest_v3(canonical_v3(asdict(self.topology.offer)))
                        or outcome is None or (outcome.outcome, outcome.reason) != ('BLOCKED', 'EVIDENCE_INCOMPLETE')
                        or inputs or {f.kind for f in facts} != {'CALIBRATION_SCOPE', 'CALIBRATION_OBSERVATION'}):
                    raise DeploymentError('POLICY_CALIBRATION_RETIREMENT_DENIED')
                worker = self.authority._open().execute(
                    "SELECT * FROM retirement_v3 WHERE generation=? AND role='WORKER'",
                    (self.run.generation,)).fetchone()
                if worker is None:
                    raise DeploymentError('POLICY_WORKER_ABSENCE')
                import json
                worker_proof = RetirementV3('WORKER', worker['identity_digest'], worker['proof'])
                raw = json.loads(worker_proof.proof)
                if (worker_proof.digest != worker['proof_digest'] or raw['binding_digest'] != self.run.digest
                        or raw['topology_digest'] != observation.offer_digest
                        or raw['identity_digest'] != digest_v3(canonical_v3(asdict(self.topology.offer.worker)))):
                    raise DeploymentError('POLICY_WORKER_RETIREMENT_CONFLICT')
                if self.calibration_evidence(observation, self.topology.offer, binding) != observation.evidence_digest:
                    raise DeploymentError('POLICY_CALIBRATION_EVIDENCE_UNREADABLE')
            else:
                if outcome is None or outcome.outcome != 'SUCCESS':
                    raise DeploymentError('POLICY_NORMAL_SUCCESS_REQUIRED')
                self.authority._complete_v3(self.run, facts, inputs)
            if not self.authority._inputs_clear_v3(self.run, inputs):
                raise DeploymentError('POLICY_INPUT_UNCERTAIN')
            # Evidence/native work finishes before this final source/deadline
            # check. Read durable revocation last, including source callbacks.
            self._authorize_native()
            self._owned(mutex)
            if self.canonical is not None:
                self.canonical.require_offer(self.topology.offer)
            status = self.authority.status_v3()
            if status.revocation_epoch != self.retirement_epoch or status.current_generation != self.run.generation:
                raise DeploymentError('POLICY_AUTHORITY_REVOKED')
            from .mvp_frozen_stop import FrozenStopFacts
            return FrozenStopFacts(self.run.digest, self.retirement_epoch, *limits)
        authorize()
        if self.canonical is not None:
            self.canonical.stop_intent(mutex)
        absence = self.topology.retire_normal_player(expected_title=expected_title,
            deadline=limits[0] / 1e9, before_retire=authorize)
        topology_digest = digest_v3(canonical_v3(asdict(self.topology.offer)))
        identity = digest_v3(canonical_v3(asdict(self.topology.offer.player)))
        # Successful closes are not inferred from process death. A failed close
        # leaves ACTIVE and open closure, with no second physical stop attempt.
        self.topology.dispose()
        if self.topology._handles:
            raise DeploymentError('POLICY_HANDLE_RESIDUE')
        close_sources()
        from . import mvp_frozen_stop as stop
        observed_ns = stop._monotonic_ns()
        if not self.run.started_ns <= observed_ns < limits[0] or stop._wall_ns() >= limits[1]:
            raise DeploymentError('POLICY_LATE_HANDLE_CLOSE')
        self._retain_active()
        disposition = 'LIFECYCLE_VERIFIED' if calibration else 'RETIRED_PRE_D'
        proof = canonical_v3(dict(schema=3, binding_digest=self.run.digest,
            identity_digest=identity, topology_digest=topology_digest,
            absence_digest=digest_v3(canonical_v3(absence)), disposition=disposition,
            observed_ns=observed_ns))
        self.authority.record_retirement_v3(self.run, RetirementV3('PLAYER', identity, proof),
                                            writer_epoch=self.epoch)
        ready = Ready(self.active.slot if calibration else (self.active.slot + 1) % 5)
        closure = ClosureProofV3(self.run.digest, 'OWNED_RELEASE_NO_PLAYER_KILL_V1', topology_digest,
            'NOT_REQUIRED', 0, disposition, digest_v3(encode_state(ready)),
            digest_v3(canonical_v3({'inputs': [i.digest for i in inputs], 'held': False})))
        def close_generation():
            self.authority.close_v3(self.run, writer_epoch=self.epoch, proof=closure)
            self.authority._require_closed_v3(self.run.generation)
            if self.canonical is not None:
                self.canonical.closed(self.authority.closure_v3(self.run.generation), mutex,
                    authority=self.authority, lifecycle=(ready, encode_state(ready)))
        # Both lanes keep the transition guard across local AND host closure.
        committed = self.state.commit_with_postcondition(ready, close_generation)
        if committed != ready or self.state.load() != ready:
            raise DeploymentError('POLICY_LIFECYCLE_READBACK')
        self.normal_closed = ready
        return ready


def run_supervised_attack(project_root: str,slots_path: str,transaction_ref: str):
    """The installed CLI calls this parent, not the old direct attack entry.

    Imports the existing native child owner lazily. No launch is possible without
    the child's original durable admission; no timeout/retry changes its state.
    """
    if os.name!='nt':raise DeploymentError('WINDOWS_REQUIRED')
    from .diagnostic_child_job import run_owned_diagnostic_child,DiagnosticChildOutcome
    from .mvp_local_gameplay import VisitResult
    from .mvp_session_authority import DurableSessionAuthority
    from .mvp_deployment_receipt import autonomy_status
    project=Path(project_root).resolve(strict=True)
    # Validate profile/grant before even spawning a worker (child revalidates).
    from .mvp_local_native import _candidate_tree
    from .mvp_deployment_profile import load_profile
    from .mvp_deployment_receipt import require_profile
    authority=DurableSessionAuthority(project/'var'/'mvp-session.sqlite3')
    try:
        status=authority.status()
        profile=load_profile(project,candidate_tree=_candidate_tree(project))
        require_profile(authority,status.run_nonce,profile.digest)
    finally:authority.close()
    with MouseLease.create(time.monotonic()+OWNER_TIMEOUT) as lease:
        command=[sys.executable,'-m','clash_rush_rebuild.mvp_deployment_worker',
                 '--project-root',str(project),'--slots',slots_path,
                 '--transaction-ref',transaction_ref,'--lease',lease.name]
        outcome=None;mouse_released=False
        try:outcome=run_owned_diagnostic_child(command,timeout=OWNER_TIMEOUT)
        finally:mouse_released=retire_mouse(lease)
        if (not mouse_released or type(outcome) is not DiagnosticChildOutcome
                or outcome.cleanup_succeeded is not True or outcome.child_wait_completed is not True
                or outcome.operational_failure is not False or outcome.returncode!=0
                or outcome.stdout!='AUTONOMOUS\n' or outcome.stderr!=''):
            raise DeploymentError('SUPERVISED_ATTACK_INCOMPLETE')
    authority=DurableSessionAuthority(project/'var'/'mvp-session.sqlite3')
    try:
        if autonomy_status(authority,transaction_ref)!='AUTONOMOUS':raise DeploymentError('AUTONOMY_UNPROVED')
    finally:authority.close()
    return VisitResult('completed','AUTONOMOUS_RETURNED_HOME',True,True)


def main(argv=None):
    global _context
    parser=argparse.ArgumentParser(description='Private bounded deployment worker')
    for flag in ('project-root','slots','transaction-ref','lease'):parser.add_argument('--'+flag,required=True)
    lease=None;complete=False
    try:
        args=parser.parse_args(argv)
        if os.name!='nt':raise DeploymentError('WINDOWS_REQUIRED')
        from .mvp_session_authority import require_legacy_command
        require_legacy_command(Path(args.project_root)/'var'/'mvp-session.sqlite3')
        lease=MouseLease.open(args.lease,expected_parent=os.getppid());_context=lease
        from .mvp_local_native import run_native_mvp_visit
        from .mvp_session_authority import DurableSessionAuthority
        from .mvp_deployment_receipt import autonomy_status
        result=run_native_mvp_visit(args.project_root,args.slots,args.transaction_ref)
        a=DurableSessionAuthority(Path(args.project_root)/'var'/'mvp-session.sqlite3')
        try:complete=getattr(result,'confirmed',None) is True and autonomy_status(a,args.transaction_ref)=='AUTONOMOUS'
        finally:a.close()
    except BaseException:
        complete=False
    finally:
        _context=None
        if lease is not None:
            try:lease.close()
            except BaseException:complete=False
    if complete:
        print('AUTONOMOUS');return 0
    sys.stderr.write('DEPLOYMENT_INCOMPLETE\n');return 2


if __name__=='__main__':raise SystemExit(main())
