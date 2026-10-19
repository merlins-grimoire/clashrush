"""Owner-authorized support deployment; synthetic evidence, never live calibration."""
from dataclasses import replace
import pytest
from test_s004_occupied_unused import SourceWorld, occupied_unused_plan

from clash_rush_rebuild.mvp_deployment import (
    DeploymentEngine, DeploymentError, DeploymentDisposition, Screen,
)


def support_plan():
    old = occupied_unused_plan()
    try:
        return replace(old, digest='', dispositions=tuple(
            (identity, DeploymentDisposition.DEPLOY) for identity, *_ in old.roster))
    except DeploymentError as error:
        pytest.fail('sealed support deployment rejected: ' + error.code)


def run(world, **ports):
    return DeploymentEngine(observe=ports.get('observe', world.observe),
        deliver=ports.get('deliver', world.deliver), release=world.release,
        live_gate=ports.get('live_gate', lambda: True), monotonic=world.clock,
        sleep=world.clock.sleep, plan=support_plan()).run()


def test_support_is_selected_and_deployed_once_and_receipt_claims_no_reserve_preservation():
    world = SourceWorld()
    result = run(world)
    assert result.complete, result
    assert [e.verb for e in world.events if e.card_id == 'clan_castle'] == ['select', 'tap']
    assert result.remaining == result.deployable_remaining == result.retained_remaining == 0
    assert result.natural_result and result.returned_home
    assert not result.retained_controls and not result.reserve_payload_sha256
    assert not result.post_source_verified
    payload = result.public_payload()
    assert payload['natural_result'] and payload['returned_home']
    assert not any('reserve' in key or 'retained' in key or key.startswith('clan_input') for key in payload)


@pytest.mark.parametrize('transient', [Screen.ARMY, Screen.UNKNOWN])
def test_final_attack_waits_for_battle_without_replaying_input(transient):
    world = SourceWorld(); original = world.observe; pending = [0]
    def observe():
        if pending[0]:
            pending[0] -= 1
            if pending[0] == 0: world.screen = Screen.BATTLE
        return original()
    def deliver(intent, proof):
        sent = world.deliver(intent, proof)
        if sent and intent.verb == 'army_attack':
            world.screen = transient; pending[0] = 4
        return sent
    result = run(world, observe=observe, deliver=deliver)
    assert result.complete, result
    assert sum(e.verb == 'army_attack' for e in world.events) == 1


def test_normal_choice_coexists_with_ranked_tile_without_global_veto():
    from test_s004_image_integration import ImageWorld
    from test_s004_source_images import paint
    world = ImageWorld(); world.screen = 'MATCH'; original = world.capture
    def capture():
        frame = original()
        paint(frame, world.assets, world.data['source_layout']['match']['ranked'])
        return frame
    world.observer.capture = capture
    try:
        observation = world.observer()
    except DeploymentError as error:
        pytest.fail('positive normal choice rejected with Ranked tile present: ' + error.code)
    assert observation.screen is Screen.MATCH
    assert observation.match_mode == 'REGULAR_MULTIPLAYER' and observation.match_cost == 50
    assert observation.control('find_match')


@pytest.mark.parametrize('fault', ['selection_missing', 'selection_ambiguous', 'terminal_missing'])
def test_support_image_proof_failure_never_retries_or_claims_completion(fault):
    from test_s004_image_integration import ImageWorld
    from test_deployment_completeness import _put
    world = ImageWorld(); original = world.capture
    def capture():
        frame = original()
        if world.screen == 'BATTLE' and world.selected == 'clan_castle':
            top = round(360*.82)
            if fault == 'selection_missing':frame[top+18:top+27,186:195] = 0
            elif fault == 'selection_ambiguous':_put(frame,world.assets['clan_depleted'],122,top+28)
            elif world.support_remaining == 0:
                frame[top+28:top+37,186:195] = 0
        return frame
    world.observer.capture = capture
    result = world.run()
    assert not result.complete
    assert [e.verb for e in world.events if e.card_id == 'clan_castle'] == (
        ['select','tap'] if fault == 'terminal_missing' else ['select'])
    assert not result.natural_result and not result.returned_home


@pytest.mark.parametrize('fault', ['timeout', 'unknown_timeout', 'wrong_screen', 'changed_army', 'intervention', 'revoked'])
def test_final_attack_pending_failure_cannot_grant_another_action(fault):
    from clash_rush_rebuild.mvp_deployment import Intervention
    world = SourceWorld(); original = world.observe; pending = [False]; gate = [True]
    def observe():
        observation = original()
        if pending[0]:
            if fault == 'changed_army':observation = replace(observation, source_inventory=())
            elif fault == 'intervention':observation = replace(observation, intervention=Intervention.DETECTED)
        return observation
    def deliver(intent, proof):
        sent = world.deliver(intent, proof)
        if sent and intent.verb == 'army_attack':
            pending[0] = True
            world.screen = Screen.RESULT if fault == 'wrong_screen' else Screen.UNKNOWN if fault == 'unknown_timeout' else Screen.ARMY
            if fault == 'revoked':gate[0] = False
        return sent
    result = run(world, observe=observe, deliver=deliver, live_gate=lambda:gate[0])
    assert not result.complete and world.closed
    assert [e.verb for e in world.events] == ['attack','find_match','army_attack']
    assert world.clock() <= 11


def test_new_receipt_roundtrip_requires_natural_result_and_has_no_reserve_policy():
    import json
    from clash_rush_rebuild.mvp_deployment_receipt import _decode
    result = run(SourceWorld())
    raw = json.dumps(result.public_payload(),sort_keys=True,separators=(',',':'))
    assert _decode(raw) == result and result.receipt_schema == 4
    for change in ({'natural_result':False}, {'returned_home':False}, {'own_exit':True},
                   {'reserve_state':'SOURCE_BOUND'}, {'post_source_verified':True}):
        with pytest.raises(DeploymentError):replace(result, **change)


def test_support_deselection_after_missing_target_never_reselects():
    world = SourceWorld(); original = world.observe; support_reads = [0]
    def observe():
        observation = original()
        if world.selected == 'clan_castle' and world.cards[2].remaining:
            support_reads[0] += 1
            if support_reads[0] == 1:
                return replace(observation, targets=())
            if support_reads[0] == 2:
                world.selected = None
                return replace(observation, page=replace(observation.page, cards=tuple(
                    replace(c, selected=False) for c in observation.page.cards)))
        return observation
    result = run(world, observe=observe)
    assert not result.complete
    assert [e.verb for e in world.events if e.card_id == 'clan_castle'] == ['select']


@pytest.mark.parametrize('fresh', [False, True])
def test_ranked_art_cannot_own_normal_find_target(fresh):
    import numpy as np
    from test_s004_review_aliases import PhysicalWorld
    from test_s004_source_images import paint
    class Collision(PhysicalWorld):
        reads = 0
        def __init__(self):
            super().__init__()
            find = self.data['controls']['MATCH']['find_match']
            ranked = self.data['source_layout']['match']['ranked']
            outer = np.random.default_rng(905).integers(0,255,(21,21,3),dtype=np.uint8)
            outer[1:10,1:10] = self.assets[find['template']]
            self.assets[ranked['template']] = outer
            ranked['roi'] = find['roi'] = [.6,0,1,.1]
            self.rebind()
        def capture(self):
            frame = super().capture()
            if self.screen == 'MATCH':
                self.reads += 1
                if not fresh or self.reads > 1:
                    frame[:36,384:] = 0
                    paint(frame,self.assets,self.data['source_layout']['match']['ranked'])
            return frame
    world = Collision(); result = world.run()
    assert not result.complete and world.hits == ['attack']
