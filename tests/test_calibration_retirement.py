"""Owner-approved calibration retirement, never semantic success."""
from dataclasses import asdict, replace
import pytest
from test_policy_parent_session import compose, prepare
from test_policy_normal_retirement import RetirementHost
from test_calibration_mode import scope_for
from test_policy_process_topology import ack_for, release_for
from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
from clash_rush_rebuild.mvp_calibration import CalibrationObservation
from clash_rush_rebuild.mvp_session_authority import RetirementV3, canonical_v3, digest_v3
from clash_rush_rebuild.lifecycle_state import Active, Ready


@pytest.fixture(autouse=True)
def trusted_test_time(monkeypatch):
    from clash_rush_rebuild import mvp_frozen_stop as stop
    monkeypatch.setattr(stop, '_monotonic_ns', lambda: 1_000_000_000)
    monkeypatch.setattr(stop, '_wall_ns', lambda: 1_000_000_000)


def test_parent_finish_wires_calibration_retirement(tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from clash_rush_rebuild.mvp_policy_run import finish_parent_session
    from clash_rush_rebuild.mvp_process_topology import ReleaseStageReceipt
    s,h,state,clock,mutex = captured(tmp_path)
    observation = s.authority.calibration_observation_v3(s.run)
    s.expected_title = 'Synthetic'
    s.calibration_issuer = SimpleNamespace(revoke=lambda:None)
    @contextmanager
    def owned(): yield mutex
    class Channel:
        def __init__(self):
            self.context = SimpleNamespace(calibration=object(), run=s.run)
            self.messages = [observation]
        def pending(self): return bool(self.messages)
        def receive(self, **kw): return self.messages.pop(0)
        def send(self, revoke): self.messages.append(ReleaseStageReceipt(s.topology.offer,
            revoke.token, 'NOT_REQUIRED', (s.topology.offer.worker,), True))
        def close(self): pass
    # Existing worker proof must exactly match finish's real proof, not fixture bytes.
    # Use a fresh fixture without that record.
    s.authority.close()
    s,h,state,clock,mutex = captured(tmp_path/'fresh', worker_record=False, retired=False)
    observation = s.authority.calibration_observation_v3(s.run)
    s.expected_title = 'Synthetic'
    s.calibration_issuer = SimpleNamespace(revoke=lambda:None)
    try:
        assert finish_parent_session(s,Channel(),mutex_factory=owned,clock=lambda:1.) == observation
        assert state.load() == Ready(0)
        assert s.authority.closure_v3(1).player_disposition == 'LIFECYCLE_VERIFIED'
    finally: s.authority.close()


class Host(RetirementHost):
    def set_kill_on_close(self, job): self.flags[job] = 0x2000


def test_parent_reopens_sealed_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_calibration_composition import specification, scope_for as spec_scope
    from clash_rush_rebuild import mvp_calibration_native as m
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild import mvp_deployment_profile as files
    from clash_rush_rebuild.mvp_calibration import WindowObservation
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    raw = canonical_v3(specification()); scope = spec_scope(raw)
    identity = ProcessIdentity(1,2); offer = SimpleNamespace()
    # Use a real dataclass offer from the session, not synthesized native evidence.
    s,h,state,clock,mutex = captured(tmp_path/'session')
    offer = s.topology.offer
    spec = m.CalibrationSpec.decode(raw,scope)
    config = object.__new__(m.CalibrationConfiguration)
    config.project=tmp_path; config.scope=scope; config.spec=spec
    config.run=object(); config.approval_current=lambda r:True
    stored=[]
    sink=m.NarrowEvidenceSink(tmp_path,spec,write=lambda p,r,b:(stored.append(b) or b))
    digest=sink.persist(scope,offer,WindowObservation(offer.player,3,4,800,600,True,offer.player,'f'*32),
        {'HOME_ANCHOR':bytes([50])*12,'SOURCE_ANCHOR':bytes([100])*12},100)
    notice=CalibrationObservation(scope.digest,digest_v3(canonical_v3(asdict(offer))),'CAPTURED',100,digest)
    monkeypatch.setattr(ingress,'_require_private',lambda p:None)
    monkeypatch.setattr(ingress,'_read_input_state',lambda :(0,)*256)
    monkeypatch.setattr(files,'read_private',lambda *a:stored[0])
    try:
        assert hasattr(config,'retirement_evidence'), 'parent independent evidence reader missing'
        assert config.retirement_evidence(notice,offer)==digest
        stored[0]=b'unreadable'
        with pytest.raises(RuntimeError): config.retirement_evidence(notice,offer)
    finally: s.authority.close()



def captured(tmp_path, worker_record=True, retired=True):
    s, _, state, clock, mutex = compose(tmp_path)
    from test_lifecycle_state import RecordingFilePort
    from clash_rush_rebuild.lifecycle_state import LifecycleStateStore
    class RepeatPort(RecordingFilePort):
        def create_new_temp_write_through(self,path):
            if path.name != '.lifecycle-state.transition': self.payload=b''
            return super().create_new_temp_write_through(path)
    port=RepeatPort()
    state=LifecycleStateStore(tmp_path,port)
    state.initialize_ready()
    s.state=state
    scope = scope_for(s.run)
    s.run = replace(s.run, binding_digest=scope.digest, provider_digest=scope.digest)
    host = Host()
    s.topology = ParentProcessTopology(host, run_nonce=s.run.run_nonce, generation=1,
                                       purpose='CALIBRATION_READ_ONLY')
    s.calibration_scope = scope
    offer = prepare(s, mutex)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run, producer_epoch=1, now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer), mutex=mutex)
    from clash_rush_rebuild.mvp_calibration import WindowObservation
    window = WindowObservation(offer.player,80,81,1280,720,True,offer.player,'f'*32)
    if retired:
        s.topology.pin_capture(window, 'Synthetic')
    else:
        # The real finish caller pins while W is still alive; file IO alone is doubled.
        s.capture_window = lambda *a:window
    if retired:
        s.topology.terminate_worker(release_for(s.topology, offer, host))
    observation = CalibrationObservation(scope.digest, digest_v3(canonical_v3(asdict(offer))),
                                         'CAPTURED', 1_000_000_000, 'e'*64)
    s.authority.record_calibration_observation_v3(s.run, observation, writer_epoch=1)
    s.finalize(mutex=mutex, outcome='BLOCKED', reason='EVIDENCE_INCOMPLETE')
    identity = digest_v3(canonical_v3(asdict(offer.worker)))
    proof = canonical_v3(dict(schema=3, binding_digest=s.run.digest, identity_digest=identity,
        topology_digest=observation.offer_digest, absence_digest='a'*64,
        disposition='ABSENT', observed_ns=1_000_000_000))
    if worker_record:
        s.authority.record_retirement_v3(s.run, RetirementV3('WORKER', identity, proof), writer_epoch=1)
    s.calibration_evidence = lambda notice, retained, binding=None: notice.evidence_digest
    return s, host, state, clock, mutex


def test_stale_worker_retirement_cannot_authorize_player_stop(tmp_path):
    import json
    s,h,state,clock,mutex=captured(tmp_path,worker_record=False)
    offer=s.topology.offer
    identity=digest_v3(canonical_v3(asdict(offer.worker)))
    proof=canonical_v3(dict(schema=3,binding_digest=s.run.digest,identity_digest=identity,
        topology_digest='f'*64,absence_digest='a'*64,disposition='ABSENT',observed_ns=1_000_000_000))
    s.authority.record_retirement_v3(s.run,RetirementV3('WORKER',identity,proof),writer_epoch=1)
    try:
        with pytest.raises(RuntimeError): s.retire_calibration(mutex=mutex,expected_title='Synthetic')
        assert ('kill','job1') not in h.calls
    finally: s.authority.close()


@pytest.mark.parametrize('change', ['binding_digest','topology_digest','observed_ns'])
def test_conflicting_or_stale_retirement_evidence_is_rejected(tmp_path,change):
    import json
    s,h,state,clock,mutex=captured(tmp_path)
    try:
        s.retire_calibration(mutex=mutex,expected_title='Synthetic')
        row=s.authority._open().execute("SELECT * FROM retirement_v3 WHERE role='PLAYER'").fetchone()
        proof=RetirementV3('PLAYER',row['identity_digest'],row['proof'])
        s.authority.record_retirement_v3(s.run,proof,writer_epoch=1)
        raw=json.loads(proof.proof)
        raw[change]=s.run.deadline_ns if change=='observed_ns' else 'f'*64
        with pytest.raises(RuntimeError):
            s.authority.record_retirement_v3(s.run,RetirementV3('PLAYER',row['identity_digest'],canonical_v3(raw)),writer_epoch=1)
        assert s.authority.outcome_v3(1).outcome=='BLOCKED'
    finally: s.authority.close()


@pytest.mark.parametrize('cut',['before_close','after_close'])
def test_cross_store_crash_cut_never_exposes_ready(tmp_path, cut):
    s,h,state,clock,mutex=captured(tmp_path)
    original=s.authority.close_v3
    def crash(*a,**kw):
        if cut=='after_close': original(*a,**kw)
        raise RuntimeError('crash cut')
    s.authority.close_v3=crash
    try:
        with pytest.raises(RuntimeError): s.retire_calibration(mutex=mutex,expected_title='Synthetic')
        with pytest.raises(RuntimeError,match='transition'):
            state.load()
        assert s.authority.outcome_v3(1).outcome=='BLOCKED'
    finally: s.authority.close()


def test_calibration_closes_with_independent_lifecycle_result(tmp_path):
    s,h,state,clock,mutex = captured(tmp_path)
    try:
        before = s.authority.outcome_v3(1)
        assert hasattr(s, 'retire_calibration'), 'same-parent calibration retirement missing'
        assert s.retire_calibration(mutex=mutex, expected_title='Synthetic') == Ready(0)
        assert s.authority.outcome_v3(1) == before
        assert before.outcome == 'BLOCKED' and before.reason == 'EVIDENCE_INCOMPLETE'
        assert s.authority.closure_v3(1).player_disposition == 'LIFECYCLE_VERIFIED'
        calls = list(h.calls)
        assert s.retire_calibration(mutex=mutex, expected_title='Synthetic') == Ready(0)
        assert calls == h.calls
        s.dispose(mutex=mutex)
        assert state.load() == Ready(0)
    finally: s.authority.close()


@pytest.mark.parametrize('fault', ['worker_alive','worker_helper','unreadable','changed_evidence',
    'binding','revoked','expired','stop_failure','handle_close','worker_record','scope',
    'authority_revoked'])
def test_calibration_negative_cuts_preserve_active(tmp_path, fault):
    s,h,state,clock,mutex = captured(tmp_path, worker_record=fault != 'worker_record')
    before = s.authority.outcome_v3(1)
    def fail(*a, **k): raise RuntimeError('injected cut')
    if fault == 'worker_alive': h.exited.remove(10)
    if fault == 'worker_helper': h.jobs['job0'].append(99)
    if fault == 'unreadable': s.calibration_evidence = fail
    if fault == 'changed_evidence': s.calibration_evidence = lambda *a:'f'*64
    if fault == 'binding': h.identities[11] = replace(h.identities[11], creation_time_100ns=999)
    if fault == 'revoked': s.approval_current = lambda r:False
    if fault == 'expired': clock.now = 10.
    if fault == 'stop_failure': h.terminate_job = fail
    if fault == 'handle_close': h.close_handle = fail

    if fault == 'scope': s.calibration_scope = replace(s.calibration_scope, slot=1)
    if fault == 'authority_revoked': s.authority.revoke_v3(writer_epoch=1)
    try:
        with pytest.raises(RuntimeError): s.retire_calibration(mutex=mutex, expected_title='Synthetic')
        assert isinstance(state.load(), Active)
        assert s.authority.outcome_v3(1) == before
        assert s.authority.closure_v3(1).closed == 0
        if fault not in ('handle_close',): assert ('kill','job1') not in h.calls
    finally: s.authority.close()
