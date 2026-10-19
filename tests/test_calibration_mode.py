"""Closed calibration mode: synthetic evidence, never game/native acceptance."""
import inspect
import importlib
from dataclasses import asdict, replace
from hashlib import sha256
import pytest
from test_policy_process_topology import InertHost, prepare, ack_for
from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology


class CalibrationHost(InertHost):
    def set_kill_on_close(self, job):
        self.calls.append(('flags', job, 0x2000))
        self.flags[job] = 0x2000


def calibration_topology(host):
    # Baseline fallback exposes existing wrong behavior, not a missing symbol.
    kw = {'purpose': 'CALIBRATION_READ_ONLY'} if 'purpose' in inspect.signature(ParentProcessTopology).parameters else {}
    return ParentProcessTopology(host, run_nonce='a'*32, generation=1, **kw)


def test_calibration_contains_worker_only_and_preserves_gameplay_default():
    host = CalibrationHost()
    topology = calibration_topology(host)
    offer = prepare(topology)
    assert host.flags == {'job0': 0x2000, 'job1': 0}
    topology.start_worker()
    topology.accept_worker_ack(ack_for(offer))
    topology.dispose()
    assert ('close', 'job0') in host.calls and ('close', 'job1') in host.calls
    assert not [c for c in host.calls if c[0] == 'kill']
    host = CalibrationHost()
    prepare(ParentProcessTopology(host, run_nonce='b'*32, generation=1))
    assert host.flags == {'job0': 0, 'job1': 0}


def test_calibration_player_exclusion_precedes_assignment():
    host = CalibrationHost()
    original = host.assign_to_job
    def assign(job, process):
        if process == 11:
            host.jobs['job0'].append(process)
        original(job, process)
    host.assign_to_job = assign
    topology = calibration_topology(host)
    with pytest.raises(RuntimeError):
        prepare(topology)
    assert not [c for c in host.calls if c[0] == 'resume']


def test_player_exclusion_is_queried_before_assignment():
    host=CalibrationHost()
    original=host.is_process_in_job
    def query(process,job):
        host.calls.append(('membership',job,process))
        return original(process,job)
    host.is_process_in_job=query
    prepare(calibration_topology(host))
    assert host.calls.index(('membership','job0',11)) < host.calls.index(('assign','job1',11))


def calibration_module():
    name = 'clash_rush_rebuild.mvp_calibration'
    assert importlib.util.find_spec(name) is not None, 'closed calibration issuer/consumer is missing'
    return importlib.import_module(name)


def scope_for(run):
    m = calibration_module()
    return m.CalibrationScope(candidate_commit='a'*40, candidate_tree=run.source_tree,
        profile_digest=run.profile_digest, slot=0, instance_digest='b'*64,
        package='com.supercell.clashofclans', private_root='private/calibration',
        run_nonce=run.run_nonce, generation=run.generation,
        issued_ns=run.started_ns, expires_ns=run.deadline_ns)


def test_narrow_one_use_capability_rejects_replay_and_all_broad_operations():
    from test_policy_finalization_v3 import binding_v3
    m = calibration_module()
    scope = scope_for(binding_v3())
    issuer = m.CalibrationIssuer(scope, current=lambda: True, clock_ns=lambda: scope.issued_ns)
    cap = issuer.issue()
    assert cap.scope == scope and cap.issuer == 'P_CALIBRATION_V1'
    for operation in ('input', 'click', 'key', 'touch', 'adb', 'shell', 'native', '*',
                      'package_install', 'package_update', 'terminate', 'cleanup', 'return_home'):
        with pytest.raises(RuntimeError):
            issuer.consume(cap, scope=scope, operations=(operation,))
    lease = issuer.consume(cap, scope=scope, operations=m.OPERATIONS)
    assert lease.check() is True
    with pytest.raises(RuntimeError): issuer.consume(cap, scope=scope, operations=m.OPERATIONS)
    issuer.revoke()
    with pytest.raises(RuntimeError): lease.check()


@pytest.mark.parametrize('field,value', [('slot',1), ('instance_digest','c'*64),
    ('candidate_commit','c'*40), ('candidate_tree','c'*40), ('profile_digest','e'*64),
    ('run_nonce','c'*32), ('generation',2), ('private_root','private/other')])
def test_capability_exact_scope_mismatch_denied(field,value):
    from test_policy_finalization_v3 import binding_v3
    m = calibration_module(); scope = scope_for(binding_v3())
    issuer = m.CalibrationIssuer(scope,current=lambda:True,clock_ns=lambda:scope.issued_ns)
    with pytest.raises(RuntimeError):
        issuer.consume(issuer.issue(),scope=replace(scope,**{field:value}),operations=m.OPERATIONS)


@pytest.mark.parametrize('fault', ['expiry','backwards','revoked','missing','forged','malformed'])
def test_capability_lifetime_and_issuer_binding(fault):
    from test_policy_finalization_v3 import binding_v3
    m=calibration_module(); scope=scope_for(binding_v3()); now=[scope.issued_ns]
    issuer=m.CalibrationIssuer(scope,current=lambda:fault!='revoked',clock_ns=lambda:now[0])
    if fault=='revoked':
        with pytest.raises(RuntimeError): issuer.issue()
        return
    cap=issuer.issue()
    if fault=='expiry': now[0]=scope.expires_ns
    if fault=='backwards': now[0]=scope.issued_ns-1
    if fault=='missing': cap=None
    if fault=='forged': cap=replace(cap,nonce='f'*32)
    if fault=='malformed': cap=object()
    with pytest.raises(RuntimeError): issuer.consume(cap,scope=scope,operations=m.OPERATIONS)


@pytest.mark.parametrize('field,value', [('package','wrong.package'),('private_root','../outside'),
    ('private_root','C:/outside'),('slot',True),('instance_digest','bad')])
def test_scope_rejects_malformed_or_unapproved_values(field,value):
    from test_policy_finalization_v3 import binding_v3
    with pytest.raises(RuntimeError): replace(scope_for(binding_v3()),**{field:value})


def test_calibration_reservation_is_durable_and_cannot_be_gameplay(tmp_path):
    from test_policy_parent_session import compose, prepare as prepare_session
    from test_policy_finalization_v3 import snapshot, stopped, next_binding
    m=calibration_module()
    s,_,state,clock,mutex=compose(tmp_path)
    scope=scope_for(s.run)
    s.run=replace(s.run,binding_digest=scope.digest,provider_digest=scope.digest)
    host=CalibrationHost()
    s.topology=ParentProcessTopology(host,run_nonce=s.run.run_nonce,generation=1,purpose=m.PURPOSE)
    s.calibration_scope=scope
    original=host.create_policy_suspended
    def checked(*args):
        assert s.authority.calibration_scope_v3(s.run)==scope
        return original(*args)
    host.create_policy_suspended=checked
    offer=prepare_session(s,mutex)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer),mutex=mutex)
    with pytest.raises(RuntimeError): s.authority.record_snapshot_v3(snapshot(s.run))
    with pytest.raises(RuntimeError): s.finalize(mutex=mutex,outcome='SUCCESS',reason='PRE_D_COMPLETE')
    with pytest.raises(RuntimeError): s.authority.reserve_v3(next_binding(s.run),writer_epoch=1,proof=stopped())
    s.dispose(mutex=mutex)
    assert type(state.state).__name__=='Active'
    s.authority.close()


def test_bootstrap_issues_calibration_from_prepared_descriptor(tmp_path):
    from types import SimpleNamespace
    from test_policy_parent_session import compose
    from test_policy_finalization_v3 import stopped
    from clash_rush_rebuild.mvp_policy_ingress import PreparedRunV3
    from clash_rush_rebuild.mvp_policy_run import bootstrap_parent
    m=calibration_module()
    assert 'calibration_scope' in inspect.signature(PreparedRunV3).parameters
    original,_,state,clock,mutex=compose(tmp_path)
    scope=scope_for(original.run)
    run=replace(original.run,binding_digest=scope.digest,provider_digest=scope.digest)
    prepared=PreparedRunV3(original.authority,run,stopped(),mutex,0,1,
                           original.runner_manifest_bytes,calibration_scope=scope)
    host=CalibrationHost()
    published=[]
    def register_worker(session):
        assert not [c for c in host.calls if c[0]=='resume']
        published.append(session.run.run_nonce)
    source=SimpleNamespace(approval_current=lambda r:r==run, expected_title='Synthetic',
                           retirement_evidence=lambda *a:None, captured_window=lambda *a:None,
                           register_worker=register_worker)
    s,c=bootstrap_parent(prepared,state=state,host=host,
        listener=SimpleNamespace(parent=host.identities[99]),sources=source,
        worker_executable='worker',worker_command='worker',player_executable='player',
        player_command='player',clock=lambda:1.)
    assert published == [run.run_nonce]
    assert c.calibration.scope==scope and s.calibration_issuer._used
    assert host.flags=={'job0':0x2000,'job1':0}
    s.dispose(mutex=mutex); s.authority.close()


def test_downgraded_calibration_context_denies_before_gameplay_import(tmp_path,monkeypatch):
    import builtins
    from types import SimpleNamespace
    from clash_rush_rebuild.mvp_policy_run import execute_worker_session
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    imported=[]; original=builtins.__import__
    def guarded(name,*args,**kw):
        if name in ('mvp_deployment_integration','mvp_local_native','mvp_deployment_worker'):
            imported.append(name)
            raise RuntimeError('gameplay import reached')
        return original(name,*args,**kw)
    monkeypatch.setattr(builtins,'__import__',guarded)
    with pytest.raises(RuntimeError):
        execute_worker_session(SimpleNamespace(context=replace(c,calibration=None)),
            authority=s.authority,host=host,sources=None)
    assert imported==[]
    s.dispose(mutex=mutex); s.authority.close()


@pytest.mark.parametrize('fault',['worker-crash','parent-loss','capture-before-persist','readback'])
def test_calibration_failure_keeps_active_and_no_completion(tmp_path,fault):
    from contextlib import contextmanager
    from test_policy_finalization_v3 import next_binding,stopped
    from clash_rush_rebuild.mvp_policy_run import finish_parent_session
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    s.accept_worker(ack_for(c.offer),mutex=mutex)
    @contextmanager
    def owned():yield mutex
    class BrokenChannel:
        context=c
        def pending(self):raise RuntimeError('inert worker loss')
        def close(self):pass
    if fault=='parent-loss':
        s.dispose(mutex=mutex)
    else:
        if fault=='readback':s.authority._readback_v3=lambda *a:None
        with pytest.raises(RuntimeError):
            finish_parent_session(s,BrokenChannel(),mutex_factory=owned,clock=lambda:1.)
    assert type(state.state).__name__=='Active'
    assert s.authority.calibration_observation_v3(s.run) is None
    outcome=s.authority.outcome_v3(1)
    assert outcome is None or outcome.outcome!='SUCCESS'
    with pytest.raises(RuntimeError):s.authority.reserve_v3(next_binding(s.run),writer_epoch=1,proof=stopped())
    assert not [call for call in host.calls if call[:2]==('kill','job1')]
    s.authority.close()


def test_calibration_command_purpose_and_imports_are_closed():
    import subprocess,sys
    from clash_rush_rebuild.mvp_policy_run import run_policy_command
    assert 'purpose' in inspect.signature(run_policy_command).parameters
    code="import sys; import clash_rush_rebuild.mvp_policy_run; assert not any(n in sys.modules for n in ['clash_rush_rebuild.mvp_deployment_input','clash_rush_rebuild.mvp_deployment_integration','clash_rush_rebuild.mvp_local_native','clash_rush_rebuild.mvp_deployment_worker'])"
    subprocess.run([sys.executable,'-c',code],check=True)


def test_worker_process_surface_has_no_control_methods():
    m=calibration_module()
    assert hasattr(m,'CalibrationProcessQueries'), 'worker still receives broad lifecycle host'
    host=CalibrationHost()
    host.handle_is_inheritable=lambda h:False
    queries=m.CalibrationProcessQueries.from_host(host)
    for name in ('terminate_job','terminate_retained_process','resume_thread','assign_to_job',
                 'create_policy_suspended','set_kill_on_close','duplicate_query_wait_to_worker'):
        assert not hasattr(queries,name)
    assert queries.current_process_id()==99


def test_calibration_ingress_uses_spec_not_fabricated_completed_profile(tmp_path,monkeypatch):
    from test_policy_ingress_command import database,fixture_sources,approve
    from test_policy_finalization_v3 import binding_v3
    from clash_rush_rebuild.mvp_session_authority import canonical_v3,digest_v3
    database(tmp_path)
    policy,api,port=fixture_sources(tmp_path,monkeypatch)
    policy.migrate_stopped(backup_name='backup.sqlite3')
    deployment=tmp_path/'private/deployment'
    spec=canonical_v3({'schema':1,'requested':'already_visible_narrow_crops'})
    (deployment/'calibration-spec.json').write_bytes(spec)
    (tmp_path/'private/calibration').mkdir()
    run=replace(binding_v3(),source_tree='a'*40,profile_digest=digest_v3(spec),
        started_ns=99_000_000_000,deadline_ns=110_000_000_000,
        runner_manifest_digest=digest_v3((deployment/'runner-manifest.json').read_bytes()))
    scope=scope_for(run)
    run=replace(run,binding_digest=scope.digest,provider_digest=scope.digest)
    (deployment/'calibration.json').write_bytes(canonical_v3(asdict(scope)))
    request=asdict(run);del request['approval_digest']
    approve(tmp_path,port,purpose='CALIBRATION_READ_ONLY',run=request,
        profile_digest=run.profile_digest,provider_digest=run.provider_digest)
    (deployment/'profile.json').unlink();(deployment/'provider.json').unlink()
    with policy.prepare_run(purpose='CALIBRATION_READ_ONLY') as prepared:
        assert prepared.calibration_scope==scope
        with pytest.raises(RuntimeError): prepared.require_runtime_activation()
    assert not [c for c in api.calls if c[0]=='process:create']


@pytest.mark.parametrize('fault', [None,'package','instance','pid-reuse','window','geometry','navigation','missing'])
def test_read_only_observation_interface_rejects_bad_binding_without_input(tmp_path,fault):
    from types import SimpleNamespace
    m=calibration_module()
    assert hasattr(m,'CalibrationSources'), 'narrow observer composition absent'
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    calls=[]
    identity=c.offer.player
    package=m.PackageObservation(c.calibration.scope.instance_digest,
        'com.supercell.clashofclans',identity,True)
    if fault=='package': package=replace(package,package='other.app')
    if fault=='instance': package=replace(package,instance_digest='f'*64)
    if fault=='pid-reuse': package=replace(package,player=replace(identity,creation_time_100ns=1))
    if fault=='missing': package=None
    class Observer:
        def package_query(self,scope,offer):calls.append('package'); return package
        def window_geometry(self,offer):
            calls.append('window')
            return m.WindowObservation(identity if fault!='window' else replace(identity,pid=33),
                12,13,0 if fault=='geometry' else 1920,1080,fault!='navigation')
        def capture_private_evidence(self,scope,offer,window):
            calls.append('capture'); return 'e'*64
    sources=m.CalibrationSources(c.calibration.scope,current=lambda:True,observer=Observer(),
        clock_ns=lambda:1_000_000_000,package_metadata=package)
    lease=SimpleNamespace(active=lambda:True)
    if fault in (None,'navigation','missing'):
        result=sources.observe(c,lease)
        assert result.status==('NEEDS_NAVIGATION_INPUT' if fault=='navigation' else 'CAPTURED')
        assert ('capture' in calls)==(fault!='navigation')
    else:
        with pytest.raises(RuntimeError): sources.observe(c,lease)
        assert 'capture' not in calls
    with pytest.raises(RuntimeError): m.CalibrationSources(c.calibration.scope,current=lambda:True)
    s.dispose(mutex=mutex);s.authority.close()


def calibrated_session(tmp_path):
    from test_policy_parent_session import compose, prepare as prepare_session
    from clash_rush_rebuild.mvp_policy_channel import PolicyContext
    m=calibration_module()
    s,_,state,clock,mutex=compose(tmp_path)
    scope=scope_for(s.run)
    s.run=replace(s.run,binding_digest=scope.digest,provider_digest=scope.digest)
    host=CalibrationHost()
    s.topology=ParentProcessTopology(host,run_nonce=s.run.run_nonce,generation=1,purpose=m.PURPOSE)
    s.calibration_scope=scope
    offer=prepare_session(s,mutex)
    issuer=m.CalibrationIssuer(scope,current=lambda:True,clock_ns=lambda:1_000_000_000)
    cap=issuer.issue(); issuer.consume(cap,scope=scope,operations=m.OPERATIONS)
    s.calibration_issuer=issuer
    assert 'calibration' in inspect.signature(PolicyContext).parameters, 'calibration protocol dispatch absent'
    context=PolicyContext(s.run,1,offer,s.runner_manifest_bytes,calibration=cap)
    return s,host,state,clock,mutex,context


def test_closed_codec_calibration_cannot_send_gameplay_outcome(tmp_path):
    from clash_rush_rebuild.mvp_policy_channel import PolicyCodec,PlayerReady,OutcomeNotice
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    p,w=PolicyCodec('P'),PolicyCodec('W')
    assert w.decode(p.encode(c))==c
    p.decode(w.encode(ack_for(c.offer))); w.decode(p.encode(PlayerReady(c.offer)))
    with pytest.raises(RuntimeError): w.encode(OutcomeNotice('SUCCESS','PRE_D_COMPLETE'))
    s.dispose(mutex=mutex); s.authority.close()


@pytest.mark.parametrize('status',['CAPTURED','NEEDS_NAVIGATION_INPUT','SOURCE_UNAVAILABLE'])
def test_observer_worker_dispatch_and_parent_durable_provisional_receipt(tmp_path,monkeypatch,status):
    from contextlib import contextmanager
    from clash_rush_rebuild.mvp_policy_run import execute_worker_session,finish_parent_session
    from clash_rush_rebuild.mvp_policy_channel import PolicyCodec,PlayerReady,Revoke
    from clash_rush_rebuild.mvp_process_topology import ReleaseStageReceipt
    m=calibration_module()
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    s.start_worker(mutex=mutex)
    p,w=PolicyCodec('P'),PolicyCodec('W'); w.decode(p.encode(c))
    @contextmanager
    def owned(): yield mutex
    # Only handoff's low-level OS identity leaves are inert; durable consumption,
    # worker dispatch, wire validation and parent finalization remain real.
    host.identities[c.offer.remote_player_handle]=c.offer.player
    host.flags[c.offer.remote_player_job_query]=0
    host.jobs[c.offer.remote_player_job_query]=[c.offer.remote_player_handle]
    host.handle_is_inheritable=lambda h:False
    host.current_process_id=lambda:c.offer.worker.pid
    host.current_process_handle=host.current_process_id
    events=[]; outgoing=[]
    result=m.CalibrationObservation(scope_digest=c.calibration.scope.digest,
        offer_digest=sha256(__import__('clash_rush_rebuild.mvp_session_authority',fromlist=['canonical_v3']).canonical_v3(asdict(c.offer))).hexdigest(),
        status=status, observed_ns=1_000_000_000,
        evidence_digest='e'*64 if status=='CAPTURED' else None)
    class Sources:
        def require_context(self,context): assert context==c
        def observe(self,context,lease):
            assert lease.active(); events.append('observe'); return result
    class WorkerChannel:
        context=c; no_more_gameplay=False; _closed=False
        def send(self,message):
            outgoing.append(p.decode(w.encode(message)))
        def receive(self,**kw):
            if not w.ready: return w.decode(p.encode(PlayerReady(c.offer)))
            return w.decode(p.encode(Revoke('d'*32)))
        def close(self): self._closed=True
    wc=WorkerChannel()
    assert execute_worker_session(wc,authority=s.authority,host=host,sources=Sources(),
        mutex_factory=owned,clock=lambda:1.,wait=lambda _:setattr(wc,'_closed',True))==result
    assert events==['observe']
    assert [type(x).__name__ for x in outgoing]==['HandoffAck','CalibrationObservation','ReleaseStageReceipt']
    host.current_process_id=lambda:99; host.current_process_handle=host.current_process_id
    s.accept_worker(outgoing[0],mutex=mutex)
    # A second real codec pair represents the parent's receive queue without
    # replaying durable consumption or invoking a gameplay executor.
    class ParentChannel:
        context=c; no_more_gameplay=False
        def __init__(self):self.queue=[result]
        def pending(self):return bool(self.queue)
        def receive(self,**kw):return self.queue.pop(0)
        def send(self,message):self.queue.append(ReleaseStageReceipt(c.offer,message.token,'NOT_REQUIRED',(c.offer.worker,),True))
        def close(self):pass
    host.wait_process=lambda h,t:bool(t)
    host.job_active_count=lambda j:0
    if status == 'CAPTURED':
        s.expected_title='Synthetic'
        s.calibration_evidence=lambda *a:None  # no independently readable sealed file
        with pytest.raises(RuntimeError):
            finish_parent_session(s,ParentChannel(),mutex_factory=owned,clock=lambda:1.)
    else:
        receipt=finish_parent_session(s,ParentChannel(),mutex_factory=owned,clock=lambda:1.)
        assert receipt==result
    assert s.authority.calibration_observation_v3(s.run)==result
    assert s.authority.outcome_v3(1).outcome!='SUCCESS'
    assert type(state.state).__name__=='Active' and s.authority.closure_v3(1).closed==0
    assert not [call for call in host.calls if call[:2]==('kill','job1')]
    s.authority.record_calibration_observation_v3(s.run,result,writer_epoch=1)
    with pytest.raises(RuntimeError):
        s.authority.record_calibration_observation_v3(s.run,replace(result,observed_ns=2_000_000_000),writer_epoch=1)
    s.authority.close()
