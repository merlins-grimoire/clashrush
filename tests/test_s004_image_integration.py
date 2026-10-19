"""Real image observer → executor → guarded input, synthetic mouse only."""
import copy
import cv2
import numpy as np
import pytest
from types import SimpleNamespace

from test_s004_source_images import source_pack, source_frame, paint, BUILD_BINDING, installed_build
from test_deployment_completeness import _glyphs, _card, _put
from test_deployment_repair import Clock
from clash_rush_rebuild.mvp_deployment import DeploymentEngine, Intervention, Screen
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile, FrameObserver
from clash_rush_rebuild.mvp_deployment_input import GuardedDeploymentInput, InputOwner, ObservedInput


class SyntheticEventMouse:
    """Exact synthetic observations, never aggregate counters or native hooks."""
    def __init__(self, world):
        self.world=world; self.observations=[]; self.expected=None
    def arm(self, kind, marker):
        assert self.expected is None
        self.expected=(kind,marker)
        return True
    def event(self, kind, operation):
        assert self.expected==(kind,self.marker)
        result=operation()
        if result is not False:
            self.observations.append(ObservedInput(len(self.observations)+1,
                kind,self.marker,self.world.clock()))
        self.expected=None
        return result
    def prepare(self, point):return self.world.prepare(point)
    def at_target(self, point):return self.world.at_target(point)
    def move(self, point):return self.event('MOVE',lambda:self.world.move(point))
    def down(self):return self.event('DOWN',self.world.down)
    def up(self):return self.event('UP',self.world.up)


class ImageWorld:
    def __init__(self,post_fault=None):
        self.data,self.assets=source_pack(); self.clock=Clock(); self.screen='HOME'
        self.events=[]; self.counter=0; self.intent=None; self.selected=None; self.support_remaining=1
        self.remaining=1; self.terminal_reads=0; self.source_visits=0; self.post_fault=post_fault
        d,a=self.data,self.assets; rng=np.random.default_rng(809)
        def locator(key,box):
            a[key]=rng.integers(0,255,(9,9,3),dtype=np.uint8)
            d['assets'][key]={'file':key+'.png','sha256':'b'*64}
            return dict(template=key,roi=box)
        for screen in d['screens']:
            d['screens'][screen]=[locator(screen.lower()+'_one',[0,0,.2,.1]),
                                  locator(screen.lower()+'_two',[.2,0,.4,.1])]
        for screen,controls in d['controls'].items():
            for i,verb in enumerate(controls):
                controls[verb]=locator('control_'+verb,[.6+i*.2,0,.8+i*.2,.1])
        d['view_anchors']=[locator('anchor_'+str(i),[i*.1,.25,(i+1)*.1,.3]) for i in range(3)]
        d['ground']['marker']=locator('ground_marker',[.3,.25,.4,.3])
        d['ground']['margin_pixels']=3
        d['ground']['playfield']=[[.02,.1],[.98,.1],[.98,.8],[.02,.8]]
        d['bar']['geometry']=dict(card_width=.12,width_tolerance=.006,peak_height=.7,peak_distance=5)
        a.update(_glyphs())
        d['cards'][1]['icons']=[locator('clan_icon',[0,0,1,1])['template']]
        for state in ('available','selected','depleted','deployed'):
            d['cards'][1][state]=[locator('clan_'+state,[0,0,1,1])['template']]
        for spec in d['cards']:
            spec['state_rois']=dict(available=[0,.3,.2,.5],depleted=[0,.49,.2,.69],
                                   deployed=[.8,.49,1,.69],selected=[.8,.3,1,.5])
        c=d['source_layout']['categories'][0]
        c['empty_status']=locator('ordinary_empty_status',c['status']['roi'])
        self.profile=DeploymentProfile(d,a,'a'*64)
        self.observer=FrameObserver(profile=self.profile,capture=self.capture,monotonic=self.clock,
            intervention=lambda:Intervention.CLEAR,home_verified=lambda _:True,build_binding=BUILD_BINDING,installed_build=installed_build)

    def capture(self):
        self.clock.now+=.005
        d,a=self.data,self.assets
        if self.screen=='BATTLE' and self.remaining==0 and self.support_remaining==0:
            self.terminal_reads+=1
            if self.terminal_reads>=8:self.screen='RESULT'
        if self.screen=='ARMY':
            frame=source_frame(d,a)
            # Render the exact calibrated small count bank used in Battle too.
            for c in d['source_layout']['categories']:
                if c['state']!='POPULATED':continue
                box=c['slots'][0]; x,y=int(box[0]*640),int(box[1]*360)
                frame[y+36:y+90,x:x+128]=0
                for i,k in enumerate('x4' if c['category']=='clan' else 'x1'):
                    _put(frame,a[k],x+5+i*12,y+45)
            if self.source_visits>1:
                c=d['source_layout']['categories'][0]; box=c['status']['roi']
                frame[int(box[1]*360):int(box[3]*360),:128]=0
                paint(frame,a,c['empty_status'])
                box=c['slots'][0]; frame[int(box[1]*360):int(box[3]*360),:128]=0
                paint(frame,a,dict(template='source_empty',roi=box))
                if self.post_fault=='changed':
                    box=d['source_layout']['categories'][3]['slots'][0]; x,y=int(box[0]*640),int(box[1]*360)
                    frame[y+36:y+90,x:x+128]=0
                    for i,k in enumerate('x2'):_put(frame,a[k],x+5+i*12,y+45)
                elif self.post_fault=='missing':frame[:,384:512]=0
                elif self.post_fault=='ambiguous':paint(frame,a,d['source_layout']['categories'][3]['continues'])
            return frame
        frame=np.zeros((360,640,3),np.uint8)
        for r in d['screens'][self.screen]+list(d['controls'].get(self.screen,{}).values()):paint(frame,a,r)
        if self.screen=='MATCH':
            match=d['source_layout']['match']
            paint(frame,a,match['normal'])
            box=match['cost']['roi']; x,y=int(box[0]*640),int(box[1]*360)
            image=a[match['cost']['template']]; frame[y:y+image.shape[0],x:x+image.shape[1]]=image
        if self.screen=='BATTLE':
            cv2.rectangle(frame,(230,145),(390,235),(0,0,255),2)
            for r in d['view_anchors']+[d['ground']['marker']]:paint(frame,a,r)
            _put(frame,a['left_end'],5,120); _put(frame,a['right_end'],25,120)
            top=round(360*.82)
            for left,icon in ((20,'card'),(120,'clan_icon')):
                _card(frame,a,left,icon=icon)
                _put(frame,a['available' if left==20 else 'clan_available'],left+2,top+18)
            if self.selected=='troop':_put(frame,a['selected'],86,top+18)
            elif self.selected=='clan_castle':_put(frame,a['clan_selected'],186,top+18)
            if self.support_remaining==0:
                frame[top+18:top+27,122:131]=100
                _put(frame,a['clan_deployed'],186,top+28)
            if self.remaining==0:
                frame[top+18:top+27,22:31]=100
                _put(frame,a['depleted'],22,top+28)
                _put(frame,a['0'],54,top+39)
                patch=frame[top+12:top+34,35:82]
                patch[:]=cv2.cvtColor(cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR)
        return frame

    def prepare(self,point):return True
    def move(self,point):self.counter+=1; self.point=point; return True
    def at_target(self,point):return True
    def down(self):
        self.counter+=1; intent=self.intent; self.events.append(intent)
        if intent.verb=='attack':self.screen='MATCH'
        elif intent.verb=='find_match':self.screen='ARMY'
        elif intent.verb=='army_attack':self.screen='BATTLE'
        elif intent.verb=='return_home':self.screen='HOME'
        elif intent.verb=='select':self.selected=intent.card_id
        elif intent.verb=='hold':self.remaining=0
        elif intent.verb=='tap':self.support_remaining=0
        return True
    def up(self):self.counter+=1; return True

    def run(self,authorize=None):
        # This fixture checks the gameplay/input seam, not parent finalization.
        # The separate native-factory test below retains that caller obligation.
        mouse=SyntheticEventMouse(self)
        guarded=GuardedDeploymentInput(mouse=mouse,authorize=authorize or (lambda _:True),
            monitor=lambda:Intervention.CLEAR,lease=SimpleNamespace(set_held=lambda _:None),
            monotonic=self.clock,wait=self.clock.sleep,
            observed_events=lambda:tuple(mouse.observations),arm_event=mouse.arm,
            owner=InputOwner('a'*32,1,'b'*64,'c'*64),ownership_live=lambda:True)
        self.guarded=guarded
        def deliver(intent,proof):
            self.intent=intent
            return guarded.deliver(intent,proof)
        return DeploymentEngine(observe=self.observer,deliver=deliver,release=guarded.release,
            live_gate=lambda:True,monotonic=self.clock,sleep=self.clock.sleep,
            visit_deadline=100,plan=self.profile.plan).run()


@pytest.mark.parametrize('post_fault',[None,'changed','missing','ambiguous'])
def test_real_source_images_through_guarded_executor_and_post_reconciliation(post_fault):
    world=ImageWorld(post_fault)
    result=world.run()
    assert result.natural_result and result.returned_home, result
    assert result.complete, result
    assert not result.post_source_verified
    assert result.remaining==0 and result.deployable_remaining==0
    assert [e.verb for e in world.events if e.card_id=='clan_castle']==['select','tap']
    assert [e.verb for e in world.events[:3]]==['attack','find_match','army_attack']
    assert world.source_visits==0


@pytest.mark.parametrize('allowed',['RETURN_HOME','ATTACK_NAVIGATION'])
def test_source_permission_and_attack_permission_do_not_cross_grant(allowed):
    world=ImageWorld(); result=world.run(authorize=lambda action:action.value==allowed)
    assert not result.complete
    assert [e.verb for e in world.events]==(['attack','find_match','army_attack'] if allowed=='ATTACK_NAVIGATION' else [])


@pytest.mark.parametrize('policy_composition',[False,True])
def test_native_factory_composes_real_observer_guard_and_durable_receipt(monkeypatch,tmp_path,policy_composition):
    from clash_rush_rebuild import mvp_deployment_input as inputs
    from clash_rush_rebuild import mvp_deployment_integration as integration
    from clash_rush_rebuild.input_authorization import InputAuthorization
    from dataclasses import replace
    from clash_rush_rebuild.mvp_policy_evidence import RunEvidence
    from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
    from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
    from test_policy_finalization_v3 import binding_v3,stopped
    world=ImageWorld(); authority=DurableSessionAuthority(tmp_path/'session.sqlite3')
    class AdvancingClock(Clock):
        def __call__(self):
            self.now+=.000001
            return self.now
    world.clock=AdvancingClock()
    if policy_composition:
        world.data['source_layout']['build']='18.600.7'
        world.profile=DeploymentProfile(world.data,world.assets,world.profile.digest)
    authority.setup(MvpConfiguration('team','account','slot-0','a'*64))
    authority._migrate_stopped_v3(proof=stopped(),backup_path=tmp_path/'backup.sqlite3')
    run=replace(binding_v3(),started_ns=0,deadline_ns=100_000_000_000,
                profile_digest=world.profile.digest)
    authority.reserve_v3(run,writer_epoch=1,proof=stopped())
    authority.consume_v3(run,producer_epoch=1,now_ns=0)
    evidence=RunEvidence(authority,run,epoch=1,clock=world.clock,
        approval_current=lambda _:True,installed_current=lambda _:'e'*64,
        account_current=lambda *_:True)
    diagnostics=[]
    observe=evidence.observe
    def inspected(value):
        try:return observe(value)
        except BaseException as error:
            diagnostics.append((type(error).__name__,str(error)))
            raise
    evidence.observe=inspected
    mouse=SyntheticEventMouse(world)
    monkeypatch.setattr(inputs,'WindowsInterventionMonitor',lambda _:SimpleNamespace(
        start=lambda:None,state=lambda:Intervention.CLEAR,close=lambda:True,
        events=lambda:tuple(mouse.observations),arm=mouse.arm))
    monkeypatch.setattr(integration.time,'monotonic',world.clock)
    monkeypatch.setattr(integration.time,'sleep',world.clock.sleep)
    # The synthetic mouse decodes the calibrated target instead of receiving
    # a native event. Observer and guarded delivery remain the real composition.
    def down():
        if world.screen=='BATTLE':
            verb='select' if world.point.y>.82 else 'hold' if world.selected=='troop' else 'tap'
            card_id=('troop' if world.point.x<.17 else 'clan_castle') if verb=='select' else world.selected
        else:
            verb={'HOME':'attack','MATCH':'find_match','ARMY':'army_attack','RESULT':'return_home'}[world.screen]
            card_id=None
        world.intent=SimpleNamespace(verb=verb,card_id=card_id)
        return ImageWorld.down(world)
    world.down=down
    monkeypatch.setattr(inputs,'TaggedWin32Mouse',lambda *_:mouse)
    lease=SimpleNamespace(marker=1,deadline=100,active=lambda:True,set_held=lambda _:None)
    if policy_composition:
        from test_policy_player_view import view
        from test_policy_installed_closure import setup
        from clash_rush_rebuild import mvp_local_native as native
        player_view,host,_=view(run,world.clock)
        m,low,build,provider=setup()
        low.player=player_view.offer.player
        build=replace(build,run_nonce=run.run_nonce,generation=run.generation,process_identity=low.player)
        def capture(_binding, *, before_capture):
            before_capture()
            bgr=world.capture()
            return bgr.shape[1],bgr.shape[0],np.dstack((bgr,np.zeros(bgr.shape[:2],np.uint8))).tobytes()
        host.capture_bgra=capture
        # Only the Win32 leaf constructor is replaced; the policy view, provider,
        # executor, recognizer, input state machine and SQLite are real consumers.
        monkeypatch.setattr(native,'Win32BoundInput',lambda binding,check,auth,**kw:
                            SimpleNamespace(_authorization=auth))
        executor=integration.build_policy_executor(player_view=player_view,authority=authority,
            epoch=1,profile=world.profile,lease=lease,provider=None,build_binding=build,
            version_code=180600008,approval_current=lambda _:True,
            account_current=lambda *_:True,home_verified=lambda _:True)
    else:
        executor=integration.build_native_executor(binding=BUILD_BINDING,installed_build=installed_build,
        bound_input=SimpleNamespace(_authorization=InputAuthorization.monitored_attack(lambda:True)),
        capture_bgr=world.capture,home_verified=lambda _:True,enabled=lambda:True,
        authority=authority,transaction_ref=run.transaction_ref,profile=world.profile,
        evidence=evidence,owner=InputOwner(run.run_nonce,run.generation,run.binding_digest,'c'*64),
        ownership_live=lambda:True,
        lease=lease)
    result=executor.run()
    assert result[0], (result,diagnostics,[(e.verb,e.card_id) for e in world.events])
    world.clock.now=101.  # Historical success is administrative, not a new input.
    outcome=authority.finalize_v3(run,writer_epoch=1,outcome='SUCCESS',reason='PRE_D_COMPLETE')
    assert outcome.outcome=='SUCCESS'
    assert authority.closure_v3(1).closed==0
    assert authority._open().execute('SELECT count(*) FROM input_v3').fetchone()[0]>0
    if policy_composition:
        assert not low.commands and not low.cleaned and not low.running
        player_view.close()
    authority.close()
