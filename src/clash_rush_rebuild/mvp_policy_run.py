"""Policy Run command and exact P/W handshake, separate from legacy lifecycle.

Native gameplay activation remains unavailable. The separate read-only calibration
command admits only protected, exact-purpose approvals and measured specifications;
its observer has no input executor. Only the parent may perform the separately
approved exact retained calibration retirement after sealed evidence. Disposable
verification supplies only OS, source and clock leaves.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import secrets
import time

from .mvp_deployment import DeploymentError
from .mvp_calibration import CalibrationObservation
from .mvp_policy_channel import (
    PolicyChannel, PolicyContext, WindowsPolicyListener, PlayerReady, Revoke, OutcomeNotice,
)
from .mvp_process_topology import ParentProcessTopology, HandoffAck, ReleaseStageReceipt
from .mvp_session_authority import RetirementV3, canonical_v3, digest_v3


def _require(value, reason):
    if not value:
        raise DeploymentError('POLICY_' + reason)


@contextmanager
def policy_mutex():
    from .win32_runtime import NativeWin32Api, Win32Runtime
    mutex = Win32Runtime(NativeWin32Api()).acquire_mutex()
    try:
        _require(mutex.abandoned is False, 'MUTEX_ABANDONED')
        mutex.require_usable()
        yield mutex
    finally:
        mutex.release()


class WorkerLease:
    """Local metadata only; exact parent context is the immutable time authority.

    Marker/held never authorize release. The sole GuardedDeploymentInput does.
    A fresh instance cannot reopen the durable consumed admission.
    """
    def __init__(self, channel, *, clock=time.monotonic):
        self.channel = channel
        self.deadline = channel.context.run.deadline_ns / 1_000_000_000
        self.marker = secrets.randbelow(2**31 - 1) + 1
        self.clock = clock
        self.held = self.revoked = False

    def active(self):
        if self.revoked or self.channel.no_more_gameplay is not False:
            return False
        now = self.clock()
        # Neither the channel poll nor the clock may revoke past this latch.
        return type(now) in (int, float) and 0 <= now < self.deadline and not self.revoked

    def set_held(self, value):
        _require(type(value) is bool, 'LEASE_VALUE')
        if value:
            _require(self.active(), 'LEASE_REVOKED')
        self.held = value

    def revoke(self):
        self.revoked = True


def _message_until(channel, deadline, clock, wait):
    """Poll metadata without timing out/poisoning a healthy idle connection."""
    while clock() < deadline:
        if channel.pending():
            return channel.receive(timeout=min(5., max(.001, deadline - clock())))
        wait(.01)
    return None


def finish_parent_session(session, channel, *, mutex_factory=policy_mutex,
                          clock=time.monotonic, wait=time.sleep):
    """Wait without mutex; revoke/release/freeze precedes any worker containment.

    An absent/invalid receipt never authorizes a kill. Finalization is attempted
    solely from durable facts. Failures retain unresolved lifecycle truth; only
    fully proved normal or approved calibration retirement closes the generation.
    """
    notice = receipt = None
    lease = WorkerLease(channel, clock=clock)
    topology = session.topology
    try:
        first = _message_until(channel, session.run.deadline_ns / 1e9, clock, wait)
        if first is not None:
            _require(type(first) is (CalibrationObservation if channel.context.calibration is not None else OutcomeNotice), 'OUTCOME_MESSAGE')
            notice = first
            if type(notice) is CalibrationObservation and notice.status == 'CAPTURED':
                with mutex_factory() as mutex:
                    session._owned(mutex)
                    session.authority.record_calibration_observation_v3(session.run, notice,
                        writer_epoch=session.epoch)
                    _require(callable(getattr(session, 'capture_window', None)), 'CAPTURE_PIN_SOURCE')
                    window = session.capture_window(notice, topology.offer)
                    topology.pin_capture(window, session.expected_title)
        # Do not wait for the worker's mutex to revoke its action gate. A queued
        # authenticated control message immediately latches W no-more-gameplay.
        lease.revoke()
        token = topology.revoke('NO_MORE_GAMEPLAY')
        channel.send(Revoke(token))
        until = clock() + 5.
        while receipt is None and clock() < until:
            message = _message_until(channel, until, clock, wait)
            if type(message) in (OutcomeNotice, CalibrationObservation) and notice is None:
                notice = message
            elif type(message) is ReleaseStageReceipt:
                receipt = message
            else:
                raise DeploymentError('POLICY_RELEASE_STAGE_MISSING')
        _require(type(receipt) is ReleaseStageReceipt, 'RELEASE_STAGE_MISSING')
        with mutex_factory() as mutex:
            session.revoke(mutex=mutex, lease=lease)
            session.contain_worker(receipt, mutex=mutex)
        # Wait/readback are observations, never player retirement authority.
        host = topology._host
        _require(host.wait_process(topology.worker.process_handle, 5000) is True
                 and host.job_active_count(topology.worker_job) == 0, 'WORKER_ABSENCE')
        with mutex_factory() as mutex:
            result = _finalize_notice(session, mutex, notice)
            if session.canonical is not None:
                session.canonical.require_offer(topology.offer)
            identity = digest_v3(canonical_v3(asdict(topology.offer.worker)))
            topology_digest = digest_v3(canonical_v3(asdict(topology.offer)))
            proof = canonical_v3(dict(schema=3, binding_digest=session.run.digest,
                identity_digest=identity, topology_digest=topology_digest,
                absence_digest=digest_v3(canonical_v3({'process_signaled': True, 'job_active': 0})),
                disposition='ABSENT', observed_ns=int(clock() * 1e9)))
            session.authority.record_retirement_v3(session.run,
                RetirementV3('WORKER', identity, proof), writer_epoch=session.epoch)
            if (channel.context.calibration is not None and notice is not None
                    and notice.status == 'CAPTURED'):
                _require(receipt.stage == 'NOT_REQUIRED', 'INPUT_UNCERTAIN')
                session.retire_calibration(mutex=mutex, expected_title=session.expected_title)
            if (channel.context.calibration is None and result.outcome == 'SUCCESS'
                    and clock() * 1e9 < session.run.deadline_ns):
                session.retire_normal_success(mutex=mutex, expected_title=session.expected_title)
            return result
    except BaseException:
        # Missing facts/producer never authorize SUCCESS. Complete durable seals
        # may still be finalized by the administrative command after an IPC loss.
        try:
            lease.revoke()
            topology.revoke('PRODUCER_LOST')
            with mutex_factory() as mutex:
                session._retain_active()
                if session.authority.outcome_v3(session.run.generation) is None:
                    session.finalize(mutex=mutex, outcome='UNCERTAIN', reason='PRODUCER_LOST')
        except BaseException:
            pass  # Existing PENDING/ACTIVE remains the durable admission barrier.
        raise
    finally:
        try:
            channel.close()
        finally:
            with mutex_factory() as mutex:
                if topology.purpose == 'CALIBRATION_READ_ONLY':
                    session.calibration_issuer.revoke()
                session.dispose(mutex=mutex)


def _finalize_notice(session, mutex, notice):
    if session.topology.purpose == 'CALIBRATION_READ_ONLY':
        session.calibration_issuer.revoke()
        if notice is not None:
            _require(type(notice) is CalibrationObservation, 'CALIBRATION_NOTICE')
            _require(notice.offer_digest == digest_v3(canonical_v3(asdict(session.topology.offer))), 'CALIBRATION_OFFER')
            session.authority.record_calibration_observation_v3(session.run, notice, writer_epoch=session.epoch)
        session.finalize(mutex=mutex, outcome='BLOCKED' if notice is not None else 'UNCERTAIN',
                         reason='EVIDENCE_INCOMPLETE' if notice is not None else 'PRODUCER_LOST')
        return notice
    if notice is None:
        return session.finalize(mutex=mutex, outcome='UNCERTAIN', reason='PRODUCER_LOST')
    _require(type(notice) is OutcomeNotice, 'OUTCOME_MESSAGE')
    # EXPIRED requires additional live incompleteness proof; do not infer it from
    # a worker's scalar notice. Conservative uncertainty preserves no-replay.
    if notice.outcome == 'EXPIRED':
        return session.finalize(mutex=mutex, outcome='UNCERTAIN', reason='EVIDENCE_INCOMPLETE')
    return session.finalize(mutex=mutex, outcome=notice.outcome, reason=notice.reason)


def bootstrap_parent(prepared, *, state, host, listener, sources, worker_executable,
                     worker_command, player_executable, player_command, clock=time.monotonic,
                     canonical=None):
    """Called inside prepare_run's mutex. Returns before any IPC wait."""
    from .mvp_policy_ingress import PreparedRunV3
    from .mvp_deployment_worker import PolicyParentSession
    _require(type(prepared) is PreparedRunV3, 'PREPARED_RUN_REQUIRED')
    from .win32_lifecycle_host import Win32LifecycleHost
    if isinstance(host, Win32LifecycleHost):
        _require(clock is time.monotonic and canonical is not None, 'CANONICAL_CLOCK_REQUIRED')
    from .mvp_calibration import CalibrationIssuer, CalibrationScope, OPERATIONS
    scope = prepared.calibration_scope
    if scope is not None:
        _require(type(scope) is CalibrationScope and scope.slot == prepared.slot, 'CALIBRATION_SCOPE')
        scope.require_run(prepared.run)
    topology = ParentProcessTopology(host, run_nonce=prepared.run.run_nonce,
                                     generation=prepared.run.generation,
                                     purpose='CALIBRATION_READ_ONLY' if scope is not None else 'GAMEPLAY')
    _require(listener.parent == topology._parent, 'LISTENER_OWNER')
    session = PolicyParentSession(authority=prepared.authority, run=prepared.run,
        writer_epoch=prepared.writer_epoch, state=state, topology=topology,
        runner_manifest_bytes=prepared.runner_manifest_bytes,
        approval_current=sources.approval_current, clock=clock)
    session.calibration_scope = scope
    session.canonical = canonical
    session.stop_sources = sources
    session.expected_title = sources.expected_title
    capability = None
    if scope is not None:
        session.calibration_evidence = sources.retirement_evidence
        session.capture_window = sources.captured_window
        session.calibration_issuer = CalibrationIssuer(scope,
            current=lambda: sources.approval_current(prepared.run), clock_ns=lambda: int(clock() * 1e9))
    try:
        offer = session.prepare(mutex=prepared.mutex, stopped=prepared.stopped, slot=prepared.slot,
            worker_executable=worker_executable, worker_command=worker_command,
            player_executable=player_executable, player_command=player_command)
        if scope is not None:
            sources.register_worker(session)
            capability = session.calibration_issuer.issue()
            session.calibration_issuer.consume(capability, scope=scope, operations=OPERATIONS)
        context = PolicyContext(prepared.run, prepared.writer_epoch, offer, prepared.runner_manifest_bytes, capability)
        session.start_worker(mutex=prepared.mutex)
        return session, context
    except BaseException:
        session.dispose(mutex=prepared.mutex)
        raise


def accept_parent_worker(session, context, listener, *, mutex_factory=policy_mutex,
                         clock=time.monotonic, package_binding=None):
    channel = listener.accept(worker_handle=session.topology.worker.process_handle,
        context=context, timeout=min(5., max(.001, session.run.deadline_ns / 1e9 - clock())))
    try:
        ack = channel.receive(timeout=min(5., max(.001, session.run.deadline_ns / 1e9 - clock())))
        _require(type(ack) is HandoffAck, 'HANDOFF_ACK_REQUIRED')
        with mutex_factory() as mutex:
            session.accept_worker(ack, mutex=mutex)
        # W cannot capture/bind until the actual resume has returned successfully.
        fact = None
        if package_binding is not None:
            # Historical callers cannot reactivate guest transport with a binding.
            raise DeploymentError('STRICT_GUEST_BUILD_PROVIDER_DEFERRED_BY_OWNER')
        channel.send(PlayerReady(context.offer, fact))
        return channel
    except BaseException:
        channel.close()
        raise


def execute_worker_session(channel, *, authority, host, sources, mutex_factory=policy_mutex,
                           clock=time.monotonic, wait=time.sleep):
    """Real child consumer; sources are sealed file/OS providers, not result flags.

    sources verifies immutable profile/config/approval and constructs the existing
    PolicyPlayerView/build_policy_executor; no replacement visit/engine is accepted
    as a wire message. There is no native source fallback on any failure.
    """
    stored_scope = authority.calibration_scope_v3(channel.context.run)
    _require(stored_scope == (None if channel.context.calibration is None else channel.context.calibration.scope),
             'CALIBRATION_PURPOSE_MISMATCH')
    if channel.context.calibration is not None:
        from .mvp_calibration import execute_calibration_worker, CalibrationProcessQueries
        queries = CalibrationProcessQueries.from_host(host)
        return execute_calibration_worker(channel, authority=authority, host=queries, sources=sources,
            mutex_factory=mutex_factory, clock=clock, wait=wait)
    from .mvp_deployment_worker import consume_policy_handoff
    from .mvp_deployment_integration import build_policy_executor
    from .mvp_local_native import PolicyPlayerView
    context = channel.context
    lease = WorkerLease(channel, clock=clock)
    view = executor = None
    revoked_message = None
    notice = OutcomeNotice('UNCERTAIN', 'PRODUCER_LOST')
    try:
        with mutex_factory() as mutex:
            sources.require_context(context)
            ack = consume_policy_handoff(authority=authority, run=context.run,
                producer_epoch=context.producer_epoch, offer=context.offer, host=host, mutex=mutex,
                runner_manifest_bytes=context.runner_manifest_bytes, clock=clock)
        channel.send(ack)
        message = channel.receive(timeout=5)
        if type(message) is Revoke:
            revoked_message = message
        _require(type(message) is PlayerReady and message.offer == context.offer, 'PLAYER_NOT_READY')
        with mutex_factory() as mutex:
            _require(lease.active(), 'WORKER_REVOKED')
            sources.require_context(context)
            view = PolicyPlayerView(host=host, offer=context.offer, run=context.run,
                enabled=lambda: lease.active() and sources.approval_current(context.run), clock=clock)
            # No startup input fallback: only an already positively identified
            # Home profile can reach the preserved S004 attack spine.
            view.bind(sources.expected_title)
            executor = build_policy_executor(player_view=view, authority=authority,
                epoch=context.producer_epoch, profile=sources.profile, lease=lease,
                provider=sources.provider, build_binding=sources.build_binding(context),
                version_code=sources.version_code, approval_current=sources.approval_current,
                instance_current=sources.instance_current, home_verified=sources.home_verified)
            complete, _ = executor.run()
            notice = OutcomeNotice('SUCCESS', 'PRE_D_COMPLETE') if complete else OutcomeNotice(
                'UNCERTAIN', 'EMERGENCY_RELEASE' if executor.sink.emergency_release_required else 'INPUT_UNCERTAIN')
    except BaseException:
        # Never replay or reconstruct a missed physical event on the error path.
        lease.revoke()
    finally:
        lease.revoke()
        released = False
        try:
            if executor is not None:
                released = executor.release() is True
                if not released or executor.sink.uncertain:
                    notice = OutcomeNotice('UNCERTAIN', 'INPUT_UNCERTAIN')
                if executor.sink.emergency_release_required:
                    notice = OutcomeNotice('UNCERTAIN', 'EMERGENCY_RELEASE')
        except BaseException:
            notice = OutcomeNotice('UNCERTAIN', 'INPUT_UNCERTAIN')
        try:
            # The protocol will reject a notice before acknowledgment/ready. This
            # is deliberately not an alternate bootstrap completion path.
            channel.send(notice)
            revoke = revoked_message if revoked_message is not None else channel.receive(timeout=5)
            _require(type(revoke) is Revoke, 'REVOKE_REQUIRED')
            # Synchronous provider reads have ended and torn down owned children.
            # P still checks exact live Jw membership before containment.
            stage = ('NOT_REQUIRED' if released and not executor.sink.uncertain else
                     'ATTEMPTED' if executor is not None and executor.sink.emergency_release_required
                     else 'NO_AUTHORIZED_RELEASE')
            channel.send(ReleaseStageReceipt(context.offer, revoke.token, stage,
                                             (context.offer.worker,), True))
            # No spawn/capture/input after the freeze receipt. Only peer metadata.
            until = clock() + 5.
            while not channel._closed and clock() < until:
                channel.no_more_gameplay
                wait(.01)
        finally:
            try:
                if view is not None:
                    view.close()
                else:
                    try: host.close_handle(context.offer.remote_player_handle)
                    finally: host.close_handle(context.offer.remote_player_job_query)
            finally:
                channel.close()
    return notice


def run_policy_command(project_root, *, purpose='RUN_V3'):
    """No synthetic flag, legacy fallback or runtime admission from this command."""
    from pathlib import Path
    import subprocess
    import sys
    from .mvp_policy_ingress import PolicyIngress
    from .mvp_session_authority import DurableSessionAuthority
    from .mvp_installed_build import PLAYER
    from .lifecycle_state import LifecycleStateStore
    from .win32_state_io import Win32StateFilePort, NativeWin32StateApi
    from .win32_lifecycle_host import Win32LifecycleHost, NativeLifecycleApi
    project = Path(project_root).resolve(strict=True)
    from .mvp_canonical_runtime import open_canonical
    canonical = open_canonical(project)
    _require(purpose in ('RUN_V3','CALIBRATION_READ_ONLY'), 'RUN_PURPOSE')
    if purpose == 'CALIBRATION_READ_ONLY':
        from .mvp_calibration_native import CalibrationConfiguration
        # Missing/invalid private measurements deny before any desktop/lifecycle
        # constructor. This read-only validation creates no approval or profile.
        CalibrationConfiguration(project)
    session = listener = channel = authority = None
    disposed = False
    try:
        with PolicyIngress(project).prepare_run(purpose=purpose) as prepared:
            if prepared.calibration_scope is not None:
                _require(purpose == 'CALIBRATION_READ_ONLY', 'CALIBRATION_PURPOSE_MISMATCH')
                sources = CalibrationConfiguration(project,prepared.run,prepared.calibration_scope)
            else:
                prepared.require_runtime_activation()  # gameplay remains denied
                from .mvp_policy_sources import PolicyWorkerSources
                sources = PolicyWorkerSources(project, prepared.run)
            _require(sources.data['slot'] == prepared.slot, 'SELECTED_SLOT')
            host = Win32LifecycleHost(NativeLifecycleApi(), nonce_factory=lambda: secrets.token_hex(16))
            state = LifecycleStateStore(project, Win32StateFilePort(project, NativeWin32StateApi()))
            listener = WindowsPolicyListener()
            command = subprocess.list2cmdline([sys.executable, '-m',
                'clash_rush_rebuild.mvp_policy_run', '--project-root', str(project),
                '--pipe', listener.name, '--parent-pid', str(listener.parent.pid),
                '--parent-created', str(listener.parent.creation_time_100ns),
                '--ownership-nonce', prepared.run.run_nonce] +
                (['--calibration-read-only'] if prepared.calibration_scope is not None else []))
            player_command = subprocess.list2cmdline([PLAYER, '--instance', sources.slot.internal_name])
            session, context = bootstrap_parent(prepared, state=state, host=host, listener=listener, sources=sources,
                worker_executable=sys.executable, worker_command=command,
                player_executable=PLAYER, player_command=player_command, canonical=canonical)
        # prepare_run deliberately releases/closes before any W IPC wait. Reopen
        # the SAME store, never a second authority or copied snapshot.
        authority = DurableSessionAuthority(project / 'var/mvp-session.sqlite3')
        session.authority = authority
        channel = accept_parent_worker(session, context, listener)
        try:
            return finish_parent_session(session, channel)
        finally:
            disposed = True  # finish owns non-killing disposal on every exit
    finally:
        try:
            if session is not None and not disposed:
                # Reopen if the prepare context already closed the connection.
                if authority is None:
                    authority = DurableSessionAuthority(project / 'var/mvp-session.sqlite3')
                    session.authority = authority
                with policy_mutex() as mutex:
                    session.dispose(mutex=mutex)
        finally:
            try:
                if channel is not None: channel.close()
                if listener is not None: listener.close()
            finally:
                if authority is not None: authority.close()


def worker_main(argv=None):
    """Hidden module entry: no legacy mouse lease, child-created grant or stdout."""
    import argparse
    from pathlib import Path
    from .lifecycle import ProcessIdentity
    from .mvp_policy_ingress import require_runtime_activation
    from .mvp_session_authority import DurableSessionAuthority
    from .win32_lifecycle_host import Win32LifecycleHost, NativeLifecycleApi
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--calibration-read-only',action='store_true')
    parser.add_argument('--ownership-nonce')
    for name in ('project-root', 'pipe'):
        parser.add_argument('--' + name, required=True)
    for name in ('parent-pid', 'parent-created'):
        parser.add_argument('--' + name, required=True, type=int)
    channel = authority = None
    try:
        args = parser.parse_args(argv)
        if not args.calibration_read_only:
            require_runtime_activation()
        channel = PolicyChannel.connect(args.pipe,
            parent=ProcessIdentity(args.parent_pid, args.parent_created))
        _require(args.calibration_read_only == (channel.context.calibration is not None),
                 'CALIBRATION_PURPOSE_MISMATCH')
        if channel.context.calibration is not None:
            from .mvp_calibration_native import CalibrationConfiguration
            _require(args.ownership_nonce == channel.context.run.run_nonce, 'WORKER_OWNERSHIP_NONCE')
            configuration = CalibrationConfiguration(args.project_root,channel.context.run,
                channel.context.calibration.scope)
        else:
            from .mvp_policy_sources import PolicyWorkerSources
            sources = PolicyWorkerSources(args.project_root, channel.context.run)
        authority = DurableSessionAuthority(Path(args.project_root) / 'var/mvp-session.sqlite3')
        host = Win32LifecycleHost(NativeLifecycleApi(), nonce_factory=lambda: secrets.token_hex(16))
        if channel.context.calibration is not None:
            sources = configuration.worker_sources(channel,host)
        execute_worker_session(channel, authority=authority, host=host, sources=sources)
        return 0
    except BaseException:
        return 2
    finally:
        try:
            if authority is not None: authority.close()
        finally:
            if channel is not None: channel.close()


if __name__ == '__main__':
    raise SystemExit(worker_main())
