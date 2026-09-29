"""Synthetic-only RED specification. No Windows imports, game frames or network."""
from dataclasses import replace
import json
import math
import pytest

from clash_rush_rebuild import mvp_deployment as deployment_module
from clash_rush_rebuild.mvp_deployment import (
    Budget, Card, CardKind, CardState, Spell, Page, Observation, Screen,
    Point, Target, TargetKind, Policy, DeploymentError, DeploymentEngine,
    Inventory, DeploymentResult, Intervention, Action,
    CompiledDeploymentPlan,
)


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


def card(name='troop', kind=CardKind.TROOP, count=2, spell=Spell.NONE,
         state=CardState.AVAILABLE, selected=False, x=.15):
    return Card(name, kind, spell, count, state, Point(x, .92), selected)


def page(cards, *, left=True, right=True):
    return Page(tuple(cards), left, right)


class World:
    """Closed synthetic environment, exercising the real state machine."""
    def __init__(self, cards=None):
        self.clock = Clock()
        self.cards = list(cards or [card(), card('zap', CardKind.SPELL, 2, Spell.LIGHTNING, x=.25)])
        self.screen = Screen.BATTLE
        self.selected = None
        self.events = []
        self.intervention = Intervention.CLEAR
        self.seq = 0
        self.no_progress = False
        self.fail_at = None
        self.complete_targets = True
        self.closed = False
        self.with_confirm = True
        self.exit_at = None
        self.natural_result_after = 6
        self.terminal_reads = 0
    def observe(self):
        self.seq += 1
        self.clock.now += .01
        if (self.screen is Screen.BATTLE and self.cards
                and all(item.exhausted for item in self.cards)):
            self.terminal_reads += 1
            if (self.natural_result_after is not None
                    and self.terminal_reads >= self.natural_result_after):
                self.screen = Screen.RESULT
        cards = tuple(replace(c, selected=c.identity == self.selected) for c in self.cards)
        targets = () if not self.complete_targets else (
            Target(TargetKind.GROUND, Point(.3,.7), 1.0),
            Target(TargetKind.ENEMY_STRUCTURE, Point(.5,.4), 1.0),
            Target(TargetKind.WALL_CLUSTER, Point(.5,.5), 1.0),
            Target(TargetKind.FRIENDLY_COHORT, Point(.45,.55), 1.0),
            Target(TargetKind.INJURED_COHORT, Point(.46,.56), 1.0),
        )
        controls = {
            Screen.HOME: (('attack',Point(.08,.85)),),
            Screen.MATCH: (('find_match',Point(.2,.7)),),
            Screen.ARMY: (('army_attack',Point(.9,.85)),),
            Screen.BATTLE: (('end_battle',Point(.1,.75)),),
            Screen.END_CONFIRM: (('confirm_end',Point(.5,.6)),),
            Screen.RESULT: (('return_home',Point(.5,.83)),),
        }.get(self.screen, ())
        return Observation(self.seq, self.clock(), 'view-a', self.screen,
            page(cards) if self.screen is Screen.BATTLE else None,
            targets, controls, self.intervention, self.screen is Screen.HOME)
    def deliver(self, intent, check):
        if not check(): return False
        self.events.append(intent)
        if self.fail_at == len(self.events): raise RuntimeError('private must not escape')
        if intent.verb == 'select': self.selected = intent.card_id
        elif intent.verb in ('hold','tap'):
            self.clock.now += intent.duration
            if not self.no_progress:
                for i,c in enumerate(self.cards):
                    if c.identity == intent.card_id:
                        q = c.remaining - 1
                        self.cards[i] = replace(c, remaining=q,
                            state=CardState.DEPLETED if q==0 else CardState.AVAILABLE)
        elif intent.verb == 'end_battle':
            self.exit_at = self.clock()
            self.screen = Screen.END_CONFIRM if self.with_confirm else Screen.RESULT
        elif intent.verb == 'confirm_end': self.screen = Screen.RESULT
        elif intent.verb == 'return_home': self.screen = Screen.HOME
        elif intent.verb == 'attack': self.screen = Screen.MATCH
        elif intent.verb == 'find_match': self.screen = Screen.ARMY
        elif intent.verb == 'army_attack': self.screen = Screen.BATTLE
        return True
    def release(self): self.closed = True; return True
    def engine(self, **kwargs):
        if 'plan' not in kwargs:
            try:
                kwargs['plan']=CompiledDeploymentPlan(tuple(
                    (c.identity,c.kind,c.spell,c.remaining) for c in self.cards),6)
            except DeploymentError:
                kwargs['plan']=None
        return DeploymentEngine(observe=self.observe, deliver=self.deliver,
            release=self.release, live_gate=lambda: True,
            monotonic=self.clock, sleep=self.clock.sleep, **kwargs)


def test_lightning_two_uses_are_discrete_count_verified_taps():
    w=World(); r=w.engine().run()
    assert r.complete and r.home_verified and not r.own_exit
    spells=[e for e in w.events if e.card_id=='zap' and e.verb in ('tap','hold')]
    assert [e.verb for e in spells] == ['tap','tap']
    assert r.spells_consumed == 2 and r.remaining == 0
    assert w.closed

@pytest.mark.parametrize('kind',[CardKind.HERO,CardKind.CLAN])
def test_singletons_get_one_placement_not_holds(kind):
    w=World([card('one',kind,1)]); r=w.engine().run()
    assert r.complete
    assert len([e for e in w.events if e.verb=='tap'])==1
    assert not any(e.verb=='hold' for e in w.events)

@pytest.mark.parametrize('kind,spell',[(CardKind.UNKNOWN,Spell.NONE),(CardKind.SPELL,Spell.UNKNOWN)])
def test_unknown_type_or_spell_has_no_deployment(kind,spell):
    w=World([card('unknown',kind,1,spell)])
    r=w.engine().run()
    assert not r.complete
    assert not any(e.verb in ('select','hold','tap') for e in w.events)

@pytest.mark.parametrize('spell',[Spell.LIGHTNING,Spell.EARTHQUAKE,Spell.RAGE,Spell.HEAL])
def test_every_supported_spell_policy_consumes_quantity(spell):
    w=World([card('troop',count=4),card('spell',CardKind.SPELL,2,spell,x=.3)])
    r=w.engine().run(); assert r.complete and r.spells_consumed==2


def test_unknown_target_does_not_cast_or_choose_center():
    w=World([card('spell',CardKind.SPELL,2,Spell.HEAL)])
    w.complete_targets=False; r=w.engine().run()
    assert not r.complete
    assert not any(e.verb in ('tap','hold') for e in w.events)


def test_troop_holds_are_bounded_and_count_decreases():
    w=World([card(count=3)]); r=w.engine().run()
    assert r.complete
    holds=[e.duration for e in w.events if e.verb=='hold']
    assert len(holds)==3 and all(0<d<=.25 for d in holds)


def test_first_pass_cannot_override_global_deadline():
    w=World([card(f't{i}',count=999,x=.1+i*.15) for i in range(5)])
    w.no_progress=True
    r=w.engine(policy=Policy(deployment_seconds=1.0)).run()
    assert not r.complete and w.clock() < 1.6
    assert not any(e.verb=='end_battle' for e in w.events)


def test_slow_capture_cannot_authorize_late_field_input():
    w=World(); original=w.observe
    def capture():
        o=original(); w.clock.now+=61.0; return o
    e=w.engine(); e.observe=capture; r=e.run()
    assert not r.complete and not w.events


def test_grey_false_is_not_success_without_decrement():
    w=World([card(count=2)]); w.no_progress=True
    r=w.engine().run(); assert not r.complete
    assert len([e for e in w.events if e.verb=='hold'])==1


def test_nr01_nr02_terminal_battle_waits_read_only_then_returns_home_once():
    w=World([card(count=1)]); r=w.engine().run()
    assert r.complete and r.home_verified and not r.own_exit
    assert w.terminal_reads >= w.natural_result_after
    assert [e.verb for e in w.events if e.verb in {
        'end_battle', 'confirm_end', 'return_home'}] == ['return_home']


def test_nr02_natural_result_may_arrive_after_deployment_budget_before_visit_reserve():
    w=World([card(count=1)]); w.natural_result_after=20
    result=w.engine(policy=Policy(
        deployment_seconds=.5, visit_seconds=3.0, exit_seconds=1.0)).run()
    assert result.complete and result.home_verified and not result.own_exit
    assert w.clock() > .5
    assert [e.verb for e in w.events if e.verb in {
        'end_battle', 'confirm_end', 'return_home'}] == ['return_home']


@pytest.mark.parametrize('fault,kind,expected_reason', [
    ('revived', CardKind.TROOP, 'UNEXPLAINED_CARD_CHANGE'),
    ('state_changed', CardKind.HERO, 'UNEXPLAINED_CARD_CHANGE'),
    ('stale', CardKind.TROOP, 'OBSERVATION_STALE'),
    ('malformed', CardKind.TROOP, 'OBSERVATION_INVALID'),
])
def test_nr_wait_reconciles_every_battle_observation_atomically(fault, kind, expected_reason):
    w=World([card(kind=kind, count=1)])
    original=w.observe
    injected=[False]

    def observe():
        observation=original()
        if (not injected[0] and observation.screen is Screen.BATTLE
                and w.terminal_reads >= 5):
            injected[0]=True
            if fault == 'revived':
                changed=replace(observation.page.cards[0], remaining=1,
                                state=CardState.AVAILABLE)
                observation=replace(observation, page=page([changed]))
            elif fault == 'state_changed':
                changed=replace(observation.page.cards[0], state=CardState.DEPLOYED)
                observation=replace(observation, page=page([changed]))
            elif fault == 'stale':
                object.__setattr__(observation, 'sequence', 1)
            else:
                object.__setattr__(observation, 'targets', list(observation.targets))
        return observation

    w.observe=observe
    result=w.engine().run()

    assert injected[0]
    assert not result.complete
    assert result.reason == expected_reason
    assert result.remaining == 0
    expected_deploy = 'tap' if kind is CardKind.HERO else 'hold'
    assert [event.verb for event in w.events] == ['select', expected_deploy]
    assert not any(event.verb in {'end_battle', 'confirm_end', 'return_home'}
                   for event in w.events)
    assert w.closed


def test_nr_wait_reconciles_valid_unchanged_terminal_battle(monkeypatch):
    w=World([card(count=1)])
    original_sync=deployment_module._sync
    reconciled_terminal_reads=[]

    def sync(core, observation, permitted=None):
        if (observation.screen is Screen.BATTLE and w.terminal_reads >= 5
                and all(item.exhausted for item in observation.page.cards)):
            reconciled_terminal_reads.append(w.terminal_reads)
        return original_sync(core, observation, permitted)

    monkeypatch.setattr(deployment_module, '_sync', sync)
    result=w.engine().run()

    assert result.complete and result.home_verified and not result.own_exit
    assert 5 in reconciled_terminal_reads
    assert [event.verb for event in w.events if event.verb in {
        'end_battle', 'confirm_end', 'return_home'}] == ['return_home']


def test_nr03_natural_result_timeout_is_incomplete_without_end_fallback():
    w=World([card(count=1)]); w.natural_result_after=None
    result=w.engine(policy=Policy(visit_seconds=2.0, exit_seconds=1.0)).run()
    assert not result.complete and not result.own_exit
    assert not any(e.verb in {'end_battle','confirm_end','return_home'} for e in w.events)


def test_manual_surrender_cannot_satisfy_success():
    w=World(); w.screen=Screen.RESULT
    r=w.engine().run(); assert not r.complete and not r.own_exit
    assert not w.events


@pytest.mark.parametrize('fault', ['malformed','stale','unknown','intervention'])
def test_nr04_wait_faults_are_incomplete_without_end_or_success(fault):
    w=World([card(count=1)]); original=w.observe
    def observe():
        observation=original()
        if w.screen is Screen.BATTLE and w.terminal_reads >= 5:
            if fault=='malformed':
                object.__setattr__(observation,'targets',list(observation.targets))
            elif fault=='stale':
                object.__setattr__(observation,'sequence',1)
            elif fault=='unknown':
                object.__setattr__(observation,'screen',Screen.UNKNOWN)
                object.__setattr__(observation,'page',None)
            else:
                object.__setattr__(observation,'intervention',Intervention.DETECTED)
        return observation
    w.observe=observe
    result=w.engine().run()
    assert not result.complete
    assert not any(e.verb in {'end_battle','confirm_end','return_home'} for e in w.events)


def test_nr05_immediate_end_confirm_is_not_a_supported_success_intent():
    w=World([card(count=1)])
    result=w.engine().run()
    assert result.complete
    assert not result.own_exit
    assert not any(e.verb in {'end_battle','confirm_end'} for e in w.events)


@pytest.mark.parametrize('intervention',[Intervention.DETECTED,Intervention.UNKNOWN])
def test_intervention_blocks_new_input_and_releases(intervention):
    w=World(); w.intervention=intervention
    r=w.engine().run(); assert not r.complete and not w.events and w.closed


def test_input_exception_is_sanitized_and_release_runs():
    w=World(); w.fail_at=2; r=w.engine().run()
    assert not r.complete and w.closed
    assert 'private' not in json.dumps(r.public_payload())


def test_navigation_runs_complete_gated_chain():
    w=World([card(count=1)]); w.screen=Screen.HOME
    r=w.engine().run()
    assert r.complete
    assert [e.verb for e in w.events][:3]==['attack','find_match','army_attack']


def test_inventory_rejects_cross_page_coverage_even_with_overlap():
    i=Inventory()
    with pytest.raises(DeploymentError,match='SINGLE_PAGE_COVERAGE_UNPROVED'):
        i.add(page([card('a'),card('b',x=.3),card('c',x=.5)],right=False))


def test_missing_overlap_is_not_end_of_bar():
    i=Inventory()
    with pytest.raises(DeploymentError): i.add(page([card('a'),card('b',x=.4)],right=False))


def test_repeated_portrait_overlap_is_ambiguous_not_guessed():
    i=Inventory()
    with pytest.raises(DeploymentError): i.add(page([card('a'),card('a',x=.4)],right=False))

@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),-1.0])
def test_invalid_clock_fails_closed(value):
    with pytest.raises(DeploymentError): Budget(lambda:value,lambda _s:None,10.0)


def test_clock_regression_rejected():
    clock=Clock(); b=Budget(clock,clock.sleep,10)
    clock.now=1.; b.check(); clock.now=.5
    with pytest.raises(DeploymentError): b.check()


def test_receipt_cannot_promote_home_alone():
    r=DeploymentResult(False,'INTERVENTION',0,0,False,True,True)
    assert not r.autonomous(True)
    assert not r.autonomous(False)


def test_ground_evidence_may_appear_only_after_card_selection():
    w=World([card(count=1)]); original=w.observe
    def observe():
        o=original()
        return replace(o,targets=() if w.selected is None else o.targets)
    w.observe=observe
    assert w.engine().run().complete


def test_troop_bursts_rotate_only_among_current_proved_ground_points():
    w=World([card(count=3)]); original=w.observe
    def observe():
        o=original()
        return replace(o,targets=(Target(TargetKind.GROUND,Point(.2,.7),1.),
                                  Target(TargetKind.GROUND,Point(.7,.6),1.)))
    w.observe=observe
    assert w.engine().run().complete
    assert [e.point for e in w.events if e.verb=='hold']==[Point(.2,.7),Point(.7,.6),Point(.2,.7)]


def test_one_frame_false_decrement_never_permits_second_cast():
    w=World([card('zap',CardKind.SPELL,2,Spell.LIGHTNING)]); original=w.observe
    w.no_progress=True; first_decrement=[True]
    def observe():
        o=original()
        if any(e.verb=='tap' for e in w.events) and first_decrement[0]:
            first_decrement[0]=False
            return replace(o,page=page([replace(o.page.cards[0],remaining=1)]))
        return o
    w.observe=observe
    r=w.engine().run()
    assert not r.complete and r.spells_consumed==0
    assert len([e for e in w.events if e.verb=='tap'])==1


def test_manual_result_between_last_decrement_and_exit_is_not_success():
    w=World([card(count=1)]); original=w.observe; calls=[0]
    def observe():
        if w.cards[0].remaining==0:
            calls[0]+=1
            if calls[0]>1:
                w.intervention=Intervention.DETECTED
                w.screen=Screen.RESULT
        return original()
    w.observe=observe
    r=w.engine().run()
    assert not r.complete and not r.own_exit
    assert not any(e.verb=='end_battle' for e in w.events)


def test_multiple_known_pages_have_no_active_scrolling_authority():
    class ScrollingWorld(World):
        def __init__(self):
            super().__init__([card(f't{i}',count=1,x=.1+i*.1) for i in range(6)])
            self.page_index=0
        def observe(self):
            o=super().observe()
            if o.screen is not Screen.BATTLE: return o
            start=(0,2,4)[self.page_index]
            cs=tuple(replace(c,point=Point(.15+j*.2,.92)) for j,c in enumerate(o.page.cards[start:start+3]))
            return replace(o,page=page(cs,left=start==0,right=start==4),
                           controls=o.controls+(('scroll_left',cs[0].point),('scroll_right',cs[-1].point)))
        def deliver(self,intent,check):
            if intent.verb.startswith('scroll_'):
                if check() is not True: return False
                self.events.append(intent)
                self.page_index+=1 if intent.verb=='scroll_right' else -1
                return True
            return super().deliver(intent,check)
    w=ScrollingWorld(); r=w.engine().run()
    assert not r.complete and r.reason=='OBSERVATION_UNAVAILABLE'
    assert not w.events


def test_support_spell_waits_read_only_for_a_current_eligible_cohort():
    w=World([card('heal',CardKind.SPELL,1,Spell.HEAL)]); original=w.observe
    def observe():
        o=original()
        return replace(o,targets=() if w.clock()<.4 else o.targets)
    w.observe=observe
    assert w.engine().run().complete
    assert len([e for e in w.events if e.verb=='tap'])==1
