"""Fresh instance policy provisioning, not runtime/native acceptance."""
from pathlib import Path
import importlib
import pytest
import json
import os
from clash_rush_rebuild import mvp_policy_ingress as ingress
from clash_rush_rebuild.mvp_session_authority import canonical_v3
from test_win32_runtime import FakeRuntimeApi
from test_win32_primitives import FakeToolhelpApi
from test_lifecycle_state import RecordingFilePort


def setup_sources(root, monkeypatch):
    from canonical_fixture import install_fixture
    install_fixture(root, monkeypatch)
    module = importlib.import_module('clash_rush_rebuild.mvp_policy_setup')
    api = FakeRuntimeApi(); api.already_exists = True
    port = RecordingFilePort()
    policy = ingress.PolicyIngress(root, runtime_api=api,
        process_api=FakeToolhelpApi(((os.getpid(), 1, 'python.exe'),)), state_port=port)
    monkeypatch.setattr(module, '_seal_private_path', lambda *a: None)
    monkeypatch.setattr(ingress, '_read_path_security', lambda path: (
        'S-1-5-21-1234', 'D:P(A;;FA;;;SY)(A;;FA;;;S-1-5-21-1234)' if path.is_file()
        else 'D:P(A;;FA;;;SY)(A;OICIIO;GA;;;SY)(A;;FA;;;S-1-5-21-1234)(A;OICIIO;GA;;;S-1-5-21-1234)'))
    monkeypatch.setattr(ingress, '_read_input_state', lambda: (0,) * 256)
    def write(project, relative, data):
        with (project / relative).open('xb') as f: f.write(data)
        return (project / relative).read_bytes()
    monkeypatch.setattr(module, 'write_protected_record', write)
    slots = {'schema':2,'slots':[{'slot_id':f'slot-{i:03}', 'internal_name':f'Pie64_{i}',
        'display_label':f'Test {i}'} for i in range(5)]}
    registry = root / 'registration.json'; registry.write_bytes(canonical_v3(slots))
    conf = root / 'synthetic.conf'
    conf.write_text('\n'.join(f'bst.instance.Pie64_{i}.{k}="{v}"' for i in range(5)
        for k,v in [('display_name',f'Test {i}'),('fb_width',1280),('fb_height',720),('dpi',240)]))
    monkeypatch.setattr(module, 'CONFIG', conf)
    return module, policy, port, registry


def test_bootstrap_cli_composition_creates_only_stopped_instance_state(tmp_path, monkeypatch):
    module, policy, port, registry = setup_sources(tmp_path, monkeypatch)
    module.bootstrap(policy, registry=registry, team_ref='team-test', source_tree='a'*40,
        approve=True)
    authority = DurableSessionAuthority(tmp_path/'var/mvp-session.sqlite3', read_only=True)
    try:
        assert authority.status_v3().generations == ()
        assert authority._open().execute('SELECT player_tag_sha256 FROM configuration').fetchone()[0] is None
    finally: authority.close()
    assert json.loads((tmp_path/'private/slots.json').read_bytes())['schema'] == 2
    assert not (tmp_path/'var/approvals/policy-v3.json').exists()
    assert not (tmp_path/'var/.policy-setup.pending').exists()
    from clash_rush_rebuild.lifecycle_state import Ready, encode_state
    assert port.payload == encode_state(Ready(0))
    with pytest.raises(RuntimeError):
        module.bootstrap(policy, registry=registry, team_ref='team-test', source_tree='a'*40, approve=True)


@pytest.mark.parametrize('fault', ['no-approval','player','worker','held','existing','bad-registry'])
def test_bootstrap_denials_never_initialize(tmp_path, monkeypatch, fault):
    module, policy, port, registry = setup_sources(tmp_path, monkeypatch)
    if fault in ('player','worker'):
        policy._process_api = FakeToolhelpApi(((os.getpid(),1,'python.exe'),
            (123,1,'HD-Player.exe' if fault == 'player' else 'python.exe')))
        if fault == 'worker':
            from clash_rush_rebuild.mvp_canonical_runtime import open_canonical
            anchor = open_canonical(tmp_path).anchor
            anchor.path.write_bytes(canonical_v3(dict(anchor.load(),phase='PREPARING',run={},parent={})))
    if fault == 'held': monkeypatch.setattr(ingress,'_read_input_state',lambda:(1,)*256)
    if fault == 'existing':
        (tmp_path/'var').mkdir(); (tmp_path/'var/lifecycle-state.json').write_bytes(b'preserve')
    if fault == 'bad-registry': registry.write_bytes(b'{}')
    with pytest.raises((RuntimeError,ValueError)):
        module.bootstrap(policy, registry=registry, team_ref='team-test',source_tree='a'*40,
            approve=fault != 'no-approval')
    assert not (tmp_path/'var/mvp-session.sqlite3').exists()
    assert not (tmp_path/'private/slots.json').exists()

import pytest
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority, SessionAuthorityError


def test_issue_migration_approval_binds_real_stopped_sources(tmp_path, monkeypatch):
    from test_policy_ingress_command import database, fixture_sources
    module = importlib.import_module('clash_rush_rebuild.mvp_policy_setup')
    database(tmp_path)
    policy, _, port = fixture_sources(tmp_path, monkeypatch)
    (tmp_path/'var/approvals/policy-v3.json').unlink()
    def write(root, relative, data):
        with (root/relative).open('xb') as stream: stream.write(data)
        return (root/relative).read_bytes()
    monkeypatch.setattr(module,'write_protected_record',write)
    monkeypatch.setattr(module,'_replace_write_through',lambda src,dst:os.replace(src,dst))
    module.issue_approval(policy,purpose='MIGRATE_SCHEMA3',source_tree='a'*40,
        lifetime_seconds=100,approve=True)
    raw, payload = policy._approval('MIGRATE_SCHEMA3')
    assert raw['run'] is None and raw['profile_digest'] is None
    assert raw['issued_at'] == 100 and raw['expires_at'] == 200
    policy.migrate_stopped(backup_name='backup.sqlite3')
    reader=DurableSessionAuthority(policy.path,read_only=True)
    try: assert reader.status_v3().generations == ()
    finally: reader.close()


@pytest.mark.parametrize('fault',[None,'generation','slot','expired','host','unresolved'])
def test_calibration_issuer_composes_current_scope_without_gameplay(tmp_path, monkeypatch, fault):
    from dataclasses import replace, asdict
    from test_calibration_composition import specification, scope_for
    from test_policy_ingress_command import fixture_sources
    from clash_rush_rebuild import mvp_installed_build as installed
    from clash_rush_rebuild.mvp_calibration_native import HOST_PROFILE
    from clash_rush_rebuild.mvp_session_authority import digest_v3
    module, policy, port, registry = setup_sources(tmp_path, monkeypatch)
    module.bootstrap(policy,registry=registry,team_ref='team-test',source_tree='a'*40,approve=True)
    policy, _, port = fixture_sources(tmp_path,monkeypatch)
    (tmp_path/'var/approvals/policy-v3.json').unlink()
    monkeypatch.setattr(installed,'CONFIG',module.CONFIG)
    monkeypatch.setattr(module,'_replace_write_through',lambda src,dst:os.replace(src,dst))
    host=canonical_v3(dict(schema=1,host_profile_digest=HOST_PROFILE,
        configuration_digest=digest_v3(module.CONFIG.read_bytes()),source_tree='a'*40,
        native_size=[800,600],windows_dpi=96,observed_at=90,expires_at=200))
    d=specification(); d.update(slot_id='slot-000',instance_digest=digest_v3(b'Pie64_0'),
        slots_digest=digest_v3(registry.read_bytes()),configuration_digest=digest_v3(module.CONFIG.read_bytes()),
        host_witness_digest=digest_v3(host))
    spec=canonical_v3(d)
    scope=replace(scope_for(spec),instance_digest=d['instance_digest'],
        issued_ns=99_000_000_000,expires_ns=199_000_000_000)
    if fault=='generation': scope=replace(scope,generation=2)
    if fault=='slot': scope=replace(scope,slot=1)
    if fault=='expired': scope=replace(scope,expires_ns=100_000_000_000)
    if fault=='host': host=b'{}'
    (tmp_path/scope.private_root).mkdir(exist_ok=True)
    for name,data in [('calibration.json',canonical_v3(asdict(scope))),('calibration-spec.json',spec),('calibration-host.json',host)]:
        (tmp_path/'private/deployment'/name).write_bytes(data)
    if fault=='unresolved':
        # Durable missing closure, not a fabricated gameplay fact.
        from test_policy_finalization_v3 import binding_v3
        from clash_rush_rebuild.mvp_session_authority import StoppedProofV3
        a=DurableSessionAuthority(policy.path)
        try: a.reserve_v3(replace(binding_v3(),account_ref='slot-000'),writer_epoch=1,proof=StoppedProofV3(*(['a'*64]*8)))
        finally: a.close()
    if fault:
        with pytest.raises(RuntimeError):
            module.issue_approval(policy,purpose='CALIBRATION_READ_ONLY',source_tree='a'*40,approve=True)
        assert not (tmp_path/'var/approvals/policy-v3.json').exists()
        return
    module.issue_approval(policy,purpose='CALIBRATION_READ_ONLY',source_tree='a'*40,approve=True)
    with policy.prepare_run(purpose='CALIBRATION_READ_ONLY') as prepared:
        assert prepared.run.account_ref == 'slot-000'
        assert prepared.calibration_scope == scope
        assert prepared.authority.status_v3().generations == ()
        assert not prepared.authority._open().execute('SELECT 1 FROM fact_v3').fetchone()
        with pytest.raises(RuntimeError,match='ACTIVATION_DENIED'): prepared.require_runtime_activation()


@pytest.mark.parametrize('command', ['mvp-setup-instance','issue-policy-approval'])
def test_explicit_cli_missing_prerequisites_fail_before_native(tmp_path,monkeypatch,command):
    from clash_rush_rebuild import cli
    module=importlib.import_module('clash_rush_rebuild.mvp_policy_setup')
    calls=[]
    def native():
        calls.append('native'); raise AssertionError('native reached')
    monkeypatch.setattr(module,'NativeWin32Api',native)
    args=[command,'--project-root',str(tmp_path),'--source-tree','a'*40]
    if command=='mvp-setup-instance': args += ['--registry',str(tmp_path/'missing.json'),'--team-ref','team-test','--approve-instance-setup']
    else: args += ['--purpose','CALIBRATION_READ_ONLY','--approve-purpose']
    assert cli.main(args)==1
    assert calls==[] and not (tmp_path/'var').exists()


@pytest.mark.parametrize('fault',['purpose','approval','lifetime','active-approval','active-state','held','worker','manifest','readback'])
def test_issuer_denials_preserve_existing_approval(tmp_path,monkeypatch,fault):
    from test_policy_ingress_command import database, fixture_sources
    from clash_rush_rebuild.lifecycle_state import Active, encode_state
    module=importlib.import_module('clash_rush_rebuild.mvp_policy_setup')
    database(tmp_path); policy,_,port=fixture_sources(tmp_path,monkeypatch)
    target=tmp_path/'var/approvals/policy-v3.json'
    old=json.loads(target.read_bytes()); old.update(issued_at=1,expires_at=2)
    if fault=='active-approval': old.update(issued_at=99,expires_at=200)
    target.write_bytes(canonical_v3(old)); original=target.read_bytes()
    def write(root,relative,data):
        with (root/relative).open('xb') as f: f.write(data)
        return (root/relative).read_bytes()
    monkeypatch.setattr(module,'write_protected_record',write)
    monkeypatch.setattr(module,'_replace_write_through',lambda src,dst:os.replace(src,dst))
    if fault=='active-state': port.payload=encode_state(Active(0,'2'*32,None))
    if fault=='held': monkeypatch.setattr(ingress,'_read_input_state',lambda:(1,)*256)
    if fault=='worker':
        from clash_rush_rebuild.mvp_canonical_runtime import open_canonical
        anchor = open_canonical(tmp_path).anchor
        anchor.path.write_bytes(canonical_v3(dict(anchor.load(),phase='PREPARING',run={},parent={})))
    if fault=='manifest': (tmp_path/'private/deployment/runner-manifest.json').write_bytes(b'{}')
    if fault=='readback':
        monkeypatch.setattr(module,'_replace_write_through',lambda *a:None)
    with pytest.raises(RuntimeError):
        module.issue_approval(policy,purpose='RUN_V3' if fault=='purpose' else 'MIGRATE_SCHEMA3',
            source_tree='a'*40,lifetime_seconds=301 if fault=='lifetime' else 100,
            approve=fault!='approval')
    assert target.read_bytes()==original


@pytest.mark.parametrize('fault',['legacy','commit'])
def test_fresh_authority_does_not_rewrite_history_on_failure(tmp_path,monkeypatch,fault):
    from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
    a=DurableSessionAuthority(tmp_path/'session.sqlite3')
    try:
        if fault=='legacy': a.setup(MvpConfiguration('team','account','slot-0','a'*64))
        before='\n'.join(a._open().iterdump())
        if fault=='commit':
            def fail(): raise RuntimeError('commit failed')
            monkeypatch.setattr(a,'_commit_v3',fail)
        with pytest.raises(RuntimeError): a.setup_instance_v3(team_ref='team',slot_id='slot-001',instance_ref='slot-0')
        assert '\n'.join(a._open().iterdump())==before
    finally:a.close()


def test_pending_setup_blocks_existing_policy_ingress(tmp_path, monkeypatch):
    from test_policy_ingress_command import database, fixture_sources
    database(tmp_path)
    policy, _, _ = fixture_sources(tmp_path, monkeypatch)
    (tmp_path/'var/.policy-setup.pending').write_bytes(b'incomplete')
    with pytest.raises(RuntimeError, match='SETUP_INCOMPLETE'):
        policy._paths()


def test_fresh_instance_authority_has_no_tag_or_gameplay_history(tmp_path):
    path = tmp_path / 'session.sqlite3'
    authority = DurableSessionAuthority(path)
    try:
        authority.setup_instance_v3(team_ref='team-test', slot_id='slot-001', instance_ref='slot-0')
        assert authority.status_v3().generations == ()
        row = authority._open().execute('SELECT * FROM configuration').fetchone()
        assert row['account_ref'] == 'slot-001'
        assert row['player_tag_sha256'] is None
        assert authority._open().execute('SELECT mode FROM control').fetchone()[0] == 'STOPPED'
        with pytest.raises(SessionAuthorityError):
            authority.setup_instance_v3(team_ref='team-test', slot_id='slot-001', instance_ref='slot-0')
    finally:
        authority.close()
    reader = DurableSessionAuthority(path, read_only=True)
    try:
        assert reader.status_v3().generations == ()
    finally:
        reader.close()
