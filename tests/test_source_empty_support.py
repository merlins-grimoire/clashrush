"""Source-bound support policy; generated pixels and synthetic input only."""
import copy
from dataclasses import replace

import pytest

from test_s004_source_images import source_pack, source_frame, observe
from test_s004_image_integration import ImageWorld
from clash_rush_rebuild.mvp_deployment import (
    CardKind, DeploymentError, DeploymentEngine, _copy_plan,
)
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile


def empty_support(data):
    data['source_layout']['allow_empty_support'] = True
    data['source_layout']['categories'][3]['state'] = 'EMPTY'
    data['source_inventory'] = [e for e in data['source_inventory'] if e['category'] != 'clan']
    data['source_layout']['entries'] = [e for e in data['source_layout']['entries'] if e['category'] != 'clan']
    support_ids = {c['identity'] for c in data['cards'] if c['kind'] == 'CLAN'}
    data['plan']['roster'] = [e for e in data['plan']['roster'] if e['identity'] not in support_ids]
    data['dispositions'] = [e for e in data['dispositions'] if e['identity'] not in support_ids]


def test_observed_empty_support_projects_no_clan_and_preserves_copies():
    data, assets = source_pack(); empty_support(data)
    profile = DeploymentProfile(data, assets, 'a'*64)
    observation = observe(data, assets, source_frame(data, assets))
    assert observation.source_categories[3] == ('clan', 'EMPTY')
    assert not any(e.category == 'clan' for e in observation.source_inventory)
    assert not any(e[1] is CardKind.CLAN for e in profile.plan.roster)
    assert _copy_plan(profile.plan) == profile.plan
    world = ImageWorld()
    engine = DeploymentEngine(observe=world.observer, deliver=lambda *_: False,
        release=lambda: True, live_gate=lambda: True, monotonic=world.clock,
        sleep=world.clock.sleep, plan=profile.plan)
    assert engine.plan == profile.plan
    engine.run()


@pytest.mark.parametrize('opt_in', [False, True])
def test_populated_support_retains_one_semantic_entry_and_separate_identities(opt_in):
    data, assets = source_pack()
    if opt_in: data['source_layout']['allow_empty_support'] = True
    profile = DeploymentProfile(data, assets, 'a'*64)
    observation = observe(data, assets, source_frame(data, assets))
    assert [e.identity for e in observation.source_inventory] == ['ordinary.troop', 'reserve.troop']
    assert sum(e[1] is CardKind.CLAN for e in profile.plan.roster) == 1
    assert _copy_plan(profile.plan) == profile.plan


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_support_policy_requires_exact_boolean(value):
    data, assets = source_pack(); data['source_layout']['allow_empty_support'] = value
    with pytest.raises(DeploymentError): DeploymentProfile(data, assets, 'a'*64)


def test_legacy_profile_still_requires_populated_support():
    data, assets = source_pack(); empty_support(data)
    del data['source_layout']['allow_empty_support']
    with pytest.raises(DeploymentError, match='SOURCE_CATEGORY_MISMATCH'):
        DeploymentProfile(data, assets, 'a'*64)


@pytest.mark.parametrize('state', ['LOCKED', 'UNKNOWN', '', None])
def test_empty_is_not_unknown_or_unsupported(state):
    data, assets = source_pack(); empty_support(data)
    data['source_layout']['categories'][3]['state'] = state
    with pytest.raises(DeploymentError): DeploymentProfile(data, assets, 'a'*64)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'extra'])
def test_populated_source_rejects_bad_plan_projection(fault):
    data, assets = source_pack(); data['source_layout']['allow_empty_support'] = True
    if fault == 'missing':
        data['plan']['roster'].pop(); data['dispositions'].pop()
    else:
        entry = copy.deepcopy(data['plan']['roster'][-1])
        if fault == 'extra':
            spec = copy.deepcopy(data['cards'][-1]); spec['identity'] = 'other_support'
            data['cards'].append(spec); entry['identity'] = spec['identity']
        data['plan']['roster'].append(entry)
        data['dispositions'].append(dict(identity=entry['identity'], disposition='DEPLOY'))
    with pytest.raises(DeploymentError): DeploymentProfile(data, assets, 'a'*64)


@pytest.mark.parametrize('fault', ['missing_category', 'missing_status', 'ambiguous', 'unknown', 'populated'])
def test_empty_support_still_requires_positive_complete_pixels(fault):
    data, assets = source_pack(); empty_support(data)
    frame = source_frame(data, assets); c = data['source_layout']['categories'][3]
    if fault in ('missing_category', 'missing_status'):
        box = c['header' if fault == 'missing_category' else 'status']['roi']
        l,t,r,b = [int(v*s) for v,s in zip(box,(640,360,640,360))]; frame[t:b,l:r] = 0
    else:
        l,t,r,b = [int(v*s) for v,s in zip(c['slots'][0],(640,360,640,360))]
        frame[t+15:t+24,l+20:l+29] = assets['source_available' if fault == 'ambiguous' else
                                                      'source_troop' if fault == 'populated' else 'card']
    with pytest.raises(DeploymentError): observe(data, assets, frame)


class EmptyWorld(ImageWorld):
    def __init__(self):
        super().__init__(); empty_support(self.data); self.support_remaining = 0
        self.profile = DeploymentProfile(self.data, self.assets, 'a'*64)
        self.observer.profile = self.profile
    def capture(self):
        frame = super().capture()
        if self.screen == 'BATTLE': frame[round(360*.82):,120:200] = 0
        return frame


def test_empty_support_cannot_generate_select_or_deploy_input():
    world = EmptyWorld(); result = world.run()
    assert result.complete, result
    assert result.natural_result and result.returned_home
    assert [e.verb for e in world.events if e.card_id == 'troop'] == ['select', 'hold']
    assert not any(e.card_id == 'clan_castle' for e in world.events)


@pytest.mark.parametrize('empty,fault', [(True,'extra'), (False,'missing'), (False,'extra'), (False,'duplicate')])
def test_battle_projection_must_match_observed_support_before_any_deployment(empty, fault):
    from clash_rush_rebuild.mvp_deployment import Screen, Point
    world = EmptyWorld() if empty else ImageWorld(); original = world.observer
    # Real pixel observer first, then inject a contradictory typed Battle projection.
    def observe_fault():
        obs = original()
        if obs.screen is Screen.BATTLE:
            cards = list(obs.page.cards)
            if fault == 'missing': cards = [c for c in cards if c.kind is not CardKind.CLAN]
            elif empty:
                from test_s004_occupied_unused import clan_card
                cards.append(clan_card(x=.4))
            else:
                support = next(c for c in cards if c.kind is CardKind.CLAN)
                cards.append(replace(support, identity='extra_support' if fault == 'extra' else support.identity,
                                     point=Point(.4,.9)))
            obs = replace(obs,page=replace(obs.page,cards=tuple(cards)))
        return obs
    world.observer = observe_fault
    result = world.run()
    assert not result.complete
    assert not any(e.verb in ('select','tap','hold') for e in world.events)


@pytest.mark.parametrize('fault', ['policy', 'state', 'content', 'context'])
def test_support_authority_rejects_stale_seal(fault):
    data, assets = source_pack(); data['source_layout']['allow_empty_support'] = True
    plan = DeploymentProfile(data, assets, 'a'*64).plan
    changes = {'allow_empty_support': False} if fault == 'policy' else (
        {'source_categories': tuple((k,'EMPTY' if k=='clan' else v) for k,v in plan.source_categories)}
        if fault == 'state' else {'source_inventory': plan.source_inventory[:-1] +
            (replace(plan.source_inventory[-1],quantity=2),)} if fault == 'content' else {'source_context': ()})
    with pytest.raises(DeploymentError): replace(plan, **changes)


def test_policy_changes_plan_digest_without_changing_legacy_digest():
    data, assets = source_pack(); old = DeploymentProfile(data, assets, 'a'*64).plan
    data['source_layout']['allow_empty_support'] = False
    assert DeploymentProfile(data, assets, 'a'*64).plan.digest == old.digest
    data['source_layout']['allow_empty_support'] = True
    assert DeploymentProfile(data, assets, 'a'*64).plan.digest != old.digest


@pytest.mark.parametrize('empty,number', [(True,1),(True,2),(False,0),(False,2)])
def test_compiled_support_cardinality_is_source_derived(empty, number):
    from clash_rush_rebuild.mvp_deployment import Spell, DeploymentDisposition
    data, assets = source_pack()
    if empty: empty_support(data)
    else: data['source_layout']['allow_empty_support'] = True
    plan = DeploymentProfile(data, assets, 'a'*64).plan
    roster = tuple(e for e in plan.roster if e[1] is not CardKind.CLAN) + tuple(
        ('support'+str(i),CardKind.CLAN,Spell.NONE,1) for i in range(number))
    with pytest.raises(DeploymentError,match='SOURCE_PROJECTION_INVALID'):
        replace(plan,digest='',roster=roster,dispositions=tuple(
            (e[0],DeploymentDisposition.DEPLOY) for e in roster))


@pytest.mark.parametrize('fault', ['state','contents','missing','context'])
def test_fresh_source_support_change_blocks_final_attack(fault):
    from clash_rush_rebuild.mvp_deployment import Screen
    world = EmptyWorld(); original = world.observer; reads = [0]
    populated = DeploymentProfile(*source_pack(),'b'*64).plan
    def changed():
        obs = original()
        if obs.screen is Screen.ARMY:
            reads[0] += 1
            if reads[0] > 1:
                if fault == 'state': obs=replace(obs,source_categories=populated.source_categories)
                elif fault == 'contents': obs=replace(obs,source_inventory=populated.source_inventory)
                elif fault == 'missing': obs=replace(obs,source_categories=())
                else: obs=replace(obs,source_context=populated.source_context)
        return obs
    world.observer=changed; result=world.run()
    assert not result.complete
    assert [e.verb for e in world.events] == ['attack','find_match']


def test_canonical_policy_bytes_bind_profile_and_plan(tmp_path,monkeypatch):
    import json
    import test_deployment_profile as fixture
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    monkeypatch.setattr(fixture,'profile_data',source_pack)
    fixture.write_pack(tmp_path,runtime=False)
    first=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    path=tmp_path/'private/deployment/profile.json'; data=json.loads(path.read_text())
    data['source_layout']['allow_empty_support']=True
    path.write_bytes(json.dumps(data,sort_keys=True,separators=(',',':')).encode()+b'\n')
    second=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    assert first.digest!=second.digest and first.plan.digest!=second.plan.digest
    object.__setattr__(second.plan,'allow_empty_support',False)
    with pytest.raises(DeploymentError,match='PLAN_DIGEST_MISMATCH'):_copy_plan(second.plan)
