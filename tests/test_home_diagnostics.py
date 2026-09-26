"""Public synthetic HOME diagnostics; no native capture or private fixtures."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.mvp_local_runtime import (
    BgraGameplayRecognizer, RuntimeSafetyError,
)


BINDING = PlayerBinding(
    ProcessIdentity(100, 200), ProcessIdentity(100, 200), 10, 11,
    1280, 720, "0" * 32,
)
ACCOUNT = "synthetic-account"


def frame(color=(0, 100, 220), count=3200, alpha=0):
    pixels = bytearray(BINDING.width * BINDING.height * 4)
    painted = 0
    for y in range(648, 698):
        for x in range(44, 108):
            if painted < count:
                offset = (y * BINDING.width + x) * 4
                pixels[offset:offset + 4] = bytes((*color, alpha))
                painted += 1
    return BINDING.width, BINDING.height, bytes(pixels)


def recognizer_for(value):
    calls = []
    def capture(binding):
        calls.append(binding)
        return value
    return BgraGameplayRecognizer(BINDING, capture, account_verified=False), calls


@pytest.mark.parametrize("count,reason,home", [
    (0, "COLOR_ABSENT", False),
    (960, "FRACTION_LOW", False),
    (961, "HOME_POSITIVE", True),
    (3200, "HOME_POSITIVE", True),
])
def test_diagnostic_uses_same_single_frame_and_exact_mask_denominator(count, reason, home):
    recognizer, calls = recognizer_for(frame(count=count))
    assert recognizer.home_diagnostic is None
    observed = recognizer.recognize(BINDING, ACCOUNT)
    assert observed.home is home
    assert observed.account_matches is False
    assert calls == [BINDING]
    diagnostic = recognizer.home_diagnostic
    assert json.loads(diagnostic.to_json()) == {
        "schema": 1, "reason": reason, "width": 1280, "height": 720,
        "left": 44, "top": 648, "right": 108, "bottom": 698,
        "roi_pixels": 3200, "hue_pixels": count, "saturation_pixels": count,
        "value_pixels": count, "orange_pixels": count,
    }
    assert all(type(value) in {int, str} for value in asdict(diagnostic).values())
    assert not any(type(value) is bytes for value in vars(recognizer).values())


@pytest.mark.parametrize("color,counts", [
    ((0, 100, 220), (3200, 3200, 3200, 3200)),
    ((220, 100, 0), (0, 3200, 3200, 0)),  # swapped red/blue must not match
    ((255, 255, 255), (0, 0, 3200, 0)),
    ((0, 0, 0), (0, 0, 0, 0)),
    ((0, 20, 119), (3200, 3200, 0, 0)),
    ((122, 160, 200), (3200, 0, 3200, 0)),
])
@pytest.mark.parametrize("alpha", [0, 255])
def test_component_counts_distinguish_hue_saturation_value_and_ignore_alpha(color, counts, alpha):
    recognizer, _calls = recognizer_for(frame(color, alpha=alpha))
    recognizer.recognize(BINDING, ACCOUNT)
    diagnostic = recognizer.home_diagnostic
    assert (diagnostic.hue_pixels, diagnostic.saturation_pixels,
            diagnostic.value_pixels, diagnostic.orange_pixels) == counts


def test_orange_outside_roi_never_authorizes_or_enters_diagnostic():
    width, height, pixels = frame()
    shifted = bytearray(len(pixels))
    # Mirror the populated bottom ROI to the top: protects top-down orientation.
    for y in range(height):
        shifted[y * width * 4:(y + 1) * width * 4] = pixels[
            (height - y - 1) * width * 4:(height - y) * width * 4
        ]
    recognizer, _calls = recognizer_for((width, height, bytes(shifted)))
    assert recognizer.recognize(BINDING, ACCOUNT).home is False
    assert recognizer.home_diagnostic.orange_pixels == 0


@pytest.mark.parametrize("value,reason", [
    ((1279, 720, b"synthetic-secret"), "FRAME_GEOMETRY_MISMATCH"),
    ((True, 720, b"synthetic-secret"), "FRAME_GEOMETRY_MISMATCH"),
    ((1280, 720, b"synthetic-secret"), "FRAME_BYTES_INVALID"),
    ((1280, 720, bytearray(b"synthetic-secret")), "FRAME_BYTES_INVALID"),
    ((1280,), "FRAME_SHAPE_INVALID"),
])
def test_malformed_frame_has_closed_reason_and_no_pixels_in_recognizer_traceback(value, reason):
    recognizer, _calls = recognizer_for(value)
    with pytest.raises(RuntimeSafetyError, match=reason) as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert recognizer.home_diagnostic is None
    assert "synthetic-secret" not in str(caught.value)
    trace = caught.value.__traceback__
    while trace:
        if trace.tb_frame.f_globals.get("__name__") == "clash_rush_rebuild.mvp_local_runtime":
            assert not any(type(item) in {bytes, bytearray} for item in trace.tb_frame.f_locals.values())
            assert not any(type(item) is tuple and any(type(part) in {bytes, bytearray} for part in item)
                           for item in trace.tb_frame.f_locals.values())
        trace = trace.tb_next


def test_failed_observation_does_not_reuse_previous_diagnostic():
    values = iter([frame(), (1280, 720, b"bad")])
    recognizer = BgraGameplayRecognizer(BINDING, lambda _binding: next(values), account_verified=False)
    assert recognizer.recognize(BINDING, ACCOUNT).home is True
    with pytest.raises(RuntimeSafetyError, match="FRAME_BYTES_INVALID"):
        recognizer.recognize(BINDING, ACCOUNT)
    assert recognizer.home_diagnostic is None


@pytest.mark.parametrize("width,height", [(640, 360), (1024, 768), (1920, 1080)])
def test_diagnostic_normalizes_against_exact_render_geometry(width, height):
    binding = PlayerBinding(BINDING.identity, BINDING.render_identity, 10, 11,
                            width, height, "0" * 32)
    recognizer = BgraGameplayRecognizer(
        binding, lambda _binding: (width, height, bytes((0, 100, 220, 0)) * (width * height)),
        account_verified=False,
    )
    assert recognizer.recognize(binding, ACCOUNT).home is True
    diagnostic = recognizer.home_diagnostic
    assert (diagnostic.left, diagnostic.top, diagnostic.right, diagnostic.bottom) == (
        int(width * .035), int(height * .90), int(width * .085), int(height * .97),
    )
    assert diagnostic.orange_pixels == diagnostic.roi_pixels
    assert diagnostic.roi_pixels == (diagnostic.right - diagnostic.left) * (diagnostic.bottom - diagnostic.top)


def test_component_counts_are_not_substitutes_for_joint_orange_predicate():
    width, height, pixels = frame()
    mixed = bytearray(pixels)
    # Each individual component passes on >30%, but the joint mask is empty.
    colors = [(0, 20, 119), (122, 160, 200), (220, 100, 0)]
    for y in range(648, 698):
        for x in range(44, 108):
            offset = (y * width + x) * 4
            mixed[offset:offset + 4] = bytes((*colors[(x + y) % 3], 0))
    recognizer, _calls = recognizer_for((width, height, bytes(mixed)))
    assert recognizer.recognize(BINDING, ACCOUNT).home is False
    diagnostic = recognizer.home_diagnostic
    assert min(diagnostic.hue_pixels, diagnostic.saturation_pixels, diagnostic.value_pixels) > 960
    assert diagnostic.orange_pixels == 0
    assert diagnostic.reason == "COLOR_ABSENT"


def test_diagnostic_processing_failure_releases_frame_locals(monkeypatch):
    recognizer = BgraGameplayRecognizer(BINDING, lambda _binding: frame(), account_verified=False)
    def fail(*_channels):
        raise RuntimeSafetyError("synthetic reduction failure")
    monkeypatch.setattr(BgraGameplayRecognizer, "_hsv", fail)
    with pytest.raises(RuntimeSafetyError) as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert recognizer.home_diagnostic is None
    trace = caught.value.__traceback__
    while trace:
        if trace.tb_frame.f_code.co_name in {"recognize", "_diagnose_home"}:
            assert trace.tb_frame.f_locals["frame"] is None
            assert trace.tb_frame.f_locals.get("pixels") is None
        trace = trace.tb_next
