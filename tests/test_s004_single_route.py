"""Owner-authorized one-route transaction, generated images only."""
from test_s004_image_integration import ImageWorld
import pytest
from dataclasses import replace
from clash_rush_rebuild.mvp_deployment import DeploymentError


def test_missing_actual_match_mode_never_delivers_find_match():
    world = ImageWorld()
    original=world.capture
    def capture():
        frame=original()
        if world.screen=='MATCH':frame[36:72,:128]=0
        return frame
    world.observer.capture=capture
    result = world.run()
    assert not any(e.verb == 'find_match' for e in world.events)


def test_source_pixels_do_not_prove_installed_build():
    from test_s004_source_images import source_pack, source_frame
    from clash_rush_rebuild.mvp_deployment_profile import FrameObserver, DeploymentProfile
    from clash_rush_rebuild.mvp_deployment import Intervention
    data, assets = source_pack()
    observer=FrameObserver(profile=DeploymentProfile(data,assets,'a'*64),capture=lambda:source_frame(data,assets),
        monotonic=lambda:1.,intervention=lambda:Intervention.CLEAR,home_verified=lambda _:False)
    with pytest.raises(DeploymentError):observer()


def test_support_completion_does_not_need_or_claim_postproof():
    world = ImageWorld()
    result = world.run()
    without_post = replace(result, post_source_verified=False)
    assert without_post.complete



def test_single_route_never_opens_direct_army_and_completes_without_postproof():
    world = ImageWorld()
    result = world.run()
    assert result.complete, result
    assert [e.verb for e in world.events[:3]] == ['attack', 'find_match', 'army_attack']
    assert world.events[-1].verb == 'return_home'
    assert not result.post_source_verified
    assert result.reserve_state == 'UNVERIFIED'
    assert result.clan_input_proposed is None
    assert result.clan_input_delivered is None

@pytest.mark.parametrize('fault',['missing_normal','ranked','wrong_fee','missing_fee','ambiguous_normal'])
@pytest.mark.parametrize('fresh',[False,True])
def test_match_evidence_is_positive_exact_and_rechecked(fault,fresh):
    from test_s004_source_images import paint
    world=ImageWorld(); original=world.capture; reads=0
    def capture():
        nonlocal reads
        frame=original()
        if world.screen=='MATCH':
            reads+=1
            if not fresh or reads>1:
                match=world.data['source_layout']['match']
                if fault=='missing_normal':frame[36:72,:128]=0
                elif fault=='ranked':
                    frame[36:72,:128]=0
                    paint(frame,world.assets,match['ranked'])
                elif fault=='wrong_fee':frame[345,280]=255-frame[345,280]
                elif fault=='missing_fee':frame[342:360,256:384]=0
                else:paint(frame,world.assets,dict(match['normal'],roi=[.1,.1,.2,.2]))
        return frame
    world.observer.capture=capture; result=world.run()
    assert not result.complete and [e.verb for e in world.events]==['attack']
    assert result.reserve_state=='UNVERIFIED'


@pytest.mark.parametrize('fault',['missing','extra','wrong_count','unknown','selected','ambiguous','missing_final'])
@pytest.mark.parametrize('fresh',[False,True])
def test_real_army_proof_precedes_one_final_attack(fault,fresh):
    from test_s004_source_images import paint
    world=ImageWorld(); original=world.capture; reads=0
    def capture():
        nonlocal reads
        frame=original()
        if world.screen=='ARMY':
            reads+=1
            if not fresh or reads>1:
                if fault=='missing':frame[110:119,2:11]=0
                elif fault=='extra':frame[345:354,10:19]=world.assets['source_troop']
                elif fault=='wrong_count':frame[153:162,17:24]=world.assets['2']
                elif fault=='unknown':frame[110:119,2:11]=world.assets['card']
                elif fault=='selected':frame[110:119,70:79]=world.assets['source_selected']
                elif fault=='ambiguous':paint(frame,world.assets,world.data['source_layout']['categories'][0]['continues'])
                elif fault=='missing_final':frame[:36,384:512]=0
        return frame
    world.observer.capture=capture; result=world.run()
    assert not result.complete and [e.verb for e in world.events]==['attack','find_match']
    assert world.source_visits==0


@pytest.mark.parametrize('fault',['absent','boolean','wrong_type','wrong_package','wrong_version','wrong_binding','exception'])
def test_installed_build_port_requires_exact_selected_binding(fault):
    from clash_rush_rebuild.mvp_deployment_profile import InstallationEvidence, require_installation
    from test_s004_source_images import BUILD_BINDING, installed_build
    world=ImageWorld()
    value=installed_build(BUILD_BINDING)
    if fault=='boolean':value=True
    elif fault=='wrong_type':value={'version':'synthetic-build'}
    elif fault=='wrong_package':value=replace(value,package='other.package')
    elif fault=='wrong_version':value=replace(value,version='different')
    elif fault=='wrong_binding':value=replace(value,binding=object())
    def reader(_):
        if fault=='exception':raise OSError('unavailable')
        return value
    if fault=='absent':
        assert require_installation(world.profile,BUILD_BINDING,None) is None
        return
    with pytest.raises(DeploymentError,match='PACKAGE_METADATA_CONFLICT'):
        require_installation(world.profile,BUILD_BINDING,None if fault=='absent' else reader)


def test_missing_policy_and_build_blocks_before_any_native_or_capture_port():
    from clash_rush_rebuild.mvp_deployment_integration import build_native_executor
    world=ImageWorld()
    # Policy ingress must precede even a fresh read-only build transport.
    with pytest.raises(DeploymentError,match='POLICY_EVIDENCE_REQUIRED'):
        build_native_executor(binding=object(),bound_input=None,capture_bgr=None,home_verified=None,
            enabled=None,authority=None,transaction_ref=None,profile=world.profile,lease=None)
    assert world.events==[]


def test_fresh_build_contradiction_revokes_final_attack():
    from test_s004_source_images import installed_build
    world=ImageWorld(); reads=0
    def reader(binding):
        nonlocal reads
        if world.screen=='ARMY':
            reads+=1
            if reads>1:return replace(installed_build(binding),version='conflicting')
        return installed_build(binding)
    world.observer.installed_build=reader
    result=world.run()
    assert not result.complete and [e.verb for e in world.events]==['attack','find_match']


def test_exact_route_receipt_schema_preserves_old_stronger_evidence():
    import json
    from clash_rush_rebuild.mvp_deployment_receipt import _decode
    from test_deployment_receipt import retained_result
    result=replace(retained_result(),post_source_verified=False,reserve_state='SOURCE_BOUND',
                   clan_input_proposed=False,clan_input_delivered=False)
    raw=lambda r:json.dumps(r.public_payload(),sort_keys=True,separators=(',',':'))
    assert result.public_payload()['schema']==3 and _decode(raw(result))==result
    old=replace(result,post_source_verified=True,reserve_state='VERIFIED_UNCHANGED',
                clan_input_proposed=None,clan_input_delivered=None)
    assert old.public_payload()['schema']==2 and _decode(raw(old))==old
    for changes in ({'clan_input_proposed':True},{'clan_input_delivered':True},
                    {'reserve_state':'VERIFIED_UNCHANGED'},
                    {'clan_input_proposed':None,'clan_input_delivered':None}):
        with pytest.raises(DeploymentError):replace(result,**changes)


def test_find_match_attempt_and_final_proof_cannot_be_replayed():
    from test_s004_occupied_unused import SourceWorld, occupied_unused_plan
    from clash_rush_rebuild.mvp_deployment import DeploymentEngine
    world=SourceWorld(); tokens=[]
    def deliver(intent,proof):
        if intent.verb in ('find_match','army_attack'):tokens.append(proof)
        return world.deliver(intent,proof)
    engine=DeploymentEngine(observe=world.observe,deliver=deliver,release=world.release,
        live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=occupied_unused_plan())
    assert engine.run().complete
    assert all(token() is False for token in tokens)
    with pytest.raises(DeploymentError,match='EXECUTOR_ALREADY_USED'):engine.run()
    assert sum(e.verb=='find_match' for e in world.events)==1
    assert sum(e.verb=='army_attack' for e in world.events)==1
