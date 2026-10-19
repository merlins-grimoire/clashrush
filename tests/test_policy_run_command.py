"""Complete command/protocol negatives; all gameplay leaves stay inert."""
import pytest
from test_policy_channel import pair
from clash_rush_rebuild.mvp_policy_channel import PolicyChannelError, OutcomeNotice
from clash_rush_rebuild.mvp_process_topology import HandoffAck


def test_worker_cannot_report_gameplay_before_parent_resumes_player():
    parent, worker, context = pair()
    parent.decode(worker.encode(HandoffAck(context.offer, True, True)))
    with pytest.raises(PolicyChannelError):
        worker.encode(OutcomeNotice('SUCCESS', 'PRE_D_COMPLETE'))


def parent_ready(tmp_path):
    from contextlib import contextmanager
    from test_policy_parent_session import compose, prepare
    from test_policy_process_topology import ack_for
    from clash_rush_rebuild.mvp_policy_channel import PolicyCodec, PolicyContext, PlayerReady
    s, host, state, clock, mutex = compose(tmp_path)
    offer = prepare(s, mutex)
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(s.run, producer_epoch=1, now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer), mutex=mutex)
    p,w=PolicyCodec('P'),PolicyCodec('W')
    context=PolicyContext(s.run,1,offer,s.runner_manifest_bytes)
    w.decode(p.encode(context));p.decode(w.encode(ack_for(offer)))
    w.decode(p.encode(PlayerReady(offer)))
    @contextmanager
    def owned(): yield mutex
    host.wait_process=lambda handle,timeout: bool(timeout)
    host.job_active_count=lambda job: 0
    return s,host,state,clock,owned,p,w,context


@pytest.mark.parametrize('fault',[None,'missing-seal','no-receipt','wrong-stage','player-member','readback'])
def test_complete_parent_protocol_keeps_player_and_success_separate(tmp_path,fault):
    from test_policy_finalization_v3 import snapshot, next_binding, stopped
    from clash_rush_rebuild.mvp_policy_run import finish_parent_session
    from clash_rush_rebuild.mvp_process_topology import ReleaseStageReceipt
    from clash_rush_rebuild.mvp_policy_channel import Revoke
    from clash_rush_rebuild.lifecycle_state import Active,BlockReason
    s,host,state,clock,owned,p,w,context=parent_ready(tmp_path)
    if fault!='missing-seal':s.authority.record_snapshot_v3(snapshot(s.run))
    class Channel:
        _closed=False
        no_more_gameplay=False
        def __init__(self):
            self.context=context
            self.queue=[w.encode(OutcomeNotice('SUCCESS','PRE_D_COMPLETE'))]
        def pending(self):return bool(self.queue)
        def receive(self,**kw):
            clock.now=11.  # post-D administrative finalization is permitted
            return p.decode(self.queue.pop(0))
        def send(self,message,**kw):
            assert type(message) is Revoke
            received=w.decode(p.encode(message))
            if fault=='no-receipt':return
            members=(context.offer.worker,context.offer.player) if fault=='player-member' else (context.offer.worker,)
            receipt=ReleaseStageReceipt(context.offer,received.token,
                'WRONG' if fault=='wrong-stage' else 'NOT_REQUIRED',members,True)
            self.queue.append(w.encode(receipt))
        def close(self):self._closed=True
    channel=Channel()
    readback=s.authority._readback_v3
    if fault=='readback':s.authority._readback_v3=lambda *args:None
    if fault is None:
        result=finish_parent_session(s,channel,mutex_factory=owned,clock=lambda:clock.now,
            wait=lambda seconds:setattr(clock,'now',clock.now+seconds))
        assert result.outcome=='SUCCESS'
        assert s.authority.closure_v3(1).worker_retired_digest is not None
    else:
        with pytest.raises(RuntimeError):
            finish_parent_session(s,channel,mutex_factory=owned,clock=lambda:clock.now,
                wait=lambda seconds:setattr(clock,'now',clock.now+seconds))
    assert state.state==Active(0,s.run.run_nonce,BlockReason.STOP_PROOF)
    s.authority._readback_v3=readback
    assert not [c for c in host.calls if c[:2]==('kill','job1')]
    if fault in ('no-receipt','wrong-stage','player-member'):
        assert not [c for c in host.calls if c[0]=='kill']
    assert s.authority.closure_v3(1).closed==0
    with pytest.raises(RuntimeError):s.authority.reserve_v3(next_binding(s.run),writer_epoch=1,proof=stopped())
    assert channel._closed
    s.authority.close()


def test_schema3_run_reaches_real_protected_ingress_not_legacy_launch(tmp_path,monkeypatch):
    from test_policy_sources import prepared_sources
    from clash_rush_rebuild import cli
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild import win32_lifecycle_host as native
    reader,assets,policy,api,port=prepared_sources(tmp_path,monkeypatch)
    # Only native OS/file adapters; policy parsing, CLI and activation denial real.
    original=ingress.PolicyIngress.__init__
    def constructor(self,project,**kw):
        original(self,project,runtime_api=api,process_api=policy._process_api,state_port=port)
    monkeypatch.setattr(ingress.PolicyIngress,'__init__',constructor)
    calls=[]
    monkeypatch.setattr(native,'NativeLifecycleApi',lambda:calls.append('forbidden'))
    monkeypatch.setattr(cli,'_start_mvp_session',lambda *a,**kw:calls.append('legacy'))
    assert cli.main(['mvp-run','--project-root',str(tmp_path)])==1
    assert not calls
    assert ingress.read_policy_status(tmp_path).generations==()


def test_explicit_cli_migration_and_direct_worker_are_default_denied(tmp_path,monkeypatch):
    from clash_rush_rebuild import cli
    from clash_rush_rebuild.mvp_policy_run import worker_main
    from clash_rush_rebuild import mvp_policy_channel as channel
    calls=[]
    monkeypatch.setattr(channel.PolicyChannel,'connect',lambda *a,**kw:calls.append('connect'))
    assert worker_main(['--project-root',str(tmp_path),'--pipe','invalid',
        '--parent-pid','1','--parent-created','1'])==2
    assert calls==[]
    assert cli.main(['mvp-migrate-policy','--project-root',str(tmp_path),'--backup-name','b.sqlite3'])==1
    assert not (tmp_path/'var').exists()


@pytest.mark.parametrize('fault',[None,'wrong-ack','unconsumed','missing-peer'])
def test_real_ingress_bootstrap_and_ack_root_resume_player_only_after_consumption(tmp_path,monkeypatch,fault):
    from contextlib import contextmanager
    from dataclasses import replace
    from types import SimpleNamespace
    from test_policy_sources import prepared_sources
    from test_policy_process_topology import InertHost
    from clash_rush_rebuild.mvp_policy_run import bootstrap_parent,accept_parent_worker
    from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
    from clash_rush_rebuild.lifecycle_state import LifecycleStateStore,Active
    from clash_rush_rebuild.mvp_policy_channel import PlayerReady
    from clash_rush_rebuild.win32_runtime import Win32Runtime
    sources,assets,ingress,api,port=prepared_sources(tmp_path,monkeypatch)
    host=InertHost();sent=[]
    class Listener:
        parent=host.identities[99]
        def accept(self,*,worker_handle,context,timeout):
            assert worker_handle==context.offer.worker.pid and timeout>0
            if fault=='missing-peer':raise RuntimeError('synthetic peer missing')
            ack=HandoffAck(context.offer,True,True)
            if fault=='wrong-ack':ack=replace(ack,accepted=False)
            return SimpleNamespace(receive=lambda **kw:ack,send=sent.append,close=lambda:None)
    from clash_rush_rebuild.win32_state_io import Win32StateFilePort,NativeWin32StateApi
    state=LifecycleStateStore(tmp_path,Win32StateFilePort(tmp_path,NativeWin32StateApi()))
    with ingress.prepare_run() as prepared:
        session,context=bootstrap_parent(prepared,state=state,host=host,listener=Listener(),sources=sources,
            worker_executable='synthetic-worker',worker_command='worker',
            player_executable='synthetic-player',player_command='player',clock=lambda:100.)
        assert ('resume',session.topology.worker.thread_handle) in host.calls
        assert ('resume',session.topology.player.thread_handle) not in host.calls
        assert type(state.load()) is Active
    authority=DurableSessionAuthority(tmp_path/'var/mvp-session.sqlite3');session.authority=authority
    @contextmanager
    def mutex():
        value=Win32Runtime(api).acquire_mutex()
        try:yield value
        finally:value.release()
    try:
        if fault!='unconsumed':authority.consume_v3(context.run,producer_epoch=1,now_ns=100_000_000_000)
        if fault is None:
            accept_parent_worker(session,context,Listener(),mutex_factory=mutex,clock=lambda:100.)
            assert sent==[PlayerReady(context.offer)]
            assert ('resume',session.topology.player.thread_handle) in host.calls
        else:
            with pytest.raises(RuntimeError):
                accept_parent_worker(session,context,Listener(),mutex_factory=mutex,clock=lambda:100.)
            assert not sent
            assert ('resume',session.topology.player.thread_handle) not in host.calls
    finally:
        with mutex() as value:session.dispose(mutex=value)
        authority.close()
    assert not [c for c in host.calls if c[0]=='kill']
