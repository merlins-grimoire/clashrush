"""Real W root, protected source predicates, images/input states and SQLite.

Only OS leaves, synthetic frames/events, clocks and pipe bytes are substituted.
This is not native acceptance or a reviewed account calibration.
"""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest

from test_s004_image_integration import ImageWorld, SyntheticEventMouse
from test_deployment_repair import Clock
from test_policy_sources import prepared_sources
from test_policy_player_view import view
from test_policy_installed_closure import setup
from clash_rush_rebuild import mvp_deployment_input as inputs
from clash_rush_rebuild import mvp_deployment_integration as integration
from clash_rush_rebuild import mvp_local_native as native
from clash_rush_rebuild.mvp_deployment import Intervention
from clash_rush_rebuild.mvp_policy_channel import PolicyContext, PolicyCodec, PlayerReady, Revoke, OutcomeNotice
from clash_rush_rebuild.mvp_process_topology import HandoffAck, ReleaseStageReceipt
from clash_rush_rebuild.mvp_policy_run import execute_worker_session
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority


@pytest.mark.parametrize('fault',[None,'wrong-home','build-change','before-ready','parent-lost'])
def test_worker_root_observes_real_sources_without_gameplay_leaves(tmp_path,monkeypatch,fault):
    class AdvancingClock(Clock):
        def __call__(self):self.now+=.000001;return self.now
    world=ImageWorld();world.clock=AdvancingClock();world.clock.now=100.
    sources,assets,ingress,api,port=prepared_sources(tmp_path,monkeypatch,world)
    with ingress.prepare_run() as prepared:
        run=prepared.run; manifest=prepared.runner_manifest_bytes
        prepared.authority.reserve_v3(run,writer_epoch=1,proof=prepared.stopped)
    authority=DurableSessionAuthority(tmp_path/'var/mvp-session.sqlite3')
    player_view,host,_=view(run,world.clock)
    offer=player_view.offer
    m,low,build,provider=setup()
    low.player=offer.player
    low.conf=(tmp_path/'synthetic.conf').read_bytes()
    low.argv=(m.PLAYER,'--instance',sources.slot.internal_name)
    # Decision B never invokes the retired provider. Even an explicitly supplied
    # old provider must reject, rather than silently minting readiness.
    sources.provider=provider if fault=='build-change' else None
    world.profile=sources.profile
    mouse=SyntheticEventMouse(world)
    monkeypatch.setattr(inputs,'WindowsInterventionMonitor',lambda _:SimpleNamespace(
        start=lambda:None,state=lambda:Intervention.CLEAR,close=lambda:True,
        events=lambda:tuple(mouse.observations),arm=mouse.arm))
    monkeypatch.setattr(inputs,'TaggedWin32Mouse',lambda *_:mouse)
    monkeypatch.setattr(integration.time,'monotonic',world.clock)
    monkeypatch.setattr(integration.time,'sleep',world.clock.sleep)
    monkeypatch.setattr(native,'Win32BoundInput',lambda binding,check,auth,**kw:SimpleNamespace(_authorization=auth))
    def down():
        if world.screen=='BATTLE':
            verb='select' if world.point.y>.82 else 'hold' if world.selected=='troop' else 'tap'
            card=('troop' if world.point.x<.17 else 'clan_castle') if verb=='select' else world.selected
        else:verb={'HOME':'attack','MATCH':'find_match','ARMY':'army_attack','RESULT':'return_home'}[world.screen];card=None
        world.intent=SimpleNamespace(verb=verb,card_id=card)
        return ImageWorld.down(world)
    world.down=down
    def capture(_binding, *, before_capture):
        before_capture()
        frame=world.capture()
        if world.screen=='HOME' and fault=='wrong-home':frame.fill(0)
        return 640,360,np.dstack((frame,np.zeros(frame.shape[:2],np.uint8))).tobytes()
    host.capture_bgra=capture
    if fault=='build-change':low.dumps=['invalid synthetic package']
    p,w=PolicyCodec('P'),PolicyCodec('W')
    context=PolicyContext(run,1,offer,manifest);w.decode(p.encode(context))
    token='b'*32
    sent=[]
    class Channel:
        _closed=False
        def __init__(self):self.context=context;self.messages=[];self.ack=False
        @property
        def no_more_gameplay(self):return self._closed or (self.ack and fault=='parent-lost')
        def send(self,message,**kw):
            message=p.decode(w.encode(message));sent.append(message)
            if type(message) is HandoffAck:
                self.ack=True
                self.messages.append(p.encode(Revoke(token) if fault=='before-ready' else PlayerReady(offer)))
            if type(message) is OutcomeNotice and fault!='before-ready':self.messages.append(p.encode(Revoke(token)))
            if type(message) is ReleaseStageReceipt:self._closed=True
        def receive(self,**kw):return w.decode(self.messages.pop(0))
        def close(self):self._closed=True
    channel=Channel()
    # Reuse the exact safe mutex leaf used by protected ingress.
    @contextmanager
    def mutex():
        from clash_rush_rebuild.win32_runtime import Win32Runtime
        value=Win32Runtime(api).acquire_mutex()
        try:yield value
        finally:value.release()
    try:
        result=execute_worker_session(channel,authority=authority,host=host,sources=sources,
            mutex_factory=mutex,clock=world.clock,wait=world.clock.sleep)
        if fault is None:
            assert result.outcome=='SUCCESS',([type(v).__name__ for v in sent],world.events)
            world.clock.now=201.
            assert authority.finalize_v3(run,writer_epoch=1,outcome='SUCCESS',reason='PRE_D_COMPLETE').outcome=='SUCCESS'
            assert authority.closure_v3(run.generation).closed==0
        else:
            assert result.outcome=='UNCERTAIN'
            assert not world.events
            assert authority.outcome_v3(run.generation) is None  # only P finalizes
        assert channel._closed
        assert any(type(v) is ReleaseStageReceipt for v in sent)
        assert not [call for call in host.calls if call[0]=='kill']
    finally:authority.close()
