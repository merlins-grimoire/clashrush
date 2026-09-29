"""Generated H1 extensibility and single-page safety regressions."""
from dataclasses import replace

import pytest

from clash_rush_rebuild import mvp_deployment_profile as profile_module
from clash_rush_rebuild.mvp_deployment import (
    CardKind,
    CardState,
    DeploymentEngine,
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


def _callback_engine(world, plan, boundary, attack, *, policy=Policy(), release=None):
    ports = {
        "observe": world.observe,
        "deliver": world.deliver,
        "release": release or world.release,
        "live_gate": lambda: True,
        "monotonic": world.clock,
        "sleep": world.clock.sleep,
    }
    if boundary == "clock":
        original = ports["monotonic"]
        ports["monotonic"] = lambda: (attack(), original())[1]
    elif boundary == "live_gate":
        ports["live_gate"] = lambda: (attack(), True)[1]
    elif boundary == "observe":
        original = ports["observe"]
        ports["observe"] = lambda: (attack(), original())[1]
    elif boundary == "deliver":
        original = ports["deliver"]
        ports["deliver"] = lambda intent, proof: (attack(), original(intent, proof))[1]
    elif boundary == "proof":
        original = ports["deliver"]
        ports["deliver"] = lambda intent, proof: original(
            intent, lambda: (attack(), proof())[1])
    elif boundary == "sleep":
        original = ports["sleep"]
        ports["sleep"] = lambda seconds: (attack(), original(seconds))[1]
    elif boundary == "release":
        original = ports["release"]
        ports["release"] = lambda: (attack(), original())[1]
    return DeploymentEngine(
        observe=ports["observe"], deliver=ports["deliver"], release=ports["release"],
        live_gate=ports["live_gate"], monotonic=ports["monotonic"],
        sleep=ports["sleep"], policy=policy, plan=plan,
    )


@pytest.mark.parametrize("boundary", [
    "clock", "live_gate", "observe", "deliver", "proof", "sleep", "release",
])
def test_engine_reference_callback_cannot_replace_closed_run_authority(boundary):
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    replacement = profile_module.CompiledDeploymentPlan((_entry(1),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    engine_ref = {}
    fired = False

    def attack():
        nonlocal fired
        if fired:
            return
        fired = True
        engine = engine_ref["engine"]
        engine._plan = replacement
        engine._plan_seal = replacement.digest
        engine._validate_plan = lambda: None
        engine.actions = -100
        engine.spells = 999
        engine.budget = object()

    engine = _callback_engine(world, approved, boundary, attack)
    engine_ref["engine"] = engine
    detached_plan = engine.plan
    result = engine.run()

    assert fired
    assert result.complete
    assert result.cards_total == 1
    assert result.spells_consumed == 0
    assert detached_plan.signatures == approved.signatures
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
    engine_ref = {}

    def attack():
        engine_ref["engine"].release = lambda: True

    engine = _callback_engine(world, approved, "observe", attack, release=lambda: False)
    engine_ref["engine"] = engine
    result = engine.run()

    assert not result.complete
    assert result.reason == "RELEASE_UNPROVED"


def test_callback_exit_seconds_shadow_cannot_extend_frozen_exit_deadline():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    policy = Policy(exit_seconds=1.0)
    engine_ref = {}
    original_observe = world.observe
    delayed = False

    def observe():
        nonlocal delayed
        if world.screen is profile_module.Screen.END_CONFIRM and not delayed:
            delayed = True
            world.clock.now += 2.0
        return original_observe()

    world.observe = observe
    def attack():
        engine_ref["engine"].policy = Policy(exit_seconds=30.0)
        object.__setattr__(policy, "exit_seconds", 30.0)

    engine = _callback_engine(world, approved, "deliver", attack, policy=policy)
    engine_ref["engine"] = engine
    result = engine.run()

    assert delayed
    assert result.reason == "DEADLINE"
    assert not result.complete
    assert [event.verb for event in world.events if event.verb in {
        "end_battle", "confirm_end", "return_home"}] == ["end_battle"]
    assert world.closed


def test_callback_deadline_shadow_cannot_extend_frozen_visit_deadline():
    approved = profile_module.CompiledDeploymentPlan((_entry(0),), viewport_limit=6)
    world = World([card("card_0", count=1, x=.2)])
    world.screen = profile_module.Screen.HOME
    engine_ref = {}
    original_observe = world.observe
    delayed = False

    def observe():
        nonlocal delayed
        if world.screen is profile_module.Screen.MATCH and not delayed:
            delayed = True
            world.clock.now += 3.0
        return original_observe()

    world.observe = observe
    def attack():
        engine_ref["engine"].visit_deadline = 999.0
        engine_ref["engine"].budget = object()

    engine = _callback_engine(
        world, approved, "observe", attack,
        policy=Policy(visit_seconds=3.0, exit_seconds=1.0),
    )
    engine_ref["engine"] = engine
    result = engine.run()

    assert delayed
    assert result.reason == "DEADLINE"
    assert not result.complete
    assert [event.verb for event in world.events
            if event.action.name == "ATTACK_NAVIGATION"] == ["attack"]
    assert world.closed
