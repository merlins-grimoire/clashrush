"""Single canonical installation and write-ahead host occupancy.

No activation issuer, migration-on-read, timeout cleanup or orphan stop exists.
The production locator is machine-wide (KnownFolder ProgramData), never supplied
by the caller, environment, project or worktree. File ports are existing durable
Win32 ports. Offline tests inject only file/OS leaves into the same state machine.
"""
from contextlib import contextmanager
from dataclasses import asdict
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading

from .mvp_session_authority import canonical_v3, digest_v3


class CanonicalRuntimeError(RuntimeError):
    pass


def require(value):
    if value is not True:
        raise CanonicalRuntimeError('CANONICAL_RUNTIME_UNPROVED')


def machine_root():
    """SHGetKnownFolderPath(FOLDERID_ProgramData), no environment fallback."""
    import uuid
    require(os.name == 'nt')
    folder = (ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID('62AB5D82-FDC1-4DC3-A9DD-070D1D495D97').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    value = ctypes.c_void_p()
    try:
        require(shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(value)) == 0)
        return Path(ctypes.wstring_at(value)) / 'ClashRush-native'
    finally:
        if value.value:
            ole.CoTaskMemFree(value)


def plain(path):
    from .mvp_policy_ingress import _existing_root
    path = Path(path).absolute()
    _existing_root(path.parent)
    from .mvp_deployment_profile import _no_link
    info = _no_link(path)
    require(path.resolve(strict=True) == path and info.st_nlink == 1)
    return path


def occupancy(raw):
    """Closed versioned shape, shared by durable and direct reconciliation.

    The offer is historical evidence only. Decoding never opens its handles or
    asks whether its original parent is still alive.
    """
    from .mvp_policy_channel import _identity, _offer
    def hexadecimal(value, length):
        require(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None)
    require(type(raw) is dict and type(raw.get('schema')) is int and raw['schema'] in (1, 2))
    keys = {'schema', 'installation', 'phase', 'run', 'parent', 'worker', 'player', 'closure'}
    require(set(raw) == (keys | {'offer'} if raw['schema'] == 2 else keys))
    hexadecimal(raw['installation'], 64)
    phase = raw['phase']
    require(type(phase) is str and phase in ('STOPPED', 'PREPARING', 'WORKER_PENDING',
            'PLAYER_PENDING', 'PUBLISHED', 'STOP_INTENT', 'CLOSED'))
    if phase == 'STOPPED':
        require(all(raw[k] is None for k in ('run', 'parent', 'worker', 'player', 'closure'))
                and raw.get('offer') is None)
        return raw
    run = raw['run']
    require(type(run) is dict and set(run) == {'generation', 'nonce', 'digest', 'source_tree', 'manifest'}
            and type(run['generation']) is int and 0 < run['generation'] < 2**63)
    for key, length in (('nonce', 32), ('digest', 64), ('source_tree', 40), ('manifest', 64)):
        hexadecimal(run[key], length)
    try:
        _identity(raw['parent'])
        if phase in ('PREPARING', 'WORKER_PENDING'):
            require(raw['worker'] is None and raw['player'] is None)
        else:
            _identity(raw['worker'])
            require(raw['parent']['pid'] != raw['worker']['pid'])
            if phase == 'PLAYER_PENDING':
                require(raw['player'] is None)
            else:
                _identity(raw['player'])
                require(len({raw[k]['pid'] for k in ('parent', 'worker', 'player')}) == 3)
        if raw['schema'] == 2:
            if phase in ('PREPARING', 'WORKER_PENDING', 'PLAYER_PENDING'):
                require(raw['offer'] is None)
            else:
                offer = _offer(raw['offer'])
                require(offer.generation == run['generation'] and offer.run_nonce == run['nonce']
                        and all(raw['offer'][k] == raw[k] for k in ('parent', 'worker', 'player')))
    except (RuntimeError, TypeError, ValueError, KeyError):
        raise CanonicalRuntimeError('CANONICAL_RUNTIME_UNPROVED') from None
    if phase == 'CLOSED':
        hexadecimal(raw['closure'], 64)
    else:
        require(raw['closure'] is None)
    return raw


class HostAnchor:
    """Guarded replace/readback, under the existing protected host mutex.

    The nonterminal record is never removed. Even failed PREPARING publication
    leaves a guard. A terminal record is a tombstone, not a recovered capability.
    """
    def __init__(self, root, port):
        self.root = Path(root)
        self.port = port
        self.path = self.root / 'var/occupancy.json'
        self.guard = self.root / 'var/.occupancy.pending'

    def _read(self, path):
        handle = self.port.open_read_exclusive_no_reparse(path)
        try:
            return self.port.read_bounded(handle, 32768)
        finally:
            self.port.close(handle)

    def load(self):
        try:
            self._read(self.guard)
        except FileNotFoundError:
            pass
        else:
            raise CanonicalRuntimeError('CANONICAL_TRANSITION_PENDING')
        payload = self._read(self.path)
        raw = json.loads(payload)
        require(canonical_v3(raw) == payload)
        return occupancy(raw)

    def _write(self, path, data):
        handle = self.port.create_new_temp_write_through(path)
        try:
            view = memoryview(data)
            while view:
                count = self.port.write(handle, view)
                require(type(count) is int and 0 < count <= len(view))
                view = view[count:]
            self.port.flush(handle)
        finally:
            self.port.close(handle)

    def transition(self, before, after, mutex):
        require(mutex.abandoned is False)
        mutex.require_usable()
        occupancy(before)
        occupancy(after)
        require(self.load() == before)
        self._write(self.guard, b'pending\n')
        temporary = self.path.with_name('.occupancy.' + secrets.token_hex(16) + '.tmp')
        payload = canonical_v3(after)
        self._write(temporary, payload)
        self.port.replace_write_through(temporary, self.path)
        require(self._read(self.path) == payload)
        self.port.delete_file_no_reparse(self.guard)
        require(self.load() == after)


class CanonicalAdmission:
    """Same-parent bootstrap capability, not serializable/recoverable authority."""
    def __init__(self, anchor, installation, *, project, package, executable, coverage):
        self.anchor = anchor
        self.installation = installation
        self.project, self.package, self.executable = str(project), str(package), str(executable)
        self.coverage = coverage
        self.owner = (os.getpid(), threading.get_ident())
        self.record = None
        self.commands = None

    def _owner(self):
        require(self.owner == (os.getpid(), threading.get_ident()))

    def stopped(self, authority, state):
        self._owner()
        record = self.anchor.load()
        self.reconcile_stopped(authority, state.load_with_bytes(), record)
        self.coverage()
        return record

    def reconcile_stopped(self, authority, lifecycle, record):
        """Read-only three-store join; also used by diagnostic Status.

        A terminal host record seals the latest SQLite closure, which seals the
        CURRENT canonical READY bytes. Historical closed rows alone cannot prove
        the current cursor. This returns no admission or cleanup capability.
        """
        from .lifecycle_state import Ready, decode_state, encode_state
        occupancy(record)
        require(record['installation'] == self.installation and record['phase'] in ('STOPPED', 'CLOSED'))
        ready, payload = lifecycle
        require(type(ready) is Ready and type(payload) is bytes
                and decode_state(payload) == ready and encode_state(ready) == payload)
        status = authority.status_v3()
        require(not status.legacy_generations)
        latest = None
        for generation in status.generations:
            validated = authority._require_closed_v3(generation)
            if generation == status.current_generation:
                latest = validated
        if record['phase'] == 'CLOSED':
            require(record['schema'] == 2 and bool(status.generations)
                    and type(status.current_generation) is int
                    and record['run']['generation'] == status.current_generation == max(status.generations))
            require(latest is not None)
            run, closure, retirements = latest
            require(record['run'] == dict(generation=run.generation, nonce=run.run_nonce,
                    digest=run.digest, source_tree=run.source_tree, manifest=run.runner_manifest_digest))
            # Existing authority proof already validates each role's binding,
            # payload/hash, topology and closure. Join its validated identities
            # to the host and the complete ORIGINAL offer preimage.
            require(digest_v3(canonical_v3(record['offer'])) == closure.topology_digest)
            for retirement in retirements:
                require(retirement.identity_digest == digest_v3(canonical_v3(record[retirement.role.lower()])))
            require(closure.closed == 1 and digest_v3(payload) == closure.lifecycle_readback_digest
                    and digest_v3(canonical_v3(asdict(closure))) == record['closure'])
        else:
            require(not status.generations and status.current_generation is None)

    def reserve(self, run, parent, *, authority, state, mutex, commands):
        self._owner()
        require(self.record is None and type(commands) is tuple and len(commands) == 2)
        before = self.stopped(authority, state)
        self.commands = commands
        after = dict(schema=2, installation=self.installation, phase='PREPARING',
            run={'generation': run.generation, 'nonce': run.run_nonce, 'digest': run.digest,
                 'source_tree': run.source_tree, 'manifest': run.runner_manifest_digest},
            parent=asdict(parent), worker=None, player=None, closure=None, offer=None)
        self.anchor.transition(before, after, mutex)
        self.record = after

    def current(self):
        self._owner()
        require(self.record is not None and self.anchor.load() == self.record)
        self.coverage()

    def step(self, phase, mutex, **fields):
        self.current()
        before = self.record
        allowed = {'PREPARING': 'WORKER_PENDING', 'WORKER_PENDING': 'PLAYER_PENDING',
                   'PLAYER_PENDING': 'PUBLISHED', 'PUBLISHED': 'STOP_INTENT', 'STOP_INTENT': 'CLOSED'}
        require(allowed.get(before['phase']) == phase)
        after = dict(before, phase=phase, **fields)
        self.anchor.transition(before, after, mutex)
        self.record = after

    @contextmanager
    def creating(self, role, mutex, topology):
        self.current()
        index = 0 if role == 'WORKER' else 1
        require(role in ('WORKER', 'PLAYER') and topology._parent ==
                __import__('clash_rush_rebuild.lifecycle', fromlist=['ProcessIdentity']).ProcessIdentity(**self.record['parent']))
        self.step('WORKER_PENDING' if index == 0 else 'PLAYER_PENDING', mutex,
                  **({} if index == 0 else {'worker': asdict(topology._worker_identity)}))
        require(getattr(_creation, 'permit', None) is None)
        permit = [self.owner, self.commands[index], False, self]
        _creation.permit = permit
        try:
            yield
        finally:
            _creation.permit = None

    def published(self, offer, mutex):
        self.step('PUBLISHED', mutex, worker=asdict(offer.worker), player=asdict(offer.player), offer=asdict(offer))

    def require_offer(self, offer):
        self.current()
        require(self.record['phase'] in ('PUBLISHED', 'STOP_INTENT')
                and self.record['offer'] == asdict(offer))

    def stop_intent(self, mutex):
        self.step('STOP_INTENT', mutex)

    def closed(self, closure, mutex, *, authority, lifecycle):
        """Writer-only postcondition with already verified READY bytes.

        Ordinary readers still load through the lifecycle guard. The guarded
        writer supplies its exact committed value, never an ignore-guard read.
        """
        self.current()
        require(closure.closed == 1)
        prospective = dict(self.record, phase='CLOSED', closure=digest_v3(canonical_v3(asdict(closure))))
        self.reconcile_stopped(authority, lifecycle, prospective)
        self.step('CLOSED', mutex, closure=digest_v3(canonical_v3(asdict(closure))))


_creation = threading.local()


def consume_native_creation(executable, command, inherit, flags):
    """Concrete native leaf, including callers bypassing host/runtime wrappers."""
    permit = getattr(_creation, 'permit', None)
    require(type(permit) is list and len(permit) == 4
            and permit[0] == (os.getpid(), threading.get_ident())
            and permit[1] == (executable, command) and permit[2] is False
            and inherit is False and type(flags) is int and flags == 4)
    admission = permit[3]
    require(type(admission) is CanonicalAdmission)
    fresh = open_canonical(admission.project)
    require(fresh.installation == admission.installation
            and fresh.anchor.load() == admission.record
            and admission.record['phase'] in ('WORKER_PENDING', 'PLAYER_PENDING'))
    admission.current()
    permit[2] = True


def open_canonical(project):
    """Read-only admission; missing activation or coverage fails before native IO."""
    from .mvp_policy_ingress import _existing_root, _require_private
    from .mvp_deployment_profile import _read_windows
    from .win32_state_io import Win32StateFilePort, NativeWin32StateApi
    # Only the closed wheel/venv layout is activatable; editable/source copies
    # cannot become canonical merely by choosing their path in a descriptor.
    package = Path(__file__).parent
    require(package.parent.name.casefold() == 'site-packages'
            and package.parent.parent.name.casefold() == 'lib'
            and Path(sys.executable).parent.name.casefold() == 'scripts'
            and Path(sys.executable).parent.parent == package.parent.parent.parent)
    root = _existing_root(machine_root())
    for p in (root, root / 'var', root / 'var/installation.json', root / 'var/occupancy.json'):
        _require_private(p)
    from .mvp_legacy_decommission import canonical_inventory
    payload = _read_windows(root, ('var', 'installation.json'), 2 * 1024 * 1024)
    d = json.loads(payload)
    require(type(d) is dict and set(d) == {'schema', 'state', 'project', 'package',
            'executable', 'source_tree', 'manifest', 'coverage', 'provenance'}
            and canonical_inventory(d) == payload and type(d['schema']) is int and d['schema'] == 1
            and d['state'] == 'ACTIVATED'
            and d['project'] == str(_existing_root(project))
            and d['package'] == str(_existing_root(Path(__file__).parent))
            and d['executable'] == str(plain(sys.executable)))
    require(type(d['provenance']) is dict and set(d['provenance']) == {'board', 'card', 'decision'}
            and all(type(v) is str and bool(v) for v in d['provenance'].values()))
    def coverage():
        from .mvp_legacy_decommission import verify_decommissioned
        require(_read_windows(root, ('var', 'installation.json'), 2 * 1024 * 1024) == payload)
        require(type(d['coverage']) is list and 0 < len(d['coverage']) <= 64)
        for receipt in d['coverage']:
            verify_decommissioned(receipt)
        from .mvp_runner_manifest import verify_runner_manifest
        manifest = _read_windows(Path(d['project']), ('private', 'deployment', 'runner-manifest.json'), 2000000)
        verify_runner_manifest(manifest, approved_sha256=d['manifest'], approved_tree=d['source_tree'],
                               package_root=Path(d['package']))
    coverage()
    return CanonicalAdmission(HostAnchor(root, Win32StateFilePort(root, NativeWin32StateApi())),
        digest_v3(payload), project=d['project'], package=d['package'], executable=d['executable'], coverage=coverage)
