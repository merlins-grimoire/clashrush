"""BasePilot-derived memory-only capture adapter.

Copied and adapted from BasePilot ``app/services/window.py:68-85,295-350``
at commit 4ede1efd220ffc79a5b490cfd3788b44d2584da4 (MIT).
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

from .lifecycle import PlayerBinding


class BasePilotCaptureError(RuntimeError):
    """A lifecycle-owned capture could not be converted without retaining pixels."""


def _raise_capture_error() -> None:
    raise BasePilotCaptureError("owned screenshot capture failed") from None


def _convert_bgra(width: int, height: int, payload: bytes) -> np.ndarray:
    mutable: bytearray | None = None
    pixels: np.ndarray | None = None
    try:
        mutable = bytearray(payload)
        pixels = np.frombuffer(mutable, dtype=np.uint8).reshape((height, width, 4))
        return cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
    finally:
        pixels = None
        if mutable is not None:
            mutable[:] = b"\x00" * len(mutable)
        mutable = None


class WindowService:
    """Preserve BasePilot's ``screenshot()`` shape over an exact owned binding."""

    def __init__(
        self,
        binding: PlayerBinding,
        capture_owned: Callable[[PlayerBinding], tuple[int, int, bytes]],
    ) -> None:
        if type(binding) is not PlayerBinding or not callable(capture_owned):
            raise BasePilotCaptureError("exact owned capture binding required")
        self._binding = binding
        self._capture_owned = capture_owned

    def screenshot(self) -> np.ndarray:
        """Capture once through the reviewed lifecycle owner and return BGR pixels."""
        captured: tuple[int, int, bytes] | None = None
        payload: bytes | None = None
        frame: np.ndarray | None = None
        failed = False
        try:
            captured = self._capture_owned(self._binding)
            if (
                type(captured) is not tuple
                or len(captured) != 3
                or type(captured[0]) is not int
                or type(captured[1]) is not int
                or (captured[0], captured[1])
                != (self._binding.width, self._binding.height)
                or type(captured[2]) is not bytes
                or len(captured[2]) != self._binding.width * self._binding.height * 4
            ):
                raise BasePilotCaptureError("owned capture result is malformed")
            width, height, payload = captured
            frame = _convert_bgra(width, height, payload)
            if (
                type(frame) is not np.ndarray
                or frame.dtype != np.uint8
                or frame.shape != (height, width, 3)
            ):
                frame = None
                raise BasePilotCaptureError("owned screenshot conversion is malformed")
        except BaseException:
            failed = True
        captured = None
        payload = None
        if failed:
            frame = None
            _raise_capture_error()
        if type(frame) is not np.ndarray:
            _raise_capture_error()
        return frame
