"""Explicit fixture permission for historical disposable process tests only.

This never grants product admission. Canonical integration tests do not use it.
Only the current test interpreter is eligible; no player/ADB executable or generic
command shell is accepted. The tested topology and actual Windows Jobs stay real.
"""
import ctypes
from pathlib import Path
import sys


def permit_disposable_interpreter(executable, command, inherit, flags):
    assert Path(executable).resolve(strict=True) in {
        Path(sys.executable).resolve(strict=True), Path(sys._base_executable).resolve(strict=True)}
    assert inherit is False and flags == 4
    assert 'HD-Player' not in command and 'HD-Adb' not in command


def install():
    from clash_rush_rebuild import mvp_canonical_runtime
    mvp_canonical_runtime.consume_native_creation = permit_disposable_interpreter
