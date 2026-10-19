"""Disposable handoff receiver; no input, capture, game or transport imports."""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = (
        ('GetProcessId', [w.HANDLE], w.DWORD),
        ('GetProcessTimes', [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
        ('GetHandleInformation', [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        ('CloseHandle', [w.HANDLE], w.BOOL),
        ('TerminateProcess', [w.HANDLE, w.UINT], w.BOOL),
        ('OpenEventW', [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
        ('SetEvent', [w.HANDLE], w.BOOL),
    )
    for name, arguments, result in signatures:
        method = getattr(kernel, name)
        method.argtypes, method.restype = arguments, result

    def identity(handle):
        stamps = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(s) for s in stamps)):
            raise ctypes.WinError(ctypes.get_last_error())
        return [kernel.GetProcessId(handle), (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime]

    request, response, event_name = sys.argv[1:]
    record = json.loads(Path(request).read_text(encoding='ascii'))
    handle = record['handle']
    child = None
    result = {'accepted': False, 'duplicate_closed': False}
    try:
        actual = identity(handle)
        flags = w.DWORD()
        assert kernel.GetHandleInformation(handle, ctypes.byref(flags))
        assert flags.value & 1 == 0
        # Actual kernel denial, not a mocked least-privilege assertion. This
        # process can target only the controller-created disposable witness.
        ctypes.set_last_error(0)
        assert not kernel.TerminateProcess(handle, 99)
        assert ctypes.get_last_error() == 5
        result.update(identity=actual, noninheritable=True, terminate_denied=True)
        result['accepted'] = actual == record['identity']
        if result['accepted'] and record['descendant']:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     close_fds=True, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result['descendant'] = identity(int(child._handle))
    except BaseException:
        result['accepted'] = False
    finally:
        result['duplicate_closed'] = bool(kernel.CloseHandle(handle))
    Path(response).write_text(json.dumps(result), encoding='ascii')
    event = kernel.OpenEventW(2, False, event_name)
    assert event
    try:
        assert kernel.SetEvent(event)
    finally:
        assert kernel.CloseHandle(event)
    # No readiness sleep oracle: the parent waits on the event, then controls
    # only its dedicated Job. This is a finite emergency actor lifetime.
    time.sleep(30)


if __name__ == '__main__':
    main()
