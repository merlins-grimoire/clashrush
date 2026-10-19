"""S004-REVIEW-01/02: remaining route roles, generated physical-hit evidence."""
import copy
from dataclasses import replace
import numpy as np
import pytest
from clash_rush_rebuild import mvp_deployment as d
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile, FrameObserver
from test_s004_source_images import source_pack, source_frame, observe, paint, BUILD_BINDING, installed_build
from test_s004_occupied_unused import SourceWorld, occupied_unused_plan
from test_s004_image_integration import ImageWorld


@pytest.mark.parametrize('renamed',[False,True,'gray'])
def test_static_preparatory_final_alias_rejected(renamed):
    data,assets=source_pack()
    final=data['controls']['ARMY']['army_attack']; prep=data['controls']['MATCH']['find_match']
    if renamed:
        assets[prep['template']]=assets[final['template']].copy()
        if renamed=='gray':
            import cv2
            gray=cv2.cvtColor(assets[final['template']],cv2.COLOR_BGR2GRAY)
            assets[prep['template']]=np.repeat(gray[:,:,None],3,axis=2)
    else:data['controls']['MATCH']['find_match']=copy.deepcopy(final)
    with pytest.raises(d.DeploymentError):DeploymentProfile(data,assets,'a'*64)


class PhysicalWorld(ImageWorld):
    """Mouse resolves actual points, never intent.verb."""
    def __init__(self,collision=False):
        super().__init__(); self.point=None; self.hits=[]; self.collision=collision
        if collision:
            prep=self.data['controls']['MATCH']['find_match']; final=self.data['controls']['ARMY']['army_attack']
            outer=np.random.default_rng(91).integers(0,255,(11,11,3),dtype=np.uint8)
            outer[1:10,1:10]=self.assets[final['template']]
            self.assets[prep['template']]=outer
            prep['roi']=final['roi']=[.6,0,1,.1]
            self.rebind()
    def rebind(self):
        self.profile=DeploymentProfile(self.data,self.assets,'a'*64)
        self.observer=FrameObserver(profile=self.profile,capture=self.capture,
            monotonic=self.clock,intervention=lambda:d.Intervention.CLEAR,
            home_verified=lambda _:True,build_binding=BUILD_BINDING,installed_build=installed_build)
    def move(self,point):self.point=point; self.counter+=1; return True
    def at_target(self,point):return self.point is not None and self.point.near(point)
    def down(self):
        self.counter+=1; x,y=self.point.x*639,self.point.y*359
        if self.screen=='BATTLE':
            if 20<=x<=100 and y>=295:hit='troop'; self.selected='troop'
            elif 120<=x<=200 and y>=295:hit='support'; self.selected='clan_castle'
            elif y<288 and self.selected=='troop':hit='ground'; self.remaining=0
            elif y<288 and self.selected=='clan_castle':hit='support_ground'; self.support_remaining=0
            else:raise AssertionError('unexpected battle coordinate')
        else:
            hits=[]
            for name,rule in self.data['controls'][self.screen].items():
                a=self.assets[rule['template']]
                left,top=int(rule['roi'][0]*640)+2,int(rule['roi'][1]*360)+2
                if left<=x<left+a.shape[1] and top<=y<top+a.shape[0]:hits.append(name)
            hit=hits[0] if len(hits)==1 else 'unknown'
            if self.collision and self.screen=='MATCH':hit='army_attack'
            self.screen={'attack':'MATCH','find_match':'ARMY','army_attack':'BATTLE','return_home':'HOME'}[hit]
        self.hits.append(hit); return True


def test_physical_preparatory_final_collision_blocks_before_paid_down():
    world=PhysicalWorld(collision=True); result=world.run()
    assert world.hits==['attack'] and not result.complete


def test_physical_distinct_controls_complete_path():
    world=PhysicalWorld(); result=world.run()
    assert result.complete and not result.post_source_verified,result
    assert world.hits==['attack','find_match','army_attack','troop','ground','support','support_ground','return_home']


@pytest.mark.parametrize('allowed',['RETURN_HOME','ATTACK_NAVIGATION'])
def test_physical_permissions_do_not_cross_grant(allowed):
    world=PhysicalWorld(); result=world.run(authorize=lambda action:action.value==allowed)
    assert not result.complete
    assert world.hits==(['attack','find_match','army_attack'] if allowed=='ATTACK_NAVIGATION' else [])


def test_real_army_observer_rejects_same_title_context_occurrence():
    data,assets=source_pack()
    context=data['source_layout']['context']['source']; title=data['screens']['ARMY'][0]
    outer=np.random.default_rng(73).integers(0,255,(13,13,3),dtype=np.uint8)
    outer[2:11,2:11]=assets[title['template']]; assets[context['template']]=outer
    title['roi']=context['roi']=[0,0,.2,.1]
    frame=source_frame(data,assets)
    with pytest.raises(d.DeploymentError):observe(data,assets,frame)
    assert not frame.any()


def test_distinct_context_and_shared_category_art_supported():
    data,assets=source_pack(); result=observe(data,assets,source_frame(data,assets))
    assert [(e.category,e.quantity) for e in result.source_inventory]==[('ordinary',1),('clan',4)]


@pytest.mark.parametrize('fresh_only',[False,True])
@pytest.mark.parametrize('fault',['collision','missing'])
def test_typed_preparatory_intent_cannot_alias_final_control(fresh_only,fault):
    world=SourceWorld(); original=world.observe; reads=0
    def observer():
        nonlocal reads
        obs=original()
        if obs.screen is d.Screen.MATCH:
            reads+=1
            if not fresh_only or reads>1:
                point=obs.control('find_match')
                controls=() if fault=='missing' else obs.controls+(('army_attack',point),)
                obs=replace(obs,controls=controls)
        return obs
    result=d.DeploymentEngine(observe=observer,deliver=world.deliver,release=world.release,
        live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=occupied_unused_plan()).run()
    assert [e.verb for e in world.events]==['attack'] and not result.complete


@pytest.mark.parametrize('fault',['missing','ambiguous'])
def test_real_fresh_home_proof_failure_before_down(fault):
    class FreshWorld(PhysicalWorld):
        reads=0
        def capture(self):
            frame=super().capture(); self.reads+=1
            if self.screen=='HOME' and self.reads>1:
                attack=self.data['controls']['HOME']['attack']
                if fault=='missing':frame[:36,384:512]=0
                else:paint(frame,self.assets,dict(template=attack['template'],roi=[.7,0,.8,.1]))
            return frame
    world=FreshWorld(); result=world.run()
    assert world.reads>=2 and world.hits==[] and not result.complete


def test_overlapping_search_regions_with_distinct_role_occurrences():
    data,assets=source_pack(); frame=source_frame(data,assets)
    for rule in data['source_layout']['context'].values():rule['roi']=[0,0,1,1]
    assert observe(data,assets,frame).screen is d.Screen.ARMY


def test_real_fresh_final_control_appears_inside_preparatory_hit_area():
    class FreshCollisionWorld(PhysicalWorld):
        reads=0
        def __init__(self):
            super().__init__()
            prep=self.data['controls']['MATCH']['find_match']; final=self.data['controls']['ARMY']['army_attack']
            outer=np.random.default_rng(95).integers(0,255,(11,11,3),dtype=np.uint8)
            outer[1:10,1:10]=self.assets[prep['template']]; self.assets[final['template']]=outer
            final['roi']=prep['roi']=[.6,0,1,.1]; self.rebind()
        def capture(self):
            frame=super().capture()
            if self.screen=='MATCH':
                self.reads+=1
                if self.reads>1:
                    frame[:36,384:]=0
                    rule=self.data['controls']['ARMY']['army_attack']
                    frame[1:12,385:396]=self.assets[rule['template']]
            return frame
    world=FreshCollisionWorld(); result=world.run()
    assert world.reads>=2 and world.hits==['attack'] and not result.complete


def test_offset_nested_final_hit_area_is_not_preparatory_authority():
    class OffsetCollisionWorld(PhysicalWorld):
        def __init__(self):
            super().__init__()
            prep=self.data['controls']['MATCH']['find_match']; final=self.data['controls']['ARMY']['army_attack']
            outer=np.random.default_rng(99).integers(0,255,(21,21,3),dtype=np.uint8)
            outer[1:10,1:10]=self.assets[prep['template']]
            self.assets[final['template']]=outer
            prep['roi']=final['roi']=[.6,0,1,.1]
            self.rebind()
        def capture(self):
            frame=super().capture()
            if self.screen=='MATCH':
                frame[:36,384:]=0
                paint(frame,self.assets,self.data['controls']['ARMY']['army_attack'])
            return frame
    world=OffsetCollisionWorld(); result=world.run()
    assert world.hits==['attack'], 'prep center was inside the offset final control hit area'
    assert not result.complete
