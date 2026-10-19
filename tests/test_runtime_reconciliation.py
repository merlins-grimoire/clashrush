"""Run101 correction: actual closure writers, current-state joins and public Status.

Real SQLite + guarded lifecycle codec + disposable Windows anchor; no game leaves.
"""
from dataclasses import asdict
import pytest

from test_canonical_closure import connected
from test_calibration_retirement import captured
from test_policy_normal_retirement import session_success
from test_policy_parent_session import compose
from test_lifecycle_state import RecordingFilePort
from canonical_fixture import install_fixture, phase_record
from clash_rush_rebuild import cli, mvp_policy_ingress as ingress
from clash_rush_rebuild.lifecycle_state import LifecycleStateStore, Ready, Active, encode_state
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority, canonical_v3


def readers(root, state, monkeypatch):
    calls = []
    def authority(path, *, read_only):
        assert read_only is True
        return DurableSessionAuthority(root / 'session.sqlite3', read_only=True)
    def lifecycle(*args):
        calls.append('lifecycle')
        return state
    monkeypatch.setattr(ingress, '_require_private', lambda path: None)
    monkeypatch.setattr(ingress, 'DurableSessionAuthority', authority)
    monkeypatch.setattr(cli, 'DurableSessionAuthority', authority)
    monkeypatch.setattr(ingress, 'LifecycleStateStore', lifecycle)
    return calls


def alter_state(state, fault):
    if fault == 'active':
        state._port.payload = encode_state(Active(0, 'a'*32, None))
    elif fault == 'mismatch':
        state._port.payload = encode_state(Ready((state.load().next_slot + 1) % 5))
    elif fault == 'missing':
        state._port.state_present = False
    elif fault == 'corrupt':
        state._port.payload = b'not canonical state'
    elif fault == 'noncanonical':
        state._port.payload = state._port.payload.rstrip(b'\n')
    elif fault == 'guarded':
        state._port.guard_present = True


@pytest.mark.parametrize('lane', ['normal', 'calibration'])
@pytest.mark.parametrize('fault', ['none', 'mismatch', 'active', 'missing', 'corrupt', 'noncanonical', 'guarded'])
def test_closed_current_ready_join_and_operator_status(connected, monkeypatch, capsys, lane, fault):
    session, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
    retire = session.retire_calibration if lane == 'calibration' else session.retire_normal_success
    try:
        retire(mutex=mutex, expected_title='Synthetic')
        alter_state(state, fault)
        readers(connected, state, monkeypatch)
        before = (session.authority._path.read_bytes(), session.canonical.anchor.path.read_bytes(),
                  state._port.payload, state._port.guard_present, list(host.calls))
        if fault == 'none':
            assert session.canonical.stopped(session.authority, state)['phase'] == 'CLOSED'
        else:
            with pytest.raises((RuntimeError, OSError)):
                session.canonical.stopped(session.authority, state)
        result = ingress.read_runtime_dimensions(connected)
        expected = 'LIFECYCLE_VERIFIED' if fault == 'none' else (
            'UNRESOLVED' if fault in ('mismatch', 'active') else 'UNPROVED')
        assert result['lifecycle'] == expected
        assert result['host'] == 'CLOSED'
        outcome = ('BLOCKED', 'EVIDENCE_INCOMPLETE') if lane == 'calibration' else ('SUCCESS', 'PRE_D_COMPLETE')
        assert (result['outcome'], result['reason']) == outcome
        assert cli.main(['mvp-status', '--project-root', str(connected)]) == 0
        text = capsys.readouterr().out
        for field, value in result.items():
            assert f'{field}={value}' in text
        assert 'runtime=DENIED' in text
        assert str(connected) not in text and session.run.run_nonce not in text
        assert before == (session.authority._path.read_bytes(), session.canonical.anchor.path.read_bytes(),
                          state._port.payload, state._port.guard_present, list(host.calls))
    finally:
        session.authority.close()


@pytest.mark.parametrize('phase', ['STOPPED', 'PREPARING', 'WORKER_PENDING', 'PLAYER_PENDING', 'PUBLISHED', 'STOP_INTENT', 'CLOSED', 'absent', 'guarded', 'corrupt'])
@pytest.mark.parametrize('fault', ['none', 'active', 'missing', 'corrupt', 'guarded'])
def test_zero_generation_independently_reads_state_and_host(tmp_path, monkeypatch, fault, phase):
    session, host, _, clock, mutex = compose(tmp_path)
    state = LifecycleStateStore(tmp_path, RecordingFilePort())
    state.initialize_ready()
    admission = install_fixture(tmp_path, monkeypatch)
    alter_state(state, fault)
    if phase == 'absent':
        admission.anchor.path.unlink()
    elif phase == 'guarded':
        admission.anchor.guard.write_bytes(b'pending\n')
    elif phase == 'corrupt':
        admission.anchor.path.write_bytes(b'corrupt')
    elif phase != 'STOPPED':
        record = phase_record(session.run, phase)
        admission.anchor.path.write_bytes(canonical_v3(record))
    calls = readers(tmp_path, state, monkeypatch)
    try:
        result = ingress.read_runtime_dimensions(tmp_path)
        assert calls, 'generation zero must not skip lifecycle/guard read'
        assert (result['outcome'], result['reason']) == ('NONE', 'NONE')
        expected = 'STOPPED' if phase == 'STOPPED' and fault == 'none' else (
            'UNPROVED' if fault in ('missing', 'corrupt', 'guarded') else 'UNRESOLVED')
        assert result['lifecycle'] == expected
        assert result['host'] == (phase if phase not in ('absent', 'guarded', 'corrupt') else 'UNPROVED')
        assert not host.calls
    finally:
        session.authority.close()


@pytest.mark.parametrize('fault', ['generation', 'nonce', 'digest', 'source_tree', 'manifest', 'closure', 'installation', 'phase'])
def test_closed_host_binding_cannot_be_stale_or_conflicting(connected, monkeypatch, fault):
    session, host, state, clock, mutex = session_success(connected)
    try:
        session.retire_normal_success(mutex=mutex, expected_title='Synthetic')
        record = session.canonical.anchor.load()
        if fault in ('generation', 'nonce', 'digest', 'source_tree', 'manifest'):
            record['run'][fault] = 2 if fault == 'generation' else '0'*len(record['run'][fault])
        elif fault == 'phase':
            record.update(phase='STOPPED', run=None, parent=None, worker=None, player=None, closure=None, offer=None)
        else:
            record[fault] = '0'*64
        session.canonical.anchor.path.write_bytes(canonical_v3(record))
        readers(connected, state, monkeypatch)
        with pytest.raises((RuntimeError, OSError)):
            session.canonical.stopped(session.authority, state)
        assert ingress.read_runtime_dimensions(connected)['lifecycle'] == 'UNRESOLVED'
    finally:
        session.authority.close()


def test_status_missing_project_is_nonmutating(tmp_path):
    root = tmp_path / 'not-created'
    assert ingress.read_runtime_dimensions(root) == dict(outcome='UNPROVED', reason='UNPROVED', lifecycle='UNPROVED', host='UNPROVED')
    assert cli.main(['mvp-status', '--project-root', str(root)]) == 0
    assert not root.exists()


def test_failed_database_read_does_not_hide_host_or_read_state_as_stopped(tmp_path, monkeypatch):
    session, host, _, clock, mutex = compose(tmp_path)
    state = LifecycleStateStore(tmp_path, RecordingFilePort())
    state.initialize_ready()
    install_fixture(tmp_path, monkeypatch)
    calls = readers(tmp_path, state, monkeypatch)
    def broken(*a, **kw):
        raise OSError('private error must not escape')
    monkeypatch.setattr(ingress, 'DurableSessionAuthority', broken)
    try:
        result = ingress.read_runtime_dimensions(tmp_path)
        assert calls
        assert result == dict(outcome='UNPROVED', reason='UNPROVED', lifecycle='UNRESOLVED', host='STOPPED')
    finally:
        session.authority.close()


@pytest.mark.parametrize('lane', ['normal', 'calibration'])
@pytest.mark.parametrize('phase', ['PREPARING', 'WORKER_PENDING', 'PLAYER_PENDING', 'PUBLISHED', 'STOP_INTENT', 'absent', 'guarded', 'corrupt'])
def test_closed_local_history_never_overrides_host_uncertainty(connected, monkeypatch, lane, phase):
    session, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
    try:
        retire = session.retire_calibration if lane == 'calibration' else session.retire_normal_success
        retire(mutex=mutex, expected_title='Synthetic')
        anchor = session.canonical.anchor
        if phase == 'absent':
            anchor.path.unlink()
        elif phase == 'guarded':
            anchor.guard.write_bytes(b'pending\n')
        elif phase == 'corrupt':
            anchor.path.write_bytes(b'corrupt')
        else:
            record = phase_record(session.run, phase, session.topology.offer)
            anchor.path.write_bytes(canonical_v3(record))
        readers(connected, state, monkeypatch)
        result = ingress.read_runtime_dimensions(connected)
        assert result['lifecycle'] == 'UNRESOLVED'
        assert result['host'] == (phase if phase not in ('absent', 'guarded', 'corrupt') else 'UNPROVED')
        assert result['outcome'] == ('BLOCKED' if lane == 'calibration' else 'SUCCESS')
    finally:
        session.authority.close()


@pytest.mark.parametrize('fault', ['zero', 'absent', 'newer', 'closure_digest', 'ready_digest', 'open'])
def test_sqlite_current_or_closure_conflict_denies(connected, monkeypatch, fault):
    session, host, state, clock, mutex = session_success(connected)
    try:
        session.retire_normal_success(mutex=mutex, expected_title='Synthetic')
        c = session.authority._open()
        if fault in ('zero', 'absent', 'newer'):
            value = {'zero': 0, 'absent': None, 'newer': 2}[fault]
            c.execute('UPDATE authority_v3 SET current_generation=?', (value,))
        else:
            # Historical closure is immutable: the production trigger rejects
            # corruption before any reader. Do not drop it to fabricate a join.
            import sqlite3
            column = 'lifecycle_readback_digest' if fault == 'ready_digest' else 'closure_digest'
            with pytest.raises(sqlite3.IntegrityError, match='closure conflict'):
                if fault == 'open':
                    c.execute('UPDATE closure_v3 SET closed=0')
                else:
                    c.execute(f'UPDATE closure_v3 SET {column}=?', ('0'*64,))
            assert session.canonical.stopped(session.authority, state)['phase'] == 'CLOSED'
            return
        readers(connected, state, monkeypatch)
        with pytest.raises((RuntimeError, OSError)):
            session.canonical.stopped(session.authority, state)
        assert ingress.read_runtime_dimensions(connected)['lifecycle'] == 'UNRESOLVED'
    finally:
        session.authority.close()


@pytest.mark.parametrize('caller', ['reserve', 'ingress', 'issuer'])
@pytest.mark.parametrize('lane', ['normal', 'calibration'])
def test_current_ready_conflict_reaches_actual_admission_callers(connected, monkeypatch, caller, lane):
    from test_policy_ingress_command import fixture_sources, approve, run_request
    from clash_rush_rebuild import mvp_canonical_runtime as canonical
    session, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
    try:
        retire = session.retire_calibration if lane == 'calibration' else session.retire_normal_success
        retire(mutex=mutex, expected_title='Synthetic')
        alter_state(state, 'mismatch')
        before = list(host.calls)
        if caller == 'reserve':
            reopened = canonical.open_canonical(connected)
            with pytest.raises(canonical.CanonicalRuntimeError):
                reopened.reserve(session.run, session.topology.offer.parent, authority=session.authority,
                                 state=state, mutex=mutex, commands=(('worker', 'worker'), ('player', 'player')))
        else:
            # Approval binds the substituted READY: the closure join must deny.
            target = connected / 'var/mvp-session.sqlite3'
            import sqlite3
            with sqlite3.connect(target) as db:
                session.authority._open().backup(db)
            policy, api, port = fixture_sources(connected, monkeypatch)
            port.payload = state._port.payload
            if caller == 'issuer':
                from clash_rush_rebuild.mvp_policy_setup import issue_approval
                (connected / 'var/approvals/policy-v3.json').unlink()
                with pytest.raises(canonical.CanonicalRuntimeError):
                    issue_approval(policy, purpose='CALIBRATION_READ_ONLY', source_tree='a'*40, approve=True)
                assert not (connected / 'var/approvals/policy-v3.json').exists()
                return
            approve(connected, port, purpose='RUN_V3', run=run_request(connected))
            with pytest.raises(canonical.CanonicalRuntimeError):
                with policy.prepare_run():
                    pytest.fail('conflicting READY admitted')
        assert host.calls == before
        assert session.canonical.anchor.load()['phase'] == 'CLOSED'
    finally:
        session.authority.close()


@pytest.mark.parametrize('lane', ['normal', 'calibration'])
def test_duplicate_closed_response_reconciles_current_host_without_replaying(connected, lane):
    session, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
    try:
        retire = session.retire_calibration if lane == 'calibration' else session.retire_normal_success
        retire(mutex=mutex, expected_title='Synthetic')
        anchor = session.canonical.anchor
        record = anchor.load()
        anchor.path.write_bytes(canonical_v3(dict(record, phase='STOP_INTENT')))
        before = list(host.calls)
        with pytest.raises(RuntimeError):
            retire(mutex=mutex, expected_title='Synthetic')
        assert host.calls == before
    finally:
        session.authority.close()
