"""Policy CLI boundaries: only disposable databases, no native constructors."""
from pathlib import Path
import sqlite3
import pytest

from clash_rush_rebuild import cli
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from test_policy_finalization_v3 import stopped


def database(root, *, migrate=True):
    path=root/'var'/'mvp-session.sqlite3'
    authority=DurableSessionAuthority(path)
    authority.setup(MvpConfiguration('team','account','slot-0','a'*64))
    if migrate:
        authority._migrate_stopped_v3(proof=stopped(),backup_path=path.with_suffix('.backup'))
    authority.close()
    return path


def test_status_missing_store_does_not_create_database_or_parent(tmp_path):
    root=tmp_path/'absent'
    try:
        cli.main(['mvp-status','--project-root',str(root)])
    except SystemExit:
        pass
    assert not root.exists()


def test_status_does_not_upgrade_legacy_database(tmp_path):
    path=database(tmp_path,migrate=False)
    before=path.read_bytes()
    assert cli.main(['mvp-status','--project-root',str(tmp_path)])==0
    assert path.read_bytes()==before
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()==(1,)
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='deployment_receipts'").fetchall()


@pytest.mark.parametrize('command,extra',[
    ('visit-one',['--owner-approved']),('visit-one-child',['--owner-approved']),
    ('donor-visit-one',['--owner-approved']),('diagnose-home',[]),('diagnose-home-child',[]),
    ('startup-debug-one',['--allow-private-full-frames']),
    ('startup-debug-child',['--allow-private-full-frames']),('startup-continue-one',[]),
    ('initialize',[]),('reconcile-window-binding',[]),
])
def test_migrated_policy_denies_every_legacy_native_dispatch(tmp_path,command,extra):
    database(tmp_path)
    calls=[]
    def forbidden(*a,**kw):
        calls.append(command)
        raise RuntimeError('forbidden test-only leaf')
    args=[command,'--project-root',str(tmp_path),'--slots','unused',*extra]
    try:
        cli.main(args,cycle_builder=forbidden,diagnostic_cycle_builder=forbidden,
            diagnostic_child_runner=forbidden,inert_child_runner=forbidden,
            authorized_visit_runner=forbidden,startup_continue_runner=forbidden,
            startup_debug_cycle_builder=forbidden,reconciler_builder=forbidden)
    except SystemExit:
        pass
    assert calls==[]


def test_policy_status_reports_no_runtime_admission(tmp_path,capsys):
    path=database(tmp_path)
    before=path.read_bytes()
    assert cli.main(['mvp-status','--project-root',str(tmp_path)])==0
    out=capsys.readouterr().out
    assert 'schema=3' in out and 'runtime=DENIED' in out
    assert path.read_bytes()==before


@pytest.mark.parametrize('command,extra',[
    ('mvp-run',[]),('mvp-resume',[]),
    ('mvp-visit-one',['--slots','unused','--transaction-ref','a'*32]),
])
def test_policy_rejects_attack_roots_before_native_construction(tmp_path,monkeypatch,command,extra):
    database(tmp_path)
    calls=[]
    def forbidden(*a,**kw):
        calls.append('native')
        raise RuntimeError('inert constructor sentinel')
    monkeypatch.setattr(cli,'build_native_state_store',forbidden)
    monkeypatch.setattr(cli,'run_native_mvp_visit',forbidden)
    try:
        cli.main([command,'--project-root',str(tmp_path),*extra])
    except SystemExit:
        pass
    assert calls==[]


@pytest.mark.parametrize('readiness_only',[False,True])
def test_direct_legacy_worker_rejects_policy_before_context_or_native(tmp_path,monkeypatch,readiness_only):
    database(tmp_path)
    from clash_rush_rebuild import mvp_local_native as native,mvp_deployment_worker as worker
    calls=[]
    def forbidden(*a,**kw):
        calls.append('construction')
        raise RuntimeError('inert constructor sentinel')
    monkeypatch.setattr(native,'NativeLifecycleApi',forbidden)
    monkeypatch.setattr(worker,'require_worker_context',forbidden)
    with pytest.raises(RuntimeError):
        native.run_native_mvp_visit(str(tmp_path),'unused','a'*32,readiness_only=readiness_only)
    assert calls==[]
