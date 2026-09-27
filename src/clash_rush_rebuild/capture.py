"""Memory-only render health reduction for the inert lifecycle slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, Protocol

from .lifecycle import PlayerBinding


class CaptureError(RuntimeError):
    """Transient capture is malformed or unsafe."""


class NearBlackCaptureError(CaptureError):
    """The owned render is temporarily between visible game frames."""


@dataclass(frozen=True, slots=True)
class CaptureHealth:
    width: int
    height: int
    method: str
    dark_pixel_percent: int


class CapturePort(Protocol):
    def capture_bgra(self, binding: PlayerBinding) -> tuple[int, int, bytes]: ...


def _fail(message: str) -> NoReturn:
    raise CaptureError(message)


def capture_health(port: CapturePort, binding: PlayerBinding) -> CaptureHealth:
    """Capture, reduce to coarse health, and retain no pixel buffer or digest."""
    width, height, pixels = port.capture_bgra(binding)
    malformed = (
        type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
        or type(pixels) is not bytes
        or len(pixels) != width * height * 4
    )
    if malformed:
        del pixels
        _fail("malformed transient render frame")
    count = width * height
    dark = sum(
        1
        for offset in range(0, len(pixels), 4)
        if max(pixels[offset : offset + 3]) <= 12
    )
    dark_percent = (dark * 100) // count
    near_black = dark * 100 > count * 97
    del pixels
    if near_black:
        raise NearBlackCaptureError("near-black transient render frame")
    return CaptureHealth(width, height, "PRINTWINDOW", dark_percent)
