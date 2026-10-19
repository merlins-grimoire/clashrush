"""Synthetic subprocesses only; never invokes a bundled executable."""
from dataclasses import replace, FrozenInstanceError
import hashlib
import pytest
from clash_rush_rebuild import mvp_installed_build as m
from clash_rush_rebuild.mvp_calibration import CalibrationIssuer
from test_calibration_mode import scope_for
from test_policy_finalization_v3 import binding_v3
from test_policy_installed_closure import Leaves, CONF


def make(tmp_path, monkeypatch):
    assert hasattr(m, 'ParentPackageObserver'), 'parent-owned fixed observer absent'
    player = tmp_path / 'HD-Player.exe'; player.write_bytes(b'synthetic player')
    executable = tmp_path / 'HD-Adb.exe'; executable.write_bytes(b'synthetic client')
    monkeypatch.setattr(m, 'PLAYER', str(player))
    low = Leaves(m)
    low.conf = b'bst.enable_adb_access="1"\n' + CONF
    scope = replace(scope_for(binding_v3()), instance_digest=hashlib.sha256(b'Pie64_test').hexdigest())
    issuer = CalibrationIssuer(scope, current=lambda: True, clock_ns=lambda: scope.issued_ns)
    issuer.consume(issuer.issue(), scope=scope, operations=m.CALIBRATION_OPERATIONS)
    binding = m.BuildBinding(scope.run_nonce, scope.generation, low.player,
        scope.instance_digest, hashlib.sha256(low.conf).hexdigest())
    low.server_current = lambda executable, port: ('synthetic-server-identity', port)
    low.executable_metadata = lambda path: ('1.2.3.4', '1.2.3.4')
    outputs = ['127.0.0.1:5555\n', 'package:'+m.PACKAGE+'\n', '4321\n']
    def command(argv, timeout, limit, *, before_spawn):
        before_spawn()
        low.commands.append((argv, timeout, limit))
        return m.CommandResult(outputs[len(low.commands)-1], '', 0, True)
    low.fixed_command = command
    observer = m.ParentPackageObserver(issuer, binding, leaves=low)
    return observer, low, issuer, binding, outputs, executable


def test_fixed_parent_observation_requires_exact_installed_and_running_package(tmp_path, monkeypatch):
    observer, low, issuer, binding, outputs, executable = make(tmp_path, monkeypatch)
    fact = observer.observe()
    assert fact.running is True and fact.package == m.PACKAGE
    assert fact.player == binding.process_identity and fact.scope_digest == issuer.scope.digest
    assert fact.executable_digest == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert low.commands == [(list((str(executable), '-s', '127.0.0.1:5555') + tail), 5, 4096)
        for tail in [('get-serialno',), ('shell','pm','list','packages',m.PACKAGE), ('shell','pidof',m.PACKAGE)]]
    assert low.closed == ['player-handle']
    with pytest.raises(FrozenInstanceError): fact.running = False
    with pytest.raises(RuntimeError): observer.observe()


@pytest.mark.parametrize('index,value', [(0,'127.0.0.1:5556\n'), (0,'127.0.0.1:5555'),
    (1,''), (1,'package:com.supercell.clashofclans.beta\n'),
    (1,'package:'+m.PACKAGE+'\npackage:'+m.PACKAGE+'\n'),
    (1,'malformed\n'), (2,''), (2,'0\n'), (2,'12 13\n'), (2,'123\n123\n'),
    (2,'123x\n'), (2,'123'), (2,'1'*4097)])
def test_strict_package_and_running_grammar(tmp_path,monkeypatch,index,value):
    observer,low,issuer,_,outputs,_=make(tmp_path,monkeypatch)
    outputs[index]=value
    with pytest.raises(RuntimeError): observer.observe()
    assert len(low.commands)==index+1 and issuer._revoked


@pytest.mark.parametrize('cut', ['before','query','source','metadata','server','close'])
def test_revocation_callback_cuts_discard_result(tmp_path,monkeypatch,cut):
    observer,low,issuer,_,_,_=make(tmp_path,monkeypatch)
    if cut=='before': issuer.revoke()
    target={'query':'fixed_command','source':'read_configuration','metadata':'executable_metadata',
            'server':'server_current','close':'close_player'}.get(cut)
    if target:
        original=getattr(low,target)
        def revoke(*args, **kw):
            result=original(*args, **kw); issuer.revoke(); return result
        setattr(low,target,revoke)
    with pytest.raises(RuntimeError): observer.observe()
    assert len(low.commands) <= (3 if cut=='close' else 1)


@pytest.mark.parametrize('fault',['timeout','nonzero','stderr','oversize','incomplete','bool-exit'])
def test_command_failure_never_becomes_fact(tmp_path,monkeypatch,fault):
    observer,low,issuer,_,_,_=make(tmp_path,monkeypatch)
    def command(*args, **kw):
        low.commands.append(args)
        if fault=='timeout': raise TimeoutError('synthetic private diagnostic')
        return m.CommandResult('x'*4097 if fault=='oversize' else '127.0.0.1:5555\n',
            'private diagnostic' if fault=='stderr' else '',
            False if fault=='bool-exit' else 2 if fault=='nonzero' else 0, fault!='incomplete')
    low.fixed_command=command
    with pytest.raises(RuntimeError) as error: observer.observe()
    assert 'private diagnostic' not in str(error.value) and issuer._revoked
    assert len(low.commands)==1


@pytest.mark.parametrize('fault',['missing','ambiguous','changed','disabled','wrong-owner','reused-pid',
    'duplicate-endpoint','different-instance','stale','wrong-scope'])
def test_configuration_and_executable_binding_denials(tmp_path,monkeypatch,fault):
    observer,low,issuer,binding,_,exe=make(tmp_path,monkeypatch)
    if fault=='missing': exe.unlink()
    if fault=='ambiguous': (exe.parent/'adb.exe').write_bytes(b'other')
    if fault=='changed': exe.write_bytes(b'changed')
    if fault=='disabled':
        low.conf=low.conf.replace(b'access="1"',b'access="0"')
        low.wrong_owner=True  # Disabled settings take precedence over absent endpoint.
        observer._binding=replace(binding,configuration_digest=hashlib.sha256(low.conf).hexdigest())
    if fault=='wrong-owner': low.wrong_owner=True
    if fault=='reused-pid': low.pid_change=True
    if fault=='duplicate-endpoint':
        low.conf+=CONF.replace(b'Pie64_test',b'Pie64_other')
        observer._binding=replace(binding,configuration_digest=hashlib.sha256(low.conf).hexdigest())
    if fault=='different-instance': low.argv=(m.PLAYER,'--instance','Pie64_other')
    if fault=='stale': issuer._clock=lambda:issuer.scope.expires_ns
    if fault=='wrong-scope': issuer.scope=replace(issuer.scope,slot=1)
    reason = ('ADB_NOT_ENABLED_OWNER_SETTING_CHANGE_REQUIRED' if fault=='disabled' else
              'ADB_INSTANCE_BINDING_UNCERTAIN' if fault in ('wrong-owner','reused-pid','duplicate-endpoint','different-instance') else '.+')
    with pytest.raises(RuntimeError,match=reason):
        observer.observe()
    assert low.commands==[]


def test_no_global_path_fallback_or_arbitrary_commands(tmp_path,monkeypatch):
    observer,low,issuer,binding,_,exe=make(tmp_path,monkeypatch)
    monkeypatch.setenv('PATH',str(tmp_path/'untrusted'))
    import shutil
    monkeypatch.setattr(shutil,'which',lambda *a:pytest.fail('PATH consulted'))
    assert observer.observe().running
    assert tuple(k.name for k in m.PackageCommand)==('SERIAL','PACKAGE','RUNNING')
    with pytest.raises(ValueError): m.PackageCommand('shell input tap 1 1')
    assert not hasattr(observer,'command')


@pytest.mark.parametrize('fault',['success','stdout-limit','combined-limit','invalid-utf8','timeout'])
def test_owned_child_bounded_capture_contract(fault):
    from clash_rush_rebuild import diagnostic_child_job as d
    import io
    import subprocess
    from types import SimpleNamespace
    assert hasattr(d,'_bounded_communicate'), 'owned child has no bounded pipe capture'
    process=SimpleNamespace(stdout=io.BytesIO(b'x'*4097 if fault=='stdout-limit' else
        b'x'*3000 if fault=='combined-limit' else b'\xff' if fault=='invalid-utf8' else b'ok\n'),
        stderr=io.BytesIO(b'y'*2000 if fault=='combined-limit' else b''),
        wait=lambda timeout:0)
    if fault=='timeout':
        process.wait=lambda timeout:(_ for _ in ()).throw(subprocess.TimeoutExpired('synthetic',timeout))
    if fault in ('stdout-limit','combined-limit','timeout'):
        with pytest.raises((RuntimeError,subprocess.TimeoutExpired)): d._bounded_communicate(process,1,4096)
    else:
        out,err=d._bounded_communicate(process,1,4096)
        assert out==(b'\xff' if fault=='invalid-utf8' else b'ok\n') and err==b''


def test_parent_fact_uses_existing_ready_channel_and_worker_sources(tmp_path):
    import inspect
    from types import SimpleNamespace
    from test_calibration_mode import calibrated_session, ack_for
    from clash_rush_rebuild.mvp_calibration import ParentPackageFact, CalibrationSources, WindowObservation
    from clash_rush_rebuild.mvp_policy_channel import PlayerReady, PolicyCodec
    assert 'package_fact' in inspect.signature(PlayerReady).parameters, 'parent fact channel missing'
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    try:
        scope=c.calibration.scope
        fact=ParentPackageFact(scope.digest,c.offer.player,scope.instance_digest,scope.package,True,
            'a'*64,'b'*64,'c'*64,scope.issued_ns)
        p,w=PolicyCodec('P'),PolicyCodec('W')
        w.decode(p.encode(c)); p.decode(w.encode(ack_for(c.offer)))
        ready=w.decode(p.encode(PlayerReady(c.offer,fact)))
        assert ready.package_fact==fact and not hasattr(ready.package_fact,'endpoint')
        calls=[]
        observer=SimpleNamespace(window_geometry=lambda offer:WindowObservation(offer.player,12,13,1920,1080,True),
            capture_private_evidence=lambda *args:calls.append('capture') or 'f'*64)
        sources=CalibrationSources(scope,current=lambda:True,observer=observer,clock_ns=lambda:scope.issued_ns,
                                   parent_fact=ready.package_fact)
        assert hasattr(sources,'accept_parent_fact'), 'worker does not consume received parent fact'
        sources.accept_parent_fact(ready.package_fact,c)
        assert sources.observe(c,SimpleNamespace(active=lambda:True)).status=='CAPTURED'
        assert calls==['capture'] and not hasattr(sources,'transport')
    finally:
        s.dispose(mutex=mutex); s.authority.close()


def test_native_leaf_rejects_arbitrary_commands_and_unqualified_client(monkeypatch):
    from clash_rush_rebuild import diagnostic_child_job as d
    calls=[]
    def child(argv, **kw):
        assert kw['output_limit']==4096 and kw['timeout']==5
        assert 'PATH' not in kw['environment']
        assert callable(kw['before_spawn'])
        kw['before_spawn']()
        calls.append(argv)
        return d.DiagnosticChildOutcome(0,'ok\n','',True,True)
    monkeypatch.setattr(d,'run_owned_diagnostic_child',child)
    checked=[]
    assert 'before_spawn' in __import__('inspect').signature(m._ParentPackageLeaves.fixed_command).parameters
    with pytest.raises(RuntimeError, match='READ_ONLY_PACKAGE_TRANSPORT_FAILED'):
        m._ParentPackageLeaves.fixed_command([m.ADB,'-s','127.0.0.1:5555','get-serialno'],5,4096,
                                            before_spawn=lambda:checked.append(True))
    assert checked==[] and calls==[]
    for tail in [('shell','input','tap','1','1'),('shell','pm','clear',m.PACKAGE),
                 ('connect','127.0.0.1:5556'),('shell','echo','unsafe'),('push','x','y')]:
        with pytest.raises(RuntimeError):
            m._ParentPackageLeaves.fixed_command([m.ADB,'-s','127.0.0.1:5555',*tail],5,4096,
                                                before_spawn=lambda:None)
    assert calls==[]


def test_parent_accept_rejects_retired_provider_without_query(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from test_calibration_mode import calibrated_session, ack_for
    from clash_rush_rebuild.mvp_calibration import ParentPackageFact, CalibrationIssuer
    from clash_rush_rebuild.mvp_policy_run import accept_parent_worker
    import inspect
    assert 'package_binding' in inspect.signature(accept_parent_worker).parameters, 'parent composition missing'
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    scope=c.calibration.scope
    s.calibration_issuer=CalibrationIssuer(scope,current=lambda:True,clock_ns=lambda:scope.issued_ns)
    s.calibration_issuer.consume(s.calibration_issuer.issue(),scope=scope,operations=m.CALIBRATION_OPERATIONS)
    fact=ParentPackageFact(scope.digest,c.offer.player,scope.instance_digest,scope.package,True,
                          'a'*64,'b'*64,'c'*64,scope.issued_ns)
    calls=[]
    class Observer:
        def __init__(self,issuer,binding): assert issuer is s.calibration_issuer
        def observe(self):
            assert s.topology._state == 'RUNNING'
            calls.append('query'); return fact
    monkeypatch.setattr(m,'ParentPackageObserver',Observer)
    channel=SimpleNamespace(receive=lambda **kw:ack_for(c.offer),send=lambda ready:calls.append(ready),close=lambda:None)
    @contextmanager
    def held():yield mutex
    try:
        s.start_worker(mutex=mutex)
        s.authority.consume_v3(s.run,producer_epoch=c.producer_epoch,now_ns=scope.issued_ns)
        with pytest.raises(RuntimeError,match='STRICT_GUEST_BUILD_PROVIDER_DEFERRED_BY_OWNER'):
            accept_parent_worker(s,c,SimpleNamespace(accept=lambda **kw:channel),mutex_factory=held,
                clock=lambda:1.,package_binding=object())
        assert calls==[]
    finally:
        s.dispose(mutex=mutex); s.authority.close()


def test_real_windows_owned_bounded_child():
    import sys
    from clash_rush_rebuild.diagnostic_child_job import run_owned_diagnostic_child
    for code, success in [("print('synthetic')",True),
                          ("import os; os.write(1,b'x'*1000000)",False),
                          ("import time; time.sleep(30)",False),
                          ("import os; os.write(1,b'\\xff')",False)]:
        import subprocess
        check = ("import sys; from clash_rush_rebuild.diagnostic_child_job import run_owned_diagnostic_child; "
                 f"r=run_owned_diagnostic_child([sys.executable,'-c',{code!r}],timeout=1,output_limit=4096); "
                 "assert r.child_wait_completed is True and r.cleanup_succeeded is True; "
                 f"assert r.operational_failure is {not success!r}; "
                 + ("assert r.stdout=='synthetic\\n' and r.returncode==0" if success else
                    "assert r.stdout is None and r.stderr is None"))
        result=subprocess.run([sys.executable,'-c',check],capture_output=True,timeout=15)
        assert result.returncode==0 and result.stdout==b'' and result.stderr==b''


@pytest.mark.parametrize('field,value',[('slot',1),('candidate_commit','f'*40),
    ('candidate_tree','f'*40),('profile_digest','f'*64),('instance_digest','f'*64),
    ('run_nonce','f'*32),('generation',2)])
def test_scope_change_denies_transport_before_spawn(tmp_path,monkeypatch,field,value):
    observer,low,issuer,_,_,_=make(tmp_path,monkeypatch)
    issuer.scope=replace(issuer.scope,**{field:value})
    with pytest.raises(RuntimeError):observer.observe()
    assert low.commands==[]


def test_parent_fact_rejects_wrong_context_before_capture(tmp_path):
    import inspect
    from types import SimpleNamespace
    from test_calibration_mode import calibrated_session
    from clash_rush_rebuild.mvp_calibration import ParentPackageFact, CalibrationSources
    assert 'parent_fact' in inspect.signature(CalibrationSources).parameters, 'typed fact consumer missing'
    s,host,state,clock,mutex,c=calibrated_session(tmp_path)
    try:
        scope=c.calibration.scope
        fact=ParentPackageFact('f'*64,c.offer.player,scope.instance_digest,scope.package,True,
            'a'*64,'b'*64,'c'*64,scope.issued_ns)
        observer=SimpleNamespace(window_geometry=lambda *a:pytest.fail('window reached'),
            capture_private_evidence=lambda *a:pytest.fail('capture reached'))
        sources=CalibrationSources(scope,current=lambda:True,observer=observer,clock_ns=lambda:scope.issued_ns,
                                   parent_fact=fact)
        with pytest.raises(RuntimeError): sources.observe(c,SimpleNamespace(active=lambda:True))
    finally:
        s.dispose(mutex=mutex); s.authority.close()
