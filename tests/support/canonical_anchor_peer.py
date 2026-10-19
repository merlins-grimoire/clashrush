"""Disposable cross-process anchor contender; never creates native actors."""
import json
import os
from pathlib import Path
import sys
import time

from clash_rush_rebuild.mvp_canonical_runtime import HostAnchor
from clash_rush_rebuild.win32_runtime import NativeWin32Api, Win32Runtime
from clash_rush_rebuild.win32_state_io import NativeWin32StateApi, Win32StateFilePort

root, name = Path(sys.argv[1]), sys.argv[2]
(root / (name+'.ready')).write_text('ready')
end = time.monotonic()+15
while not (root/'start').exists():
    if time.monotonic() > end: raise RuntimeError('test barrier timeout')
    time.sleep(.01)
lease = Win32Runtime(NativeWin32Api()).acquire_mutex(timeout_ms=10000)
try:
    anchor = HostAnchor(root, Win32StateFilePort(root,NativeWin32StateApi()))
    before = anchor.load()
    if before['phase'] != 'STOPPED':
        result = 'BLOCKED_NONTERMINAL'
    else:
        # Complete synthetic pending evidence, not a native actor/offer grant.
        anchor.transition(before, dict(schema=2, installation=before['installation'],
            phase='PREPARING', run=dict(generation=1, nonce='a'*32, digest='b'*64,
                source_tree='c'*40, manifest='d'*64),
            parent=dict(pid=os.getpid(), creation_time_100ns=1),
            worker=None, player=None, offer=None, closure=None), lease)
        result = 'RESERVED'
finally:
    lease.release()
print(result)
