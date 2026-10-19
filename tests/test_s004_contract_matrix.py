"""Frozen owner matrix over the support projection and source authority."""
from dataclasses import replace
import pytest
from test_s004_occupied_unused import SourceWorld,occupied_unused_plan
from clash_rush_rebuild import mvp_deployment as d


def engine(world,**ports):
    return d.DeploymentEngine(observe=ports.get('observe',world.observe),
        deliver=ports.get('deliver',world.deliver),release=world.release,
        live_gate=ports.get('gate',lambda:True),monotonic=world.clock,sleep=world.clock.sleep,
        plan=occupied_unused_plan())


@pytest.mark.parametrize('fault',['missing','extra','duplicate','unknown','quantity','clipped','ambiguous'])
@pytest.mark.parametrize('during',['initial','progress','wait'])
def test_complete_battle_projection_rejects_every_clan_fault_atomically(fault,during):
    world=SourceWorld(); world.natural_result_after=None
    original=world.observe; captured=[]; subject=None; triggered=False
    def observe():
        nonlocal triggered
        obs=original()
        if obs.screen is not d.Screen.BATTLE:return obs
        eligible=(during=='initial' or (during=='progress' and any(e.verb=='hold' for e in world.events))
                  or (during=='wait' and all(c.exhausted for c in world.cards)))
        if not eligible:return obs
        core=d._entry_for(subject).core
        captured.append((core,core.sequence,dict(core.cards),core.inventory,len(world.events)))
        cards=list(obs.page.cards); clan=cards[2]
        if fault=='missing':cards.pop(2)
        elif fault=='extra':cards.append(replace(clan,identity='extra'))
        elif fault=='duplicate':cards.append(clan)
        elif fault=='unknown':cards[2]=replace(clan,identity='unknown_shield')
        elif fault=='quantity':cards[2]=replace(clan,remaining=1 if clan.exhausted else 0,
            state=d.CardState.AVAILABLE if clan.exhausted else d.CardState.DEPLETED)
        elif fault=='ambiguous':cards[2]=replace(clan,state=d.CardState.UNKNOWN)
        triggered=True
        return replace(obs,page=d.Page(tuple(cards),True,fault!='clipped'))
    subject=engine(world,observe=observe); result=subject.run()
    assert triggered and not result.complete
    core,seq,cards,inventory,events=captured[-1]
    assert core.sequence==seq and core.cards==cards and core.inventory is inventory
    assert len(world.events)==events
    assert not any(e.verb in ('end_battle','confirm_end') for e in world.events)


@pytest.mark.parametrize('fault',['missing','extra','duplicate','quantity','provenance','category','build','mode','profile'])
def test_source_mismatch_rolls_back_sequence_and_prevents_match_entry(fault):
    world=SourceWorld(); original=world.observe; subject=None; captured=[]
    def observe():
        obs=original()
        if obs.screen is not d.Screen.ARMY:return obs
        core=d._entry_for(subject).core; captured.append((core,core.sequence))
        entries=list(obs.source_inventory); context=obs.source_context
        if fault=='missing':entries.pop()
        elif fault=='extra':entries.append(replace(entries[-1],identity='reserve.extra'))
        elif fault=='duplicate':entries.append(entries[-1])
        elif fault=='quantity':entries[-1]=replace(entries[-1],quantity=3)
        elif fault=='provenance':entries[-1]=replace(entries[-1],provenance='OTHER_SOURCE')
        elif fault=='category':entries[-1]=replace(entries[-1],category='ordinary')
        elif fault=='build':context=context[:3]+('other-build',context[4])
        elif fault=='mode':context=context[:4]+('OTHER_MODE',)
        elif fault=='profile':context=('d'*64,)+context[1:]
        return replace(obs,source_inventory=tuple(entries),source_context=context)
    subject=engine(world,observe=observe); result=subject.run()
    assert not result.complete and captured
    core,sequence=captured[-1]; assert core.sequence==sequence
    assert [e.verb for e in world.events]==['attack','find_match']


@pytest.mark.parametrize('verb',['attack','find_match','army_attack'])
@pytest.mark.parametrize('fault',['wrong_screen','stale','replayed','revoked','wrong_phase','deadline'])
def test_final_source_delivery_requires_fresh_owned_stage(verb,fault):
    world=SourceWorld(); original=world.observe; gate=[True]; subject=None; observed=[]
    def observe():
        obs=original(); observed.append(obs); return obs
    def deliver(intent,proof):
        if intent.verb!=verb:return world.deliver(intent,proof)
        if fault=='wrong_screen':world.screen=d.Screen.RESULT
        elif fault=='stale':world.clock.now+=3
        elif fault=='revoked':gate[0]=False
        elif fault=='wrong_phase':d._entry_for(subject).core.source_stage='NONE'
        elif fault=='deadline':world.clock.now=intent.deadline
        if fault=='stale':
            d._entry_for(subject).core.ports=(lambda:observed[-1],)+d._entry_for(subject).core.ports[1:]
        if fault=='replayed':
            assert proof() is True
        assert proof() is False
        return False
    subject=engine(world,observe=observe,deliver=deliver,gate=lambda:gate[0]); result=subject.run()
    assert not result.complete
    assert not any(e.verb==verb for e in world.events)
    assert len(world.events)==['attack','find_match','army_attack'].index(verb)


@pytest.mark.parametrize('tamper',['card','alias','verb','disposition','payload'])
def test_clan_callback_and_detached_authority_tampering_cannot_grant_delivery(tamper):
    world=SourceWorld(); subject=None; attacked=False
    def deliver(intent,proof):
        nonlocal attacked
        if intent.card_id is not None and not attacked:
            attacked=True
            if tamper=='card':object.__setattr__(intent,'card_id','clan_castle')
            elif tamper=='alias':object.__setattr__(intent,'card_id',' CLAN_CASTLE ')
            elif tamper=='verb':object.__setattr__(intent,'verb','source_open')
            else:
                detached=subject.plan
                if tamper=='disposition':object.__setattr__(detached,'dispositions',())
                else:object.__setattr__(detached.source_inventory[-1],'quantity',99)
            if tamper in ('card','alias','verb'):
                assert proof() is False; return False
        return world.deliver(intent,proof)
    subject=engine(world,deliver=deliver); result=subject.run()
    assert attacked
    assert result.complete is (tamper in ('disposition','payload'))
    assert [e.verb for e in world.events if e.card_id=='clan_castle']==(
        ['select','tap'] if tamper in ('disposition','payload') else [])


@pytest.mark.parametrize('field',['dispositions','source_inventory','source_categories','source_context'])
def test_plan_seal_detects_support_authority_mutation(field):
    plan=occupied_unused_plan(); object.__setattr__(plan,field,())
    with pytest.raises(d.DeploymentError):plan.validated_digest()


def test_support_contract_cannot_start_in_battle_with_only_expected_source_payload():
    world=SourceWorld(); world.screen=d.Screen.BATTLE
    result=engine(world).run()
    assert not world.events
    assert result.reason=='SOURCE_PRECOMMIT_REQUIRED' and not result.complete


def test_ordinary_selector_cannot_alias_support_control():
    world=SourceWorld(); clan=world.cards[2]
    world.cards[0]=replace(world.cards[0],point=clan.point)
    result=engine(world).run()
    assert not any(e.verb=='select' and e.point.near(clan.point) for e in world.events)
    assert not result.complete


@pytest.mark.parametrize('verb',['source-open','SOURCE_OPEN','unknown','clan_tap'])
def test_unclassified_or_aliased_source_verb_never_reaches_mouse(verb):
    from test_deployment_input import subject
    guarded,_,mouse,_=subject()
    intent=d.Intent('attack',d.Point(.4,.4),d.Action.ATTACK_NAVIGATION,deadline=4)
    object.__setattr__(intent,'verb',verb)
    assert guarded.deliver(intent,lambda:True) is False
    assert mouse.events==[]
