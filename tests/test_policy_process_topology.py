"""Policy controller proofs: inert leaves and harmless Win32 actors only."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from clash_rush_rebuild.lifecycle import CreatedProcess, MemberSnapshot, ProcessIdentity


class InertHost:
    """Native leaf double: deliberately models legacy dangerous default flags."""
    def __init__(self):
        self.calls = []
        self.jobs = {}
        self.flags = {}
        self.identities = {99: ProcessIdentity(99, 999)}
        self.next_pid = 10
        self.fail = None

    def create_job(self):
        job = 'job' + str(len(self.jobs))
        self.jobs[job] = []
        self.flags[job] = 0x2000
        return job

    def set_non_killing_job(self, job):
        self.calls.append(('flags', job, 0))
        self.flags[job] = 0
        self.job_limit_flags(job)

    def job_limit_flags(self, job):
        return self.flags[job]

    def create_policy_suspended(self, executable, command):
        self.calls.append(('create', executable, command))
        pid = self.next_pid
        self.next_pid += 1
        self.identities[pid] = ProcessIdentity(pid, pid * 100)
        return CreatedProcess(pid, pid + 100, pid)

    def identity_from_handle(self, handle, expected_pid):
        identity = self.identities[handle]
        assert identity.pid == expected_pid
        if self.fail == 'identity':
            return replace(identity, creation_time_100ns=1)
        return identity

    def current_process_handle(self):
        return 99

    def current_process_id(self):
        return 99

    def assign_to_job(self, job, process):
        self.calls.append(('assign', job, process))
        if self.fail == 'assignment' and process == 11:
            raise RuntimeError('inert assignment failure')
        self.jobs[job].append(process)

    def is_process_in_job(self, process, job):
        return process in self.jobs[job]

    def stable_job_members(self, job):
        return MemberSnapshot(tuple(self.identities[p] for p in self.jobs[job]), lambda: None)

    def duplicate_query_wait_to_worker(self, player, worker):
        self.calls.append(('duplicate', player, worker, 0x00101000, False, 0))
        if self.fail == 'duplicate':
            raise RuntimeError('inert duplicate failure')
        return 700

    def duplicate_query_job_to_worker(self,job,worker):
        self.calls.append(('duplicate_job',job,worker,0x0004,False,0))
        if self.fail=='duplicate_job':raise RuntimeError('inert job duplicate failure')
        return 701

    def resume_thread(self, thread):
        self.calls.append(('resume', thread))
        return 1

    def wait_process(self, handle, timeout):
        return False

    def terminate_job(self, job):
        self.calls.append(('kill', job))

    def close_handle(self, handle):
        self.calls.append(('close', handle))


def controller(host):
    from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
    return ParentProcessTopology(host, run_nonce='a' * 32, generation=1)


def prepare(topology):
    return topology.prepare('C:/test/worker.exe', 'worker', 'C:/test/player.exe', 'player')


def ack_for(offer):
    from clash_rush_rebuild.mvp_process_topology import HandoffAck
    return HandoffAck(offer, accepted=True, noninheritable=True)


def release_for(topology, offer, host):
    from clash_rush_rebuild.mvp_process_topology import ReleaseStageReceipt
    return ReleaseStageReceipt(offer, topology.revoke('test containment'), 'NOT_REQUIRED',
                               tuple(host.identities[p] for p in host.jobs[topology.worker_job]), True)


def test_prepared_jobs_non_killing_and_player_waits_for_exact_ack():
    host = InertHost()
    topology = controller(host)
    offer = prepare(topology)
    assert host.flags == {'job0': 0, 'job1': 0}
    assert not [c for c in host.calls if c[0] == 'resume']
    assert ('duplicate', 11, 10, 0x00101000, False, 0) in host.calls
    topology.start_worker()
    assert [c for c in host.calls if c[0] == 'resume'] == [('resume', 110)]
    topology.accept_worker_ack(ack_for(offer))
    assert [c for c in host.calls if c[0] == 'resume'] == [('resume', 110), ('resume', 111)]


@pytest.mark.parametrize('failure', ['assignment', 'duplicate'])
def test_bootstrap_failure_never_resumes_or_kills(failure):
    host = InertHost()
    host.fail = failure
    topology = controller(host)
    with pytest.raises(RuntimeError):
        prepare(topology)
    topology.dispose()
    assert topology.blocked
    assert not [c for c in host.calls if c[0] in ('resume', 'kill')]


@pytest.mark.parametrize('fault', ['missing', 'stale', 'identity', 'membership', 'flags'])
def test_bad_ack_or_changed_topology_latches_block_without_kill(fault):
    host = InertHost()
    topology = controller(host)
    offer = prepare(topology)
    topology.start_worker()
    ack = ack_for(offer)
    if fault == 'missing':
        ack = None
    elif fault == 'stale':
        ack = replace(ack, offer=replace(offer, generation=2))
    elif fault == 'identity':
        host.fail = 'identity'
    elif fault == 'membership':
        host.jobs[topology.worker_job].append(11)
    else:
        host.flags[topology.player_job] = 0x2000
    with pytest.raises(RuntimeError):
        topology.accept_worker_ack(ack)
    assert topology.blocked
    assert ('resume', 111) not in host.calls
    assert not [c for c in host.calls if c[0] == 'kill']
    with pytest.raises(RuntimeError):
        topology.accept_worker_ack(ack_for(offer))


@pytest.mark.parametrize('fault', ['missing', 'stale', 'unfrozen', 'members', 'stage'])
def test_worker_kill_requires_bound_release_and_exact_frozen_members(fault):
    host = InertHost()
    topology = controller(host)
    offer = prepare(topology)
    topology.start_worker()
    topology.accept_worker_ack(ack_for(offer))
    receipt = release_for(topology, offer, host)
    if fault == 'missing':
        receipt = None
    elif fault == 'stale':
        receipt = replace(receipt, revoke_token='b' * 32)
    elif fault == 'unfrozen':
        receipt = replace(receipt, spawning_stopped=False)
    elif fault == 'members':
        receipt = replace(receipt, worker_members=())
    else:
        receipt = replace(receipt, stage='PENDING')
    with pytest.raises(RuntimeError):
        topology.terminate_worker(receipt)
    assert not [c for c in host.calls if c[0] == 'kill']


def test_only_worker_job_terminates_then_disposal_never_kills_player():
    host = InertHost()
    topology = controller(host)
    offer = prepare(topology)
    topology.start_worker()
    topology.accept_worker_ack(ack_for(offer))
    assert topology.terminate_worker(release_for(topology, offer, host))
    topology.dispose()
    assert [c for c in host.calls if c[0] == 'kill'] == [('kill', 'job0')]
    assert not hasattr(topology, 'terminate_player')
    assert topology.blocked


def test_unsafe_job_close_is_refused_and_safe_handles_still_disposed():
    host = InertHost()
    topology = controller(host)
    prepare(topology)
    host.flags[topology.player_job] = 0x2000
    with pytest.raises(RuntimeError):
        topology.dispose()
    assert ('close', 'job1') not in host.calls
    assert ('close', 11) in host.calls
    assert ('close', 'job0') in host.calls


def test_legacy_runtime_cannot_supply_non_killing_policy():
    """Behavioral RED before production edits, not a missing-import oracle."""
    from clash_rush_rebuild.win32_runtime import Win32Runtime
    from test_win32_runtime import FakeRuntimeApi
    api = FakeRuntimeApi()
    api.get_job_limit_flags = lambda job: 0
    runtime = Win32Runtime(api)
    # Baseline legacy only exposes killing configuration. The explicit policy
    # method must replace that choice, not change the generic legacy default.
    configure = getattr(runtime, 'set_non_killing_job', runtime.set_kill_on_close)
    configure('job')
    assert ('job:configure', 'job', 0) in api.calls


def handoff_from_dict(values):
    from clash_rush_rebuild.mvp_process_topology import PlayerHandoff
    values = dict(values)
    for role in ('parent', 'worker', 'player'):
        values[role] = ProcessIdentity(**values[role])
    return PlayerHandoff(**values)


def test_native_duplicate_leaf_uses_exact_rights_target_and_options():
    from types import SimpleNamespace
    from clash_rush_rebuild.win32_runtime import NativeWin32Api, Win32Runtime
    calls = []

    def duplicate(source_owner, source, target_owner, output, access, inherit, options):
        calls.append((source_owner, source, target_owner, access, inherit, options))
        output._obj.value = 701
        return 1

    api = NativeWin32Api.__new__(NativeWin32Api)
    api._kernel32 = SimpleNamespace(GetCurrentProcess=lambda: 99, DuplicateHandle=duplicate)
    assert Win32Runtime(api).duplicate_query_wait_to_worker(11, 10) == 701
    assert calls == [(99, 11, 10, 0x00101000, False, 0)]
    assert Win32Runtime(api).duplicate_query_job_to_worker(12,10)==701
    assert calls[-1]==(99,12,10,0x0004,False,0)


@pytest.mark.parametrize('flags', [0x2000, 1, False, None, -1])
def test_non_killing_configuration_requires_exact_zero_readback(flags):
    from clash_rush_rebuild.win32_runtime import Win32Runtime
    from test_win32_runtime import FakeRuntimeApi
    api = FakeRuntimeApi()
    api.get_job_limit_flags = lambda job: flags
    with pytest.raises(RuntimeError):
        Win32Runtime(api).set_non_killing_job('job')
    assert ('job:configure', 'job', 0) in api.calls
    assert not [c for c in api.calls if c[0] in ('job:terminate', 'process:terminate')]


def test_non_killing_configuration_cannot_migrate_live_job():
    from clash_rush_rebuild.win32_runtime import Win32Runtime
    from test_win32_runtime import FakeRuntimeApi
    api = FakeRuntimeApi()
    api.active_count_result = 1
    with pytest.raises(RuntimeError):
        Win32Runtime(api).set_non_killing_job('job')
    assert ('job:configure', 'job', 0) not in api.calls


def test_dead_worker_receipt_is_not_permission_to_kill_or_claim_retirement():
    host = InertHost()
    topology = controller(host)
    offer = prepare(topology)
    topology.start_worker()
    topology.accept_worker_ack(ack_for(offer))
    receipt = release_for(topology, offer, host)
    host.wait_process = lambda handle, timeout: handle == topology.worker.process_handle
    assert topology.terminate_worker(receipt) is False
    assert topology.blocked
    assert not [c for c in host.calls if c[0] == 'kill']


@pytest.mark.parametrize('fault', [None, 'worker', 'player', 'inheritance', 'exited'])
def test_worker_receiver_validates_actual_local_handle_without_lifecycle_authority(fault):
    from clash_rush_rebuild.mvp_process_topology import acknowledge_player_handoff
    host = InertHost()
    offer = prepare(controller(host))
    host.calls.clear()
    host.current_process_handle = lambda: 99 if fault == 'worker' else offer.worker.pid
    host.current_process_id = host.current_process_handle
    host.identities[offer.remote_player_handle] = (
        replace(offer.player, creation_time_100ns=1) if fault == 'player' else offer.player)
    host.handle_is_inheritable = lambda handle: fault == 'inheritance'
    host.wait_process = lambda handle, timeout: fault == 'exited'
    host.flags[offer.remote_player_job_query]=0
    host.jobs[offer.remote_player_job_query]=[offer.remote_player_handle]
    if fault is None:
        assert acknowledge_player_handoff(host, offer) == ack_for(offer)
    else:
        with pytest.raises(RuntimeError):
            acknowledge_player_handoff(host, offer)
    assert host.calls == []


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows process/Job proof')
@pytest.mark.parametrize('purpose', ['GAMEPLAY', 'CALIBRATION_READ_ONLY'])
@pytest.mark.parametrize('case', ['NOT_REQUIRED', 'ATTEMPTED', 'NO_AUTHORIZED_RELEASE',
                                 'dispose', 'missing_ack', 'worker_crash', 'session'])
def test_real_parent_topology_player_survives_worker_and_handle_retirement(tmp_path, case, purpose):
    import ctypes
    from ctypes import wintypes as w
    from dataclasses import asdict
    import json
    import uuid
    from test_policy_nested_job import cleanup, kernel_api
    from clash_rush_rebuild.mvp_process_topology import HandoffAck, ReleaseStageReceipt
    from clash_rush_rebuild.win32_lifecycle_host import NativeProcessApi, Win32LifecycleHost
    from clash_rush_rebuild.win32_runtime import Win32Runtime

    api = NativeProcessApi()
    host = Win32LifecycleHost(api, nonce_factory=lambda: '0' * 32)
    runtime = Win32Runtime(api)
    kernel = kernel_api()
    kernel.SetEvent.argtypes, kernel.SetEvent.restype = [w.HANDLE], w.BOOL
    names = ['Local\\ClashRush-topology-' + uuid.uuid4().hex for _ in range(4)]
    events = [kernel.CreateEventW(None, True, False, name) for name in names]
    assert all(events)
    from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
    topology = ParentProcessTopology(host, run_nonce='a'*32, generation=1, purpose=purpose)
    actors = []
    session=None
    if case=='session':
        import time
        from test_policy_parent_session import compose
        session,_,state,_,_=compose(tmp_path)
        now=time.monotonic_ns()
        session.run=replace(session.run,started_ns=now,deadline_ns=now+30_000_000_000)
        session.clock=time.monotonic
        if purpose == 'CALIBRATION_READ_ONLY':
            from test_calibration_mode import scope_for
            scope = scope_for(session.run)
            session.run = replace(session.run, binding_digest=scope.digest, provider_digest=scope.digest)
            session.calibration_scope = scope
        session.topology=topology=type(topology)(host,run_nonce=session.run.run_nonce,generation=1,purpose=purpose)
        mutex_name='Local\\ClashRush-policy-session-'+uuid.uuid4().hex
        def owned(operation,*args,**kwargs):
            lease=runtime.acquire_mutex(name=mutex_name)
            try:return operation(*args,mutex=lease,**kwargs)
            finally:lease.release()
    descendant = None
    player_descendant = None
    disposed = False
    try:
        paths = [tmp_path / name for name in ('offer.json', 'ack.json', 'revoke.json', 'release.json')]
        peer = Path(__file__).parent / 'support' / 'policy_topology_peer.py'
        executable = sys._base_executable
        player_command = [executable, '-c', 'import time; time.sleep(40)']
        if case in ('NOT_REQUIRED', 'ATTEMPTED', 'NO_AUTHORIZED_RELEASE', 'session'):
            player_command = [executable, str(peer.with_name('policy_process_peer.py')),
                              str(tmp_path / 'player-child.json'), names[3]]
        worker_command=subprocess.list2cmdline([executable,str(peer),*(str(p) for p in paths),*names[:3]])
        if session is None:
            offer=topology.prepare(executable,worker_command,executable,subprocess.list2cmdline(player_command))
        else:
            from test_policy_finalization_v3 import stopped
            offer=owned(session.prepare,stopped=stopped(),slot=0,worker_executable=executable,
                worker_command=worker_command,player_executable=executable,
                player_command=subprocess.list2cmdline(player_command))
        for actor, identity in ((topology.worker, offer.worker), (topology.player, offer.player)):
            # Additional handles belong solely to the test controller. They
            # grant cleanup of disposable actors, not production player kill.
            backup = CreatedProcess(runtime.duplicate_process_handle(actor.process_handle),
                                    runtime.duplicate_process_handle(actor.thread_handle), actor.pid)
            actors.append((backup, (identity.pid, identity.creation_time_100ns)))
        assert host.job_limit_flags(topology.worker_job) == (0x2000 if purpose == 'CALIBRATION_READ_ONLY' else 0)
        assert host.job_limit_flags(topology.player_job) == 0
        assert not host.is_process_in_job(host.current_process_handle(), topology.worker_job)
        assert not host.is_process_in_job(topology.player.process_handle, topology.worker_job)
        for handle in (topology.worker_job, topology.player_job, topology.worker.process_handle,
                       topology.worker.thread_handle, topology.player.process_handle, topology.player.thread_handle):
            assert host.handle_is_inheritable(handle) is False
        request={'offer':asdict(offer)}
        if session is not None:
            request['session']={'database':str(tmp_path/'session.sqlite3'),'mutex':mutex_name,
                               'manifest':session.runner_manifest_bytes.decode('ascii')}
        paths[0].write_text(json.dumps(request), encoding='ascii')
        if session is None:topology.start_worker()
        else:owned(session.start_worker)
        assert runtime.wait_process(events[0], 10000), 'disposable worker did not acknowledge'
        response = json.loads(paths[1].read_text(encoding='ascii'))
        assert response['terminate_denied'] is True
        assert response['job_mutation_denied'] is True
        value = response['ack']
        ack = HandoffAck(handoff_from_dict(value['offer']), value['accepted'], value['noninheritable'])
        if case == 'missing_ack':
            with pytest.raises(RuntimeError):
                topology.accept_worker_ack(None)
            with pytest.raises(RuntimeError):
                topology.terminate_worker(None)
            # Player still has exactly its initial suspension. This readback
            # increment is undone without running the disposable player.
            kernel.SuspendThread.argtypes, kernel.SuspendThread.restype = [w.HANDLE], w.DWORD
            assert kernel.SuspendThread(topology.player.thread_handle) == 1
            assert runtime.resume_thread(topology.player.thread_handle) == 2
        else:
            if session is None:topology.accept_worker_ack(ack)
            else:owned(session.accept_worker,ack)
        worker_backup, player_backup = actors[0][0], actors[1][0]
        if case in ('dispose', 'missing_ack'):
            topology.dispose()
            disposed = True
            assert runtime.wait_process(worker_backup.process_handle, 5000 if purpose == 'CALIBRATION_READ_ONLY' else 0) is (purpose == 'CALIBRATION_READ_ONLY')
        elif case == 'worker_crash':
            # Test-controller fault injection, not a production no-ack kill.
            runtime.terminate_process(worker_backup.process_handle)
            assert runtime.wait_process(worker_backup.process_handle, 5000)
            topology.dispose()
            disposed = True
        else:
            if session is None:token=topology.revoke('disposable release protocol')
            else:
                from types import SimpleNamespace
                token=owned(session.revoke,lease=SimpleNamespace(revoke=lambda:None))
            assert runtime.wait_process(events[3], 10000)
            player_child = json.loads((tmp_path / 'player-child.json').read_text(encoding='ascii'))
            player_child_identity = ProcessIdentity(player_child['pid'], player_child['creation'])
            player_descendant = kernel.OpenProcess(0x00101001, False, player_child['pid'])
            assert player_descendant
            assert host.identity_from_handle(player_descendant, player_child['pid']) == player_child_identity
            assert host.is_process_in_job(player_descendant, topology.player_job)
            assert not host.is_process_in_job(player_descendant, topology.worker_job)
            paths[2].write_text(json.dumps({'revoke_token': token,
                'stage': 'NOT_REQUIRED' if session else case, 'descendant': True}), encoding='ascii')
            assert kernel.SetEvent(events[1])
            assert runtime.wait_process(events[2], 10000)
            value = json.loads(paths[3].read_text(encoding='ascii'))
            receipt = ReleaseStageReceipt(handoff_from_dict(value['offer']), value['revoke_token'],
                        value['stage'], tuple(ProcessIdentity(**i) for i in value['worker_members']),
                        value['spawning_stopped'])
            child = next(i for i in receipt.worker_members if i != offer.worker)
            descendant = kernel.OpenProcess(0x00101001, False, child.pid)
            assert descendant
            assert host.identity_from_handle(descendant, child.pid) == child
            assert host.is_process_in_job(descendant, topology.worker_job)
            assert (topology.terminate_worker(receipt) if session is None else owned(session.contain_worker,receipt))
            assert runtime.wait_process(worker_backup.process_handle, 5000)
            assert runtime.wait_process(descendant, 5000)
            assert host.job_active_count(topology.worker_job) == 0
            if session is None:topology.dispose()
            else:
                from clash_rush_rebuild.lifecycle_state import Active,BlockReason
                outcome=owned(session.finalize,outcome='UNCERTAIN',reason='PRODUCER_LOST')
                assert outcome.outcome=='UNCERTAIN'
                assert session.authority.closure_v3(1).closed==0
                assert state.state==Active(0,session.run.run_nonce,BlockReason.STOP_PROOF)
                owned(session.dispose)
                session.authority.close()
            disposed = True
        # Last Je handle really closed: no backup Job exists in this test.
        assert not runtime.wait_process(player_backup.process_handle, 0)
        assert host.identity_from_handle(player_backup.process_handle, offer.player.pid) == offer.player
        if player_descendant is not None:
            assert not runtime.wait_process(player_descendant, 0)
            assert host.identity_from_handle(player_descendant, player_child['pid']) == player_child_identity
        assert topology.blocked
        print(f'{case}: exact player survived; duplicate termination denied; private handles noninheritable')
    finally:
        if not disposed and topology.worker_job is not None:
            # ALL members are disposable test actors, never a game/ambient Job.
            runtime.terminate_job(topology.worker_job)
        if not disposed and topology.player_job is not None:
            runtime.terminate_job(topology.player_job)  # test-owned E/child cleanup only
        if descendant is not None:
            if not runtime.wait_process(descendant, 0):
                runtime.terminate_process(descendant)
            assert runtime.wait_process(descendant, 5000)
            runtime.close_handle(descendant)
        if player_descendant is not None:
            if not runtime.wait_process(player_descendant, 0):
                runtime.terminate_process(player_descendant)
            assert runtime.wait_process(player_descendant, 5000)
            runtime.close_handle(player_descendant)
        cleanup(runtime, actors, [], events)
        topology.dispose()


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows parent-crash proof')
@pytest.mark.parametrize('purpose', ['GAMEPLAY', 'CALIBRATION_READ_ONLY'])
def test_real_parent_crash_closes_jobs_without_killing_worker_or_player(tmp_path, purpose):
    import json
    import uuid
    from test_policy_nested_job import cleanup, kernel_api, spawn
    from clash_rush_rebuild.win32_lifecycle_host import NativeProcessApi, Win32LifecycleHost
    from clash_rush_rebuild.win32_runtime import Win32Runtime
    api = NativeProcessApi()
    runtime = Win32Runtime(api)
    host = Win32LifecycleHost(api, nonce_factory=lambda: '0' * 32)
    kernel = kernel_api()
    name = 'Local\\ClashRush-parent-crash-' + uuid.uuid4().hex
    event = kernel.CreateEventW(None, True, False, name)
    assert event
    outer = runtime.create_job()
    runtime.set_non_killing_job(outer)
    actors, handles = [], [event]
    try:
        manifest = tmp_path / 'parent-manifest.json'
        peer = Path(__file__).parent / 'support' / 'policy_topology_parent_peer.py'
        parent, parent_identity = spawn(runtime, [str(peer), str(tmp_path), str(manifest), name, purpose])
        actors.append((parent, parent_identity))
        runtime.assign_to_job(outer, parent.process_handle)
        assert runtime.resume_thread(parent.thread_handle) == 1
        assert runtime.wait_process(event, 15000), 'disposable P did not publish'
        record = json.loads(manifest.read_text(encoding='ascii'))
        assert record['ambient'] is True  # explicit outer test Job, not assumed launcher state
        assert record['worker_flags'] == (0x2000 if purpose == 'CALIBRATION_READ_ONLY' else 0)
        assert record['player_flags'] == runtime.job_limit_flags(outer) == 0
        offer = handoff_from_dict(record['offer'])
        for identity in (offer.worker, offer.player):
            handle = kernel.OpenProcess(0x00101001, False, identity.pid)
            assert handle
            handles.append(handle)
            assert host.identity_from_handle(handle, identity.pid) == identity
            assert runtime.is_process_in_job(handle, outer)
        runtime.terminate_process(parent.process_handle)  # disposable crash injection only
        assert runtime.wait_process(parent.process_handle, 5000)
        for handle, identity in zip(handles[1:], (offer.worker, offer.player)):
            killed = purpose == 'CALIBRATION_READ_ONLY' and identity == offer.worker
            assert runtime.wait_process(handle, 5000 if killed else 0) is killed
            assert host.identity_from_handle(handle, identity.pid) == identity
        assert runtime.job_limit_flags(outer) == 0
        print('P crash: exact mode-specific worker containment; player survived')
    finally:
        runtime.terminate_job(outer)  # all members descend from our disposable P
        for handle in handles[1:]:
            assert runtime.wait_process(handle, 5000)
        cleanup(runtime, actors, [outer], handles)
