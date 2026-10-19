"""Retained independent probes, adapted only for explicit binding/topology APIs.

Originals remain under the private review directory. No player actions.
"""
import os
from contextlib import contextmanager
import pytest
from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.mvp_installed_build import ProcessObservation
from clash_rush_rebuild import mvp_worker_ownership as ownership
from clash_rush_rebuild.mvp_policy_ingress import PolicyIngress
from test_win32_primitives import FakeToolhelpApi
from test_calibration_retirement import captured

@pytest.fixture(autouse=True)
def trusted_time(monkeypatch):
    from clash_rush_rebuild import mvp_frozen_stop as stop
    monkeypatch.setattr(stop, '_monotonic_ns', lambda:1_000_000_000)
    monkeypatch.setattr(stop, '_wall_ns', lambda:1_000_000_000)

@pytest.mark.parametrize('image,argv',[
 ('C:/neutral/python.exe',('C:/neutral/python.exe','C:/neutral/bootstrap.py')),
 ('C:/neutral/renamed.exe',('C:/neutral/renamed.exe',)),
])
def test_unknown_unattested_payload_cannot_prove_absence(tmp_path,monkeypatch,image,argv):
    # Both can execute a runner loaded from config/environment without marker argv.
    # This is an ambiguity test, not a claim that every Python process is a worker.
    @contextmanager
    def observe(pid):yield ProcessObservation(ProcessIdentity(pid,202),image,argv)
    monkeypatch.setattr(ownership,'observe_process',observe)
    policy=PolicyIngress(tmp_path,process_api=FakeToolhelpApi(((os.getpid(),1,'controller.exe'),(101,1,'renamed.exe'))))
    with pytest.raises(RuntimeError):policy._absence()

@pytest.mark.parametrize('boundary',['evidence_second','binding_after_authorize','source_after_authorize',
                                    'source_callback','evidence_deadline'])
def test_durable_revocation_before_stop_forbids_stop(tmp_path,boundary):
    s,h,state,clock,mutex=captured(tmp_path)
    calls=[0]
    if boundary in ('evidence_second','evidence_deadline'):
        def evidence(observation,offer,binding=None):
            calls[0]+=1
            if calls[0]==2:
                if boundary=='evidence_deadline':clock.now=10.
                else:s.authority.revoke_v3(writer_epoch=s.epoch)
            return observation.evidence_digest
        s.calibration_evidence=evidence
    elif boundary=='source_callback':
        def approval(run):
            calls[0]+=1
            if calls[0]==4:s.authority.revoke_v3(writer_epoch=s.epoch)
            return True
        s.approval_current=approval
    else:
        original=h._require_capture_binding
        current=[True]
        if boundary=='source_after_authorize':s.approval_current=lambda run:current[0]
        def binding(*args):
            result=original(*args)
            if boundary=='source_after_authorize':current[0]=False
            else:s.authority.revoke_v3(writer_epoch=s.epoch)
            return result
        h._require_capture_binding=binding
    try:
        try:s.retire_calibration(mutex=mutex,expected_title='Synthetic')
        except RuntimeError:pass
        assert ('kill','job1') not in h.calls, 'player stopped after durable authority revocation'
    finally:s.authority.close()

@pytest.mark.parametrize('kind',['durable','source'])
def test_normal_success_uses_same_final_authorization_seam(tmp_path,kind):
    from test_policy_normal_retirement import session_success
    s,h,state,clock,mutex=session_success(tmp_path)
    current=[True]
    s.approval_current=lambda run:current[0]
    original=h._require_capture_binding
    def binding(*a):
        original(*a)
        if kind=='durable':s.authority.revoke_v3(writer_epoch=s.epoch)
        else:current[0]=False
    h._require_capture_binding=binding
    try:
        with pytest.raises(RuntimeError):s.retire_normal_success(mutex=mutex,expected_title='Synthetic')
        assert ('kill','job1') not in h.calls
    finally:s.authority.close()

def test_real_markerless_process_can_load_runner_without_association():
    import subprocess,sys
    module_hex='clash_rush_rebuild.mvp_policy_run'.encode().hex()
    code="import importlib,time; m=importlib.import_module(bytes.fromhex('"+module_hex+"').decode()); print(callable(m.worker_main),flush=True); time.sleep(20)"
    child=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='True'
        with ownership.observe_process(child.pid) as p:
            assert ownership.classify(p,record=ownership.load_record(p),current=None)=='AMBIGUOUS'
    finally:
        child.terminate();child.wait(timeout=10);child.stdout.close()

def test_verified_current_helper_survives_production_scanner():
    from dataclasses import asdict
    from test_policy_worker_ownership import record
    from test_policy_process_topology import InertHost,controller,prepare
    r=record();host=InertHost();topology=controller(host);offer=prepare(topology)
    r.update(run_nonce=offer.run_nonce,generation=offer.generation,worker=asdict(offer.worker),parent=asdict(offer.parent))
    r['argv'][8]=str(offer.parent.pid);r['argv'][10]=str(offer.parent.creation_time_100ns)
    helper=ProcessObservation(ProcessIdentity(303,404),'helper.exe',('helper.exe','clash-rush-helper'))
    host.identities[303]=helper.identity;host.jobs[topology.worker_job].append(303)
    assert ownership.classify(helper,record=r,current=r,owned_topology=topology)=='OWNED'
    parent=ProcessObservation(offer.parent,'parent.exe',('parent.exe',))
    @contextmanager
    def observe(pid):yield parent if pid==parent.identity.pid else helper
    # Current-run scans must explicitly carry retained topology; stopped ingress
    # must not infer it from matching record bytes.
    ownership.require_worker_absence((parent.identity.pid,303),self_identity=parent.identity,
        observe=observe,load_record=lambda p:r,current=r,owned_topology=topology)
    with pytest.raises(RuntimeError):
        ownership.require_worker_absence((parent.identity.pid,303),self_identity=parent.identity,
            observe=observe,load_record=lambda p:r,current=r)

@pytest.mark.parametrize('changed',['both','root_hwnd','render_hwnd','width','height','none'])
def test_changed_window_geometry_cannot_retire_from_old_capture(tmp_path,monkeypatch,changed):
    from dataclasses import asdict,replace
    from test_policy_parent_session import compose,prepare
    from test_calibration_retirement import Host
    from test_calibration_mode import scope_for
    from test_calibration_composition import specification
    from test_policy_process_topology import ack_for,release_for
    from test_lifecycle_state import RecordingFilePort
    from clash_rush_rebuild.lifecycle_state import LifecycleStateStore
    from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
    from clash_rush_rebuild.mvp_session_authority import canonical_v3,digest_v3,RetirementV3
    from clash_rush_rebuild.mvp_calibration import CalibrationObservation,WindowObservation
    from clash_rush_rebuild import mvp_calibration_native as native,mvp_policy_ingress as ingress,mvp_deployment_profile as files
    s,_,_,clock,mutex=compose(tmp_path)
    class RepeatPort(RecordingFilePort):
        def create_new_temp_write_through(self,path):
            if path.name!='.lifecycle-state.transition':self.payload=b''
            return super().create_new_temp_write_through(path)
    state=LifecycleStateStore(tmp_path,RepeatPort());state.initialize_ready();s.state=state
    data=specification();data['source_tree']=s.run.source_tree
    raw=canonical_v3(data);scope=replace(scope_for(s.run),profile_digest=digest_v3(raw))
    s.run=replace(s.run,profile_digest=scope.profile_digest,binding_digest=scope.digest,provider_digest=scope.digest)
    h=Host();s.topology=ParentProcessTopology(h,run_nonce=s.run.run_nonce,generation=1,purpose='CALIBRATION_READ_ONLY')
    s.calibration_scope=scope;offer=prepare(s,mutex);s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000);s.accept_worker(ack_for(offer),mutex=mutex)
    window=WindowObservation(offer.player,3,4,800,600,True,offer.player,'f'*32)
    original_binding=h.bind_exact
    h.bind_exact=lambda *a:replace(original_binding(*a),root_hwnd=3,render_hwnd=4,width=800,height=600)
    s.topology.pin_capture(window,'Synthetic')
    h.bind_exact=original_binding
    s.topology.terminate_worker(release_for(s.topology,offer,h))
    spec=native.CalibrationSpec.decode(raw,scope);stored=[]
    sink=native.NarrowEvidenceSink(tmp_path,spec,write=lambda p,r,b:(stored.append(b) or b))
    evidence=sink.persist(scope,offer,window,
        {'HOME_ANCHOR':bytes([50])*12,'SOURCE_ANCHOR':bytes([100])*12},1_000_000_000)
    observation=CalibrationObservation(scope.digest,digest_v3(canonical_v3(asdict(offer))),'CAPTURED',1_000_000_000,evidence)
    s.authority.record_calibration_observation_v3(s.run,observation,writer_epoch=1)
    s.finalize(mutex=mutex,outcome='BLOCKED',reason='EVIDENCE_INCOMPLETE')
    identity=digest_v3(canonical_v3(asdict(offer.worker)))
    proof=canonical_v3(dict(schema=3,binding_digest=s.run.digest,identity_digest=identity,topology_digest=observation.offer_digest,absence_digest='a'*64,disposition='ABSENT',observed_ns=1_000_000_000))
    s.authority.record_retirement_v3(s.run,RetirementV3('WORKER',identity,proof),writer_epoch=1)
    config=object.__new__(native.CalibrationConfiguration)
    config.project=tmp_path;config.scope=scope;config.spec=spec;config.run=s.run;config.approval_current=lambda r:True
    monkeypatch.setattr(ingress,'_require_private',lambda p:None)
    monkeypatch.setattr(ingress,'_read_input_state',lambda:(0,)*256)
    monkeypatch.setattr(files,'read_private',lambda *a:stored[0])
    s.calibration_evidence=config.retirement_evidence
    # Production rebind returns HWNDs 80/81, 1280x720, not sealed 3/4, 800x600.
    if changed!='both':
        original=h.bind_exact
        values=dict(root_hwnd=3,render_hwnd=4,width=800,height=600)
        if changed!='none':values[changed]+=1
        h.bind_exact=lambda *a:replace(original(*a),**values)
    try:
        if changed=='none':
            s.retire_calibration(mutex=mutex,expected_title='Synthetic')
            assert ('kill','job1') in h.calls
            assert s.authority.outcome_v3(1).outcome=='BLOCKED'
        else:
            with pytest.raises(RuntimeError):s.retire_calibration(mutex=mutex,expected_title='Synthetic')
            assert ('kill','job1') not in h.calls,'changed capture binding/geometry authorized stop'
    finally:s.authority.close()

@pytest.mark.parametrize('missing',['unpublished','partial_record'])
def test_marker_worker_publication_gap_is_ambiguous(tmp_path,monkeypatch,missing):
    # A known-shape suspended worker must not become unrelated before publication.
    p=ProcessObservation(ProcessIdentity(101,202),'python.exe',('python.exe','-m','clash_rush_rebuild.mvp_policy_run','--project-root',str(tmp_path),'--ownership-nonce','a'*32))
    if missing=='partial_record':
        from clash_rush_rebuild import mvp_policy_ingress as ingress
        from clash_rush_rebuild import mvp_deployment_profile as files
        monkeypatch.setattr(ingress,'_require_private',lambda p:None)
        monkeypatch.setattr(files,'read_private',lambda *a:b'{')
    with pytest.raises(RuntimeError):ownership.load_record(p)
