"""Calibration roots exercised with synthetic pixels and fake OS ports only."""
import importlib
import json
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from clash_rush_rebuild.mvp_calibration import CalibrationScope
from clash_rush_rebuild.mvp_session_authority import canonical_v3, digest_v3


def module():
    name = 'clash_rush_rebuild.mvp_calibration_native'
    assert importlib.util.find_spec(name) is not None, 'production calibration composition missing'
    return importlib.import_module(name)


def specification():
    return dict(schema=1, source_tree='a'*40, slot=0, slot_id='slot-001',
        instance_digest='b'*64, slots_digest='c'*64, configuration_digest='d'*64,
        host_profile_digest='bec50bfe3c12866ea56012d26e01295eedfcdf1311ec7825b77e0b031cc77528',
        host_witness_digest='e'*64, ui_revision='test-ui', calibration_version=1,
        source_screen='HOME', capture_method='PRINTWINDOW_BGR_NATIVE',
        native_size=[800,600], mapping=[0,0,800,600],
        crops=[dict(id=name, box=box, template_digest=digest_v3(bytes([v])*12),
                    maximum_delta=0) for name,box,v in (
            ('HOME_ANCHOR',[10,10,12,12],50),
            ('SOURCE_ANCHOR',[20,20,22,22],100))])


def scope_for(raw):
    return CalibrationScope('f'*40,'a'*40,digest_v3(raw),0,'b'*64,
        'com.supercell.clashofclans','private/calibration','1'*32,1,1,5_000_000_000)


def test_concrete_observer_persists_only_approved_crops_and_wipes_frames():
    m=module(); raw=canonical_v3(specification()); scope=scope_for(raw)
    spec=m.CalibrationSpec.decode(raw,scope)
    from clash_rush_rebuild.lifecycle import ProcessIdentity, PlayerBinding
    identity=ProcessIdentity(1,2)
    binding=PlayerBinding(identity,identity,3,4,800,600,'3'*32)
    frames=[]; writes=[]; clock=[100_000_000]
    class View:
        offer=SimpleNamespace(player=identity)
        def __init__(self): self.binding=binding
        def _bound(self): pass
        def capture_bgr(self):
            a=np.zeros((600,800,3),np.uint8)
            a[10:12,10:12]=50; a[20:22,20:22]=100
            frames.append(a); clock[0]+=150_000_000
            return a
    class Sink:
        def persist(self,scope,offer,window,crops,observed_ns):
            writes.append(crops)
            assert set(crops)=={'HOME_ANCHOR','SOURCE_ANCHOR'}
            assert all(len(v)==12 for v in crops.values())
            return '2'*64
    view=View()
    observer=m.NativeCalibrationObserver(spec,view,Sink(),current=lambda:True,
        clock_ns=lambda:clock[0],wait=lambda _:clock.__setitem__(0,clock[0]+100_000_000))
    window=observer.window_geometry(view.offer)
    assert window.supported_visible is True
    assert observer.capture_private_evidence(scope,view.offer,window)=='2'*64
    assert observer.window_geometry(view.offer)==window
    assert writes and all(not f.any() for f in frames)


@pytest.mark.parametrize('change',[
    lambda d:d.update(extra=True), lambda d:d.update(native_size=[True,300]),
    lambda d:d.update(mapping=[0,0,1280,720]), lambda d:d.update(source_screen='LOGIN'),
    lambda d:d['crops'][0].update(box=[0,0,800,600]),
    lambda d:d['crops'][0].update(id='ACCOUNT_NAME'),
    lambda d:d['crops'][0].update(maximum_delta=True),
    lambda d:d.update(host_profile_digest='0'*64),
])
def test_closed_spec_rejects_unapproved_mapping_and_sources(change):
    m=module(); d=specification(); change(d); raw=canonical_v3(d)
    with pytest.raises(RuntimeError): m.CalibrationSpec.decode(raw,scope_for(raw))


def test_calibration_cli_dispatch_is_separate_from_attack(monkeypatch,tmp_path,capsys):
    from clash_rush_rebuild import cli, mvp_policy_run
    calls=[]
    monkeypatch.setattr(mvp_policy_run,'run_policy_command',lambda root,**kw:
        calls.append((root,kw)) or SimpleNamespace(status='CAPTURED'))
    try:
        result=cli.main(['mvp-calibrate','--project-root',str(tmp_path)])
    except SystemExit:
        pytest.fail('installed calibration command missing')
    assert result==0 and calls==[(str(tmp_path),{'purpose':'CALIBRATION_READ_ONLY'})]
    assert 'gameplay=DENIED' in capsys.readouterr().out


def test_calibration_configuration_missing_is_denied_before_native(tmp_path,monkeypatch):
    m=module()
    assert hasattr(m,'CalibrationConfiguration'), 'protected calibration composition missing'
    from clash_rush_rebuild import mvp_policy_run as run
    monkeypatch.setattr(run,'policy_mutex',lambda:pytest.fail('native mutex reached'))
    with pytest.raises(RuntimeError): m.CalibrationConfiguration(tmp_path,None,None)


def test_parent_calibration_reaches_existing_bootstrap_without_gameplay(monkeypatch,tmp_path):
    from canonical_fixture import install_fixture
    install_fixture(tmp_path, monkeypatch)
    from contextlib import contextmanager
    from clash_rush_rebuild import mvp_policy_run as p, mvp_policy_ingress as ingress
    from clash_rush_rebuild import win32_lifecycle_host as hostmod, win32_state_io as stateio
    from clash_rush_rebuild import lifecycle_state
    m=module(); calls=[]
    raw=canonical_v3(specification()); scope=scope_for(raw)
    @contextmanager
    def prepared(self,**kwargs):
        assert kwargs=={'purpose':'CALIBRATION_READ_ONLY'}
        yield SimpleNamespace(calibration_scope=scope,run=SimpleNamespace(run_nonce=scope.run_nonce),slot=0,
            require_runtime_activation=lambda:pytest.fail('gameplay activation called'))
    monkeypatch.setattr(ingress.PolicyIngress,'prepare_run',prepared)
    class Configuration:
        data={'slot':0}; slot=SimpleNamespace(internal_name='SyntheticInternal')
        def __init__(self,*a): calls.append('config')
    monkeypatch.setattr(m,'CalibrationConfiguration',Configuration)
    monkeypatch.setattr(hostmod,'NativeLifecycleApi',lambda:None)
    monkeypatch.setattr(hostmod,'Win32LifecycleHost',lambda *a,**k:None)
    monkeypatch.setattr(stateio,'NativeWin32StateApi',lambda:None)
    monkeypatch.setattr(stateio,'Win32StateFilePort',lambda *a:None)
    monkeypatch.setattr(lifecycle_state,'LifecycleStateStore',lambda *a:None)
    monkeypatch.setattr(p,'WindowsPolicyListener',lambda:SimpleNamespace(name='synthetic',
        parent=SimpleNamespace(pid=1,creation_time_100ns=2),close=lambda:None))
    def bootstrap(*a,**kw):
        assert '--calibration-read-only' in kw['worker_command']
        assert '--instance SyntheticInternal' in kw['player_command']
        calls.append('bootstrap')
        raise RuntimeError('TEST_BOOTSTRAP_REACHED')
    monkeypatch.setattr(p,'bootstrap_parent',bootstrap)
    with pytest.raises(RuntimeError,match='TEST_BOOTSTRAP_REACHED'):
        p.run_policy_command(tmp_path,purpose='CALIBRATION_READ_ONLY')
    assert calls[-1]=='bootstrap'


def test_worker_root_selects_calibration_sources_after_authenticated_context(monkeypatch,tmp_path):
    from clash_rush_rebuild import mvp_policy_run as p, mvp_session_authority as auth
    from clash_rush_rebuild import win32_lifecycle_host as hostmod
    m=module(); calls=[]
    raw=canonical_v3(specification()); scope=scope_for(raw)
    context=SimpleNamespace(run=SimpleNamespace(run_nonce=scope.run_nonce),calibration=SimpleNamespace(scope=scope))
    channel=SimpleNamespace(context=context,close=lambda:calls.append('close'))
    monkeypatch.setattr(p.PolicyChannel,'connect',lambda *a,**kw:channel)
    class Configuration:
        def __init__(self,*a): calls.append('config')
        def worker_sources(self,c,h):
            assert c is channel
            calls.append('sources'); return 'calibration-sources'
    monkeypatch.setattr(m,'CalibrationConfiguration',Configuration)
    monkeypatch.setattr(auth,'DurableSessionAuthority',lambda *a:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(hostmod,'NativeLifecycleApi',lambda:None)
    monkeypatch.setattr(hostmod,'Win32LifecycleHost',lambda *a,**kw:None)
    def execute(c,**kw):
        assert kw['sources']=='calibration-sources'; calls.append('execute')
    monkeypatch.setattr(p,'execute_worker_session',execute)
    result=p.worker_main(['--project-root',str(tmp_path),'--pipe','synthetic',
        '--parent-pid','1','--parent-created','2','--calibration-read-only','--ownership-nonce',scope.run_nonce])
    assert result==0 and calls==['config','sources','execute','close']


@pytest.mark.parametrize('relative', ['private/calibration/'+'1'*32+'.json',
    'private/slots.json', 'private/deployment/runner-manifest.json',
    'var/.policy-setup.pending', 'var/approvals/'+'2'*32+'.json'])
@pytest.mark.parametrize('close_fault',[False,True])
def test_protected_writer_keeps_identity_handle_and_closes_before_readback(monkeypatch,tmp_path,close_fault,relative):
    m=module()
    from clash_rush_rebuild import mvp_policy_ingress as ingress, mvp_deployment_profile as profile
    from clash_rush_rebuild import win32_state_io as stateio, approval_reconciliation as approvals
    from pathlib import Path
    calls=[]; opened={}; serial=[0]
    class Api:
        def create_file(self,path,access,share,creation,flags):
            serial[0]+=1; h=serial[0]; opened[h]=(Path(path),access,share)
            calls.append(('open',access,share,creation))
            return h
        def final_path(self,h): return str(opened[h][0])
        def handle_attributes(self,h): return 0 if opened[h][0] == tmp_path/relative else 0x10
        def write_file(self,h,data):
            assert opened[h][1] & 0x40000000
            calls.append(('write',)); return len(data)
        def flush_file_buffers(self,h): calls.append(('flush',)); return True
        def close_handle(self,h):
            nonlocal close_fault
            if close_fault and opened[h][1] & 0x40000000:
                close_fault=False
                calls.append(('close-failed',)); return False
            del opened[h]; return True
    def security(path):
        # The existing DACL reader opens with FILE_SHARE_READ only; a retained
        # GENERIC_WRITE handle would cause ERROR_SHARING_VIOLATION on Windows.
        assert not any(a & 0x40000000 for _,a,_ in opened.values()), 'DACL read conflicts with writer'
        calls.append(('security',))
    monkeypatch.setattr(ingress,'_require_private',security)
    monkeypatch.setattr(approvals,'_seal_private_path',lambda *a:None)
    monkeypatch.setattr(stateio,'NativeWin32StateApi',Api)
    def read(*a):
        assert opened and not any(access & 0x40000000 for _,access,_ in opened.values())
        calls.append(('readback',)); return b'{}'
    monkeypatch.setattr(profile,'_read_windows',read)
    if close_fault:
        with pytest.raises(RuntimeError): m.write_protected_record(tmp_path,relative,b'{}')
        assert not opened and ('close-failed',) in calls
    else:
        assert m.write_protected_record(tmp_path,relative,b'{}')==b'{}'
        assert not opened and ('write',) in calls and ('readback',) in calls


@pytest.mark.parametrize('fault',[None,'scope','spec','host','slots','config','approval','expired'])
def test_protected_configuration_binds_real_registry_and_current_witness(monkeypatch,tmp_path,fault):
    from dataclasses import replace
    from test_registry import _conf
    from test_instance_slot_registry import registration
    from test_policy_finalization_v3 import binding_v3
    from clash_rush_rebuild import mvp_policy_ingress as ingress, mvp_deployment_profile as profile
    from clash_rush_rebuild import mvp_installed_build as installed
    m=module(); config=_conf(('A','B','C','D','E')).encode(); slots=canonical_v3(registration())
    host=canonical_v3(dict(schema=1,host_profile_digest=m.HOST_PROFILE,
        configuration_digest=digest_v3(config),source_tree='a'*40,native_size=[800,600],
        windows_dpi=96,observed_at=90,expires_at=200))
    d=specification(); d.update(slot_id='slot-000',instance_digest=digest_v3(b'Pie64'),
        slots_digest=digest_v3(slots),configuration_digest=digest_v3(config),host_witness_digest=digest_v3(host))
    raw=canonical_v3(d); scope=replace(scope_for(raw),instance_digest=d['instance_digest'])
    run=replace(binding_v3(),profile_digest=scope.profile_digest,binding_digest=scope.digest,
        provider_digest=scope.digest,run_nonce=scope.run_nonce,started_ns=scope.issued_ns,
        deadline_ns=scope.expires_ns)
    request=asdict(run); del request['approval_digest']
    approval=canonical_v3({'run':request}); run=replace(run,approval_digest=digest_v3(approval))
    files={'calibration.json':canonical_v3(asdict(scope)), 'calibration-spec.json':raw,'calibration-host.json':host}
    monkeypatch.setattr(m.time,'time',lambda:100)
    monkeypatch.setattr(m.time,'monotonic_ns',lambda:100)
    monkeypatch.setattr(ingress,'_require_private',lambda *a:None)
    monkeypatch.setattr(profile,'read_private',lambda p,r,n:files[r.split('/')[-1]])
    monkeypatch.setattr(profile,'_read_windows',lambda *a:slots)
    monkeypatch.setattr(installed,'CONFIG',SimpleNamespace(read_bytes=lambda:config))
    monkeypatch.setattr(ingress.PolicyIngress,'_approval',lambda self,purpose:(json.loads(approval),approval))
    source=m.CalibrationConfiguration(tmp_path,run,scope)
    assert source.slot.internal_name=='Pie64' and source.approval_current(run)
    if fault=='scope': files['calibration.json']=b'{}'
    if fault=='spec': files['calibration-spec.json']=b'{}'
    if fault=='host': files['calibration-host.json']=b'{}'
    if fault=='slots': slots=b'{}'
    if fault=='config': config=b'changed'
    if fault=='approval': approval=b'{}'
    if fault=='expired': monkeypatch.setattr(m.time,'time',lambda:201)
    assert source.approval_current(run) is (fault is None)
    if fault:
        # Revocation latches even if mutable sources subsequently recover.
        assert source.approval_current(run) is False


def test_worker_configuration_allows_handshake_but_not_capture_before_ready(monkeypatch,tmp_path):
    from dataclasses import replace
    import time
    from test_policy_finalization_v3 import binding_v3
    from clash_rush_rebuild.mvp_calibration import CalibrationCapability
    m=module(); raw=canonical_v3(specification()); now=time.monotonic_ns()
    scope=replace(scope_for(raw),issued_ns=now,expires_ns=now+10_000_000_000)
    run=replace(binding_v3(),profile_digest=scope.profile_digest,binding_digest=scope.digest,
        provider_digest=scope.digest,run_nonce=scope.run_nonce,started_ns=scope.issued_ns,
        deadline_ns=scope.expires_ns)
    configuration=object.__new__(m.CalibrationConfiguration)
    configuration.scope=scope; configuration.run=run; configuration.project=tmp_path
    configuration.spec=m.CalibrationSpec.decode(raw,scope); configuration.expected_title='Synthetic'
    configuration.approval_current=lambda r:r==run
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    context=SimpleNamespace(run=run,calibration=CalibrationCapability(scope,'2'*32),offer=ProcessIdentity(1,2))
    channel=SimpleNamespace(context=context,no_more_gameplay=True)
    sources=configuration.worker_sources(channel,object())
    sources.require_context(context)  # prepare/ack precedes PlayerReady
    with pytest.raises(RuntimeError):
        sources.observe(context,SimpleNamespace(active=lambda:False))


def test_native_observer_waits_for_initial_window_without_reusing_revoked_view(monkeypatch):
    m=module(); raw=canonical_v3(specification()); scope=scope_for(raw)
    now=[100]; views=[]
    class View:
        def __init__(self,**kw):
            self.__dict__.update(kw); self.binding=None
            self._current=kw['current']; self._size=kw['native_size']; views.append(self)
        def bind(self,title):
            if len(views)==1: raise RuntimeError('WINDOW_NOT_READY')
            self.binding=object()
        def _bound(self): pass
    monkeypatch.setattr(m,'CalibrationWindowView',View)
    view=View(host=object(),offer=object(),scope=scope,current=lambda:True,native_size=(800,600))
    observer=m.NativeCalibrationObserver(m.CalibrationSpec.decode(raw,scope),view,None,
        current=lambda:True,clock_ns=lambda:now[0],wait=lambda _:now.__setitem__(0,now[0]+100_000_000),
        expected_title='Synthetic')
    observer._check()
    assert len(views)==2 and observer.view is views[1] and views[0].binding is None


@pytest.mark.parametrize('fault',[None,'unsupported','revoked','readback','changed-window'])
def test_production_worker_sources_native_view_observer_sink_join(tmp_path,monkeypatch,fault):
    import time
    from dataclasses import replace
    from contextlib import nullcontext
    from test_policy_finalization_v3 import binding_v3
    from clash_rush_rebuild.mvp_calibration import CalibrationCapability
    from clash_rush_rebuild.lifecycle import ProcessIdentity,PlayerBinding
    m=module(); raw=canonical_v3(specification()); now=time.monotonic_ns()
    scope=replace(scope_for(raw),issued_ns=now,expires_ns=now+10_000_000_000)
    run=replace(binding_v3(),profile_digest=scope.profile_digest,binding_digest=scope.digest,
        provider_digest=scope.digest,run_nonce=scope.run_nonce,started_ns=scope.issued_ns,deadline_ns=scope.expires_ns)
    from test_policy_process_topology import prepare,InertHost
    from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
    topology_host=InertHost(); topology=ParentProcessTopology(topology_host,run_nonce=run.run_nonce,generation=1)
    offer=prepare(topology)
    context=SimpleNamespace(run=run,calibration=CalibrationCapability(scope,'2'*32),offer=offer)
    channel=SimpleNamespace(context=context,no_more_gameplay=False)
    active=[True]; wrote=[]; count=[0]
    binding=PlayerBinding(offer.player,offer.player,12,13,800,600,'f'*32)
    host=SimpleNamespace(stable_job_members=lambda j:SimpleNamespace(close=lambda:None,identities=(offer.player,),_closed=False),
        bind_exact=lambda *a:binding,identity_from_handle=lambda *a:offer.player,
        wait_process=lambda *a:False,job_limit_flags=lambda *a:0,is_process_in_job=lambda *a:True,
        _require_capture_binding=lambda *a:None,
        _api=SimpleNamespace(input_desktop_identity=lambda:(1,2,'test','desktop')))
    def capture(b,before_capture):
        before_capture(); count[0]+=1
        frame=np.zeros((600,800,4),np.uint8)
        frame[10:12,10:12,:3]=50; frame[20:22,20:22,:3]=100
        if fault=='unsupported': frame[:]=0
        if fault=='revoked': active[0]=False
        return 800,600,frame.tobytes()
    host.capture_bgra=capture
    configuration=object.__new__(m.CalibrationConfiguration)
    configuration.scope=scope; configuration.run=run; configuration.project=tmp_path
    configuration.spec=m.CalibrationSpec.decode(raw,scope); configuration.expected_title='Synthetic'
    configuration.approval_current=lambda r:active[0] and r==run
    original=m.NarrowEvidenceSink
    def write(p,r,payload):
        wrote.append(payload)
        if fault=='changed-window': host._require_capture_binding=lambda *a:(_ for _ in ()).throw(RuntimeError('changed'))
        return b'bad' if fault=='readback' else payload
    monkeypatch.setattr(m,'NarrowEvidenceSink',lambda p,s:original(p,s,write=write))
    sources=configuration.worker_sources(channel,host)
    sources.require_context(context)
    if fault in ('revoked','readback','changed-window'):
        with pytest.raises(RuntimeError): sources.observe(context,SimpleNamespace(active=lambda:True))
    else:
        result=sources.observe(context,SimpleNamespace(active=lambda:True))
        assert result.status==('NEEDS_NAVIGATION_INPUT' if fault else 'CAPTURED')
        assert len(wrote)==(0 if fault else 1)
        if not fault: assert result.evidence_digest==digest_v3(wrote[0]) and count[0]==4


def test_observer_clock_brackets_binding_callbacks():
    m=module(); now=[100]
    class View:
        binding=object()
        def _bound(self): now[0]+=50
    raw=canonical_v3(specification())
    observer=m.NativeCalibrationObserver(m.CalibrationSpec.decode(raw,scope_for(raw)),View(),None,
        current=lambda:True,clock_ns=lambda:now[0])
    assert observer._check()==150 and observer._last==150


def test_initial_source_settling_is_read_only_and_bounded():
    m=module(); raw=canonical_v3(specification()); scope=scope_for(raw)
    from clash_rush_rebuild.lifecycle import ProcessIdentity,PlayerBinding
    identity=ProcessIdentity(1,2); now=[100_000_000]; samples=[0]
    class View:
        offer=identity
        binding=PlayerBinding(identity,identity,3,4,800,600,'f'*32)
        def _bound(self): pass
        def capture_bgr(self):
            samples[0]+=1
            frame=np.zeros((600,800,3),np.uint8)
            if samples[0]>=2:
                frame[10:12,10:12]=50; frame[20:22,20:22]=100
            return frame
    view=View(); view.scope=scope
    observer=m.NativeCalibrationObserver(m.CalibrationSpec.decode(raw,scope),view,None,
        current=lambda:True,clock_ns=lambda:now[0],wait=lambda _:now.__setitem__(0,now[0]+100_000_000))
    assert observer.window_geometry(identity).supported_visible is True
    assert samples[0]==2


def test_narrow_sink_closed_manifest_readback_and_exclusive_record(tmp_path):
    m=module()
    assert hasattr(m,'NarrowEvidenceSink'), 'production protected sink missing'
    raw=canonical_v3(specification()); scope=scope_for(raw)
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    from clash_rush_rebuild.mvp_calibration import WindowObservation
    identity=ProcessIdentity(1,2)
    # A real dataclass offer is unnecessary for this file-port test; the sink
    # binds canonical offer data rather than interpreting native handles.
    offer=identity
    window=WindowObservation(identity,3,4,800,600,True,identity,'f'*32)
    recorded={}
    def write(project,relative,payload):
        assert relative.startswith('private/calibration/') and relative.endswith('.json')
        assert relative not in recorded
        recorded[relative]=payload
        return payload
    sink=m.NarrowEvidenceSink(tmp_path,m.CalibrationSpec.decode(raw,scope),write=write)
    crops={'HOME_ANCHOR':bytes([50])*12,'SOURCE_ANCHOR':bytes([100])*12}
    digest=sink.persist(scope,offer,window,crops,100)
    payload=next(iter(recorded.values()))
    assert digest==digest_v3(payload)
    data=json.loads(payload)
    assert data['scope_digest']==scope.digest and set(data['crops'])==set(crops)
    with pytest.raises(RuntimeError): sink.persist(scope,offer,window,crops,100)
    sink=m.NarrowEvidenceSink(tmp_path,m.CalibrationSpec.decode(raw,scope),write=lambda *a:b'changed')
    with pytest.raises(RuntimeError): sink.persist(scope,offer,window,crops,100)
    sink=m.NarrowEvidenceSink(tmp_path,m.CalibrationSpec.decode(raw,scope),write=write)
    with pytest.raises(RuntimeError): sink.persist(scope,offer,window,{**crops,'FULL_FRAME':b'x'},100)
