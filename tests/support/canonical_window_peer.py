"""Disposable off-screen Windows HWND owners. Never game/input/capture code."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

u = ctypes.WinDLL('user32', use_last_error=True)
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
u.CreateWindowExW.restype = wintypes.HWND
u.DestroyWindow.argtypes = [wintypes.HWND]
u.DestroyWindow.restype = wintypes.BOOL
u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
u.PeekMessageW.restype = wintypes.BOOL
u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
u.TranslateMessage.restype = wintypes.BOOL
u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
u.DispatchMessageW.restype = ctypes.c_ssize_t

mode, folder = sys.argv[1], Path(sys.argv[2])
parent = int(sys.argv[3]) if mode == 'render' else None
hwnd = u.CreateWindowExW(0x08000000, 'STATIC', 'ClashRush disposable window proof',
    0x10000000 | (0x40000000 if parent else 0x80000000),
    0 if parent else -10000, 0 if parent else -10000, 800, 600, parent, None, None, None)
assert hwnd
child = None
try:
    (folder / (mode + '.json')).write_text(json.dumps({'hwnd':int(hwnd)}))
    if mode == 'root':
        child = subprocess.Popen([sys.executable, __file__, 'render', str(folder), str(hwnd)],
                                 close_fds=True)
    deadline = time.monotonic() + 45
    msg = wintypes.MSG()
    while time.monotonic() < deadline and not (folder / 'exit').exists():
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
        time.sleep(.01)
finally:
    u.DestroyWindow(hwnd)
    if child is not None:
        if child.poll() is None:
            child.terminate()  # Exact retained test-owned child, harness cleanup only.
        child.wait(timeout=5)
