"""Generated hostile Observation ingress matrix; synthetic values only."""
from dataclasses import replace

import pytest

from clash_rush_rebuild.mvp_deployment import (
    Card,
    CardKind,
    CardState,
    DeploymentError,
    Intervention,
    Observation,
    Page,
    Point,
    Screen,
    Spell,
    Target,
    TargetKind,
    _copy_observation,
)
from test_deployment_repair import World, card


class TupleSubclass(tuple):
    pass


class ListSubclass(list):
    pass


class NumberSubclass(float):
    pass


class IterableSentinel:
    def __init__(self):
        self.entered = False

    def __iter__(self):
        self.entered = True
        raise AssertionError("hostile iterator entered")


class Duck:
    pass


def valid_observation():
    card = Card("troop", CardKind.TROOP, Spell.NONE, 1,
                CardState.AVAILABLE, Point(.2, .9), False)
    return Observation(
        1, .1, "view", Screen.BATTLE, Page((card,), True, True),
        (Target(TargetKind.GROUND, Point(.4, .6), 1.0),),
        (("end_battle", Point(.1, .7)),), Intervention.CLEAR,
    )


def mutated(root, path, value):
    """Mutate a fresh frozen graph without invoking normalizing constructors."""
    result = root
    if path == "page.cards":
        object.__setattr__(result.page, "cards", value)
    elif path.startswith("page.card."):
        object.__setattr__(result.page.cards[0], path.removeprefix("page.card."), value)
    elif path.startswith("page."):
        object.__setattr__(result.page, path.removeprefix("page."), value)
    elif path.startswith("target."):
        object.__setattr__(result.targets[0], path.removeprefix("target."), value)
    elif path == "control.point":
        object.__setattr__(result, "controls", (("end_battle", value),))
    else:
        object.__setattr__(result, path, value)
    return result


@pytest.mark.parametrize("path,factory", [
    ("page.cards", lambda o: list(o.page.cards)),
    ("targets", lambda o: list(o.targets)),
    ("controls", lambda o: list(o.controls)),
    ("controls", lambda o: (list(o.controls[0]),)),
    ("page.cards", lambda o: TupleSubclass(o.page.cards)),
    ("page.cards", lambda o: ListSubclass(o.page.cards)),
    ("targets", lambda o: TupleSubclass(o.targets)),
    ("targets", lambda o: ListSubclass(o.targets)),
    ("controls", lambda o: TupleSubclass(o.controls)),
    ("controls", lambda o: ListSubclass(o.controls)),
    ("controls", lambda o: (TupleSubclass(o.controls[0]),)),
    ("controls", lambda o: (ListSubclass(o.controls[0]),)),
    ("controls", lambda o: (list(o.controls[0]),)),
])
def test_in01_in02_wrong_and_subclassed_containers_reject_before_normalization(path, factory):
    observation = valid_observation()
    mutated(observation, path, factory(observation))
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


@pytest.mark.parametrize("path", ["page.cards", "targets", "controls"])
def test_in02_generic_iterables_are_rejected_without_iteration(path):
    sentinel = IterableSentinel()
    observation = mutated(valid_observation(), path, sentinel)
    with pytest.raises(DeploymentError):
        _copy_observation(observation)
    assert sentinel.entered is False


def test_in02_control_entry_iterable_is_rejected_without_iteration():
    sentinel = IterableSentinel()
    observation = mutated(valid_observation(), "controls", (sentinel,))
    with pytest.raises(DeploymentError):
        _copy_observation(observation)
    assert sentinel.entered is False


@pytest.mark.parametrize("path,base", [
    ("page", Page),
    ("page.card", Card),
    ("targets", Target),
    ("page.card.point", Point),
    ("target.point", Point),
    ("control.point", Point),
])
@pytest.mark.parametrize("form", ["duck", "subclass"])
def test_in03_nested_ducks_and_subclasses_reject_before_attribute_copy(path, base, form):
    observation = valid_observation()
    if path == "page":
        original = observation.page
    elif path == "page.card":
        original = observation.page.cards[0]
    elif path == "targets":
        original = observation.targets[0]
    elif path == "page.card.point":
        original = observation.page.cards[0].point
    elif path == "target.point":
        original = observation.targets[0].point
    else:
        original = observation.controls[0][1]
    if form == "duck":
        replacement = Duck()
        for name in getattr(base, "__dataclass_fields__", {}):
            object.__setattr__(replacement, name, getattr(original, name))
    else:
        subclass = type(f"Hostile{base.__name__}", (base,), {})
        replacement = subclass(*[getattr(original, name) for name in base.__dataclass_fields__])
    if path == "page":
        object.__setattr__(observation, "page", replacement)
    elif path == "page.card":
        object.__setattr__(observation.page, "cards", (replacement,))
    elif path == "targets":
        object.__setattr__(observation, "targets", (replacement,))
    else:
        mutated(observation, path, replacement)
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


@pytest.mark.parametrize("path,value", [
    ("screen", Duck()),
    ("intervention", Duck()),
    ("page.card.kind", Duck()),
    ("page.card.spell", Duck()),
    ("page.card.state", Duck()),
    ("target.kind", Duck()),
    ("screen", Intervention.CLEAR),
    ("intervention", Screen.BATTLE),
    ("page.card.kind", Spell.NONE),
    ("page.card.spell", CardKind.TROOP),
    ("page.card.state", Screen.BATTLE),
    ("target.kind", CardKind.TROOP),
])
def test_in04_enum_proxies_and_wrong_enums_reject_before_value_access(path, value):
    if type(value) is Duck:
        value.value = {
            "screen": "BATTLE", "intervention": "CLEAR",
            "page.card.kind": "TROOP", "page.card.spell": "NONE",
            "page.card.state": "AVAILABLE", "target.kind": "GROUND",
        }[path]
    observation = mutated(valid_observation(), path, value)
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


@pytest.mark.parametrize("path,value", [
    ("sequence", True),
    ("sequence", -1),
    ("captured_at", True),
    ("captured_at", NumberSubclass(.1)),
    ("captured_at", float("nan")),
    ("view", 1),
    ("home_verified", 1),
    ("page.left_edge", 1),
    ("page.right_edge", 1),
    ("page.card.identity", 1),
    ("page.card.remaining", True),
    ("page.card.selected", 1),
    ("target.confidence", True),
    ("target.confidence", NumberSubclass(1.0)),
])
def test_in05_in06_scalar_and_semantic_invalidity_remains_rejected(path, value):
    observation = mutated(valid_observation(), path, value)
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


def test_in08_valid_graph_is_detached_from_ordinary_alias_mutation():
    original = valid_observation()
    detached = _copy_observation(original)
    object.__setattr__(original.page.cards[0].point, "x", .99)
    object.__setattr__(original.targets[0].point, "x", .98)
    object.__setattr__(original.controls[0][1], "x", .97)
    assert detached.page.cards[0].point.x == .2
    assert detached.targets[0].point.x == .4
    assert detached.controls[0][1].x == .1


def test_in05_missing_root_field_is_a_closed_validation_error():
    observation = valid_observation()
    object.__delattr__(observation, "targets")
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


def test_in05_duplicate_card_identity_rejects_at_ingress():
    observation = valid_observation()
    second = replace(observation.page.cards[0], point=Point(.3, .9))
    object.__setattr__(observation.page, "cards", (observation.page.cards[0], second))
    with pytest.raises(DeploymentError):
        _copy_observation(observation)


PHASES = (
    "initial", "select_proof", "after_select", "hold_proof", "after_hold",
    "natural_wait", "return_proof", "after_return",
)


def _hostile_container(observation, mode):
    if mode == "cards_list":
        object.__setattr__(observation.page, "cards", list(observation.page.cards))
    elif mode == "targets_list":
        object.__setattr__(observation, "targets", list(observation.targets))
    elif mode == "controls_list":
        object.__setattr__(observation, "controls", list(observation.controls))
    else:
        object.__setattr__(observation, "controls", (list(observation.controls[0]),))
    return observation


PHASE_CASES = tuple(
    (phase, mode)
    for phase in PHASES
    for mode in ("cards_list", "targets_list", "controls_list", "control_entry_list")
    if mode != "cards_list" or phase not in {"return_proof", "after_return"}
)


@pytest.mark.parametrize("phase,mode", PHASE_CASES)
def test_in07_wrong_containers_reject_at_each_authority_phase_without_new_action(phase, mode):
    world = World([card(count=1)])
    original_observe = world.observe
    original_deliver = world.deliver
    active_proof = [None]
    injected = [False]

    def at_phase():
        verbs = [event.verb for event in world.events]
        if phase == "initial":
            return not verbs and active_proof[0] is None
        if phase in {"select_proof", "hold_proof", "return_proof"}:
            expected_verb = "return_home" if phase == "return_proof" else phase.removesuffix("_proof")
            return active_proof[0] == expected_verb
        if phase == "after_select":
            return verbs == ["select"] and active_proof[0] is None
        if phase == "after_hold":
            return verbs == ["select", "hold"] and world.terminal_reads < 5
        if phase == "natural_wait":
            return world.terminal_reads >= 5 and world.screen is Screen.BATTLE
        return verbs == ["select", "hold", "return_home"]

    def observe():
        observation = original_observe()
        if not injected[0] and at_phase():
            injected[0] = True
            return _hostile_container(observation, mode)
        return observation

    def deliver(intent, proof):
        active_proof[0] = intent.verb
        try:
            allowed = proof()
        finally:
            active_proof[0] = None
        if allowed is not True:
            return False
        return original_deliver(intent, lambda: True)

    world.observe = observe
    world.deliver = deliver
    result = world.engine().run()
    expected = {
        "initial": [], "select_proof": [], "after_select": ["select"],
        "hold_proof": ["select"], "after_hold": ["select", "hold"],
        "natural_wait": ["select", "hold"],
        "return_proof": ["select", "hold"],
        "after_return": ["select", "hold", "return_home"],
    }[phase]
    assert injected[0], (phase, mode)
    assert [event.verb for event in world.events] == expected
    assert result.complete is False
    assert world.closed is True
