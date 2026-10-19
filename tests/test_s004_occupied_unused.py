from dataclasses import replace
import pytest

from test_deployment_repair import World, card, page

from clash_rush_rebuild.mvp_deployment import (
    Action,
    Card,
    CardKind,
    CardState,
    CompiledDeploymentPlan,
    DeploymentDisposition,
    DeploymentEngine,
    Intent,
    Intervention,
    Observation,
    Point,
    Screen,
    SourceInventoryEntry,
    Spell,
)
from clash_rush_rebuild.mvp_deployment_input import GuardedDeploymentInput


@pytest.mark.parametrize('screen', [Screen.HOME, Screen.MATCH, Screen.ARMY])
def test_legacy_plan_never_admits_attack_without_source(screen):
    world = World()
    world.screen = screen
    result = world.engine().run()
    assert not any(e.verb in {'attack', 'find_match', 'army_attack'} for e in world.events)
    assert not result.complete
    assert result.reason in ('SOURCE_PRECOMMIT_REQUIRED','SOURCE_STAGE_INVALID')


def clan_card(*, x=.35, selected=False):
    return card("clan_castle", CardKind.CLAN, 1, Spell.NONE, selected=selected, x=x)


def occupied_unused_plan():
    def source(*args):
        return SourceInventoryEntry(*args,state=CardState.AVAILABLE)
    return CompiledDeploymentPlan(
        (
            ("wall_breaker", CardKind.TROOP, Spell.NONE, 3),
            ("goblin", CardKind.TROOP, Spell.NONE, 2),
            ("clan_castle", CardKind.CLAN, Spell.NONE, 1),
            ("king", CardKind.HERO, Spell.NONE, 1),
            ("lightning", CardKind.SPELL, Spell.LIGHTNING, 2),
        ),
        6,
        dispositions=(
            ("wall_breaker", DeploymentDisposition.DEPLOY),
            ("goblin", DeploymentDisposition.DEPLOY),
            ("clan_castle", DeploymentDisposition.DEPLOY),
            ("king", DeploymentDisposition.DEPLOY),
            ("lightning", DeploymentDisposition.DEPLOY),
        ),
        source_inventory=(
            source("ordinary.wall_breaker", "ordinary", CardKind.TROOP, Spell.NONE, 3),
            source("ordinary.goblin", "ordinary", CardKind.TROOP, Spell.NONE, 2),
            source("hero.king", "hero", CardKind.HERO, Spell.NONE, 1),
            source("spell.lightning", "spell", CardKind.SPELL, Spell.LIGHTNING, 2),
            source("reserve.goblin", "clan", CardKind.TROOP, Spell.NONE, 4),
            source("reserve.balloon", "clan", CardKind.TROOP, Spell.NONE, 2),
        ),
        source_categories=(('ordinary','POPULATED'),('hero','POPULATED'),('spell','POPULATED'),
                           ('clan','POPULATED'),('siege','LOCKED')),
        source_context=('a'*64,'b'*40,'c'*64,'synthetic-build','REGULAR_MULTIPLAYER'),
        match_entry_cost=50,
    )


class SourceWorld(World):
    def __init__(self, *, mutate_post_source=False, selected_clan=False):
        cards = [
            card("wall_breaker", CardKind.TROOP, 3, x=.10),
            card("goblin", CardKind.TROOP, 2, x=.22),
            clan_card(x=.34, selected=selected_clan),
            card("king", CardKind.HERO, 1, x=.46),
            card("lightning", CardKind.SPELL, 2, Spell.LIGHTNING, x=.58),
        ]
        super().__init__(cards)
        self.screen = Screen.HOME
        self.source_inventory = list(occupied_unused_plan().source_inventory)
        self.source_visits = 0
        self.mutate_post_source = mutate_post_source
        self.selected_clan = selected_clan

    def observe(self):

        observation = super().observe()
        if observation.screen is Screen.ARMY:
            observation=replace(observation,source_inventory=tuple(self.source_inventory),
                source_categories=occupied_unused_plan().source_categories,
                source_context=occupied_unused_plan().source_context)
        if observation.screen is Screen.MATCH:
            observation=replace(observation,match_mode='REGULAR_MULTIPLAYER',match_cost=50)
        if observation.screen is Screen.BATTLE and self.selected_clan:
            cards = tuple(replace(item, selected=True) if item.identity == 'clan_castle' else item
                          for item in observation.page.cards)
            observation = replace(observation, page=page(cards))
        return observation


@pytest.mark.parametrize('verb',['source_open','source_close','next'])
def test_removed_navigation_has_no_intent_or_permission(verb):
    from clash_rush_rebuild.mvp_deployment import DeploymentError
    from clash_rush_rebuild.input_authorization import InputAction
    assert 'SOURCE_NAVIGATION' not in Action.__members__
    assert 'SOURCE_NAVIGATION' not in InputAction.__members__
    with pytest.raises(DeploymentError):Intent(verb,Point(.4,.9),Action.ATTACK_NAVIGATION,deadline=4)


def test_support_deploys_with_complete_roster_and_no_post_home_probe():
    world = SourceWorld()
    result = DeploymentEngine(
        observe=world.observe,
        deliver=world.deliver,
        release=world.release,
        live_gate=lambda: True,
        monotonic=world.clock,
        sleep=world.clock.sleep,
        plan=occupied_unused_plan(),
    ).run()

    assert result.complete
    assert result.deployable_remaining == 0
    assert result.retained_remaining == 0
    assert not result.post_source_verified
    assert not result.retained_controls
    assert [event.verb for event in world.events if event.card_id == "clan_castle"] == ['select','tap']
    assert [event.verb for event in world.events[:3]] == ['attack','find_match','army_attack']
    assert world.events[-1].verb == 'return_home'
    assert world.source_visits == 0


def test_selected_clan_rejects_without_committing_ordinary_progress():
    world = SourceWorld(selected_clan=True)
    result = DeploymentEngine(
        observe=world.observe,
        deliver=world.deliver,
        release=world.release,
        live_gate=lambda: True,
        monotonic=world.clock,
        sleep=world.clock.sleep,
        plan=occupied_unused_plan(),
    ).run()

    assert not result.complete
    assert result.reason == "SUPPORT_ALREADY_SELECTED"
    assert not any(event.verb in {"select", "hold", "tap"} for event in world.events)


def test_post_home_optional_reserve_probe_is_not_part_of_completion():
    world = SourceWorld(mutate_post_source=True)
    result = DeploymentEngine(
        observe=world.observe,
        deliver=world.deliver,
        release=world.release,
        live_gate=lambda: True,
        monotonic=world.clock,
        sleep=world.clock.sleep,
        plan=occupied_unused_plan(),
    ).run()

    assert result.complete
    assert not result.post_source_verified
    assert result.deployable_remaining == 0
    assert result.retained_remaining == 0
    assert result.natural_result and result.returned_home


def test_guarded_delivery_rejects_clan_tap_disguised_as_source_navigation():
    world = SourceWorld()
    mouse_events = []

    class Mouse:
        def prepare(self, _point): mouse_events.append("prepare"); return True
        def move(self, _point): mouse_events.append("move"); return True
        def at_target(self, _point): return True
        def down(self): mouse_events.append("down"); return True
        def up(self): mouse_events.append("up"); return True

    guarded = GuardedDeploymentInput(
        mouse=Mouse(),
        authorize=lambda _action: True,
        monitor=lambda: Intervention.CLEAR,
        lease=type("Lease", (), {"set_held": lambda self, held: None})(),
        monotonic=world.clock,
        wait=world.clock.sleep,
        observed_events=lambda: len(mouse_events),
    )
    intent = Intent("tap", Point(.4, .9), Action.TROOP_DEPLOYMENT, "clan_castle", deadline=4)
    object.__setattr__(intent, "verb", "source_open")

    assert guarded.deliver(intent, lambda: True) is False
    assert mouse_events == []


@pytest.mark.parametrize('fault', ['context','category','close_changed','home_unverified'])
def test_source_precommit_rechecks_fresh_sealed_evidence_at_delivery(fault):
    world=SourceWorld(); original=world.observe; reads=0
    def observation():
        nonlocal reads
        obs=original()
        if obs.screen is Screen.ARMY:
            reads+=1
            if fault=='context': return replace(obs,source_context=('b'*64,)+obs.source_context[1:])
            if fault=='category': return replace(obs,source_categories=())
            if fault=='close_changed' and reads>1: return replace(obs,source_inventory=obs.source_inventory[:-1])
        if fault=='home_unverified' and obs.screen is Screen.HOME and world.seq>1:
            return replace(obs,home_verified=False)
        return obs
    result=DeploymentEngine(observe=observation,deliver=world.deliver,release=world.release,
        live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=occupied_unused_plan()).run()
    assert not any(e.verb == 'army_attack' for e in world.events)
    if fault=='home_unverified': assert not world.events
    assert not result.complete


def test_deployed_support_receipt_has_no_unused_reserve_claim():
    from clash_rush_rebuild.mvp_deployment_receipt import record_proof,read_proof
    from test_deployment_receipt import setup,DIGEST
    for changed in (False,True):
        world=SourceWorld(mutate_post_source=changed)
        result=DeploymentEngine(observe=world.observe,deliver=world.deliver,release=world.release,
            live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=occupied_unused_plan()).run()
        payload=result.public_payload()
        assert payload['schema']==4
        assert not any('reserve' in key or 'retained' in key or key.startswith('clan_input') for key in payload)
        assert payload['remaining']==0
        assert payload['natural_result'] and payload['returned_home']
        assert 'reserve.goblin' not in str(payload)
        authority=setup()
        assert record_proof(authority,'tx',lambda:result,DIGEST)
        assert read_proof(authority,'tx')==result
        authority.c.close()


def test_find_match_waits_for_army_without_replaying_paid_navigation():
    world=SourceWorld(); pending=[0]; original_observe=world.observe
    def observe():
        if pending[0]:
            pending[0]-=1
            if pending[0]==0:world.screen=Screen.ARMY
        return original_observe()
    def deliver(intent,proof):
        result=world.deliver(intent,proof)
        if intent.verb=='find_match' and result:
            world.screen=Screen.MATCH; pending[0]=3
        return result
    result=DeploymentEngine(observe=observe,deliver=deliver,release=world.release,
        live_gate=lambda:True,monotonic=world.clock,sleep=world.clock.sleep,plan=occupied_unused_plan()).run()
    assert result.complete, result
    assert sum(e.verb=='find_match' for e in world.events)==1
