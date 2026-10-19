"""Normal retirement: exact retained Je, never a failure-cleanup fallback."""
from dataclasses import replace

import pytest

from test_policy_process_topology import InertHost, controller, prepare, ack_for, release_for
from clash_rush_rebuild.lifecycle import PlayerBinding, PlayerSnapshot


@pytest.fixture(autouse=True)
def trusted_test_time(monkeypatch):
    from clash_rush_rebuild import mvp_frozen_stop as stop
    monkeypatch.setattr(stop, '_monotonic_ns', lambda: 1_000_000_000)
    monkeypatch.setattr(stop, '_wall_ns', lambda: 1_000_000_000)


def stop_facts():
    from clash_rush_rebuild.mvp_frozen_stop import FrozenStopFacts
    return FrozenStopFacts('a'*64, 0, 5_000_000_000, 5_000_000_000)


class RetirementHost(InertHost):
    def __init__(self):
        super().__init__()
        self.exited = set()
        self.residue = False
        self.windows = True

    def wait_process(self, handle, timeout): return handle in self.exited
    def job_active_count(self, job): return len(self.jobs[job])
    def terminate_job(self, job):
        super().terminate_job(job)
        self.exited.update(self.jobs[job])
        self.jobs[job].clear()
        if job == 'job1' and not self.residue: self.windows = False
    def bind_exact(self, identity, title, members):
        assert identity in members.identities
        return PlayerBinding(identity, identity, 80, 81, 1280, 720, 'd'*32)
    def _require_capture_binding(self, binding, job):
        assert binding.identity == self.identities[11]
        assert 11 in self.jobs[job]
    def is_window(self, hwnd): return self.windows
    def complete_player_snapshot(self):
        return PlayerSnapshot(tuple(self.identities[p] for p in self.jobs['job1']), lambda:None)


def ready():
    host = RetirementHost(); topology = controller(host)
    offer = prepare(topology)
    topology.start_worker(); topology.accept_worker_ack(ack_for(offer))
    topology.terminate_worker(release_for(topology, offer, host))
    return topology, host


def test_normal_retirement_stops_only_exact_family_and_closes_all_resources():
    topology, host = ready()
    proof = topology.retire_normal_player(expected_title='Synthetic', deadline=5.,
        before_retire=lambda binding:stop_facts())
    assert proof['process_signaled'] is True and proof['job_active'] == 0
    assert proof['windows_invalid'] is True
    assert [x for x in host.calls if x[0]=='kill'] == [('kill','job0'), ('kill','job1')]
    assert host.flags['job1'] == 0
    topology.dispose()
    assert not topology._handles


@pytest.mark.parametrize('fault', ['worker', 'identity', 'flags', 'deadline', 'approval', 'residue'])
def test_retirement_uncertainty_never_claims_absence(fault):
    topology, host = ready()
    if fault == 'worker': host.jobs['job0'].append(10)
    elif fault == 'identity': host.identities[11] = replace(host.identities[11], creation_time_100ns=999)
    elif fault == 'flags': host.flags['job1'] = 0x2000
    elif fault == 'residue': host.residue = True
    def authorize(binding):
        if fault == 'approval': raise RuntimeError('revoked')
        return stop_facts()
    with pytest.raises(RuntimeError):
        topology.retire_normal_player(expected_title='Synthetic', deadline=1. if fault=='deadline' else 5.,
            before_retire=authorize)
    if fault != 'residue': assert ('kill','job1') not in host.calls


def test_revoked_failure_disposal_does_not_retire_player():
    topology, host = ready()
    topology.dispose()
    assert ('kill','job1') not in host.calls
    assert host.jobs['job1'] == [11]


def session_success(tmp_path):
    from dataclasses import asdict
    from test_policy_parent_session import compose, prepare as prepare_session
    from test_policy_finalization_v3 import snapshot
    from clash_rush_rebuild.mvp_session_authority import RetirementV3, canonical_v3, digest_v3
    session, _, state, clock, mutex = compose(tmp_path)
    # The normal lane now shares the guarded writer used by calibration.
    from test_lifecycle_state import RecordingFilePort
    from clash_rush_rebuild.lifecycle_state import LifecycleStateStore
    class RepeatPort(RecordingFilePort):
        def create_new_temp_write_through(self,path):
            if path.name != '.lifecycle-state.transition': self.payload=b''
            return super().create_new_temp_write_through(path)
    state = LifecycleStateStore(tmp_path, RepeatPort())
    state.initialize_ready()
    session.state = state
    host = RetirementHost(); session.topology._host = host
    offer = prepare_session(session, mutex)
    session.start_worker(mutex=mutex)
    session.authority.consume_v3(session.run, producer_epoch=1, now_ns=1_000_000_000)
    session.accept_worker(ack_for(offer), mutex=mutex)
    session.authority.record_snapshot_v3(snapshot(session.run))
    session.topology.terminate_worker(release_for(session.topology, offer, host))
    session.finalize(mutex=mutex, outcome='SUCCESS', reason='PRE_D_COMPLETE')
    identity = digest_v3(canonical_v3(asdict(offer.worker)))
    proof = canonical_v3(dict(schema=3, binding_digest=session.run.digest,
        identity_digest=identity, topology_digest=digest_v3(canonical_v3(asdict(offer))),
        absence_digest='a'*64, disposition='ABSENT', observed_ns=1_000_000_000))
    session.authority.record_retirement_v3(session.run, RetirementV3('WORKER',identity,proof),writer_epoch=1)
    return session, host, state, clock, mutex


def test_joined_normal_success_ready_closure_and_duplicate(tmp_path):
    from clash_rush_rebuild.lifecycle_state import Ready
    session, host, state, clock, mutex = session_success(tmp_path)
    try:
        assert session.retire_normal_success(mutex=mutex, expected_title='Synthetic') == Ready(1)
        assert session.authority.closure_v3(1).closed == 1
        calls = list(host.calls)
        assert session.retire_normal_success(mutex=mutex, expected_title='Synthetic') == Ready(1)
        assert host.calls == calls
        session.dispose(mutex=mutex)
        assert state.load() == Ready(1)
    finally: session.authority.close()


@pytest.mark.parametrize('fault', ['residue', 'handle_close', 'late', 'state_conflict'])
def test_no_ready_on_incomplete_normal_retirement(tmp_path, fault):
    from clash_rush_rebuild.lifecycle_state import Active
    session, host, state, clock, mutex = session_success(tmp_path)
    if fault == 'residue': host.residue = True
    elif fault == 'handle_close':
        host.close_handle = lambda h: (_ for _ in ()).throw(RuntimeError('close failed'))
    elif fault == 'late': clock.now = 10.
    elif fault == 'state_conflict': state.commit(Active(1,session.run.run_nonce,None))
    try:
        with pytest.raises(RuntimeError):
            session.retire_normal_success(mutex=mutex, expected_title='Synthetic')
        assert isinstance(state.load(), Active)
        assert session.authority.closure_v3(1).closed == 0
        if fault in ('late','state_conflict'): assert ('kill','job1') not in host.calls
    finally: session.authority.close()
