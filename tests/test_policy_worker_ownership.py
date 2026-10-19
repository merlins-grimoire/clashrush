"""Positive worker ownership, not interpreter/image/paternity heuristics."""
from dataclasses import asdict
import importlib
from types import SimpleNamespace
import pytest
from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.mvp_installed_build import ProcessObservation


def module():
    name='clash_rush_rebuild.mvp_worker_ownership'
    assert importlib.util.find_spec(name), 'positive worker ownership source missing'
    return importlib.import_module(name)


@pytest.mark.parametrize('image,args', [('python.exe',('python.exe','unrelated.py')),
    ('python.exe',('python.exe','-m','hermes_cli.main')),
    ('editor.exe',('editor.exe',)), ('python.exe',('python.exe','-m','pytest'))])
def test_unattested_process_is_ambiguous_regardless_of_name(image,args):
    m=module()
    observation=ProcessObservation(ProcessIdentity(101,202),image,args)
    assert m.classify(observation, record=None, current=None) == 'AMBIGUOUS'


def test_scan_is_handle_backed_closes_on_ambiguity_and_reads_records():
    from contextlib import contextmanager
    m=module(); r=record()
    own=ProcessObservation(ProcessIdentity(5,6),'controller.exe',('controller.exe',))
    other=ProcessObservation(ProcessIdentity(101,202),'python.exe',('python.exe','ordinary.py'))
    closed=[]
    @contextmanager
    def observe(pid):
        try: yield own if pid==5 else other
        finally: closed.append(pid)
    assert hasattr(m,'require_worker_absence'), 'production absence join missing'
    with pytest.raises(RuntimeError, match='AMBIGUOUS'):
        m.require_worker_absence((5,101),self_identity=own.identity,observe=observe,
                                load_record=lambda p:None)
    assert closed == [5,101]
    other=ProcessObservation(ProcessIdentity(**r['worker']),r['executable'],tuple(r['argv']))
    with pytest.raises(RuntimeError):
        m.require_worker_absence((5,101),self_identity=own.identity,observe=observe,
                                load_record=lambda p:r)


def test_native_metadata_reader_uses_retained_handle_twice_and_closes(monkeypatch):
    m=module(); from clash_rush_rebuild import mvp_installed_build as build
    p=ProcessObservation(ProcessIdentity(101,202),'python.exe',('python.exe','ordinary.py'))
    calls=[]
    monkeypatch.setattr(build._NativeLeaves,'open_player',lambda self,pid:(calls.append(('open',pid)) or 55))
    monkeypatch.setattr(build._NativeLeaves,'observe',lambda self,h:(calls.append(('read',h)) or p))
    monkeypatch.setattr(build._NativeLeaves,'close_player',lambda self,h:calls.append(('close',h)))
    assert hasattr(m,'observe_process'), 'native ownership metadata reader missing'
    with m.observe_process(101) as actual: assert actual==p
    assert calls==[('open',101),('read',55),('read',55),('read',55),('close',55)]


def test_publish_and_read_record_joins_durable_nonterminal_run(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from test_calibration_retirement import captured
    from clash_rush_rebuild import mvp_calibration_native as native
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild import mvp_deployment_profile as files
    m=module(); s,h,state,clock,mutex=captured(tmp_path)
    import sqlite3
    (tmp_path/'var').mkdir(exist_ok=True)
    with sqlite3.connect(tmp_path/'var/mvp-session.sqlite3') as target:
        s.authority._open().backup(target)
    args=('worker.exe','-m','clash_rush_rebuild.mvp_policy_run','--project-root',str(tmp_path),
          '--parent-pid','99','--parent-created',str(s.topology.offer.parent.creation_time_100ns),
          '--ownership-nonce',s.run.run_nonce)
    process=ProcessObservation(s.topology.offer.worker,'worker.exe',args)
    helper=ProcessObservation(ProcessIdentity(303,404),'helper.exe',('helper.exe',))
    h.identities[303]=helper.identity
    h.jobs[s.topology.worker_job].append(303)
    scanned=[]
    scanner=m.require_worker_absence
    def scan(*a,**kw):
        scanned.append(kw['owned_topology'])
        return scanner(*a,**kw)
    monkeypatch.setattr(m,'require_worker_absence',scan)
    @contextmanager
    def observe(pid):
        yield (helper if pid==303 else process if pid==process.identity.pid else
               ProcessObservation(s.topology.offer.parent,'parent.exe',('parent.exe',)))
    stored={}
    monkeypatch.setattr(native,'write_protected_record',lambda p,r,b:(stored.update({r:b}) or b))
    monkeypatch.setattr(ingress,'_require_private',lambda p:None)
    monkeypatch.setattr(files,'read_private',lambda p,r,n:stored[r])
    assert hasattr(m,'publish_worker'), 'durable ownership publication missing'
    monkeypatch.setattr(m,'observe_process',observe)
    try:
        m.publish_worker(tmp_path,s)
        assert scanned==[s.topology]
        r=m.load_record(process)
        assert r['binding_digest']==s.run.digest and r['worker']==asdict(process.identity)
        assert m.classify(process,record=r,current=None)=='CONFLICT'
    finally: s.authority.close()


def test_production_ingress_denies_unattested_python(tmp_path,monkeypatch):
    import os
    from contextlib import contextmanager
    from test_win32_primitives import FakeToolhelpApi
    from clash_rush_rebuild.mvp_policy_ingress import PolicyIngress
    m=module()
    @contextmanager
    def observe(pid):
        yield ProcessObservation(ProcessIdentity(pid,202),'python.exe',('python.exe','ordinary.py'))
    monkeypatch.setattr(m,'observe_process',observe)
    policy=PolicyIngress(tmp_path,process_api=FakeToolhelpApi(((os.getpid(),1,'python.exe'),(101,1,'python.exe'))))
    with pytest.raises(RuntimeError, match='AMBIGUOUS'):
        policy._absence()


@pytest.mark.parametrize('fault',['access','creation','disagreement'])
def test_metadata_failure_never_becomes_absence(fault,monkeypatch):
    m=module(); from clash_rush_rebuild import mvp_installed_build as build
    p=ProcessObservation(ProcessIdentity(101,202),'python.exe',('python.exe','ordinary.py'))
    calls=[]
    def opened(self,pid):
        if fault=='access': raise RuntimeError('denied')
        return 55
    def observed(self,h):
        calls.append('read')
        if fault=='creation': raise RuntimeError('missing creation')
        return p if len(calls)==1 else ProcessObservation(ProcessIdentity(101,203),p.image,p.argv)
    monkeypatch.setattr(build._NativeLeaves,'open_player',opened)
    monkeypatch.setattr(build._NativeLeaves,'observe',observed)
    monkeypatch.setattr(build._NativeLeaves,'close_player',lambda *a:calls.append('close'))
    with pytest.raises(RuntimeError):
        with m.observe_process(101): pytest.fail('ambiguous process exposed')
    if fault!='access': assert calls[-1]=='close'


def test_metadata_revalidated_after_record_read(monkeypatch):
    m=module(); from clash_rush_rebuild import mvp_installed_build as build
    p=ProcessObservation(ProcessIdentity(101,202),'python.exe',('python.exe','ordinary.py'))
    changed=[False]
    monkeypatch.setattr(build._NativeLeaves,'open_player',lambda *a:55)
    monkeypatch.setattr(build._NativeLeaves,'observe',lambda *a:
        ProcessObservation(ProcessIdentity(101,203),p.image,p.argv) if changed[0] else p)
    monkeypatch.setattr(build._NativeLeaves,'close_player',lambda *a:None)
    with pytest.raises(RuntimeError):
        with m.observe_process(101): changed[0]=True


def test_real_windows_unrelated_python_identity_and_metadata():
    import os, subprocess, sys
    if os.name!='nt': pytest.skip('Windows ownership proof')
    m=module()
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    try:
        with m.observe_process(child.pid) as process:
            assert process.identity.pid==child.pid and process.identity.creation_time_100ns>0
            assert m.classify(process,record=None,current=None)=='AMBIGUOUS'
    finally:
        child.terminate(); child.wait(timeout=10)


def record():
    return dict(schema=1,project='C:/synthetic/project',source_tree='a'*40,
        run_nonce='b'*32,generation=1,binding_digest='c'*64,
        parent=dict(pid=10,creation_time_100ns=20),worker=dict(pid=101,creation_time_100ns=202),
        executable='C:/synthetic/python.exe',
        argv=['C:/synthetic/python.exe','-m','clash_rush_rebuild.mvp_policy_run',
              '--project-root','C:/synthetic/project','--pipe','synthetic',
              '--parent-pid','10','--parent-created','20'],
        state='ACTIVE')


def test_second_verified_worker_conflicts_and_exact_current_worker_does_not():
    m=module(); r=record()
    p=ProcessObservation(ProcessIdentity(**r['worker']),r['executable'],tuple(r['argv']))
    assert m.classify(p,record=r,current=None)=='CONFLICT'
    assert m.classify(p,record=r,current=r)=='OWNED'


@pytest.mark.parametrize('fault',['reused_pid','missing_creation','different_executable',
    'different_command','terminal','unrecorded','different_candidate'])
def test_disagreement_remains_ambiguous(fault):
    m=module(); r=record()
    p=ProcessObservation(ProcessIdentity(**r['worker']),r['executable'],tuple(r['argv']))
    if fault=='reused_pid': r['worker']['creation_time_100ns']=999
    if fault=='missing_creation': r['worker']['creation_time_100ns']=None
    if fault=='different_executable': r['executable']='other.exe'
    if fault=='different_command': r['argv']=['other']
    if fault=='terminal': r['state']='CLOSED'
    if fault=='unrecorded': r=None
    if fault=='different_candidate': r['source_tree']='bad'
    assert m.classify(p,record=r,current=None)=='AMBIGUOUS'


def test_verified_current_helper_requires_exact_retained_job_membership():
    from test_policy_process_topology import InertHost, controller, prepare
    m=module(); r=record(); host=InertHost(); topology=controller(host); offer=prepare(topology)
    r.update(run_nonce=offer.run_nonce,generation=offer.generation,
             worker=asdict(offer.worker),parent=asdict(offer.parent))
    r['argv'][8]=str(offer.parent.pid); r['argv'][10]=str(offer.parent.creation_time_100ns)
    p=ProcessObservation(ProcessIdentity(303,404),'helper.exe',('helper.exe','clash-rush-helper'))
    host.identities[303]=p.identity; host.jobs[topology.worker_job].append(303)
    assert m.classify(p,record=r,current=r,owned_topology=topology)=='OWNED'
    host.identities[303]=ProcessIdentity(303,405)
    assert m.classify(p,record=r,current=r,owned_topology=topology)=='AMBIGUOUS'


def test_renamed_worker_without_package_name_still_requires_ownership():
    m=module(); r=record()
    r['executable']='C:/synthetic/renamed.exe'
    r['argv']=['C:/synthetic/renamed.exe','--project-root',r['project'],
               '--parent-pid','10','--parent-created','20','--ownership-nonce',r['run_nonce']]
    p=ProcessObservation(ProcessIdentity(**r['worker']),r['executable'],tuple(r['argv']))
    assert m.runner_association(p)
    assert m.classify(p,record=None,current=None)=='AMBIGUOUS'
    assert m.classify(p,record=r,current=None)=='CONFLICT'


@pytest.mark.parametrize('project',['C:/synthetic/project','C:/synthetic/other'])
def test_hostwide_conflict_includes_other_worktree_and_renamed_executable(project):
    m=module(); r=record(); r['project']=project; r['argv'][4]=project
    r['executable']=r['argv'][0]='C:/synthetic/renamed.exe'
    p=ProcessObservation(ProcessIdentity(**r['worker']),r['executable'],tuple(r['argv']))
    assert m.classify(p,record=r,current=None)=='CONFLICT'
