"""Policy retirement is not matcher acceptance or gameplay success."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from test_policy_sources import prepared_sources
from test_s004_image_integration import ImageWorld
from test_policy_finalization_v3 import binding_v3, snapshot, fact, v3_store, success
from clash_rush_rebuild.mvp_session_authority import (
    DurableSessionAuthority, FactV3, SnapshotV3, canonical_v3, digest_v3,
)
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild import mvp_policy_sources as sources


def reseal(binding, old, facts):
    seal = fact(binding, 'PRE_D_SEAL', 1, 170,
        facts=[[f.kind, f.sequence, f.digest] for f in sorted(facts, key=lambda f: (f.kind, f.sequence))],
        inputs=[[i.gesture_seq, i.event_seq, i.digest] for i in old.inputs], emergency_release_required=False)
    return SnapshotV3(binding, 1, tuple(facts) + (seal,), old.inputs)


def instance_snapshot(binding, **changes):
    old = snapshot(binding)
    facts = []
    for item in old.facts:
        if item.kind == 'PRE_D_SEAL': continue
        if item.kind == 'SAME_ACCOUNT_HOME':
            raw = json.loads(item.payload)
            raw.update(identity_policy='IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY',
                       provider_digest=binding.provider_digest, **changes)
            item = FactV3('INSTANCE_SLOT_HOME', item.sequence, item.observed_ns, canonical_v3(raw))
        facts.append(item)
    return reseal(binding, old, facts)


def test_legacy_snapshot_bytes_unchanged_and_new_policy_explicit(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    legacy = snapshot(binding)
    before = tuple(f.payload for f in legacy.facts)
    DurableSessionAuthority._complete_v3(binding, legacy.facts, legacy.inputs)
    current = instance_snapshot(binding)
    store.record_snapshot_v3(current)
    assert success(store, binding).outcome == 'SUCCESS'
    assert store.closure_v3(binding.generation).closed == 0
    assert tuple(f.payload for f in legacy.facts) == before
    assert 'SAME_ACCOUNT_HOME' not in {f.kind for f in current.facts}
    store.close()


@pytest.mark.parametrize('fault', ['provider', 'slot_ref', 'fresh', 'mixed'])
def test_new_semantics_preserve_chain_and_binding(fault):
    binding = binding_v3()
    current = instance_snapshot(binding)
    items = list(current.facts[:-1])
    if fault == 'mixed':
        items.append(next(f for f in snapshot(binding).facts if f.kind == 'SAME_ACCOUNT_HOME'))
    else:
        home = next(f for f in items if f.kind == 'INSTANCE_SLOT_HOME')
        raw = json.loads(home.payload)
        raw[{'provider':'provider_digest', 'slot_ref':'account_ref', 'fresh':'fresh'}[fault]] = (
            '0'*64 if fault == 'provider' else 'other' if fault == 'slot_ref' else False)
        items[items.index(home)] = replace(home, payload=canonical_v3(raw))
    invalid = reseal(binding, current, items)
    with pytest.raises(RuntimeError):
        DurableSessionAuthority._complete_v3(binding, invalid.facts, invalid.inputs)


@pytest.mark.parametrize('name_pixels', [False, True])
def test_current_home_has_no_name_asset_dependency(tmp_path, monkeypatch, name_pixels):
    world = ImageWorld(); world.clock.now = 100.
    reader, assets, *_ = prepared_sources(tmp_path, monkeypatch, world)
    assert 'account_marker' not in assets
    frame = world.capture()
    # Previously required account-marker area, outside generic Home witness.
    frame[80:89,20:29] = (np.random.default_rng(80).integers(0,255,(9,9,3),dtype=np.uint8)
                         if name_pixels else 0)
    assert reader.home_verified(frame)
    assert reader.provider is None and reader.version_code is None


@pytest.mark.parametrize('field,value', [('slot_id','slot-other'), ('instance_digest','0'*64),
    ('identity_policy','PROVED'), ('schema',1)])
def test_descriptor_cannot_downgrade_to_display_or_account_authority(tmp_path, monkeypatch, field, value):
    reader, *_ = prepared_sources(tmp_path, monkeypatch)
    data = dict(reader.data); data[field] = value
    raw = canonical_v3(data)
    (tmp_path/'private/deployment/provider.json').write_bytes(raw)
    run = replace(reader.run, provider_digest=digest_v3(raw))
    with pytest.raises(RuntimeError): sources.PolicyWorkerSources(tmp_path, run, clock=lambda:100.)


def test_generic_matcher_unchanged_is_not_research_import():
    path = Path(sources.__file__)
    text = path.read_text(encoding='utf-8')
    assert 'account_template' not in text and 'account_roi' not in text
    assert 'revision_003' not in text and 'real-variation' not in text
