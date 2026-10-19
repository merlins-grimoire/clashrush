"""Disposable process-only peer. Never imports game/input/capture code."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    # The controller owns the outer test Job before this peer is resumed.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    child = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        creationflags=0x4, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    stamps = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(int(child._handle), *(ctypes.byref(s) for s in stamps)):
        raise ctypes.WinError(ctypes.get_last_error())
    stamp = (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
    Path(sys.argv[1]).write_text(json.dumps({'pid': child.pid, 'creation': stamp}), encoding='ascii')
    event = kernel.OpenEventW(0x2, False, sys.argv[2])
    if not event:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.SetEvent(event):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(event)
    # Deliberately retain the child handle. The controller kills only this
    # dedicated test Job, including the suspended disposable descendant.
    time.sleep(30)


if __name__ == '__main__':
    main()
