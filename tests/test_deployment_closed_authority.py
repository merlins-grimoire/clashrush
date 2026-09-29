"""Generated closed-run authority regressions; synthetic ports and fake devices only."""
from types import SimpleNamespace

from clash_rush_rebuild.mvp_deployment import (
    Action,
    CardKind,
    CompiledDeploymentPlan,
    DeploymentEngine,
    Intent,
    Intervention,
    Point,
    Policy,
    Spell,
)
from clash_rush_rebuild.mvp_deployment_input import GuardedDeploymentInput
from test_deployment_repair import World, card
import inspect
import threading


def _engine(world, *, policy=Policy(), observe=None, deliver=None, release=None):
    return DeploymentEngine(
        observe=observe or world.observe,
        deliver=deliver or world.deliver,
        release=release or world.release,
        live_gate=lambda: True,
        monotonic=world.clock,
        sleep=world.clock.sleep,
        policy=policy,
        plan=CompiledDeploymentPlan(tuple(
            (item.identity, item.kind, item.spell, item.remaining)
            for item in world.cards
        ), 6),
    )


def test_ra02_policy_object_setattr_cannot_admit_stale_observation():
    world = World([card(count=1)])
    policy = Policy(observation_age=.75)
    engine_ref = {}
    fired = False

    def observe():
        nonlocal fired
        observation = world.observe()
        if not fired:
            fired = True
            object.__setattr__(policy, "observation_age", 999.0)
            world.clock.now += 5.0
        engine_ref["engine"].policy = policy
        return observation

    engine = _engine(world, policy=policy, observe=observe)
    engine_ref["engine"] = engine
    result = engine.run()

    assert result.reason == "OBSERVATION_STALE"
    assert not result.complete
    assert world.events == []


def test_ra03_budget_replacement_cannot_manufacture_late_completion():
    world = World([card(count=1)])
    policy = Policy(visit_seconds=3.0, exit_seconds=1.0)
    engine_ref = {}
    delayed = False

    class NoBudget:
        def check(self, _deadline=None):
            return world.clock()
        def wait(self, seconds, _deadline=None):
            world.clock.sleep(seconds)

    def observe():
        nonlocal delayed
        observation = world.observe()
        if world.cards[0].remaining == 0 and not delayed:
            delayed = True
            engine_ref["engine"].budget = NoBudget()
            object.__setattr__(policy, "observation_age", 999.0)
            world.clock.now += 10.0
        return observation

    engine = _engine(
        world,
        policy=policy,
        observe=observe,
    )
    engine_ref["engine"] = engine
    result = engine.run()

    assert delayed
    assert result.reason == "DEADLINE"
    assert not result.complete


def test_ra04_action_counter_reset_cannot_bypass_original_max_actions():
    world = World([card(count=1)])
    engine_ref = {}

    def deliver(intent, proof):
        engine_ref["engine"].actions = -100
        return world.deliver(intent, proof)

    engine = _engine(world, policy=Policy(max_actions=1), deliver=deliver)
    engine_ref["engine"] = engine
    result = engine.run()

    assert result.reason == "ACTION_LIMIT"
    assert not result.complete
    assert [event.verb for event in world.events] == ["select"]


def test_ra06_release_spell_inflation_cannot_forge_result_count():
    world = World([card("troop", CardKind.TROOP, 1, Spell.NONE)])
    engine_ref = {}

    def release():
        engine_ref["engine"].spells = 999
        return world.release()

    engine = _engine(world, release=release)
    engine_ref["engine"] = engine
    result = engine.run()

    assert result.complete
    assert result.spells_consumed == 0


def test_ra12_possible_held_delay_has_zero_late_down():
    clock = SimpleNamespace(now=1.0)
    events = []

    class Mouse:
        def prepare(self, _point): events.append("prepare"); return True
        def move(self, _point): events.append("move"); return True
        def at_target(self, _point): events.append("target"); return True
        def down(self): events.append(("down", clock.now)); return True
        def up(self): events.append("up"); return True

    class Lease:
        def set_held(self, held):
            events.append(("held", held))
            if held:
                clock.now += 10.0

    def monotonic(): return clock.now
    def wait(seconds): clock.now += seconds
    def observed(): return sum(item in ("move", "up") or (type(item) is tuple and item[0] == "down") for item in events)

    guarded = GuardedDeploymentInput(
        mouse=Mouse(), authorize=lambda _action: True,
        monitor=lambda: Intervention.CLEAR, lease=Lease(),
        monotonic=monotonic, wait=wait, observed_events=observed,
    )
    result = guarded.deliver(
        Intent("tap", Point(.4, .4), Action.TROOP_DEPLOYMENT, "troop", deadline=3.0),
        lambda: True,
    )

    assert result is False
    assert not any(type(item) is tuple and item[0] == "down" for item in events)


def test_ra02_all_policy_scalars_are_copied_before_callbacks():
    world = World([card(count=1)])
    policy = Policy()
    engine_ref = {}
    fired = False

    def observe():
        nonlocal fired
        if not fired:
            fired = True
            for name in Policy.__dataclass_fields__:
                object.__setattr__(policy, name, True)
            engine_ref["engine"].policy = policy
        return world.observe()

    engine = _engine(world, policy=policy, observe=observe)
    engine_ref["engine"] = engine
    assert engine.run().complete


def test_ra05_ra06_public_progress_corruption_is_diagnostic_only():
    world = World([card(count=2)])
    engine_ref = {}

    def deliver(intent, proof):
        engine = engine_ref["engine"]
        engine.holds = {"troop": -999.0}
        engine.cards = {}
        engine.inventory = SimpleNamespace(complete=True)
        engine.spells = 999
        return world.deliver(intent, proof)

    engine = _engine(world, deliver=deliver)
    engine_ref["engine"] = engine
    result = engine.run()
    assert result.complete
    assert result.spells_consumed == 0
    assert len([event for event in world.events if event.verb == "hold"]) == 2


def test_ra08_replayed_final_proof_cannot_authorize_input():
    world = World([card(count=1)])

    def deliver(intent, proof):
        assert proof() is True
        assert proof() is False
        return world.deliver(intent, proof)

    result = _engine(world, deliver=deliver).run()
    assert not result.complete
    assert result.reason == "INPUT_UNCERTAIN"
    assert world.events == []
    assert world.closed


def test_ra09_completion_fields_and_return_value_cannot_be_forged():
    world = World([card(count=1)])
    engine_ref = {}

    def release():
        engine = engine_ref["engine"]
        engine.complete = True
        engine.home = True
        engine.own_exit = True
        engine.result = SimpleNamespace(complete=True)
        return False

    engine = _engine(world, release=release)
    engine_ref["engine"] = engine
    result = engine.run()
    assert not result.complete
    assert result.reason == "RELEASE_UNPROVED"


def test_ra10_facade_and_proof_graph_expose_no_live_authority():
    world = World([card(count=1)])
    engine_ref = {}
    proofs = []

    def deliver(intent, proof):
        proofs.append(proof)
        return world.deliver(intent, proof)

    engine = _engine(world, deliver=deliver)
    engine_ref["engine"] = engine
    assert engine.__dict__ == {}
    assert engine.run().complete
    assert all(not inspect.isfunction(value) and not inspect.ismethod(value)
               for value in engine.__dict__.values())
    for proof in proofs:
        assert not hasattr(proof, "__dict__")
        assert getattr(proof.__call__, "__closure__", None) is None
        assert getattr(proof.__call__, "__defaults__", None) is None
        assert vars(type(proof)).keys() >= {"__slots__", "__call__"}


def test_ra11_reentrant_and_reused_handle_have_one_terminal_run():
    world = World([card(count=1)])
    engine_ref = {}
    reentrant = []

    def deliver(intent, proof):
        if not reentrant:
            try:
                engine_ref["engine"].run()
            except Exception as error:
                reentrant.append(str(error))
        return world.deliver(intent, proof)

    engine = _engine(world, deliver=deliver)
    engine_ref["engine"] = engine
    assert engine.run().complete
    assert reentrant == ["EXECUTOR_ALREADY_USED"]
    try:
        engine.run()
    except Exception as error:
        assert str(error) == "EXECUTOR_ALREADY_USED"
    else:
        raise AssertionError("run handle replayed")


def test_ra11_concurrent_claim_allows_exactly_one_run():
    world = World([card(count=1)])
    engine = _engine(world)
    outcomes = []

    def run():
        try:
            outcomes.append(engine.run().complete)
        except Exception as error:
            outcomes.append(str(error))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(map(str, outcomes)) == ["EXECUTOR_ALREADY_USED", "True"]
