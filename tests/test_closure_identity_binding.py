"""Schema-2 CLOSED is one identity-bound historical tombstone, not authority.

Inert process leaves, real SQLite proofs and durable disposable Windows anchor.
The negatives catch omission of exact ingress or any actor/offer/retirement join.
"""
from copy import deepcopy
from dataclasses import asdict, fields, replace
import json
import sqlite3

import pytest

from test_canonical_closure import connected
from test_calibration_retirement import captured
from test_policy_normal_retirement import session_success
from test_runtime_reconciliation import readers
from clash_rush_rebuild import cli, mvp_canonical_runtime as canonical
from clash_rush_rebuild.mvp_session_authority import canonical_v3, digest_v3, RunBindingV3


@pytest.fixture(params=['normal', 'calibration'])
def closed_session(connected, request):
    s, host, state, clock, mutex = (captured if request.param == 'calibration' else session_success)(connected)
    retire = s.retire_calibration if request.param == 'calibration' else s.retire_normal_success
    retire(mutex=mutex, expected_title='Synthetic')
    try:
        yield s, host, state, mutex, retire
    finally:
        s.authority.close()


def snapshot(s, host, state):
    return (s.canonical.anchor.path.read_bytes(), tuple(s.authority._open().iterdump()),
            state._port.payload, state._port.guard_present, tuple(host.calls))


def deny_all(values, record, monkeypatch, capsys):
    s, host, state, mutex, retire = values
    s.canonical.anchor.path.write_bytes(canonical_v3(record))
    before = snapshot(s, host, state)
    root = s.authority._path.parent
    readers(root, state, monkeypatch)
    with pytest.raises(RuntimeError):
        s.canonical.stopped(s.authority, state)
    # Direct entry cannot bypass the shape check.
    with pytest.raises(RuntimeError):
        s.canonical.reconcile_stopped(s.authority, state.load_with_bytes(), record)
    reopened = canonical.open_canonical(root)
    with pytest.raises(RuntimeError):
        reopened.reserve(s.run, s.topology.offer.parent, authority=s.authority, state=state,
                         mutex=mutex, commands=(('worker', 'worker'), ('player', 'player')))
    with pytest.raises(RuntimeError):
        retire(mutex=mutex, expected_title='Synthetic')
    assert cli.main(['mvp-status', '--project-root', str(root)]) == 0
    text = capsys.readouterr().out
    assert 'lifecycle=UNRESOLVED' in text and 'runtime=DENIED' in text
    assert 'lifecycle=LIFECYCLE_VERIFIED' not in text
    assert snapshot(s, host, state) == before


@pytest.mark.parametrize('actor', ['parent', 'worker', 'player'])
@pytest.mark.parametrize('change', ['pid', 'creation', 'pair', 'null'])
def test_original_actor_conflicts_deny_every_reader(closed_session, monkeypatch, capsys, actor, change):
    s = closed_session[0]
    record = s.canonical.anchor.load()
    if change == 'null':
        record[actor] = None
    else:
        if change in ('pid', 'pair'): record[actor]['pid'] += 700000
        if change in ('creation', 'pair'): record[actor]['creation_time_100ns'] += 700000
    deny_all(closed_session, record, monkeypatch, capsys)


def mutations(original):
    """Each independent mutation retains all unrelated complete producer fields."""
    cases = []
    def add(label, path, value, delete=False):
        record = deepcopy(original)
        target = record
        for key in path[:-1]: target = target[key]
        if delete: del target[path[-1]]
        else: target[path[-1]] = value
        cases.append((label, record))
    for key in original:
        add('missing-' + key, (key,), None, True)
    add('extra', ('extra',), None)
    for v in (True, 1.0, '2', 3, None): add('schema-' + repr(v), ('schema',), v)
    for v in (None, 1, [], 'UNKNOWN'): add('phase-' + repr(v), ('phase',), v)
    for key in ('installation', 'closure'):
        for v in ('b'*64, 'g'*64, 'A'*64, '', None, 1): add(key + repr(v), (key,), v)
    for v in (None, [], {}, ''): add('run-' + repr(v), ('run',), v)
    for key in original['run']:
        add('missing-run-' + key, ('run', key), None, True)
    add('extra-run', ('run', 'extra'), 1)
    for v in (True, 1.0, '1', 0, -1, 2**63, 2): add('generation-' + repr(v), ('run', 'generation'), v)
    for key in ('nonce', 'digest', 'source_tree', 'manifest'):
        for v in ('g'*len(original['run'][key]), 'A'*len(original['run'][key]), '', None, 1, 'b'*len(original['run'][key])):
            add('run-' + key + repr(v), ('run', key), v)
    for actor in ('parent', 'worker', 'player'):
        for base in ((actor,), ('offer', actor)):
            for v in (None, {}, [], ''): add(str(base) + repr(v), base, v)
            add(str(base)+'extra', base + ('extra',), 1)
            for key in ('pid', 'creation_time_100ns'):
                add(str(base)+key+'missing', base + (key,), None, True)
                for v in (True, False, 0, -1, 1.0, '1'):
                    add(str(base)+key+repr(v), base + (key,), v)
        # Coordinated valid-shaped substitution defeats a mere host==offer check.
        r = deepcopy(original)
        r[actor]['creation_time_100ns'] += 1
        r['offer'][actor] = deepcopy(r[actor])
        cases.append(('coordinated-' + actor, r))
        add('offer-actor-only-' + actor, ('offer', actor), dict(pid=777777, creation_time_100ns=888888))
    for key in original['offer']:
        add('missing-offer-' + key, ('offer', key), None, True)
    add('extra-offer', ('offer', 'extra'), None)
    for key in ('challenge', 'run_nonce'):
        for v in ('b'*32, 'g'*32, 'A'*32, '', None, 1): add(key+repr(v), ('offer', key), v)
    for key in ('remote_player_handle', 'remote_player_job_query', 'generation'):
        for v in (True, False, 0, -1, 1.0, '1', 777777): add(key+repr(v), ('offer', key), v)
    add('equal-handles', ('offer', 'remote_player_handle'), original['offer']['remote_player_job_query'])
    for left, right in (('parent', 'worker'), ('player', 'parent')):
        r = deepcopy(original)
        r[left] = deepcopy(r[right]); r['offer'][left] = deepcopy(r[right])
        cases.append(('overlap-' + left, r))
    r = deepcopy(original)
    r['worker'], r['player'] = r['player'], r['worker']
    r['offer']['worker'], r['offer']['player'] = r['worker'], r['player']
    cases.append(('swapped-roles', r))
    return cases


def test_complete_schema2_mutation_matrix(closed_session, monkeypatch, capsys):
    s = closed_session[0]
    original = s.canonical.anchor.load()
    assert original['schema'] == 2 and original['offer'] == asdict(s.topology.offer)
    for label, record in mutations(original):
        try:
            deny_all(closed_session, record, monkeypatch, capsys)
        except BaseException as exc:
            raise AssertionError(label) from exc
    s.canonical.anchor.path.write_bytes(canonical_v3(original))
    assert s.canonical.stopped(s.authority, s.state)['phase'] == 'CLOSED'


def test_valid_closure_restart_duplicate_and_next_reserve(closed_session, monkeypatch, capsys):
    s, host, state, mutex, retire = closed_session
    record = s.canonical.anchor.load()
    assert record['schema'] == 2 and record['offer'] == asdict(s.topology.offer)
    before = snapshot(s, host, state)
    assert retire(mutex=mutex, expected_title='Synthetic') == state.load()
    # New caller is deliberately unrelated to historical P; no old-process/handle query.
    monkeypatch.setattr(canonical.os, 'getpid', lambda: 909090)
    fresh = canonical.open_canonical(s.authority._path.parent)
    assert fresh.stopped(s.authority, state) == record
    readers(s.authority._path.parent, state, monkeypatch)
    assert cli.main(['mvp-status', '--project-root', str(s.authority._path.parent)]) == 0
    text = capsys.readouterr().out
    assert 'lifecycle=LIFECYCLE_VERIFIED' in text and 'host=CLOSED' in text
    assert ('outcome=BLOCKED reason=EVIDENCE_INCOMPLETE' if s.topology.purpose == 'CALIBRATION_READ_ONLY'
            else 'outcome=SUCCESS reason=PRE_D_COMPLETE') in text
    assert snapshot(s, host, state) == before
    from test_policy_finalization_v3 import next_binding, stopped
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    next_run = next_binding(s.run)
    fresh.reserve(next_run, ProcessIdentity(909090, 456), authority=s.authority, state=state,
                  mutex=mutex, commands=(('worker', 'worker'), ('player', 'player')))
    s.authority.reserve_v3(next_run, writer_epoch=s.epoch, proof=stopped())
    assert s.authority.read_run_v3(next_run.generation) == next_run
    pending = fresh.anchor.load()
    assert pending['phase'] == 'PREPARING' and pending['offer'] is None
    assert pending['worker'] is None and pending['player'] is None
    assert tuple(host.calls) == before[-1]


def test_schema1_closed_is_not_backfilled(closed_session, monkeypatch, capsys):
    s = closed_session[0]
    record = s.canonical.anchor.load()
    record['schema'] = 1
    record.pop('offer', None)
    deny_all(closed_session, record, monkeypatch, capsys)


@pytest.mark.parametrize('actor', ['parent', 'worker', 'player', 'missing-worker'])
@pytest.mark.parametrize('caller', ['ingress-run', 'ingress-calibration', 'issuer'])
def test_bad_actor_blocks_real_issuance_and_ingress(closed_session, monkeypatch, actor, caller):
    from test_policy_ingress_command import fixture_sources, approve, run_request
    from clash_rush_rebuild.mvp_policy_setup import issue_approval
    s, host, state, mutex, retire = closed_session
    root = s.authority._path.parent
    record = s.canonical.anchor.load()
    if actor == 'missing-worker': record['worker'] = None
    else: record[actor]['creation_time_100ns'] += 1
    s.canonical.anchor.path.write_bytes(canonical_v3(record))
    target = root / 'var/mvp-session.sqlite3'
    with sqlite3.connect(target) as db: s.authority._open().backup(db)
    policy, api, port = fixture_sources(root, monkeypatch)
    port.payload = state._port.payload
    approval = root / 'var/approvals/policy-v3.json'
    if caller == 'issuer':
        approval.unlink()
    else:
        purpose = 'RUN_V3' if caller == 'ingress-run' else 'CALIBRATION_READ_ONLY'
        approve(root, port, purpose=purpose, run=run_request(root))
    before = snapshot(s, host, state)
    with sqlite3.connect(target) as db: rows = tuple(db.iterdump())
    approval_before = approval.read_bytes() if approval.exists() else None
    with pytest.raises(canonical.CanonicalRuntimeError):
        if caller == 'issuer': issue_approval(policy, purpose='CALIBRATION_READ_ONLY', source_tree='a'*40, approve=True)
        else:
            with policy.prepare_run(purpose=purpose): pytest.fail('conflicting actor admitted')
    assert snapshot(s, host, state) == before
    with sqlite3.connect(target) as db: assert tuple(db.iterdump()) == rows
    assert (approval.read_bytes() if approval.exists() else None) == approval_before


@pytest.mark.parametrize('field', [f.name for f in fields(RunBindingV3)])
def test_sealed_full_binding_cannot_be_rewritten(closed_session, field):
    s = closed_session[0]
    old = getattr(s.run, field)
    new = old + 1 if type(old) is int else (('b' if old != 'b'*len(old) else 'c') * len(old))
    with pytest.raises(sqlite3.IntegrityError):
        s.authority._open().execute(f'UPDATE run_v3 SET {field}=? WHERE generation=?', (new, s.run.generation))
    assert s.canonical.stopped(s.authority, s.state)['phase'] == 'CLOSED'


@pytest.mark.parametrize('table,column,value', [
    ('retirement_v3', 'identity_digest', 'b'*64), ('retirement_v3', 'proof_digest', 'b'*64),
    ('retirement_v3', 'proof', b'{}'), ('retirement_v3', 'role', 'OTHER'),
    ('retirement_v3', 'generation', 2), ('closure_v3', 'closure_digest', 'b'*64),
    ('closure_v3', 'topology_digest', 'b'*64), ('closure_v3', 'worker_retired_digest', 'b'*64),
    ('closure_v3', 'player_retired_digest', 'b'*64), ('closure_v3', 'outcome_readback_digest', 'b'*64)])
def test_sealed_role_and_closure_corruption_rejected(closed_session, table, column, value):
    s = closed_session[0]
    with pytest.raises(sqlite3.IntegrityError):
        s.authority._open().execute(f'UPDATE {table} SET {column}=?', (value,))
    with pytest.raises(sqlite3.IntegrityError): s.authority._open().execute(f'DELETE FROM {table}')
    assert s.canonical.stopped(s.authority, s.state)['phase'] == 'CLOSED'


@pytest.mark.parametrize('lane', ['normal', 'calibration'])
@pytest.mark.parametrize('fault', ['parent', 'worker', 'player', 'challenge'])
def test_writer_rejects_prospective_closed_conflict_inside_guard(connected, monkeypatch, lane, fault):
    s, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
    original = s.authority.close_v3
    def conflict(*args, **kwargs):
        result = original(*args, **kwargs)
        assert state._port.guard_present
        record = s.canonical.anchor.load()
        if fault == 'challenge':
            if 'offer' in record: record['offer']['challenge'] = 'b'*32
            else: record['parent']['creation_time_100ns'] += 1
        else:
            record[fault]['creation_time_100ns'] += 1
            if 'offer' in record: record['offer'][fault] = deepcopy(record[fault])
        s.canonical.anchor.path.write_bytes(canonical_v3(record))
        s.canonical.record = record
        return result
    monkeypatch.setattr(s.authority, 'close_v3', conflict)
    retire = s.retire_calibration if lane == 'calibration' else s.retire_normal_success
    try:
        with pytest.raises(RuntimeError): retire(mutex=mutex, expected_title='Synthetic')
        assert state._port.guard_present
        assert s.canonical.anchor.load()['phase'] == 'STOP_INTENT'
        before = tuple(host.calls)
        with pytest.raises(RuntimeError): retire(mutex=mutex, expected_title='Synthetic')
        assert tuple(host.calls) == before
    finally:
        s.authority.close()
