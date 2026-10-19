"""Candidate-side legacy decommission, never actual installation activation.

Whole implementation inventory, not shortcut removal. The offline executor only
accepts a disposable fixture copied by this module; real activation is deliberately
unavailable. A future owner activation must independently establish stopped state,
complete supported-surface coverage and authority before applying the same plan.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import zipfile

from .mvp_canonical_runtime import CanonicalRuntimeError, require
from .mvp_session_authority import canonical_v3, digest_v3

TOMBSTONE = b"raise RuntimeError('NONCANONICAL_RUNTIME_DECOMMISSIONED')\n"
MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024


def canonical_inventory(value):
    """Inventory envelopes are bounded independently of small authority facts."""
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode('ascii')
    require(0 < len(raw) <= 2 * 1024 * 1024)
    return raw


def inventory(root):
    """Exact explicitly selected package only. No host/private-directory search."""
    from .mvp_policy_ingress import _existing_root
    root = _existing_root(root)
    result = {}
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(directory) / name
            st = p.lstat()
            require(not stat.S_ISLNK(st.st_mode) and not getattr(st, 'st_file_attributes', 0) & 0x400)
            if name in files:
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
                total += st.st_size
                require(total <= MAX_BYTES and len(result) < MAX_FILES)
                rel = p.relative_to(root).as_posix()
                result[rel] = digest_v3(p.read_bytes())
    require('__init__.py' in result and bool(result))
    return dict(sorted(result.items()))


def directory_identity(root):
    st = Path(root).stat()
    return {'device': st.st_dev, 'inode': st.st_ino}


@dataclass(frozen=True, slots=True)
class OfflineFixture:
    root: Path
    identity: tuple
    nonce: str


_fixtures = {}


def copy_offline_fixture(source, destination):
    """Copy a selected public source package; rejects every existing destination."""
    source = Path(source)
    before = inventory(source)
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    for relative in before:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write((source / relative).read_bytes())
    require(inventory(source) == before and inventory(destination) == before)
    ident = directory_identity(destination)
    fixture = OfflineFixture(destination, (ident['device'], ident['inode']), secrets.token_hex(16))
    _fixtures[fixture.nonce] = fixture
    return fixture


def plan_fixture(fixture, *, version, source_tree):
    require(type(fixture) is OfflineFixture and _fixtures.get(fixture.nonce) is fixture)
    require(type(version) is str and bool(version) and type(source_tree) is str and len(source_tree) == 40)
    root = fixture.root.resolve(strict=True)
    require(directory_identity(root) == dict(zip(('device', 'inode'), fixture.identity)))
    before = inventory(root)
    # Every executable form is retired, not only the currently advertised CLI.
    # Caches are replaced as inert bytes too: no sourceless import or timestamp
    # cache can retain the pre-migration implementation.
    after = {p: digest_v3(TOMBSTONE if p.endswith('.py') else b'') for p in before}
    return dict(schema=1, mode='OFFLINE_FIXTURE_ONLY', root=str(root), identity=directory_identity(root),
                version=version, source_tree=source_tree, before=before, after=after)


def apply_fixture(fixture, plan, journal_root, *, stopped):
    """Journal/backup first, idempotent exact transformations on copied fixtures.

    stopped is an injected low-level fixture proof, not a production approval.
    No production caller can obtain an OfflineFixture for an existing directory.
    An interrupted plan resumes only matching before/after bytes; ambiguity stops.
    """
    require(type(fixture) is OfflineFixture and _fixtures.get(fixture.nonce) is fixture)
    require(type(plan) is dict and plan['mode'] == 'OFFLINE_FIXTURE_ONLY'
            and plan['root'] == str(fixture.root.resolve(strict=True))
            and plan['identity'] == directory_identity(fixture.root))
    require(stopped() is True)
    journal = Path(journal_root)
    require(journal.is_dir() and journal.resolve(strict=True) == journal.absolute()
            and not journal.is_relative_to(fixture.root))
    payload = canonical_inventory(plan)
    seal = digest_v3(payload)
    pending, backup, receipt = (journal / (seal + suffix) for suffix in ('.pending', '.zip', '.receipt'))
    if receipt.exists():
        result = json.loads(receipt.read_bytes())
        verify_decommissioned(result)
        return result
    if pending.exists():
        require(pending.read_bytes() == payload and backup.exists())
    else:
        require(inventory(fixture.root) == plan['before'])
        with pending.open('xb') as f:
            f.write(payload); f.flush(); os.fsync(f.fileno())
        with zipfile.ZipFile(backup, 'x', compression=zipfile.ZIP_DEFLATED) as z:
            for relative in plan['before']:
                z.write(fixture.root / relative, relative)
        with backup.open('r+b') as f:
            os.fsync(f.fileno())
    with zipfile.ZipFile(backup) as z:
        require(set(z.namelist()) == set(plan['before']) and z.testzip() is None)
        require(all(digest_v3(z.read(p)) == value for p, value in plan['before'].items()))
    current = inventory(fixture.root)
    require(set(current) == set(plan['before']))
    for relative, before in plan['before'].items():
        require(current[relative] in (before, plan['after'][relative]))
        require(stopped() is True and directory_identity(fixture.root) == plan['identity'])
        if current[relative] == plan['after'][relative]:
            continue
        target = fixture.root / relative
        data = TOMBSTONE if relative.endswith('.py') else b''
        # The fixture executor does not certify Win32 production durability.
        # Actual activation must use protected write-through replacement/readback.
        with target.open('wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        require(digest_v3(target.read_bytes()) == plan['after'][relative])
    result = dict(schema=1, root=plan['root'], identity=plan['identity'], version=plan['version'],
        source_tree=plan['source_tree'], plan_digest=seal, files=plan['after'],
        backup_digest=digest_v3(backup.read_bytes()))
    verify_decommissioned(result)
    with receipt.open('xb') as f:
        f.write(canonical_inventory(result)); f.flush(); os.fsync(f.fileno())
    require(receipt.read_bytes() == canonical_inventory(result))
    # Retain pending plan as immutable history; receipt is the completion join.
    return result


def verify_decommissioned(receipt):
    require(type(receipt) is dict and set(receipt) == {'schema', 'root', 'identity', 'version',
        'source_tree', 'plan_digest', 'files', 'backup_digest'}
        and type(receipt['schema']) is int and receipt['schema'] == 1)
    root = Path(receipt['root'])
    require(directory_identity(root) == receipt['identity'])
    actual = inventory(root)
    require(actual == receipt['files'])
    require(all(value == digest_v3(TOMBSTONE if p.endswith('.py') else b'') for p, value in actual.items()))


def activate_installed(*args, **kwargs):
    raise CanonicalRuntimeError('CANONICAL_ACTIVATION_REQUIRES_SEPARATE_OWNER_GATE')
