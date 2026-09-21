"""First-party launcher geometry seam; no donor process/input authority.

Adapted from ClashRush 949497bf0a543a43ec6ef39a8c897e366bc10362,
bluestacks_launcher.py:276-326. Only STARTUP_DEBUG composes this supervisor.
"""
from __future__ import annotations

import ctypes
import math
import time
from ctypes import wintypes

from .lifecycle import LifecycleSupervisor, PlayerBinding


class GeometryError(RuntimeError):
    """Sanitized startup geometry failure."""


def _corrected_root_width(root_width, render_width, render_height, expected_aspect):
    if (
        any(type(v) is not int or v <= 0 for v in (root_width, render_width, render_height))
        or type(expected_aspect) not in (int, float)
        or not math.isfinite(expected_aspect)
        or expected_aspect <= 0
    ):
        raise GeometryError("GEOMETRY_INVALID")
    return root_width + round(render_height * expected_aspect) - render_width


def _verify_render(current):
    expected = 1280 / 720
    if (
        current.width < 640 or current.height < 360
        or abs(current.width / current.height - expected) / expected > 0.015
    ):
        raise GeometryError("GEOMETRY_MISMATCH")


def normalize_window_geometry(player, api, rebind, *, gate, wait=time.sleep):
    """Restore, correct width at most four times, rebind before any capture.

    Rebinding is supplied by the Job owner, never by a PID/title-only lookup.
    Root and render identities/HWNDs must survive; only dimensions/nonce change.
    """
    def admitted():
        if gate() is not True:
            raise GeometryError("GEOMETRY_AUTHORIZATION_LOST")

    def checked():
        admitted()
        current = rebind()
        if type(player) is not PlayerBinding or type(current) is not PlayerBinding:
            raise GeometryError("GEOMETRY_BINDING_LOST")
        if (
            current.identity != player.identity
            or current.render_identity != player.render_identity
            or current.root_hwnd != player.root_hwnd
            or current.render_hwnd != player.render_hwnd
        ):
            raise GeometryError("GEOMETRY_BINDING_CHANGED")
        return current

    current = checked()
    admitted()
    api.restore(current.root_hwnd)
    wait(0.15)
    current = checked()
    for _ in range(4):
        try:
            _verify_render(current)
            return current
        except GeometryError:
            pass
        left, top, right, bottom = api.bounds(current.root_hwnd)
        root_width, root_height = right - left, bottom - top
        target_width = _corrected_root_width(
            root_width, current.width, current.height, 1280 / 720,
        )
        if target_width < 700 or root_height < 430:
            raise GeometryError("GEOMETRY_TOO_SMALL")
        # Revalidate after the native bounds query, immediately before mutation.
        latest = checked()
        if (latest.width, latest.height) != (current.width, current.height):
            raise GeometryError("GEOMETRY_CHANGED")
        admitted()
        api.resize(current.root_hwnd, left, top, target_width, root_height)
        wait(0.2)
        current = checked()
    _verify_render(current)
    return current


class NativeGeometryApi:
    """Pointer-width declarations and literal donor restore/resize flags."""
    def __init__(self, user32):
        self._user32 = user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

    def restore(self, root):
        # ShowWindow returns PREVIOUS visibility, not success. The owner checks
        # visible/unlocked/non-iconic exact binding again after the settle.
        self._user32.ShowWindow(wintypes.HWND(root), 9)

    def bounds(self, root):
        rectangle = wintypes.RECT()
        if not self._user32.GetWindowRect(wintypes.HWND(root), ctypes.byref(rectangle)):
            raise GeometryError("GEOMETRY_BOUNDS_FAILED")
        return rectangle.left, rectangle.top, rectangle.right, rectangle.bottom

    def resize(self, root, left, top, width, height):
        if not self._user32.SetWindowPos(
            wintypes.HWND(root), None, left, top, width, height, 0x0004 | 0x0010,
        ):
            raise GeometryError("GEOMETRY_RESIZE_FAILED")


class StartupGeometrySupervisor(LifecycleSupervisor):
    """Diagnostic-only post-launch preparation with authoritative failure stop.

    The normal lifecycle's initial capture-health probe remains memory-only.
    Diagnostic stable frames, evidence, target detection and input are created
    only after start returns the normalized binding. Other purposes are unchanged.
    """
    def __init__(self, *args, geometry_api, geometry_wait=time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self._geometry_api = geometry_api
        self._geometry_wait = geometry_wait

    def start(self, slot):
        binding = super().start(slot)
        owned = self._owned
        deadline = time.monotonic() + 10.0

        def rebind():
            members = self._host.stable_job_members(owned.job)
            try:
                current = self._host.bind_exact(binding.identity, slot.display_name, members)
                if type(current) is not PlayerBinding:
                    raise GeometryError("GEOMETRY_BINDING_LOST")
                # Identity, Job membership, ancestry, current size, unlocked
                # desktop and non-minimized state, without capturing pixels.
                self._host._require_capture_binding(current, owned.job)
                return current
            finally:
                members.close()

        try:
            normalized = normalize_window_geometry(
                binding, self._geometry_api, rebind,
                gate=lambda: time.monotonic() < deadline,
                wait=self._geometry_wait,
            )
            self._host.capture_ready(normalized, owned.job)
        except BaseException:  # noqa: BLE001 - every interrupted preparation must stop
            # Keep original binding authoritative for stop even after resize;
            # stop proves Job empty/HWND invalid, not pre-stop pixel dimensions.
            self.stop(binding, None)
            raise GeometryError("STARTUP_GEOMETRY_FAILED") from None
        owned.binding = normalized
        return normalized
