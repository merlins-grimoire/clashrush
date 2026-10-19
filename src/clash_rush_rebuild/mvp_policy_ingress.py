"""Explicit stopped schema-3 ingress; scoped issuer lives in mvp_policy_setup.

No gameplay activation or exceptional player-recovery authority is issued here.

Approval bytes live in the existing protected ``var/approvals`` namespace. This
reader does NOT seal an untrusted file and then call it approved. Its read-only
DACL predicate matches approval_reconciliation's operator/SYSTEM sealing policy.
The source predicate is exercised with synthetic low-level leaves in tests; no
synthetic result establishes native input-clear or runtime approval.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from .approval_reconciliation import _native_current_operator_sid
from .lifecycle_state import LifecycleStateStore, Ready, encode_state
from .mvp_deployment_profile import _no_link, _read_windows, read_private
from .mvp_runner_manifest import verify_runner_manifest
from .mvp_session_authority import (
    DurableSessionAuthority, RunBindingV3, StoppedProofV3, canonical_v3, digest_v3,
)
from .win32_primitives import enumerate_processes
from .win32_runtime import DEFAULT_MUTEX_NAME, NativeWin32Api, ProtectedMutexLease, Win32Runtime
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort


class PolicyIngressError(RuntimeError):
    pass


def _require(condition, reason):
    if not condition:
        raise PolicyIngressError('POLICY_' + reason)


def _read_path_security(path):
    """Read only; use the same SID/API primitives as protected approval storage."""
    _require(os.name == 'nt', 'WINDOWS_SECURITY_REQUIRED')
    api = NativeWin32Api()
    files = NativeWin32StateApi()
    handle = files.create_file(str(path), 0x00020000, 1, 3, 0x02200000)
    _require(handle not in (None, 0), 'PRIVATE_PATH_UNAVAILABLE')
    descriptor = ctypes.c_void_p()
    try:
        _require(not files.handle_attributes(handle) & 0x400
                 and Path(files.final_path(handle)) == path, 'PATH_REDIRECTED')
        # Existing API signatures; object type 1 is FILE, not a kernel mutex.
        result = api._advapi32.GetSecurityInfo(handle, 1, 4, None, None, None, None,
                                              ctypes.byref(descriptor))
        _require(result == 0, 'PRIVATE_DACL_UNAVAILABLE')
        text = ctypes.c_void_p()
        try:
            ok = api._advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 4, ctypes.byref(text), None)
            _require(bool(ok), 'PRIVATE_DACL_UNAVAILABLE')
            return _native_current_operator_sid(), ctypes.wstring_at(text)
        finally:
            if text.value:
                api._kernel32.LocalFree(text)
    finally:
        if descriptor.value:
            api._kernel32.LocalFree(descriptor)
        _require(files.close_handle(handle) is True, 'PRIVATE_PATH_CLOSE')


def _require_private(path):
    info = _no_link(path)
    _require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'PRIVATE_PATH_TYPE')
    _require(stat.S_ISDIR(info.st_mode) or info.st_nlink == 1, 'PRIVATE_PATH_LINKS')
    sid, sddl = _read_path_security(path)
    _require(type(sid) is str and re.fullmatch(r'S-\d+(?:-\d+)+', sid) is not None
             and type(sddl) is str, 'PRIVATE_DACL_UNAVAILABLE')
    expected = [f'(A;;FA;;;{principal})' for principal in ('SY', sid)]
    if stat.S_ISDIR(info.st_mode):
        expected += [f'(A;OICIIO;GA;;;{principal})' for principal in ('SY', sid)]
    actual = re.findall(r'\([^)]*\)', sddl)
    prefix = sddl.split('(', 1)[0]
    if stat.S_ISREG(info.st_mode) and prefix == 'D:AI':
        # SQLite-created backups/sidecars inherit, rather than seal, their ACL.
        # Admit only exact inherited ACEs from the same protected private parent.
        _require_private(path.parent)
        expected = [f'(A;ID;FA;;;{principal})' for principal in ('SY', sid)]
    _require(prefix in (('D:P', 'D:PAI', 'D:AI') if stat.S_ISREG(info.st_mode) else ('D:P', 'D:PAI'))
             and sorted(actual) == sorted(expected)
             and sddl == prefix + ''.join(actual), 'PRIVATE_DACL_MISMATCH')


def _unavailable_input_state():
    # GetAsyncKeyState zero alone cannot prove an accessible/current desktop,
    # monitor continuity, or absence of an owned input. Do not turn it into proof.
    raise PolicyIngressError('POLICY_INPUT_SOURCE_UNAVAILABLE')


def _read_input_state():
    """Read-only stopped preflight, not reconstruction of a past owned DOWN.

    Durable history and complete worker absence are checked separately. Require
    the active input desktop before/after both samples; unknown desktop means
    denial even if Windows returned zero for every key.
    """
    from .win32_lifecycle_host import NativeLifecycleApi
    api = NativeLifecycleApi()
    identity = api.input_desktop_identity()
    read = api._user32.GetAsyncKeyState
    read.argtypes, read.restype = [ctypes.c_int], ctypes.c_short
    first = tuple(int(read(key)) for key in range(256))
    _require(api.input_desktop_identity() == identity, 'INPUT_DESKTOP_CHANGED')
    second = tuple(int(read(key)) for key in range(256))
    _require(api.input_desktop_identity() == identity, 'INPUT_DESKTOP_CHANGED')
    # Both high-bit held and low-bit recently pressed deny stopped admission.
    _require(first == second == (0,) * 256, 'INPUT_NOT_CLEAR')
    return second


def require_runtime_activation():
    # No owner-approved native/runtime capability issuer in this offline lane.
    raise PolicyIngressError('POLICY_RUNTIME_ACTIVATION_DENIED')


def _database_digest(authority):
    connection = authority._open()
    connection.execute('BEGIN')
    try:
        return digest_v3('\n'.join(connection.iterdump()).encode())
    finally:
        connection.execute('ROLLBACK')


def _existing_root(project):
    root = Path(project).absolute()
    _require(root.resolve(strict=True) == root and root.is_dir(), 'PROJECT_PATH')
    for path in (root, *root.parents):
        _no_link(path)
    return root


def read_policy_status(project_root):
    """No missing DB creation, migration, epoch repair, or legacy unblocking."""
    try:
        root = _existing_root(project_root)
        _no_link(root / 'var')
        _no_link(root / 'var/mvp-session.sqlite3')
        authority = DurableSessionAuthority(root / 'var/mvp-session.sqlite3', read_only=True)
        try:
            return authority.status_v3()
        finally:
            authority.close()
    except OSError as exc:
        raise PolicyIngressError('POLICY_STATE_UNAVAILABLE') from exc


def read_runtime_dimensions(project_root):
    """Independent read-only observations, never admission or recovery authority.

    No generation is not proof of stoppedness. A positive lifecycle label needs
    the same current READY/SQLite/host join as stopped ingress. Failures in one
    source do not hide separately observed semantic or host truth.
    """
    result = {'outcome':'UNPROVED', 'reason':'UNPROVED', 'lifecycle':'UNPROVED', 'host':'UNPROVED'}
    unavailable = (RuntimeError, OSError, ValueError, KeyError, TypeError, sqlite3.Error)
    try:
        root = _existing_root(project_root)
    except unavailable:
        return result
    authority = None
    lifecycle = canonical = record = None
    try:
        try:
            _require_private(root / 'var/mvp-session.sqlite3')
            authority = DurableSessionAuthority(root / 'var/mvp-session.sqlite3', read_only=True)
            status = authority.status_v3()
            if status.current_generation:
                outcome = authority.outcome_v3(status.current_generation)
                if outcome is not None:
                    result.update(outcome=outcome.outcome, reason=outcome.reason)
                else:
                    result.update(outcome='PENDING', reason='NONE')
            elif not status.generations:
                result.update(outcome='NONE', reason='NONE')
        except unavailable:
            pass
        try:
            # The existing store constructor may create var. Check it exists
            # first so Status can never initialize a missing runtime directory.
            _no_link(root / 'var')
            lifecycle = LifecycleStateStore(root, Win32StateFilePort(root, NativeWin32StateApi())).load_with_bytes()
            result['lifecycle'] = 'UNRESOLVED'
        except unavailable:
            pass
        try:
            from .mvp_canonical_runtime import open_canonical
            canonical = open_canonical(root)
            record = canonical.anchor.load()
            result['host'] = record['phase']
        except unavailable:
            pass
        if authority is not None and lifecycle is not None and record is not None:
            try:
                canonical.reconcile_stopped(authority, lifecycle, record)
                result['lifecycle'] = 'STOPPED' if record['phase'] == 'STOPPED' else 'LIFECYCLE_VERIFIED'
            except unavailable:
                pass
    finally:
        if authority is not None:
            authority.close()
    return result


@dataclass(frozen=True, slots=True)
class PreparedRunV3:
    """Valid only inside prepare_run's held-mutex context; not a native permit."""
    authority: DurableSessionAuthority
    run: RunBindingV3
    stopped: StoppedProofV3
    mutex: ProtectedMutexLease
    slot: int
    writer_epoch: int
    runner_manifest_bytes: bytes
    calibration_scope: object = None

    def require_runtime_activation(self):
        require_runtime_activation()


class PolicyIngress:
    """Only dependencies are low-level OS/file leaves, never approval booleans.

    ``migrate_stopped`` is an explicit administrative action. ``prepare_run``
    never migrates, reserves, creates a process, or consumes gameplay admission.
    Both own/release the existing protected lifecycle mutex on the calling thread.
    """
    def __init__(self, project_root, *, runtime_api=None, process_api=None, state_port=None):
        self.root = _existing_root(project_root)
        self._runtime_api = runtime_api
        self._process_api = process_api
        self._state_port = state_port
        self.path = self.root / 'var/mvp-session.sqlite3'

    def _paths(self):
        pending = self.root / 'var/.policy-setup.pending'
        _require(not pending.exists() and not pending.is_symlink(), 'SETUP_INCOMPLETE')
        for path in (self.root / 'var', self.root / 'var/approvals', self.path,
                     self.root / 'var/lifecycle-state.json'):
            _require_private(path)
        # SQLite sidecars must inherit the protected directory, not redirect IO.
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _require_private(sidecar)

    def _approval_bytes(self):
        path = self.root / 'var/approvals/policy-v3.json'
        _require_private(path)
        # Reuse the existing no-reparse, ancestor-handle-retaining private reader.
        _require(os.name == 'nt', 'WINDOWS_SECURITY_REQUIRED')
        payload = _read_windows(self.root, ('var', 'approvals', 'policy-v3.json'), 8192)
        _require_private(path)
        return payload

    def _approval(self, purpose):
        payload = self._approval_bytes()
        try:
            raw = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise PolicyIngressError('POLICY_APPROVAL_MALFORMED') from exc
        expected = {'schema', 'purpose', 'approval_id', 'source_tree', 'issued_at',
                    'expires_at', 'authority_digest', 'lifecycle_digest',
                    'runner_manifest_digest', 'profile_digest', 'provider_digest', 'run'}
        _require(type(raw) is dict and set(raw) == expected
                 and canonical_v3(raw) == payload and type(raw['schema']) is int
                 and raw['schema'] == 3 and raw['purpose'] == purpose, 'APPROVAL_MALFORMED')
        for name, length in (('approval_id', 32), ('source_tree', 40),
                             ('authority_digest', 64), ('lifecycle_digest', 64),
                             ('runner_manifest_digest', 64)):
            _require(type(raw[name]) is str and re.fullmatch(f'[0-9a-f]{{{length}}}', raw[name])
                     is not None, 'APPROVAL_MALFORMED')
        self._fresh(raw)
        if purpose == 'MIGRATE_SCHEMA3':
            _require(all(raw[key] is None for key in ('run', 'profile_digest', 'provider_digest')),
                     'MIGRATION_SCOPE')
        else:
            for key in ('profile_digest', 'provider_digest'):
                _require(type(raw[key]) is str and re.fullmatch('[0-9a-f]{64}', raw[key]) is not None,
                         'APPROVAL_MALFORMED')
        return raw, payload

    @staticmethod
    def _fresh(raw):
        now = time.time()
        _require(type(now) in (int, float) and type(raw['issued_at']) is int
                 and type(raw['expires_at']) is int
                 and 0 <= raw['issued_at'] <= now < raw['expires_at']
                 and 0 < raw['expires_at'] - raw['issued_at'] <= 300, 'APPROVAL_EXPIRED')

    def _absence(self):
        api = self._process_api
        if api is None:
            from .win32_lifecycle_host import NativeProcessApi
            api = NativeProcessApi()
        entries = enumerate_processes(api)
        _require(len({entry.pid for entry in entries}) == len(entries)
                 and sum(entry.pid == os.getpid() for entry in entries) == 1, 'PROCESS_SNAPSHOT')
        players = tuple(entry for entry in entries if entry.image_name.casefold() == 'hd-player.exe')
        _require(not players, 'PLAYER_PRESENT')
        from .mvp_canonical_runtime import open_canonical
        try:
            canonical = open_canonical(self.root)
            record = canonical.anchor.load()
            _require(record['phase'] in ('STOPPED', 'CLOSED'), 'HOST_OCCUPIED')
            # STOPPED has no current-worker exemption. A referenced surviving
            # or reused PID is unresolved, never targeted for termination.
            referenced = {record[k]['pid'] for k in ('worker','player') if record[k] is not None}
            _require(not referenced.intersection(entry.pid for entry in entries), 'WORKER_RESIDUE')
        except (RuntimeError, OSError):
            raise PolicyIngressError('POLICY_WORKER_AMBIGUOUS_OR_PRESENT') from None
        return digest_v3(canonical_v3({'role': 'PLAYER', 'complete': True, 'identities': []})), \
            digest_v3(canonical_v3({'role': 'WORKER', 'complete': True, 'identities': []}))

    @staticmethod
    def _input_clear(authority, purpose):
        connection = authority._open()
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if purpose == 'MIGRATE_SCHEMA3':
            _require(version in (1, 2), 'MIGRATION_VERSION')
            control = connection.execute('SELECT mode FROM control').fetchall()
            _require(len(control) == 1 and control[0][0] == 'STOPPED', 'STOPPED_REQUIRED')
            _require(not connection.execute('SELECT 1 FROM transactions').fetchone()
                     and not connection.execute('SELECT 1 FROM runs WHERE admission_consumed=1').fetchone(),
                     'LEGACY_REQUIRES_RECOVERY')
        else:
            status = authority.status_v3()
            _require(not status.legacy_generations, 'LEGACY_REQUIRES_RECOVERY')
            for generation in status.generations:
                authority._require_closed_v3(generation)
        keys = _read_input_state()
        _require(type(keys) is tuple and len(keys) == 256
                 and all(type(value) is int and value == 0 for value in keys), 'INPUT_NOT_CLEAR')
        return digest_v3(canonical_v3({'keys': keys, 'durable_unresolved': False, 'schema': version}))

    @contextmanager
    def _stopped(self, purpose):
        api = self._runtime_api if self._runtime_api is not None else NativeWin32Api()
        mutex = Win32Runtime(api).acquire_mutex()
        authority = None
        owner = threading.get_ident()
        try:
            mutex.require_usable()
            _require(mutex.abandoned is False and mutex.name == DEFAULT_MUTEX_NAME
                     and not mutex._released and not mutex._closed, 'MUTEX_UNUSABLE')
            self._paths()
            port = self._state_port
            if port is None:
                port = Win32StateFilePort(self.root, NativeWin32StateApi())
            state = LifecycleStateStore(self.root, port)
            ready, ready_bytes = state.load_with_bytes()  # includes transition guard
            _require(type(ready) is Ready and encode_state(ready) == ready_bytes, 'READY_REQUIRED')
            raw, payload = self._approval(purpose)
            _require(digest_v3(ready_bytes) == raw['lifecycle_digest'], 'READY_BINDING')
            authority = DurableSessionAuthority(self.path, read_only=True)
            if purpose != 'MIGRATE_SCHEMA3':
                from .mvp_canonical_runtime import open_canonical
                open_canonical(self.root).stopped(authority, state)
            database_digest = _database_digest(authority)
            _require(database_digest == raw['authority_digest'], 'AUTHORITY_CHANGED')
            manifest = read_private(self.root, 'private/deployment/runner-manifest.json', 2_000_000)
            membership = verify_runner_manifest(manifest, approved_sha256=raw['runner_manifest_digest'],
                approved_tree=raw['source_tree'], package_root=Path(__file__).parent)
            player, worker = self._absence()
            input_clear = self._input_clear(authority, purpose)
            proof = StoppedProofV3(
                digest_v3(canonical_v3({'mutex': mutex.name, 'pid': os.getpid(), 'thread': owner})),
                digest_v3(ready_bytes), digest_v3(canonical_v3({'transition': 'ABSENT', 'ready': digest_v3(ready_bytes)})),
                worker, player, input_clear, membership.manifest_sha256, digest_v3(payload))
            # Recheck all fallible mutable sources before exposing authority.
            self._paths()
            _require(state.load_with_bytes() == (ready, ready_bytes), 'READY_CHANGED')
            _require(self._approval_bytes() == payload, 'APPROVAL_CHANGED')
            self._fresh(raw)
            _require(_database_digest(authority) == database_digest, 'AUTHORITY_CHANGED')
            _require(self._absence() == (player, worker), 'PROCESS_CHANGED')
            _require(self._input_clear(authority, purpose) == input_clear, 'INPUT_CHANGED')
            _require(read_private(self.root, 'private/deployment/runner-manifest.json', 2_000_000)
                     == manifest, 'RUNNER_CHANGED')
            verify_runner_manifest(manifest, approved_sha256=raw['runner_manifest_digest'],
                approved_tree=raw['source_tree'], package_root=Path(__file__).parent)
            _require(state.load_with_bytes() == (ready, ready_bytes), 'READY_CHANGED')
            _require(self._approval_bytes() == payload, 'APPROVAL_CHANGED')
            self._fresh(raw)
            _require(threading.get_ident() == owner, 'OWNER_CHANGED')
            mutex.require_usable()
            yield authority, state, ready, raw, proof, mutex, manifest
        finally:
            if authority is not None:
                authority.close()
            mutex.release()

    def migrate_stopped(self, *, backup_name):
        """Explicit only; backup/DDL atomicity is owned by the existing authority."""
        _require(type(backup_name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', backup_name)
                 is not None and backup_name not in ('.', '..'), 'BACKUP_PATH')
        backup = self.path.parent / backup_name
        _require(not backup.exists() and not backup.is_symlink(), 'BACKUP_PATH')
        with self._stopped('MIGRATE_SCHEMA3') as (reader, state, ready, raw, proof, mutex, manifest):
            writer = DurableSessionAuthority(self.path)
            try:
                _require(_database_digest(writer) == raw['authority_digest'], 'AUTHORITY_CHANGED')
                self._fresh(raw)
                mutex.require_usable()
                writer._migrate_stopped_v3(proof=proof, backup_path=backup)
                _require_private(backup)
                writer.status_v3()
                # Exact independent readback, not just a successful write call.
                check = DurableSessionAuthority(self.path, read_only=True)
                try:
                    row = check._open().execute('SELECT proof FROM migration_v3').fetchone()
                    _require(row is not None and row[0] == canonical_v3(asdict(proof)), 'MIGRATION_READBACK')
                finally:
                    check.close()
                return proof
            finally:
                writer.close()

    @contextmanager
    def prepare_run(self, *, purpose='RUN_V3'):
        """Yield verified Run/proof and writable authority under the same mutex.

        Parent must call require_runtime_activation BEFORE any native construction.
        The caller may reserve in disposable offline tests, but no native permit
        is supplied here. On context exit authority/mutex are closed/released.
        """
        _require(type(purpose) is str and purpose in ('RUN_V3', 'CALIBRATION_READ_ONLY'), 'RUN_PURPOSE')
        with self._stopped(purpose) as (reader, state, ready, raw, proof, mutex, manifest):
            request = raw['run']
            expected = {field.name for field in fields(RunBindingV3)} - {'approval_digest'}
            _require(type(request) is dict and set(request) == expected, 'RUN_ENVELOPE')
            run = RunBindingV3(**request, approval_digest=proof.owner_authorization_digest)
            scope = None
            if purpose == 'CALIBRATION_READ_ONLY':
                from .mvp_calibration import CalibrationScope
                scope = CalibrationScope.decode(read_private(self.root, 'private/deployment/calibration.json', 8192))
                scope.require_run(run)
                _require(scope.slot == ready.next_slot and scope.digest == raw['provider_digest']
                         and scope.profile_digest == raw['profile_digest'], 'CALIBRATION_SCOPE')
                specification = read_private(self.root, 'private/deployment/calibration-spec.json', 256 * 1024)
                _require(digest_v3(specification) == run.profile_digest, 'CALIBRATION_SPECIFICATION')
                _require_private(self.root / scope.private_root)
            else:
                for key in ('profile_digest', 'provider_digest'):
                    actual = digest_v3(read_private(self.root, f'private/deployment/{key[:-7]}.json', 2_000_000))
                    _require(actual == raw[key] == getattr(run, key), 'RUN_SOURCE_BINDING')
            _require(run.source_tree == raw['source_tree']
                     and run.runner_manifest_digest == raw['runner_manifest_digest'], 'RUN_MEMBERSHIP')
            status = reader.status_v3()
            configuration = reader._open().execute('SELECT * FROM configuration').fetchone()
            control = reader._open().execute('SELECT MAX(revision) FROM (SELECT revision FROM control UNION ALL SELECT control_revision AS revision FROM run_v3)').fetchone()[0]
            _require(configuration is not None and run.configuration_revision == configuration['revision']
                     and run.account_ref == configuration['account_ref']
                     and configuration['instance_ref'] == f'slot-{ready.next_slot}'
                     and run.control_revision == control + 1
                     and run.generation == max(status.generations, default=0) + 1
                     and run.revocation_epoch == status.revocation_epoch, 'RUN_STATE_BINDING')
            _require(run.started_ns <= time.monotonic_ns() < run.deadline_ns, 'RUN_DEADLINE')
            self._fresh(raw)
            _require(self._approval_bytes() == canonical_v3(raw), 'APPROVAL_CHANGED')
            writer = DurableSessionAuthority(self.path)
            try:
                _require(_database_digest(writer) == raw['authority_digest'], 'AUTHORITY_CHANGED')
                self._fresh(raw)
                _require(run.started_ns <= time.monotonic_ns() < run.deadline_ns, 'RUN_DEADLINE')
                mutex.require_usable()
                yield PreparedRunV3(writer, run, proof, mutex, ready.next_slot, status.writer_epoch, manifest, scope)
            finally:
                writer.close()
