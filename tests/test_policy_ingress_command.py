"""Offline ingress: real policy predicates/SQLite, synthetic OS leaves only."""
from __future__ import annotations

from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

import pytest

from clash_rush_rebuild.lifecycle_state import Ready, Active, encode_state
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_runner_manifest import runner_manifest
from clash_rush_rebuild.mvp_session_authority import (
    DurableSessionAuthority, StoppedProofV3, canonical_v3, digest_v3,
)
from test_win32_runtime import FakeRuntimeApi
from test_win32_primitives import FakeToolhelpApi
from test_lifecycle_state import RecordingFilePort

AVAILABLE = importlib.util.find_spec('clash_rush_rebuild.mvp_policy_ingress') is not None
if AVAILABLE:
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    NATIVE_SECURITY_READER = ingress._read_path_security


def database(root):
    (root / 'var').mkdir(exist_ok=True)
    store = DurableSessionAuthority(root / 'var' / 'mvp-session.sqlite3')
    store.setup(MvpConfiguration('team', 'account', 'slot-0', 'a' * 64))
    store.close()


@pytest.mark.parametrize('missing', ['approval', 'mutex', 'input'])
def test_untrusted_digests_cannot_authorize_migration(tmp_path, monkeypatch, missing):
    database(tmp_path)
    denied = False
    try:
        if not AVAILABLE:
            # Actual old boundary accepts naked digests despite missing sources.
            store = DurableSessionAuthority(tmp_path / 'var/mvp-session.sqlite3')
            try:
                store._migrate_stopped_v3(proof=StoppedProofV3(*(['a' * 64] * 8)),
                    backup_path=tmp_path / 'var/backup.sqlite3')
            finally:
                store.close()
        else:
            policy, api, port = fixture_sources(tmp_path, monkeypatch)
            if missing == 'approval':
                (tmp_path / 'var/approvals/policy-v3.json').unlink()
            elif missing == 'mutex':
                api.wait_result = 0x102
            else:
                monkeypatch.setattr(ingress, '_read_input_state', ingress._unavailable_input_state)
            policy.migrate_stopped(backup_name='backup.sqlite3')
    except (RuntimeError, OSError):
        denied = True
    assert denied, f'naked digests authorized migration without {missing}'


def fixture_sources(root, monkeypatch):
    from canonical_fixture import install_fixture
    canonical = install_fixture(root, monkeypatch)
    api = FakeRuntimeApi()
    api.already_exists = True
    port = RecordingFilePort()
    port.payload = encode_state(Ready(0))
    port.state_present = True
    (root / 'var/approvals').mkdir(exist_ok=True)
    (root / 'var/lifecycle-state.json').write_bytes(port.payload)
    deployment = root / 'private/deployment'
    deployment.mkdir(parents=True, exist_ok=True)
    (deployment / 'profile.json').write_bytes(b'{"synthetic_profile":1}')
    (deployment / 'provider.json').write_bytes(b'{"synthetic_provider":1}')
    package = Path(ingress.__file__).parent
    (deployment / 'runner-manifest.json').write_bytes(runner_manifest(package, 'a' * 40))
    # Replace low-level security/OS observations, never the approval predicate,
    # lifecycle codec, installed manifest predicate, or authority implementation.
    monkeypatch.setattr(ingress, '_read_path_security', lambda path: (
        'S-1-5-21-1234', 'D:P(A;;FA;;;SY)(A;;FA;;;S-1-5-21-1234)' if path.is_file()
        else 'D:P(A;;FA;;;SY)(A;OICIIO;GA;;;SY)(A;;FA;;;S-1-5-21-1234)(A;OICIIO;GA;;;S-1-5-21-1234)'))
    monkeypatch.setattr(ingress, '_read_input_state', lambda: (0,) * 256)
    monkeypatch.setattr(ingress.time, 'time', lambda: 100)
    monkeypatch.setattr(ingress.time, 'monotonic_ns', lambda: 100_000_000_000)
    policy = ingress.PolicyIngress(root, runtime_api=api,
        process_api=FakeToolhelpApi(((os.getpid(), 1, 'python.exe'),)), state_port=port)
    approve(root, port)
    return policy, api, port


def approve(root, port, *, purpose='MIGRATE_SCHEMA3', run=None, **changes):
    store = DurableSessionAuthority(root / 'var/mvp-session.sqlite3', read_only=True)
    try:
        authority_digest = digest_v3('\n'.join(store._open().iterdump()).encode())
    finally:
        store.close()
    deployment = root / 'private/deployment'
    raw = dict(schema=3, purpose=purpose, approval_id='1' * 32,
        source_tree='a' * 40, issued_at=99, expires_at=200,
        authority_digest=authority_digest, lifecycle_digest=digest_v3(port.payload),
        runner_manifest_digest=digest_v3((deployment / 'runner-manifest.json').read_bytes()),
        profile_digest=None if run is None else digest_v3((deployment / 'profile.json').read_bytes()),
        provider_digest=None if run is None else digest_v3((deployment / 'provider.json').read_bytes()), run=run)
    raw.update(changes)
    payload = canonical_v3(raw)
    (root / 'var/approvals/policy-v3.json').write_bytes(payload)
    return payload


@pytest.fixture
def sources(tmp_path, monkeypatch):
    if not AVAILABLE:
        pytest.skip('new ingress not implemented yet')
    database(tmp_path)
    return tmp_path, *fixture_sources(tmp_path, monkeypatch)


def test_migration_consistent_backup_and_exact_proof_readback(sources):
    root, policy, api, port = sources
    before = sqlite3.connect(root / 'var/mvp-session.sqlite3')
    source = '\n'.join(before.iterdump())
    before.close()
    result = policy.migrate_stopped(backup_name='backup.sqlite3')
    backup = sqlite3.connect(root / 'var/backup.sqlite3')
    assert '\n'.join(backup.iterdump()) == source
    assert backup.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    backup.close()
    store = DurableSessionAuthority(root / 'var/mvp-session.sqlite3', read_only=True)
    row = store._open().execute('SELECT * FROM migration_v3').fetchone()
    assert json.loads(row['proof']) == asdict(result)
    assert len(set(asdict(result).values())) == 8
    assert store.status_v3().generations == ()
    store.close()
    assert ('mutex:verify-dacl', 'mutex', 'D:P(A;;GA;;;SY)(A;;GA;;;S-1-5-21-1234)') in api.calls
    assert ('mutex:release', 'mutex') in api.calls
    assert 'create' not in port.events


@pytest.mark.parametrize('fault', ['expired', 'future', 'purpose', 'noncanonical', 'extra',
    'approval-redirect', 'acl', 'manifest', 'authority', 'ready', 'transition',
    'player', 'worker', 'enumeration', 'held', 'unknown-input', 'mutex-dacl', 'abandoned',
    'backup-existing', 'backup-outside'])
def test_hard_negatives_leave_schema_and_backup_untouched(sources, monkeypatch, fault):
    root, policy, api, port = sources
    approval = root / 'var/approvals/policy-v3.json'
    backup_name = 'backup.sqlite3'
    if fault == 'expired': approve(root, port, expires_at=100)
    elif fault == 'future': approve(root, port, issued_at=101)
    elif fault == 'purpose': approve(root, port, purpose='RUN_V3')
    elif fault == 'noncanonical': approval.write_bytes(approval.read_bytes() + b'\n')
    elif fault == 'extra': approve(root, port, approved=True)
    elif fault == 'approval-redirect':
        # A deterministic reparse observation, without creating privileged links.
        original = ingress._no_link
        def redirected(path):
            if path == approval: raise ingress.PolicyIngressError('POLICY_PATH_REDIRECTED')
            return original(path)
        monkeypatch.setattr(ingress, '_no_link', redirected)
    elif fault == 'acl': monkeypatch.setattr(ingress, '_read_path_security', lambda path: ('S-1-5-21-1234', 'D:(A;;FA;;;WD)'))
    elif fault == 'manifest': (root / 'private/deployment/runner-manifest.json').write_bytes(b'{}')
    elif fault == 'authority':
        store = DurableSessionAuthority(root / 'var/mvp-session.sqlite3')
        store._open().execute('UPDATE control SET revision=revision+1')
        store.close()
    elif fault == 'ready': port.payload = encode_state(Active(0, '2' * 32, None))
    elif fault == 'transition': port.guard_present = True
    elif fault == 'player': policy._process_api = FakeToolhelpApi(((os.getpid(), 1, 'python.exe'), (9, 1, 'HD-Player.exe')))
    elif fault == 'worker':
        from clash_rush_rebuild.mvp_canonical_runtime import open_canonical
        anchor = open_canonical(root).anchor
        record = anchor.load()
        record.update(phase='PREPARING', run={'generation':1}, parent={'pid':9})
        anchor.path.write_bytes(canonical_v3(record))
    elif fault == 'enumeration': policy._process_api = FakeToolhelpApi((), terminal_error=5)
    elif fault == 'held': monkeypatch.setattr(ingress, '_read_input_state', lambda: (0x8000,) + (0,) * 255)
    elif fault == 'unknown-input': monkeypatch.setattr(ingress, '_read_input_state', lambda: (False,) * 256)
    elif fault == 'mutex-dacl': api.dacl_matches = False
    elif fault == 'abandoned':
        from clash_rush_rebuild import win32_runtime
        monkeypatch.setattr(win32_runtime, '_ABANDONED_MUTEX_NAMES', set())
        api.wait_result = 0x80
    elif fault == 'backup-existing': (root / 'var/backup.sqlite3').write_bytes(b'preserve')
    elif fault == 'backup-outside': backup_name = '../backup.sqlite3'
    with pytest.raises((RuntimeError, OSError)):
        policy.migrate_stopped(backup_name=backup_name)
    with sqlite3.connect(root / 'var/mvp-session.sqlite3') as check:
        assert check.execute('PRAGMA user_version').fetchone() == (1,)
    if fault == 'backup-existing': assert (root / 'var/backup.sqlite3').read_bytes() == b'preserve'
    else: assert not (root / 'var/backup.sqlite3').exists()


def test_default_input_source_never_assumes_clear():
    if not AVAILABLE: pytest.skip('new ingress not implemented yet')
    with pytest.raises(ingress.PolicyIngressError, match='INPUT_SOURCE_UNAVAILABLE'):
        ingress._unavailable_input_state()


def run_request(root):
    deployment = root / 'private/deployment'
    return dict(generation=1, run_nonce='2' * 32, transaction_ref='3' * 32,
        configuration_revision=1, control_revision=2, source_tree='a' * 40,
        runner_manifest_digest=digest_v3((deployment / 'runner-manifest.json').read_bytes()),
        profile_digest=digest_v3((deployment / 'profile.json').read_bytes()),
        provider_digest=digest_v3((deployment / 'provider.json').read_bytes()),
        account_ref='account', binding_digest='f' * 64, clock_domain='synthetic-clock',
        started_ns=99_000_000_000, deadline_ns=199_000_000_000, revocation_epoch=0)


def test_preparation_requires_explicit_migration_and_never_activates(sources):
    root, policy, api, port = sources
    request = run_request(root)
    approve(root, port, purpose='RUN_V3', run=request)
    with pytest.raises(RuntimeError):
        with policy.prepare_run(): pass
    assert not (root / 'var/backup.sqlite3').exists()
    approve(root, port)
    policy.migrate_stopped(backup_name='backup.sqlite3')
    payload = approve(root, port, purpose='RUN_V3', run=request)
    with policy.prepare_run() as prepared:
        assert prepared.run.approval_digest == digest_v3(payload)
        assert prepared.slot == 0 and prepared.writer_epoch == 1
        assert prepared.authority.status_v3().generations == ()
        with pytest.raises(ingress.PolicyIngressError, match='RUNTIME_ACTIVATION_DENIED'):
            prepared.require_runtime_activation()
        prepared.authority.reserve_v3(prepared.run, writer_epoch=prepared.writer_epoch, proof=prepared.stopped)
    with pytest.raises(RuntimeError):
        with policy.prepare_run(): pass


@pytest.mark.parametrize('change', ['profile', 'provider', 'account', 'generation', 'control', 'expired-run'])
def test_run_binding_sources_are_not_self_asserted(sources, change):
    root, policy, api, port = sources
    policy.migrate_stopped(backup_name='backup.sqlite3')
    request = run_request(root)
    if change == 'account': request['account_ref'] = 'other'
    elif change == 'generation': request['generation'] = 2
    elif change == 'control': request['control_revision'] = 99
    elif change == 'expired-run': request['deadline_ns'] = 100_000_000_000
    approve(root, port, purpose='RUN_V3', run=request)
    if change in ('profile', 'provider'):
        (root / f'private/deployment/{change}.json').write_bytes(b'changed')
    with pytest.raises(RuntimeError):
        with policy.prepare_run(): pass


def test_readonly_status_does_not_initialize_or_recover(tmp_path):
    if not AVAILABLE: pytest.skip('new ingress not implemented yet')
    with pytest.raises(RuntimeError): ingress.read_policy_status(tmp_path)
    assert not (tmp_path / 'var').exists()


def test_approval_changed_during_source_collection_denies(sources, monkeypatch):
    root, policy, api, port = sources
    def changed():
        approve(root, port, approval_id='4' * 32)
        return (0,) * 256
    monkeypatch.setattr(ingress, '_read_input_state', changed)
    with pytest.raises(RuntimeError, match='CHANGED'):
        policy.migrate_stopped(backup_name='backup.sqlite3')
    assert not (root / 'var/backup.sqlite3').exists()


def test_manifest_changed_during_source_collection_denies(sources, monkeypatch):
    root, policy, api, port = sources
    def changed():
        (root / 'private/deployment/runner-manifest.json').write_bytes(b'changed')
        return (0,) * 256
    monkeypatch.setattr(ingress, '_read_input_state', changed)
    with pytest.raises(RuntimeError, match='RUNNER_CHANGED'):
        policy.migrate_stopped(backup_name='backup.sqlite3')
    assert not (root / 'var/backup.sqlite3').exists()


def test_migration_preserves_unresolved_legacy_barrier(sources):
    root, policy, api, port = sources
    from clash_rush_rebuild.mvp_session_authority import ControlMode
    store = DurableSessionAuthority(root / 'var/mvp-session.sqlite3')
    store.run(purpose='MVP_ACCOUNT_READINESS', candidate_tree='a' * 40,
        ready_bytes=port.payload, deadline=200, now=100, nonce='5' * 32)
    store.command(ControlMode.STOPPED, command_id='stop')
    store.close()
    approve(root, port)
    policy.migrate_stopped(backup_name='backup.sqlite3')
    approve(root, port, purpose='RUN_V3', run=run_request(root))
    with pytest.raises(RuntimeError):
        with policy.prepare_run(): pass
    status = ingress.read_policy_status(root)
    assert status.legacy_generations == (1,)


@pytest.mark.skipif(os.name != 'nt', reason='Windows disposable filesystem proof')
def test_native_private_reader_verifies_existing_acl_without_resealing(tmp_path):
    if not AVAILABLE: pytest.skip('new ingress not implemented yet')
    from clash_rush_rebuild.approval_reconciliation import _seal_private_path
    private = tmp_path / 'protected'
    private.mkdir()
    _seal_private_path(private, True)
    artifact = private / 'artifact.json'
    artifact.write_bytes(b'private synthetic bytes')
    # SQLite backup/sidecars inherit this exact private parent policy.
    before = ingress._read_path_security(artifact)
    ingress._require_private(private)
    ingress._require_private(artifact)
    assert ingress._read_path_security(artifact) == before
    assert artifact.read_bytes() == b'private synthetic bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows disposable filesystem proof')
def test_migration_with_real_protected_filesystem_leaves(sources, monkeypatch):
    root, policy, api, port = sources
    from clash_rush_rebuild.approval_reconciliation import _seal_private_path
    from clash_rush_rebuild.win32_state_io import NativeWin32StateApi, Win32StateFilePort
    for directory in (root / 'var', root / 'var/approvals'):
        _seal_private_path(directory, True)
    for filename in ('mvp-session.sqlite3', 'lifecycle-state.json', 'approvals/policy-v3.json'):
        _seal_private_path(root / 'var' / filename, False)
    monkeypatch.setattr(ingress, '_read_path_security', NATIVE_SECURITY_READER)
    policy._state_port = Win32StateFilePort(root, NativeWin32StateApi())
    policy.migrate_stopped(backup_name='native-backup.sqlite3')
    assert ingress.read_policy_status(root).generations == ()
    ingress._require_private(root / 'var/native-backup.sqlite3')


def test_consumed_legacy_migration_is_denied_before_backup(sources):
    root, policy, api, port = sources
    from clash_rush_rebuild.mvp_session_authority import ControlMode
    store = DurableSessionAuthority(root / 'var/mvp-session.sqlite3')
    grant = store.run(purpose='MVP_ACCOUNT_READINESS', candidate_tree='a' * 40,
        ready_bytes=port.payload, deadline=200, now=100, nonce='5' * 32)
    store.admit(run_nonce=grant.run_nonce, transaction_ref='tx', purpose='MVP_ACCOUNT_READINESS',
        candidate_tree='a' * 40, ready_bytes=port.payload, configuration_revision=1,
        control_revision=grant.control_revision, now=101)
    store.command(ControlMode.STOPPED, command_id='stop')
    store.close()
    approve(root, port)
    with pytest.raises(RuntimeError, match='LEGACY_REQUIRES_RECOVERY'):
        policy.migrate_stopped(backup_name='backup.sqlite3')
    assert not (root / 'var/backup.sqlite3').exists()


def test_backup_failure_does_not_migrate_or_destroy_approval(sources, monkeypatch):
    root, policy, api, port = sources
    approval = (root / 'var/approvals/policy-v3.json').read_bytes()
    original = Path.open
    def fail_backup(path, mode='r', *args, **kwargs):
        if path.name == 'backup.sqlite3' and mode == 'xb':
            raise OSError('synthetic exclusive backup create failure')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail_backup)
    with pytest.raises(OSError):
        policy.migrate_stopped(backup_name='backup.sqlite3')
    with sqlite3.connect(root / 'var/mvp-session.sqlite3') as check:
        assert check.execute('PRAGMA user_version').fetchone() == (1,)
    assert (root / 'var/approvals/policy-v3.json').read_bytes() == approval
