"""Explicit stopped instance Setup and scoped approval issuance; never launches.

Reuses the registry, authority, protected mutex and exclusive protected writer.
A partial Setup remains blocked, never auto-repaired into a runnable installation.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import re
import secrets
import time

from . import mvp_policy_ingress as ingress
from .approval_reconciliation import _seal_private_path, _replace_write_through
from .lifecycle_state import LifecycleStateStore, Ready, encode_state
from .mvp_calibration_native import write_protected_record
from .mvp_deployment_profile import _no_link, read_private
from .mvp_installed_build import CONFIG
from .mvp_runner_manifest import runner_manifest, verify_runner_manifest
from .mvp_session_authority import DurableSessionAuthority, RunBindingV3, canonical_v3, digest_v3
from .registry import load_instance_slots
from .win32_runtime import NativeWin32Api, Win32Runtime, DEFAULT_MUTEX_NAME
from .win32_state_io import Win32StateFilePort, NativeWin32StateApi

need = ingress._require


@contextmanager
def _locked(policy):
    api = policy._runtime_api if policy._runtime_api is not None else NativeWin32Api()
    mutex = Win32Runtime(api).acquire_mutex()
    try:
        mutex.require_usable()
        need(mutex.abandoned is False and mutex.name == DEFAULT_MUTEX_NAME
             and not mutex._released and not mutex._closed, 'MUTEX_UNUSABLE')
        yield mutex
    finally:
        mutex.release()


def _state(policy):
    port = policy._state_port
    if port is None:
        port = Win32StateFilePort(policy.root, NativeWin32StateApi())
    return LifecycleStateStore(policy.root, port)


def _clear():
    keys = ingress._read_input_state()
    need(type(keys) is tuple and len(keys) == 256
         and all(type(k) is int and k == 0 for k in keys), 'INPUT_NOT_CLEAR')


def _directory(path):
    if not path.exists():
        path.mkdir()
    _no_link(path)
    need(path.is_dir(), 'PRIVATE_PATH_TYPE')
    _seal_private_path(path, True)
    ingress._require_private(path)


def bootstrap(policy, *, registry, team_ref, source_tree, approve=False):
    """New installation only. Explicit operator-selected registry, no identities minted."""
    need(approve is True, 'EXPLICIT_SETUP_APPROVAL_REQUIRED')
    need(type(team_ref) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',team_ref)
         and type(source_tree) is str and re.fullmatch('[0-9a-f]{40}',source_tree), 'SETUP_ARGUMENTS')
    # Missing/invalid supplied configuration fails before touching native leaves.
    source = Path(registry).absolute()
    for path in (source, *source.parents): _no_link(path)
    with source.open('rb') as stream: registration = stream.read(8193)
    need(len(registration) <= 8192, 'REGISTRY_SIZE')
    raw = json.loads(registration)
    need(canonical_v3(raw) == registration, 'REGISTRY_CANONICAL')
    configuration = CONFIG.read_bytes()
    slots = load_instance_slots(configuration.decode('utf-8'),raw)
    manifest = runner_manifest(Path(__file__).parent, source_tree)
    verify_runner_manifest(manifest, approved_sha256=digest_v3(manifest),
                           approved_tree=source_tree, package_root=Path(__file__).parent)
    with _locked(policy) as mutex:
        targets = ('private/slots.json','private/deployment/runner-manifest.json',
            'var/mvp-session.sqlite3','var/mvp-session.sqlite3-wal','var/mvp-session.sqlite3-shm',
            'var/mvp-session.sqlite3-journal','var/lifecycle-state.json',
            'var/.lifecycle-state.transition','var/.policy-setup.pending',
            'var/approvals/policy-v3.json')
        for relative in targets:
            path = policy.root / relative
            need(not path.exists() and not path.is_symlink(), 'FRESH_SETUP_ONLY')
        policy._absence(); _clear()
        for relative in ('private','private/deployment','var','var/approvals'):
            _directory(policy.root / relative)
        # Durable incomplete marker gates every subsequent policy ingress. No
        # rollback deletes possibly acknowledged state or an existing identity.
        write_protected_record(policy.root,'var/.policy-setup.pending',canonical_v3({
            'schema':1,'purpose':'INSTANCE_SETUP','slots_digest':digest_v3(registration),
            'source_tree':source_tree,'runner_manifest_digest':digest_v3(manifest)}))
        need(CONFIG.read_bytes() == configuration and source.read_bytes() == registration,'SETUP_SOURCE_CHANGED')
        policy._absence(); _clear(); mutex.require_usable()
        write_protected_record(policy.root,'private/slots.json',registration)
        write_protected_record(policy.root,'private/deployment/runner-manifest.json',manifest)
        authority = DurableSessionAuthority(policy.path)
        try:
            authority.setup_instance_v3(team_ref=team_ref,slot_id=slots[0].slot_id,instance_ref='slot-0')
        finally:
            authority.close()
        ingress._require_private(policy.path)
        check = DurableSessionAuthority(policy.path,read_only=True)
        try:
            need(check.status_v3().generations == (), 'SETUP_READBACK')
            row = check._open().execute('SELECT * FROM configuration').fetchone()
            need(row['account_ref'] == slots[0].slot_id and row['instance_ref'] == 'slot-0'
                 and row['team_ref'] == team_ref and row['player_tag_sha256'] is None,'SETUP_READBACK')
        finally: check.close()
        policy._absence(); _clear(); mutex.require_usable()
        state = _state(policy)
        state.initialize_ready()
        need(state.load_with_bytes() == (Ready(0),encode_state(Ready(0))), 'SETUP_READBACK')
        pending = policy.root/'var/.policy-setup.pending'
        ingress._require_private(pending)
        pending.unlink()
        need(not pending.exists(), 'SETUP_INCOMPLETE')


def issue_approval(policy, *, purpose, source_tree, lifetime_seconds=300, approve=False):
    """Explicit local operator action. Never renews/approves from a Run command."""
    need(approve is True, 'EXPLICIT_APPROVAL_REQUIRED')
    need(type(purpose) is str and purpose in ('MIGRATE_SCHEMA3','CALIBRATION_READ_ONLY'), 'ISSUER_PURPOSE')
    need(type(source_tree) is str and re.fullmatch('[0-9a-f]{40}',source_tree)
         and type(lifetime_seconds) is int and 1 <= lifetime_seconds <= 300,'ISSUER_ARGUMENTS')
    # No native construction at all on an unprovisioned installation.
    policy._paths()
    with _locked(policy) as mutex:
        policy._paths()
        state = _state(policy)
        ready, ready_bytes = state.load_with_bytes()
        need(type(ready) is Ready and encode_state(ready) == ready_bytes, 'READY_REQUIRED')
        target = policy.root/'var/approvals/policy-v3.json'
        previous = None
        if target.exists() or target.is_symlink():
            previous = policy._approval_bytes()
            old = json.loads(previous)
            need(type(old) is dict and canonical_v3(old) == previous
                 and type(old.get('expires_at')) is int and old['expires_at'] <= time.time(),
                 'APPROVAL_STILL_CURRENT')
        manifest_path = policy.root/'private/deployment/runner-manifest.json'
        ingress._require_private(manifest_path)
        manifest = read_private(policy.root,'private/deployment/runner-manifest.json',2_000_000)
        verify_runner_manifest(manifest,approved_sha256=digest_v3(manifest),
                               approved_tree=source_tree,package_root=Path(__file__).parent)
        authority = DurableSessionAuthority(policy.path,read_only=True)
        try:
            if purpose != 'MIGRATE_SCHEMA3':
                from .mvp_canonical_runtime import open_canonical
                open_canonical(policy.root).stopped(authority, state)
            database = ingress._database_digest(authority)
            absence = policy._absence()
            clear = policy._input_clear(authority,purpose)
            now = int(time.time())
            raw = dict(schema=3,purpose=purpose,approval_id=secrets.token_hex(16),
                source_tree=source_tree,issued_at=now,expires_at=now+lifetime_seconds,
                authority_digest=database,lifecycle_digest=digest_v3(ready_bytes),
                runner_manifest_digest=digest_v3(manifest),profile_digest=None,
                provider_digest=None,run=None)
            calibration = None
            if purpose == 'CALIBRATION_READ_ONLY':
                from .mvp_calibration_native import CalibrationConfiguration
                calibration = CalibrationConfiguration(policy.root)
                scope = calibration.scope
                status = authority.status_v3()
                config = authority._open().execute('SELECT * FROM configuration').fetchone()
                revision = authority._open().execute('SELECT MAX(revision) FROM (SELECT revision FROM control UNION ALL SELECT control_revision AS revision FROM run_v3)').fetchone()[0]
                need(config is not None and config['instance_ref'] == f'slot-{ready.next_slot}'
                     and scope.slot == ready.next_slot and scope.candidate_tree == source_tree
                     and scope.generation == max(status.generations,default=0)+1
                     and scope.issued_ns <= time.monotonic_ns() < scope.expires_ns, 'CALIBRATION_STATE_BINDING')
                request = dict(generation=scope.generation,run_nonce=scope.run_nonce,
                    transaction_ref=secrets.token_hex(16),configuration_revision=config['revision'],
                    control_revision=revision+1,source_tree=source_tree,runner_manifest_digest=digest_v3(manifest),
                    profile_digest=scope.profile_digest,provider_digest=scope.digest,
                    account_ref=config['account_ref'],binding_digest=scope.digest,
                    clock_domain='WINDOWS_MONOTONIC_NS',started_ns=scope.issued_ns,
                    deadline_ns=scope.expires_ns,revocation_epoch=status.revocation_epoch)
                raw.update(profile_digest=scope.profile_digest,provider_digest=scope.digest,run=request)
            payload = canonical_v3(raw)
            if calibration is not None:
                run = RunBindingV3(**raw['run'],approval_digest=digest_v3(payload))
                calibration.scope.require_run(run)
                current = CalibrationConfiguration(policy.root)
                need(current.scope == calibration.scope and current.spec == calibration.spec
                     and current.witness == calibration.witness and current.slot == calibration.slot,
                     'CALIBRATION_SOURCE_CHANGED')
            policy._paths(); mutex.require_usable()
            need(state.load_with_bytes() == (ready,ready_bytes)
                 and ingress._database_digest(authority) == database
                 and policy._absence() == absence
                 and policy._input_clear(authority,purpose) == clear,'ISSUER_SOURCE_CHANGED')
            need(read_private(policy.root,'private/deployment/runner-manifest.json',2_000_000) == manifest,
                 'RUNNER_CHANGED')
            verify_runner_manifest(manifest,approved_sha256=digest_v3(manifest),
                                   approved_tree=source_tree,package_root=Path(__file__).parent)
            ingress.PolicyIngress._fresh(raw)
            need((policy._approval_bytes() if target.exists() else None) == previous,'APPROVAL_CHANGED')
            # Preserve prior bytes, publish a sealed unique temporary via the
            # existing write-through replace, then independently parse/read back.
            if previous is not None:
                archive = 'var/approvals/'+secrets.token_hex(16)+'.json'
                write_protected_record(policy.root,archive,previous)
            temporary = 'var/approvals/'+secrets.token_hex(16)+'.json'
            write_protected_record(policy.root,temporary,payload)
            mutex.require_usable()
            _replace_write_through(policy.root/temporary,target)
            need(policy._approval(purpose) == (raw,payload),'APPROVAL_READBACK')
        finally:
            authority.close()

