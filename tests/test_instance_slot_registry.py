"""Instance-centric schema uses explicit keys, never a display-name fallback."""
import copy

import pytest

from clash_rush_rebuild import registry
from test_registry import _conf


def registration():
    return {'schema': 2, 'slots': [
        {'slot_id': f'slot-{i:03d}',
         'internal_name': 'Pie64' if i == 0 else f'Pie64_{i + 1}',
         'display_label': f'Label {i}'} for i in range(5)]}


def load(raw=None, conf=None):
    return registry.load_instance_slots(
        _conf(('A', 'B', 'C', 'D', 'E')) if conf is None else conf,
        registration() if raw is None else raw)


def test_explicit_slot_and_internal_identity_survive_label_changes():
    slots = load()
    assert slots[0].slot_id == 'slot-000'
    assert slots[0].internal_name == 'Pie64'
    assert slots[0].display_name == 'A'  # current window corroboration only
    data = registration()
    data['slots'][0]['display_label'] = 'Changed operator label'
    assert load(data) == slots
    renamed = load(data, _conf(('Renamed', 'B', 'C', 'D', 'E')))
    assert renamed[0].slot_id == slots[0].slot_id
    assert renamed[0].internal_name == slots[0].internal_name
    assert renamed[0].display_name == 'Renamed'


@pytest.mark.parametrize('fault', ['wrong_instance', 'display_only', 'duplicate_slot',
    'duplicate_instance', 'missing_slot', 'bool_schema', 'bad_id', 'unknown_field',
    'count', 'nonpie', 'whitespace_instance'])
def test_no_ambiguous_or_display_only_admission(fault):
    raw = copy.deepcopy(registration())
    if fault == 'wrong_instance': raw['slots'][0]['internal_name'] = 'Pie64_99'
    elif fault == 'display_only': del raw['slots'][0]['internal_name']
    elif fault == 'duplicate_slot': raw['slots'][1]['slot_id'] = raw['slots'][0]['slot_id']
    elif fault == 'duplicate_instance': raw['slots'][1]['internal_name'] = 'Pie64'
    elif fault == 'missing_slot': del raw['slots'][0]['slot_id']
    elif fault == 'bool_schema': raw['schema'] = True
    elif fault == 'bad_id': raw['slots'][0]['slot_id'] = '../slot'
    elif fault == 'unknown_field': raw['slots'][0]['player_tag'] = None
    elif fault == 'count': raw['slots'].pop()
    elif fault == 'nonpie': raw['slots'][0]['internal_name'] = 'Nougat32'
    elif fault == 'whitespace_instance': raw['slots'][0]['internal_name'] = ' Pie64'
    with pytest.raises(registry.RegistryError): load(raw)


@pytest.mark.parametrize('geometry', [('1920', '1080', '240'), ('1280', '720', '0240')])
def test_instance_registry_rejects_unsupported_geometry(geometry):
    with pytest.raises(registry.RegistryError):
        load(conf=_conf(('A', 'B', 'C', 'D', 'E'), geometry=geometry))


def test_duplicate_config_properties_reject():
    with pytest.raises(registry.RegistryError):
        load(conf=_conf(('A', 'B', 'C', 'D', 'E')) + '\nbst.instance.Pie64.dpi="240"')


def test_duplicate_display_labels_do_not_override_explicit_internal_keys():
    data = registration()
    for item in data['slots']: item['display_label'] = 'Same label'
    assert tuple(s.internal_name for s in load(data)) == ('Pie64', 'Pie64_2', 'Pie64_3', 'Pie64_4', 'Pie64_5')
