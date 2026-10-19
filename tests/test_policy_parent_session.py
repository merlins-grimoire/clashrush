"""Parent orchestration joins real SQLite to the tested process topology.

The host/mutex/lifecycle leaves here are inert. The separate topology suite proves
actual disposable Windows process behavior; neither suite is live admission.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_policy_finalization_v3 import binding_v3,stopped,next_binding
from test_policy_process_topology import InertHost,ack_for
from test_lifecycle import FakeStateStore
from clash_rush_rebuild.lifecycle_state import Ready,Active,BlockReason
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_worker import PolicyParentSession
from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology,ReleaseStageReceipt
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority,SessionAuthorityError
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration


def compose(tmp_path):
    authority=DurableSessionAuthority(tmp_path/'session.sqlite3')
    authority.setup(MvpConfiguration('team','account','slot-0','a'*64))
    authority._migrate_stopped_v3(proof=stopped(),backup_path=tmp_path/'backup.sqlite3')
    run=replace(binding_v3(),started_ns=0,deadline_ns=10_000_000_000)
    from clash_rush_rebuild.mvp_runner_manifest import runner_manifest
    import clash_rush_rebuild
    manifest=runner_manifest(Path(clash_rush_rebuild.__file__).parent,run.source_tree)
    run=replace(run,runner_manifest_digest=sha256(manifest).hexdigest())
    state=FakeStateStore(Ready(0),[])
    host=InertHost()
    topology=ParentProcessTopology(host,run_nonce=run.run_nonce,generation=run.generation)
    clock=SimpleNamespace(now=1.)
    session=PolicyParentSession(authority=authority,run=run,writer_epoch=1,state=state,
                               topology=topology,runner_manifest_bytes=manifest,
                               approval_current=lambda _:True,clock=lambda:clock.now)
    mutex=SimpleNamespace(abandoned=False,require_usable=lambda:None)
    return session,host,state,clock,mutex


def prepare(s,mutex):
    return s.prepare(mutex=mutex,stopped=stopped(),slot=0,worker_executable='worker.exe',
        worker_command='worker',player_executable='player.exe',player_command='player')


def test_active_and_pending_durable_before_any_process_creation(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    create=host.create_policy_suspended
    def checked(*args):
        assert state.state==Active(0,s.run.run_nonce,None)
        assert s.authority.read_run_v3(1)==s.run
        assert s.authority._open().execute('SELECT admission FROM run_v3').fetchone()[0]=='PENDING'
        return create(*args)
    host.create_policy_suspended=checked
    offer=prepare(s,mutex)
    s.start_worker(mutex=mutex)
    assert ('resume',111) not in host.calls
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer),mutex=mutex)
    assert ('resume',111) in host.calls
    assert not [c for c in host.calls if c[0]=='kill']


def test_unconsumed_ack_is_permanently_revoked_not_retryable(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    offer=prepare(s,mutex)
    s.start_worker(mutex=mutex)
    with pytest.raises(SessionAuthorityError):s.accept_worker(ack_for(offer),mutex=mutex)
    assert s.topology.blocked
    assert state.state==Active(0,s.run.run_nonce,BlockReason.STOP_PROOF)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    with pytest.raises(RuntimeError):s.accept_worker(ack_for(offer),mutex=mutex)
    assert ('resume',111) not in host.calls


@pytest.mark.parametrize('fault',['active','assignment','deadline','readback'])
def test_bootstrap_uncertainty_has_no_automatic_player_kill_or_ready(tmp_path,fault):
    s,host,state,clock,mutex=compose(tmp_path)
    if fault=='active':
        state.commit=lambda value: (_ for _ in ()).throw(OSError('inert durable failure'))
    if fault=='assignment':host.fail='assignment'
    if fault=='deadline':
        assign=host.assign_to_job
        def late(*args):
            assign(*args)
            clock.now=10.
        host.assign_to_job=late
    if fault=='readback':
        s.authority._readback_v3=lambda *a:None
    with pytest.raises((RuntimeError,OSError)):
        prepare(s,mutex)
    assert not [c for c in host.calls if c[0] in ('resume','kill')]
    assert s.authority._open().execute('SELECT count(*) FROM run_v3').fetchone()[0]==1
    with pytest.raises(SessionAuthorityError):
        s.authority.reserve_v3(next_binding(s.run),writer_epoch=1,proof=stopped())


def test_last_membership_check_crossing_deadline_forbids_player_resume(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    offer=prepare(s,mutex)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    members=host.stable_job_members
    def delayed(job):
        result=members(job)
        clock.now=10.
        return result
    host.stable_job_members=delayed
    with pytest.raises(DeploymentError,match='POLICY_DEADLINE'):
        s.accept_worker(ack_for(offer),mutex=mutex)
    assert ('resume',111) not in host.calls
    assert state.state==Active(0,s.run.run_nonce,BlockReason.STOP_PROOF)
    assert not [c for c in host.calls if c[0]=='kill']


def test_release_stage_then_worker_only_containment_never_closes_player(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    offer=prepare(s,mutex)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run,producer_epoch=1,now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer),mutex=mutex)
    clock.now=11.
    calls=[]
    token=s.revoke(mutex=mutex,lease=SimpleNamespace(revoke=lambda:calls.append('revoked')))
    receipt=ReleaseStageReceipt(offer,token,'NO_AUTHORIZED_RELEASE',(offer.worker,),True)
    assert s.contain_worker(receipt,mutex=mutex)
    outcome=s.finalize(mutex=mutex,outcome='UNCERTAIN',reason='INPUT_UNCERTAIN')
    assert outcome.outcome=='UNCERTAIN'
    assert state.state==Active(0,s.run.run_nonce,BlockReason.STOP_PROOF)
    assert s.authority.closure_v3(1).closed==0
    assert [c for c in host.calls if c[0]=='kill']==[('kill','job0')]
    s.dispose(mutex=mutex)
    assert [c for c in host.calls if c[0]=='kill']==[('kill','job0')]
    assert calls==['revoked']
    with pytest.raises(SessionAuthorityError):
        s.authority.reserve_v3(next_binding(s.run),writer_epoch=1,proof=stopped())


def test_parent_does_not_accept_scalar_success(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    prepare(s,mutex)
    with pytest.raises(SessionAuthorityError):
        s.finalize(mutex=mutex,outcome='SUCCESS',reason='PRE_D_COMPLETE')
    assert s.authority.outcome_v3(1) is None
    assert state.state==Active(0,s.run.run_nonce,BlockReason.STOP_PROOF)
