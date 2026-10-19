"""Installed command -> real SQLite finalizer, synthetic pre-D facts only."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import pytest
import clash_rush_rebuild
from clash_rush_rebuild import cli
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_runner_manifest import runner_manifest
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
from test_policy_finalization_v3 import binding_v3,stopped,snapshot,next_binding


def prepared(root, *, sealed=True):
    a=DurableSessionAuthority(root/'var'/'mvp-session.sqlite3')
    a.setup(MvpConfiguration('team','account','slot-0','a'*64))
    a._migrate_stopped_v3(proof=stopped(),backup_path=root/'var'/'backup.sqlite3')
    b=binding_v3()
    raw=runner_manifest(Path(clash_rush_rebuild.__file__).parent,b.source_tree)
    b=replace(b,runner_manifest_digest=sha256(raw).hexdigest())
    a.reserve_v3(b,writer_epoch=1,proof=stopped())
    a.consume_v3(b,producer_epoch=1,now_ns=101)
    if sealed:a.record_snapshot_v3(snapshot(b))
    a.close()
    manifest=root/'var'/'runner-manifest.json'
    manifest.write_bytes(raw)
    return b,manifest


def command(root,b,manifest):
    return ['mvp-finalize','--project-root',str(root),'--generation',str(b.generation),
        '--binding-digest',b.digest,'--runner-manifest',str(manifest),
        '--outcome','SUCCESS','--reason','PRE_D_COMPLETE']


@pytest.mark.parametrize('fault',[None,'binding','manifest','missing-seal','abandoned'])
def test_cli_finalization_is_exact_and_never_closes_player(tmp_path,monkeypatch,capsys,fault):
    b,manifest=prepared(tmp_path,sealed=fault!='missing-seal')
    calls=[]
    lease=SimpleNamespace(abandoned=fault=='abandoned',
        require_usable=lambda:calls.append('owned'),release=lambda:calls.append('released'))
    monkeypatch.setattr(cli,'_policy_finalizer_mutex',lambda:lease)
    def forbidden(*a,**kw):raise AssertionError('finalization constructed native gameplay')
    for name in ('build_native_state_store','NativeLifecycleApi','run_native_mvp_visit','build_inert_cycle'):
        monkeypatch.setattr(cli,name,forbidden)
    args=command(tmp_path,b,manifest)
    if fault=='binding':args[args.index('--binding-digest')+1]='f'*64
    if fault=='manifest':manifest.write_bytes(b'{}')
    # Deliberately long after the synthetic D: no fresh clock/game observation.
    monkeypatch.setattr(cli.time,'monotonic',lambda:10000.)
    result=cli.main(args)
    a=DurableSessionAuthority(tmp_path/'var'/'mvp-session.sqlite3')
    try:
        if fault is None:
            assert result==0
            assert a.outcome_v3(1).outcome=='SUCCESS'
            assert a.closure_v3(1).closed==0
            assert 'eligibility=BLOCKED' in capsys.readouterr().out
            assert cli.main(args)==0
            assert a._open().execute('SELECT count(*) FROM outcome_v3').fetchone()[0]==1
            with pytest.raises(RuntimeError):a.reserve_v3(next_binding(b),writer_epoch=1,proof=stopped())
        else:
            assert result!=0 and a.outcome_v3(1) is None
        if calls:assert calls[-1]=='released'
    finally:a.close()


@pytest.mark.parametrize('state',['pending','terminal','expired','stale-epoch'])
def test_nonlive_evidence_cannot_construct_build_transport(tmp_path,state):
    from test_policy_finalization_v3 import v3_store
    from clash_rush_rebuild.mvp_policy_evidence import RunEvidence
    from clash_rush_rebuild.mvp_deployment_input import InputOwner
    from clash_rush_rebuild.mvp_deployment_integration import build_native_executor
    a,b=v3_store(tmp_path,consume=state!='pending')
    if state=='terminal':a.finalize_v3(b,writer_epoch=1,outcome='BLOCKED',reason='PRODUCER_LOST')
    clock=lambda:(201 if state=='expired' else 110)/1_000_000_000
    evidence=RunEvidence(a,b,epoch=2 if state=='stale-epoch' else 1,clock=clock,
        approval_current=lambda r:True,installed_current=lambda r:'a'*64,account_current=lambda *a:True)
    calls=[]
    def forbidden(binding):
        calls.append('build-transport')
        raise RuntimeError('inert source leaf')
    try:
        assert evidence.active() is False
        with pytest.raises(RuntimeError):
            build_native_executor(binding=object(),bound_input=None,capture_bgr=lambda:None,
                home_verified=lambda f:True,enabled=lambda:True,authority=a,
                transaction_ref=b.transaction_ref,profile=SimpleNamespace(data={'schema':3},digest=b.profile_digest),
                lease=SimpleNamespace(active=lambda:True,marker=1),installed_build=forbidden,evidence=evidence,
                owner=InputOwner(b.run_nonce,b.generation,b.binding_digest,'a'*64),ownership_live=lambda:True)
        assert calls==[]
    finally:a.close()
