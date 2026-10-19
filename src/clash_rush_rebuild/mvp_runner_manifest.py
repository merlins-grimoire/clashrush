"""Exact installed Python source membership, independent of a source checkout.

The approved digest is supplied by the consumed external approval, NOT read from
this manifest. Integrity is not approval. This helper performs no activation.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sys


class RunnerManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerMembership:
    source_tree:str
    manifest_sha256:str
    files:tuple[tuple[str,str],...]


def runner_manifest(package_root:Path,source_tree:str)->bytes:
    """Build-time only; hash the complete package, including policy data/assets."""
    root=Path(package_root).resolve(strict=True)
    if type(source_tree) is not str or re.fullmatch('[0-9a-f]{40}',source_tree) is None:
        raise RunnerManifestError('runner tree invalid')
    files=[]
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts or path.suffix=='.pyc':continue
        if path.is_symlink() or path.resolve().is_relative_to(root) is not True:
            raise RunnerManifestError('runner link unavailable')
        if path.is_file():files.append([relative.as_posix(),sha256(path.read_bytes()).hexdigest()])
    if not files:raise RunnerManifestError('runner source absent')
    return json.dumps({'schema':1,'source_tree':source_tree,'files':files},
                       sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def verify_runner_manifest(payload:bytes,*,approved_sha256:str,approved_tree:str,
                           package_root:Path,loaded_modules=None)->RunnerMembership:
    """Verify bytes and imported module locations; a Git hash label is insufficient."""
    if (type(payload) is not bytes or not 0<len(payload)<=2_000_000
            or type(approved_sha256) is not str
            or re.fullmatch('[0-9a-f]{64}',approved_sha256) is None
            or sha256(payload).hexdigest()!=approved_sha256):
        raise RunnerManifestError('runner approval mismatch')
    try:
        parsed=json.loads(payload)
        if (type(parsed) is not dict or set(parsed)!={'schema','source_tree','files'}
                or type(parsed['schema']) is not int or parsed['schema']!=1
                or parsed['source_tree']!=approved_tree):
            raise ValueError
        root=Path(package_root).resolve(strict=True)
        # Byte-canonical regeneration rejects duplicates, unknown/missing files,
        # aliased paths, modified data, and manifests from another source tree.
        if runner_manifest(root,approved_tree)!=payload:raise ValueError
        members=dict(parsed['files'])
        modules=sys.modules if loaded_modules is None else loaded_modules
        for name,module in tuple(modules.items()):
            if name!='clash_rush_rebuild' and not name.startswith('clash_rush_rebuild.'):continue
            source=Path(module.__file__).resolve(strict=True)
            relative=source.relative_to(root).as_posix()
            if relative not in members or sha256(source.read_bytes()).hexdigest()!=members[relative]:
                raise ValueError
            if name=='clash_rush_rebuild' and tuple(Path(p).resolve() for p in module.__path__)!=(root,):
                raise ValueError
        return RunnerMembership(approved_tree,approved_sha256,tuple(tuple(row) for row in parsed['files']))
    except (AttributeError,KeyError,OSError,TypeError,ValueError):
        raise RunnerManifestError('installed runner membership unavailable') from None
