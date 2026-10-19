"""Crashable disposable P; its W/E are harmless test actors in an outer test Job."""
import ctypes
from ctypes import wintypes as w
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from surrogate_creation import install
install()  # Explicit harmless interpreter-only permission in this disposable peer.
from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.mvp_process_topology import HandoffAck, ParentProcessTopology, PlayerHandoff
from clash_rush_rebuild.win32_lifecycle_host import NativeProcessApi, Win32LifecycleHost


def main():
    root, manifest, ready_name, purpose = sys.argv[1:]
    root = Path(root)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('CreateEventW', [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
        ('OpenEventW', [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
        ('SetEvent', [w.HANDLE], w.BOOL),
    ):
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    host = Win32LifecycleHost(NativeProcessApi(), nonce_factory=lambda: '0' * 32)
    ready = kernel.OpenEventW(2, False, ready_name)
    assert ready
    names = ['Local\\ClashRush-parent-proof-' + uuid.uuid4().hex for _ in range(3)]
    events = [kernel.CreateEventW(None, True, False, name) for name in names]
    assert all(events)
    topology = ParentProcessTopology(host, run_nonce='c' * 32, generation=1, purpose=purpose)
    try:
        paths = [root / n for n in ('offer.json', 'ack.json', 'revoke.json', 'release.json')]
        peer = Path(__file__).with_name('policy_topology_peer.py')
        exe = sys.executable
        offer = topology.prepare(exe, subprocess.list2cmdline([exe, str(peer),
                       *(str(p) for p in paths), *names]), exe,
                       subprocess.list2cmdline([exe, '-c', 'import time; time.sleep(40)']))
        paths[0].write_text(json.dumps({'offer': asdict(offer)}), encoding='ascii')
        topology.start_worker()
        assert host.wait_process(events[0], 10000)
        record = json.loads(paths[1].read_text(encoding='ascii'))['ack']
        values = record['offer']
        for role in ('parent', 'worker', 'player'):
            values[role] = ProcessIdentity(**values[role])
        topology.accept_worker_ack(HandoffAck(PlayerHandoff(**values), record['accepted'], record['noninheritable']))
        Path(manifest).write_text(json.dumps({'offer': asdict(offer),
            'ambient': host.is_process_in_job(host.current_process_handle(), None),
            'worker_flags': host.job_limit_flags(topology.worker_job),
            'player_flags': host.job_limit_flags(topology.player_job)}), encoding='ascii')
        assert kernel.SetEvent(ready)
        host.wait_process(host.current_process_handle(), 30000)
    finally:
        topology.dispose()
        for event in [ready, *events]:
            host.close_handle(event)


if __name__ == '__main__':
    main()
