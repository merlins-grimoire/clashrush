"""Generic generated pixels only; no private calibration or game images."""
import copy
import numpy as np
import pytest

from test_deployment_profile import profile_data
from test_deployment_vision import digits, quantity
from clash_rush_rebuild.mvp_deployment import DeploymentError, Intervention, Screen, CardState
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile, FrameObserver, InstallationEvidence


def source_pack():
    data, assets = profile_data()
    rng = np.random.default_rng(804)
    def marker(key):
        assets[key] = rng.integers(0, 255, (9, 9, 3), dtype=np.uint8)
        data['assets'][key] = {'file': key + '.png', 'sha256': 'b' * 64}
        return key
    def loc(key, box):
        return {'template': marker(key), 'roi': box}
    for k, v in digits().items():
        assets[k] = np.repeat(v[:, :, None], 3, axis=2)
    data['schema'] = 3
    data['screens']['ARMY'] = [loc('source_a', [0, 0, .2, .1]), loc('source_b', [.2, 0, .4, .1])]
    data['controls']['ARMY'] = {'army_attack': loc('green_attack', [.6, 0, .8, .1])}
    data['controls']['HOME']['attack']=loc('home_attack',[.6,0,.8,.1])
    data['controls']['MATCH']['find_match']=loc('find_match',[.6,0,.8,.1])
    clan = copy.deepcopy(data['cards'][0])
    clan.update(identity='clan_castle', kind='CLAN', capacity=1, icons=[marker('clan_card')])
    for state in ('available','depleted','deployed','selected'):clan[state]=[marker('clan_'+state)]
    data['cards'].append(clan)
    data['plan']['roster'].append({'identity': 'clan_castle', 'initial_quantity': 1})
    data['dispositions'] = [{'identity': 'troop', 'disposition': 'DEPLOY'},
                            {'identity': 'clan_castle', 'disposition': 'DEPLOY'}]
    data['source_inventory'] = [
        dict(identity='ordinary.troop', category='ordinary', kind='TROOP', spell='NONE', quantity=1),
        dict(identity='reserve.troop', category='clan', kind='TROOP', spell='NONE', quantity=4),
    ]
    categories = []
    for i, name in enumerate(('ordinary', 'hero', 'spell', 'clan', 'siege')):
        x = i * .2
        categories.append(dict(category=name, state='LOCKED' if name == 'siege' else
                               'POPULATED' if name in ('ordinary', 'clan') else 'EMPTY',
            header=loc(name+'_header', [x, .1, x+.2, .16]),
            status=loc(name+'_status', [x, .16, x+.2, .22]),
            complete=loc(name+'_complete', [x, .22, x+.2, .28]),
            end=loc(name+'_end', [x, .85, x+.2, .9]),
            continues=loc(name+'_more', [x, .9, x+.2, .95]),
            slots=[[x, .3, x+.2, .55], [x, .56, x+.2, .81]]))
    data['source_layout'] = dict(build='synthetic-build', mode='REGULAR_MULTIPLAYER',
        categories=categories, empty=marker('source_empty'),
        icon_roi=[0, 0, .4, .4], count_roi=[0, .4, 1, 1], state_roi=[.5, 0, 1, .4],
        available=marker('source_available'), selected=marker('source_selected'),
        entries=[dict(identity=e['identity'], category=e['category'], kind=e['kind'], spell=e['spell'],
                      capacity=99, icons=[marker('source_troop')]) for e in data['source_inventory']])
    marker('source_vacant')
    assets['source_vacant']=np.zeros((90,128,3),np.uint8)
    assets['source_vacant'][2:11,2:11]=assets['source_empty']
    data['source_layout']['vacant']='source_vacant'
    data['source_layout']['context']={'source':loc('context_source',[.2,.95,.4,1.])}
    cost=loc('match_cost',[.4,.95,.6,1.])
    assets['match_cost']=rng.integers(0,255,(18,128,3),dtype=np.uint8)
    data['source_layout']['match']=dict(normal=loc('normal_mode',[0,.1,.2,.2]),
        ranked=loc('ranked_mode',[.2,.1,.4,.2]),cost=cost,expected_cost=50)
    return data, assets


def paint(frame, assets, locator):
    box = locator['roi']; x = int(box[0]*640)+2; y = int(box[1]*360)+2
    a = assets[locator['template']]
    frame[y:y+a.shape[0], x:x+a.shape[1]] = a


def source_frame(data, assets, reserve=4):
    frame = np.zeros((360, 640, 3), np.uint8)
    for rule in data['screens']['ARMY'] + list(data['controls']['ARMY'].values()):
        paint(frame, assets, rule)
    for rule in data['source_layout']['context'].values():paint(frame,assets,rule)
    for c in data['source_layout']['categories']:
        for role in ('header', 'status', 'complete', 'end'):
            paint(frame, assets, c[role])
        for index, box in enumerate(c['slots']):
            x, y = int(box[0]*640), int(box[1]*360)
            if index == 0 and c['state'] == 'POPULATED':
                frame[y+2:y+11, x+2:x+11] = assets['source_troop']
                frame[y+2:y+11, x+70:x+79] = assets['source_available']
                q, _ = quantity('x'+str(reserve if c['category']=='clan' else 1))
                frame[y+40:y+40+q.shape[0], x+2:x+2+q.shape[1]] = q[:, :, None]
            else:
                frame[y+2:y+11, x+2:x+11] = assets['source_empty']
    return frame


BUILD_BINDING=object()
def installed_build(binding):
    return InstallationEvidence(binding,'com.supercell.clashofclans','synthetic-build')


def observe(data, assets, frame):
    return FrameObserver(profile=DeploymentProfile(data, assets, 'a'*64), capture=lambda: frame,
                         monotonic=lambda: 1., intervention=lambda: Intervention.CLEAR,
                         home_verified=lambda _: False,build_binding=BUILD_BINDING,installed_build=installed_build)()


def test_source_observer_reads_actual_count_instead_of_expected_payload():
    data, assets = source_pack()
    # Count differs from the expected payload: observation must report pixels,
    # leaving exact precommit comparison to the engine.
    frame = source_frame(data, assets, reserve=2)
    try:
        result = observe(data, assets, frame)
    except DeploymentError as error:
        pytest.fail('valid calibrated source image was not observed: '+error.code)
    assert result.screen is Screen.ARMY
    assert [(e.category, e.quantity) for e in result.source_inventory] == [('ordinary', 1), ('clan', 2)]
    assert all(e.state is CardState.AVAILABLE for e in result.source_inventory), 'source state must be explicit evidence'
    assert result.source_categories == (('ordinary', 'POPULATED'), ('hero', 'EMPTY'),
                                        ('spell', 'EMPTY'), ('clan', 'POPULATED'), ('siege', 'LOCKED'))
    assert not frame.any()


@pytest.mark.parametrize('fault', ['missing_category', 'continuation', 'clipped', 'unknown',
    'duplicate', 'selected', 'extra_outside', 'duplicate_header', 'empty_count', 'ambiguous'])
def test_source_image_requires_complete_unambiguous_owned_contents(fault):
    data, assets = source_pack(); frame = source_frame(data, assets)
    c = data['source_layout']['categories'][3]
    x, y = int(c['slots'][0][0]*640), int(c['slots'][0][1]*360)
    if fault == 'missing_category':
        box=c['header']['roi']; frame[int(box[1]*360):int(box[3]*360), int(box[0]*640):int(box[2]*640)]=0
    elif fault == 'continuation': paint(frame, assets, c['continues'])
    elif fault == 'clipped': frame[y+2:y+7,x+2:x+11]=0
    elif fault == 'unknown': frame[y+2:y+11,x+2:x+11]=assets['card']
    elif fault == 'duplicate':
        y2=int(c['slots'][1][1]*360); frame[y2:y2+80,x:x+120]=frame[y:y+80,x:x+120]
    elif fault == 'selected': frame[y+2:y+11,x+70:x+79]=assets['source_selected']
    elif fault == 'extra_outside': frame[345:354,10:19]=assets['source_troop']
    elif fault == 'duplicate_header':
        box=c['header']['roi']; paint(frame,assets,{'template':c['header']['template'], 'roi':[box[0]+.05,box[1],box[2],box[3]]})
    elif fault == 'empty_count':
        q,_=quantity('x2'); frame[240:240+q.shape[0],130:130+q.shape[1]]=q[:,:,None]
    elif fault == 'ambiguous': frame[y+14:y+23,x+2:x+11]=assets['source_empty']
    with pytest.raises(DeploymentError): observe(data, assets, frame)
    assert not frame.any()


def test_legacy_review_flag_cannot_admit_runtime_profile(tmp_path):
    from test_deployment_profile import write_pack
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    write_pack(tmp_path, runtime=True)
    with pytest.raises(DeploymentError, match='SOURCE_PROFILE_REQUIRED'):
        load_profile(tmp_path, candidate_tree='a'*40)
    assert load_profile(tmp_path, candidate_tree='a'*40, runtime=False).data['schema']==2


def test_native_composition_rejects_legacy_before_accessing_native_ports():
    from clash_rush_rebuild.mvp_deployment_integration import build_native_executor
    data,assets=profile_data(); profile=DeploymentProfile(data,assets,'a'*64)
    with pytest.raises(DeploymentError, match='SOURCE_PROFILE_REQUIRED'):
        build_native_executor(binding=None,bound_input=None,capture_bgr=None,home_verified=None,
                              enabled=None,authority=None,transaction_ref=None,profile=profile,lease=None)


@pytest.mark.parametrize('change', ['build','tree','digest','category'])
def test_compiled_source_plan_binds_calibration_and_categories(change):
    data,assets=source_pack(); first=DeploymentProfile(data,assets,'a'*64)
    digest='a'*64
    if change=='build': data['source_layout']['build']='other-build'
    elif change=='tree': data['reviewed_tree']='c'*40
    elif change=='digest': digest='b'*64
    else: data['source_layout']['categories'][1]['state']='LOCKED'
    second=DeploymentProfile(data,assets,digest)
    assert first.plan.digest != second.plan.digest
    from clash_rush_rebuild.mvp_deployment import DeploymentEngine, _copy_observation
    from test_deployment_repair import World
    world=World()
    engine=DeploymentEngine(observe=world.observe,deliver=world.deliver,release=world.release,
        live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=first.plan)
    assert engine.plan == first.plan
    engine.run()  # Retire the test's registered core even though its World differs.
    observation=observe(*source_pack(), source_frame(*source_pack()))
    assert _copy_observation(observation)==observation


@pytest.mark.parametrize('fault', ['missing_clan','extra_reserve','wrong_count','wrong_category',
    'duplicate_definition','unknown_category','no_selected_art','retain_clan','missing_siege',
    'unbound_projection','overlap_slots','aliased_state'])
def test_source_profile_reconciles_catalog_categories_and_projection(fault):
    data,assets=source_pack()
    if fault=='missing_clan': data['source_inventory'].pop()
    elif fault=='extra_reserve': data['source_inventory'].append(dict(data['source_inventory'][-1],identity='reserve.extra'))
    elif fault=='wrong_count': data['source_inventory'][0]['quantity']=2
    elif fault=='wrong_category': data['source_inventory'][0]['category']='clan'
    elif fault=='duplicate_definition': data['source_layout']['entries'].append(data['source_layout']['entries'][0].copy())
    elif fault=='unknown_category': data['source_layout']['entries'][0]['category']='unknown'
    elif fault=='no_selected_art': data['cards'][-1]['selected']=[]
    elif fault=='retain_clan': data['dispositions'][-1]['disposition']='RETAIN'
    elif fault=='missing_siege': data['source_layout']['categories'].pop()
    elif fault=='unbound_projection': data['source_inventory'][0]['identity']='ordinary.other'
    elif fault=='overlap_slots': data['source_layout']['categories'][0]['slots'][1]=data['source_layout']['categories'][0]['slots'][0]
    elif fault=='aliased_state': assets['source_selected']=assets['source_available'].copy()
    with pytest.raises(DeploymentError): DeploymentProfile(data,assets,'a'*64)


def test_post_source_observes_empty_ordinary_category_independently_of_reserves():
    data,assets=source_pack()
    c=data['source_layout']['categories'][0]
    c['empty_status']={'template':'source_empty','roi':c['status']['roi']}
    frame=source_frame(data,assets)
    box=c['status']['roi']; frame[int(box[1]*360):int(box[3]*360),:128]=0
    paint(frame,assets,c['empty_status'])
    box=c['slots'][0]; frame[int(box[1]*360):int(box[3]*360),:128]=0
    paint(frame,assets,{'template':'source_empty','roi':box})
    try: result=observe(data,assets,frame)
    except DeploymentError as error:pytest.fail('post-source empty category must remain observable: '+error.code)
    assert result.source_categories[0]==('ordinary','EMPTY')
    assert [(e.category,e.quantity) for e in result.source_inventory]==[('clan',4)]


def test_source_failure_does_not_advance_observer_sequence():
    data,assets=source_pack(); frame=source_frame(data,assets)
    frame[110:119,2:11]=0
    observer=FrameObserver(profile=DeploymentProfile(data,assets,'a'*64),capture=lambda:frame,
        monotonic=lambda:1.,intervention=lambda:Intervention.CLEAR,home_verified=lambda _:False,build_binding=BUILD_BINDING,installed_build=installed_build)
    with pytest.raises(DeploymentError):observer()
    assert observer.sequence==0


def test_calibrated_empty_slot_rejects_unrecognized_extra_art():
    data,assets=source_pack(); frame=source_frame(data,assets)
    frame[220:229,50:59]=assets['card']
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_source_capable_plan_cannot_seal_unspecified_occupant_state():
    from dataclasses import replace
    plan=DeploymentProfile(*source_pack(),'a'*64).plan
    entries=(replace(plan.source_inventory[0],state=None),)+plan.source_inventory[1:]
    with pytest.raises(DeploymentError):replace(plan,digest='',source_inventory=entries)


def test_source_hero_is_available_singleton_without_reading_portrait_as_quantity():
    data,assets=source_pack(); hero=copy.deepcopy(data['cards'][1]); hero.update(identity='king',kind='HERO')
    rng=np.random.default_rng(813)
    for state in ('icons','available','depleted','deployed','selected'):
        key='hero_'+state; assets[key]=rng.integers(0,255,(9,9,3),dtype=np.uint8)
        data['assets'][key]={'file':key+'.png','sha256':'b'*64}; hero[state]=[key]
    data['cards'].append(hero); data['plan']['roster'].append(dict(identity='king',initial_quantity=1))
    data['dispositions'].append(dict(identity='king',disposition='DEPLOY'))
    data['source_inventory'].insert(1,dict(identity='hero.king',category='hero',kind='HERO',spell='NONE',quantity=1))
    data['source_layout']['entries'].insert(1,dict(identity='hero.king',category='hero',kind='HERO',spell='NONE',
        capacity=1,icons=['source_troop']))
    data['source_layout']['categories'][1]['state']='POPULATED'
    frame=source_frame(data,assets)
    frame[144:198,128:256]=0  # No troop-count field in the calibrated hero slot.
    try: result=observe(data,assets,frame)
    except DeploymentError as error:pytest.fail('available source hero singleton rejected: '+error.code)
    hero=next(e for e in result.source_inventory if e.category=='hero')
    assert hero.quantity==1 and hero.state is CardState.AVAILABLE


@pytest.mark.parametrize('fault',['battle_identity','battle_state','source_identity'])
def test_ambiguous_calibration_is_rejected_before_attack_admission(fault):
    data,assets=source_pack()
    if fault=='battle_identity':data['cards'][-1]['icons']=data['cards'][0]['icons'][:]
    elif fault=='battle_state':data['cards'][-1]['selected']=data['cards'][0]['selected'][:]
    else:
        data['source_inventory'].append(dict(data['source_inventory'][-1],identity='reserve.other'))
        data['source_layout']['entries'].append(dict(data['source_layout']['entries'][-1],identity='reserve.other'))
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)


@pytest.mark.parametrize('role',['source'])
def test_source_provenance_requires_fresh_calibrated_context_pixels(role):
    data,assets=source_pack(); frame=source_frame(data,assets)
    try: positive=observe(data,assets,frame)
    except DeploymentError as error:pytest.fail('calibrated source context rejected: '+error.code)
    assert positive.source_context[3:]==('synthetic-build','REGULAR_MULTIPLAYER')
    frame=source_frame(data,assets); box=data['source_layout']['context'][role]['roi']
    frame[int(box[1]*360):int(box[3]*360),int(box[0]*640):int(box[2]*640)]=0
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_generated_v3_pack_loads_byte_bound_and_observes_real_source(tmp_path,monkeypatch):
    import test_deployment_profile as fixtures
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    monkeypatch.setattr(fixtures,'profile_data',source_pack)
    data,assets=fixtures.write_pack(tmp_path,runtime=True)
    profile=load_profile(tmp_path,candidate_tree='a'*40)
    frame=source_frame(data,assets)
    result=FrameObserver(profile=profile,capture=lambda:frame,monotonic=lambda:1.,
        intervention=lambda:Intervention.CLEAR,home_verified=lambda _:False,build_binding=BUILD_BINDING,installed_build=installed_build)()
    assert result.source_inventory==profile.plan.source_inventory
    assert result.source_context==profile.plan.source_context
    assert result.source_context[0]==profile.digest
    with pytest.raises(DeploymentError):load_profile(tmp_path,candidate_tree='b'*40)


def test_source_spell_quantity_is_read_from_uses_field():
    data,assets=source_pack()
    data['cards'][0].update(identity='lightning',kind='SPELL',spell='LIGHTNING')
    data['plan']['roster'][0].update(identity='lightning',initial_quantity=2)
    data['dispositions'][0]['identity']='lightning'
    data['source_inventory'][0].update(identity='spell.lightning',category='spell',kind='SPELL',spell='LIGHTNING',quantity=2)
    data['source_layout']['entries'][0].update(identity='spell.lightning',category='spell',kind='SPELL',spell='LIGHTNING')
    data['source_layout']['categories'][0]['state']='EMPTY'
    data['source_layout']['categories'][2]['state']='POPULATED'
    frame=source_frame(data,assets); frame[144:198,256:384]=0
    q,_=quantity('x2'); frame[148:148+q.shape[0],258:258+q.shape[1]]=q[:,:,None]
    result=observe(data,assets,frame)
    assert result.source_inventory[0].quantity==2
    assert result.source_inventory[0].spell.value=='LIGHTNING'
