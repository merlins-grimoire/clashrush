from __future__ import annotations

import pytest

from clash_rush_rebuild.capture import CaptureError, capture_health
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity


class FakeCapturePort:
    def __init__(self, pixels: bytes, *, width: int = 2, height: int = 2) -> None:
        self.pixels = pixels
        self.width = width
        self.height = height

    def capture_bgra(self, binding: PlayerBinding) -> tuple[int, int, bytes]:
        return self.width, self.height, self.pixels


def _binding() -> PlayerBinding:
    identity = ProcessIdentity(100, 9001)
    return PlayerBinding(identity, identity, 101, 102, 1280, 720, "a" * 32)


def test_capture_immediately_reduces_frame_to_nonidentifying_health() -> None:
    port = FakeCapturePort(bytes([30, 60, 90, 0]) * 4)
    health = capture_health(port, _binding())
    assert health.width == 2
    assert health.height == 2
    assert health.method == "PRINTWINDOW"
    assert health.dark_pixel_percent == 0
    assert not hasattr(health, "pixels")
    assert "Private Name" not in repr(health)


def test_capture_rejects_near_black_frame() -> None:
    port = FakeCapturePort(bytes([0, 0, 0, 0]) * 4)
    with pytest.raises(CaptureError, match="near-black"):
        capture_health(port, _binding())


def test_capture_error_traceback_does_not_retain_full_frame() -> None:
    secret_frame = bytes([0, 0, 0, 0]) * 4
    port = FakeCapturePort(secret_frame)
    try:
        capture_health(port, _binding())
    except CaptureError as exc:
        traceback = exc.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_filename.replace("\\", "/").endswith(
                "/src/clash_rush_rebuild/capture.py"
            ):
                assert all(
                    value is not secret_frame
                    for value in traceback.tb_frame.f_locals.values()
                )
            traceback = traceback.tb_next
    else:
        raise AssertionError("near-black frame was accepted")


def test_capture_rejects_malformed_buffer() -> None:
    port = FakeCapturePort(b"bad")
    with pytest.raises(CaptureError, match="malformed"):
        capture_health(port, _binding())
