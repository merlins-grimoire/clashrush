"""Generated H1 extensibility and single-page safety regressions."""
from dataclasses import replace

import pytest

from clash_rush_rebuild import mvp_deployment_profile as profile_module
from clash_rush_rebuild.mvp_deployment import (
    CardKind,
    CardState,
    DeploymentError,
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
