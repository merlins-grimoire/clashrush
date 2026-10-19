"""Offline behavioral transplant regressions; synthetic gameplay only."""
import json
import pytest

from clash_rush_rebuild.mvp_deployment_receipt import _decode
from test_deployment_repair import World, card


def test_exhaustion_waits_for_natural_result_without_end_or_confirmation():
    world = World([card(count=1)])
    result = world.engine().run()
    assert not any(event.verb in ('end_battle', 'confirm_end') for event in world.events)
    assert result.complete and not result.own_exit
    assert result.public_payload()['schema'] == 4
    assert result.public_payload()['natural_result'] is True
    assert result.public_payload()['returned_home'] is True


def test_new_natural_result_receipt_is_explicit_and_byte_canonical():
    payload = dict(schema=4, complete=True, reason='AUTONOMOUS_DEPLOYMENT_COMPLETE',
                   cards_total=1, spells_consumed=0, own_exit=False,
                   home_verified=True, intervention_free=True, remaining=0,
                   natural_result=True, returned_home=True)
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    result = _decode(raw)
    assert json.dumps(result.public_payload(), sort_keys=True, separators=(',', ':')) == raw


@pytest.mark.parametrize('raw', [
    '{"cards_total":2,"complete":true,"home_verified":true,"intervention_free":true,"own_exit":true,"reason":"AUTONOMOUS_DEPLOYMENT_COMPLETE","remaining":0,"schema":1,"spells_consumed":2}',
    '{"cards_total":5,"complete":true,"deployable_remaining":0,"home_verified":true,"intervention_free":true,"natural_result":true,"own_exit":false,"post_source_verified":true,"reason":"AUTONOMOUS_DEPLOYMENT_COMPLETE","remaining":1,"reserve_payload_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","reserve_state":"VERIFIED_UNCHANGED","retained_controls":["clan_castle"],"retained_remaining":1,"returned_home":true,"schema":2,"sealed_reserve_units":6,"spells_consumed":2}',
    '{"cards_total":5,"clan_input_delivered":false,"clan_input_proposed":false,"complete":true,"deployable_remaining":0,"home_verified":true,"intervention_free":true,"natural_result":true,"own_exit":false,"post_source_verified":false,"reason":"AUTONOMOUS_DEPLOYMENT_COMPLETE","remaining":1,"reserve_payload_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","reserve_state":"SOURCE_BOUND","retained_controls":["clan_castle"],"retained_remaining":1,"returned_home":true,"schema":3,"sealed_reserve_units":6,"spells_consumed":2}',
])
def test_historical_receipt_bytes_are_not_upgraded_or_reinterpreted(raw):
    restored = _decode(raw)
    assert json.dumps(restored.public_payload(), sort_keys=True, separators=(',', ':')) == raw
    assert restored.receipt_schema != 4
    data = json.loads(raw)
    assert restored.own_exit is data['own_exit']
    if data['schema'] in (2, 3):
        assert restored.retained_controls == ('clan_castle',)
        assert restored.post_source_verified is data['post_source_verified']
        assert restored.reserve_state == data['reserve_state']
