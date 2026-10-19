"""Disposable host fixture; real anchor state machine and durable Win32 files."""
from pathlib import Path
from clash_rush_rebuild.mvp_canonical_runtime import CanonicalAdmission, HostAnchor
from clash_rush_rebuild.mvp_session_authority import canonical_v3
from clash_rush_rebuild.win32_state_io import Win32StateFilePort, NativeWin32StateApi


def install_fixture(root, monkeypatch):
    from clash_rush_rebuild import mvp_canonical_runtime as canonical
    root = Path(root)
    host = root / 'synthetic-host'
    (host / 'var').mkdir(parents=True, exist_ok=True)
    path = host / 'var/occupancy.json'
    if not path.exists():
        path.write_bytes(canonical_v3(dict(schema=1, installation='a'*64,
            phase='STOPPED', run=None, parent=None, worker=None, player=None, closure=None)))
    anchor = HostAnchor(host, Win32StateFilePort(host, NativeWin32StateApi()))
    def opener(project):
        assert Path(project).resolve() == root.resolve()
        return CanonicalAdmission(anchor, 'a'*64, project=root,
            package=Path(canonical.__file__).parent, executable='synthetic.exe', coverage=lambda:None)
    monkeypatch.setattr(canonical, 'open_canonical', opener)
    return opener(root)


def phase_record(run, phase, offer=None):
    """Complete phase fixtures, including legitimately not-yet-created actors."""
    from dataclasses import asdict
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    from clash_rush_rebuild.mvp_process_topology import PlayerHandoff
    offer = offer or PlayerHandoff(run.run_nonce, run.generation, 'f'*32,
        ProcessIdentity(98765, 1), ProcessIdentity(98766, 2), ProcessIdentity(98767, 3), 400, 401)
    record = dict(schema=2, installation='a'*64, phase=phase,
        run=dict(generation=run.generation, nonce=run.run_nonce, digest=run.digest,
                 source_tree=run.source_tree, manifest=run.runner_manifest_digest),
        parent=asdict(offer.parent), worker=None, player=None, offer=None, closure=None)
    if phase not in ('PREPARING', 'WORKER_PENDING'):
        record['worker'] = asdict(offer.worker)
    if phase in ('PUBLISHED', 'STOP_INTENT', 'CLOSED'):
        record.update(player=asdict(offer.player), offer=asdict(offer))
    if phase == 'CLOSED': record['closure'] = 'b'*64
    return record
