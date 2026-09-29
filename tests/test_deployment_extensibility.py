"""Generated H1 extensibility and single-page safety regressions."""
from dataclasses import replace

import pytest

from clash_rush_rebuild import mvp_deployment_profile as profile_module
from clash_rush_rebuild.mvp_deployment import (
    CardKind,
    CardState,
    DeploymentError,
    Policy,
    Spell,
    Page,
)
from test_deployment_repair import World, card


def _entry(index: int, *, quantity: int = 1):
    return (f"card_{index}", CardKind.TROOP, Spell.NONE, quantity)


@pytest.mark.parametrize("card_count", [3, 4, 5, 6])
def test_same_sealed_single_page_core_accepts_finite_n_card_plans(card_count):
    plan_type = getattr(profile_module, "CompiledDeploymentPlan", None)
    assert plan_type is not None, "H1 compiled plan capability is missing"
    plan = plan_type(tuple(_entry(index) for index in range(card_count)), viewport_limit=6)
    world = World([card(entry[0], count=1, x=.08 + index * .12)
                   for index, entry in enumerate(plan.roster)])

    result = world.engine(plan=plan).run()

    assert result.complete
    assert result.cards_total == card_count


def test_single_page_plan_rejects_viewport_overflow_instead_of_truncating():
    plan_type = getattr(profile_module, "CompiledDeploymentPlan", None)
    assert plan_type is not None, "H1 compiled plan capability is missing"
    with pytest.raises(DeploymentError, match="PLAN_VIEWPORT_OVERFLOW"):
        plan_type(tuple(_entry(index) for index in range(7)), viewport_limit=6)


def test_sealed_plan_initial_quantities_are_generic_and_exact_before_input():
    plan_type = getattr(profile_module, "CompiledDeploymentPlan", None)
    assert plan_type is not None, "H1 compiled plan capability is missing"
    plan = plan_type((_entry(0, quantity=2), _entry(1, quantity=3)), viewport_limit=6)
    world = World([card("card_0", count=2, x=.1), card("card_1", count=2, x=.3)])

    result = world.engine(plan=plan).run()

    assert result.reason == "INITIAL_QUANTITY_MISMATCH"
    assert not any(event.verb in {"select", "hold", "tap", "end_battle", "return_home"}
                   for event in world.events)
    assert world.closed


def test_final_scan_rejects_any_whole_roster_change_before_exit():
    plan_type = getattr(profile_module, "CompiledDeploymentPlan", None)
    assert plan_type is not None, "H1 compiled plan capability is missing"
    plan = plan_type((_entry(0), _entry(1)), viewport_limit=6)
    world = World([card("card_0", count=1, x=.1), card("card_1", count=1, x=.3)])
    original = world.observe
    terminal_reads = 0

    def observe():
        nonlocal terminal_reads
        if world.cards and all(item.exhausted for item in world.cards):
            terminal_reads += 1
            if terminal_reads >= 4 and len(world.cards) == 2:
                world.cards.append(card("residue", count=0, state=CardState.DEPLETED, x=.7))
        return original()

    world.observe = observe
    result = world.engine(plan=plan).run()

    assert result.reason == "ROSTER_ADDITIONAL"
    assert not any(event.verb in {"end_battle", "return_home"} for event in world.events)


def test_single_page_capability_rejects_cross_page_even_with_both_roster_parts():
    plan = profile_module.CompiledDeploymentPlan((_entry(0), _entry(1)), viewport_limit=6)
    world = World([card("card_0", count=1, x=.1), card("card_1", count=1, x=.3)])
    original = world.observe

    def observe():
        observation = original()
        if observation.page is not None:
            observation = replace(observation,
                page=Page(observation.page.cards, True, False))
        return observation

    world.observe = observe
    result = world.engine(plan=plan).run()

    assert result.reason == "SINGLE_PAGE_COVERAGE_UNPROVED"
    assert not any(event.verb in {"select", "hold", "tap", "end_battle", "return_home"}
                   for event in world.events)


def test_first_profile_policy_is_data_not_reusable_core_constants():
    plan = profile_module.CompiledDeploymentPlan((
        ("wall_breaker", CardKind.TROOP, Spell.NONE, 4),
        ("goblin", CardKind.TROOP, Spell.NONE, 8),
        ("barbarian_king", CardKind.HERO, Spell.NONE, 1),
        ("lightning", CardKind.SPELL, Spell.LIGHTNING, 2),
    ), viewport_limit=6)
    assert tuple(entry[0] for entry in plan.roster) == (
        "wall_breaker", "goblin", "barbarian_king", "lightning")
    assert plan.quantities == (4, 8, 1, 2)


def test_malformed_partial_like_page_values_are_not_completion_authority():
    with pytest.raises(DeploymentError, match="PAGE_INVALID"):
        Page((card("card_0"),), "END", True)


def test_stale_digest_plan_mutation_is_rejected_at_engine_ingress():
    plan = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    object.__setattr__(plan, "roster", (_entry(1),))
    world = World([card("card_1", count=1, x=.2)])

    with pytest.raises(DeploymentError, match="PLAN_DIGEST_MISMATCH"):
        world.engine(plan=plan)
    assert not world.events


def test_engine_detaches_the_original_plan_alias_at_ingress():
    plan = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=plan)
    object.__setattr__(plan, "roster", (_entry(1),))

    assert engine.run().complete
    assert not any(event.card_id == "card_1" for event in world.events)


@pytest.mark.parametrize("boundary", ["observe", "deliver", "live_gate"])
def test_plan_replacement_during_arbitrary_callback_fails_before_new_down(boundary):
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    replacement = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved)
    fired = False

    def replace_plan():
        nonlocal fired
        if not fired:
            fired = True
            engine.plan = replacement

    if boundary == "observe":
        original = engine.observe
        def observe():
            replace_plan()
            return original()
        engine.observe = observe
    elif boundary == "deliver":
        original = engine.deliver
        def deliver(intent, proof):
            replace_plan()
            return original(intent, proof)
        engine.deliver = deliver
    else:
        def live_gate():
            replace_plan()
            return True
        engine.live_gate = live_gate

    result = engine.run()

    assert not result.complete
    assert not world.events


def test_observe_cannot_replace_and_reseal_run_authority():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    replacement = profile_module.CompiledDeploymentPlan((_entry(1),), viewport_limit=6)
    world = World([card("card_1", count=1, x=.2)])
    engine = world.engine(plan=approved)
    original = engine.observe
    fired = False

    def observe():
        nonlocal fired
        if not fired:
            fired = True
            engine._plan = replacement
            engine._plan_seal = replacement.digest
            engine._validate_plan = lambda: None
        return original()

    engine.observe = observe
    result = engine.run()

    assert not result.complete
    assert not any(event.card_id == "card_1" for event in world.events)


@pytest.mark.parametrize("boundary", [
    "clock", "live_gate", "observe", "deliver", "proof", "sleep", "release",
])
def test_engine_reference_callback_cannot_replace_closed_run_authority(boundary):
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    replacement = profile_module.CompiledDeploymentPlan((_entry(1),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved)
    fired = False

    def attack():
        nonlocal fired
        if fired:
            return
        fired = True
        engine._plan = replacement
        engine._plan_seal = replacement.digest
        engine._validate_plan = lambda: None

    if boundary == "clock":
        original = engine.clock
        engine.clock = lambda: (attack(), original())[1]
    elif boundary == "live_gate":
        engine.live_gate = lambda: (attack(), True)[1]
    elif boundary == "observe":
        original = engine.observe
        engine.observe = lambda: (attack(), original())[1]
    elif boundary == "deliver":
        original = engine.deliver
        engine.deliver = lambda intent, proof: (attack(), original(intent, proof))[1]
    elif boundary == "proof":
        original = engine.deliver
        engine.deliver = lambda intent, proof: original(
            intent, lambda: (attack(), proof())[1])
    elif boundary == "sleep":
        original = engine.sleep
        engine.sleep = lambda seconds: (attack(), original(seconds))[1]
    else:
        original = engine.release
        engine.release = lambda: (attack(), original())[1]

    result = engine.run()

    assert fired
    assert result.complete
    assert engine.plan.signatures == approved.signatures
    assert any(event.card_id == "card_0" for event in world.events)
    assert not any(event.card_id == "card_1" for event in world.events)
    assert world.closed


def test_engine_plan_property_returns_only_a_detached_copy():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved)
    exposed = engine.plan
    object.__setattr__(exposed, "roster", (_entry(1),))

    assert engine.run().complete
    assert not any(event.card_id == "card_1" for event in world.events)


def test_callback_cannot_shadow_failed_release_into_false_completion():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved)
    original_observe = engine.observe
    engine.release = lambda: False

    def observe():
        engine.release = lambda: True
        return original_observe()

    engine.observe = observe
    result = engine.run()

    assert not result.complete
    assert result.reason == "RELEASE_UNPROVED"


@pytest.mark.parametrize("boundary", ["observe", "deliver", "proof", "sleep", "release"])
def test_callback_policy_shadow_cannot_accept_stale_evidence(boundary):
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved, policy=Policy(observation_age=.75))
    original_observe = engine.observe
    fired = False
    stale_emitted = False

    def shadow_policy():
        nonlocal fired
        if fired:
            return
        fired = True
        replacement = Policy(observation_age=.75)
        object.__setattr__(replacement, "observation_age", 999.0)
        engine.policy = replacement

    def observe():
        nonlocal stale_emitted
        observation = original_observe()
        if boundary == "observe":
            shadow_policy()
        if fired and boundary != "release" and not stale_emitted:
            stale_emitted = True
            world.clock.now += .8 if boundary == "sleep" else 5.0
        return observation

    engine.observe = observe
    if boundary == "deliver":
        original = engine.deliver
        engine.deliver = lambda intent, proof: (shadow_policy(), original(intent, proof))[1]
    elif boundary == "proof":
        original = engine.deliver
        engine.deliver = lambda intent, proof: original(
            intent, lambda: (shadow_policy(), proof())[1])
    elif boundary == "sleep":
        original = engine.sleep
        engine.sleep = lambda seconds: (shadow_policy(), original(seconds))[1]
    elif boundary == "release":
        original = engine.release
        engine.release = lambda: (shadow_policy(), original())[1]

    result = engine.run()

    assert fired
    assert world.closed
    if boundary == "release":
        assert result.complete
    else:
        assert result.reason == "OBSERVATION_STALE"
        assert not result.complete
        assert not any(event.verb in {"end_battle", "confirm_end", "return_home"}
                       for event in world.events)


def test_callback_exit_seconds_shadow_cannot_extend_frozen_exit_deadline():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine = world.engine(plan=approved, policy=Policy(exit_seconds=1.0))
    original_deliver = engine.deliver
    original_observe = engine.observe
    fired = False
    delayed_exit = False

    def deliver(intent, proof):
        nonlocal fired
        if not fired:
            fired = True
            engine.policy = Policy(exit_seconds=30.0)
        return original_deliver(intent, proof)

    def observe():
        nonlocal delayed_exit
        if world.screen.name == "END_CONFIRM" and not delayed_exit:
            delayed_exit = True
            world.clock.now += 2.0
        return original_observe()

    engine.deliver = deliver
    engine.observe = observe
    result = engine.run()

    assert fired and delayed_exit
    assert result.reason == "DEADLINE"
    assert not result.complete
    assert [event.verb for event in world.events if event.verb in {
        "end_battle", "confirm_end", "return_home"}] == ["end_battle"]
    assert world.closed


def test_callback_deadline_shadow_cannot_extend_frozen_visit_deadline():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    world.screen = profile_module.Screen.HOME
    engine = world.engine(
        plan=approved,
        policy=Policy(visit_seconds=3.0, exit_seconds=1.0),
    )
    original_observe = engine.observe
    fired = False
    delayed_transition = False

    def observe():
        nonlocal fired, delayed_transition
        if world.screen is profile_module.Screen.MATCH and not delayed_transition:
            delayed_transition = True
            world.clock.now += 3.0
        observation = original_observe()
        if not fired:
            fired = True
            engine.visit_deadline = 999.0
            engine.budget.deadline = 999.0
        return observation

    engine.observe = observe
    result = engine.run()

    assert fired and delayed_transition
    assert result.reason == "DEADLINE"
    assert not result.complete
    assert [event.verb for event in world.events if event.action.name == "ATTACK_NAVIGATION"] == ["attack"]
    assert world.closed
