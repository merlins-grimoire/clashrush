from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import clash_rush_rebuild.basepilot_vision as vision_module
from clash_rush_rebuild.basepilot_vision import (
    BasePilotGeometry,
    BasePilotRecognitionError,
    VisionService,
)
from clash_rush_rebuild.basepilot_window import BasePilotCaptureError, WindowService
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.no_input_home_diagnostic import (
    HomeDiagnosticResult,
    NoInputHomeDiagnosticController,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "donor" / "home_screen.png"
_PROVENANCE = _FIXTURE.with_suffix(".redaction.json")
_REDACTION_COLOR = np.array([24, 24, 24], dtype=np.uint8)
_IDENTIFYING_HUD_REGIONS = (
    (0, 0, 775, 220),
    (850, 0, 1728, 220),
    (0, 220, 360, 1080),
    (1368, 220, 1728, 1080),
    (360, 850, 1368, 1080),
)
_IDENTIFYING_SCENE_REGIONS = (
    (610, 340, 760, 405),
)


def test_copied_basepilot_recognizer_classifies_tracked_real_home_fixture() -> None:
    frame = cv2.imread(str(_FIXTURE), cv2.IMREAD_COLOR)
    assert frame is not None

    result = NoInputHomeDiagnosticController.detect_frame(frame)

    assert result is HomeDiagnosticResult.HOME


def test_tracked_home_derivative_has_no_readable_identifying_hud_fields() -> None:
    frame = cv2.imread(str(_FIXTURE), cv2.IMREAD_COLOR)
    assert frame is not None
    assert frame.shape == (1080, 1728, 3)

    for x0, y0, x1, y1 in (
        *_IDENTIFYING_HUD_REGIONS,
        *_IDENTIFYING_SCENE_REGIONS,
    ):
        region = frame[y0:y1, x0:x1]
        assert region.size > 0
        assert np.all(region == _REDACTION_COLOR)


def test_tracked_home_derivative_records_source_and_redaction_provenance() -> None:
    provenance = json.loads(_PROVENANCE.read_text(encoding="utf-8"))

    assert provenance == {
        "derived_sha256": "d5a9cdded32cf353da3473b23bb9015b7d0b3332ee8c28d6644fae3a9dc9aa84",
        "derived_size": 1623612,
        "detector_region_preserved": [775, 0, 850, 220],
        "dimensions": [1728, 1080],
        "redaction_bgr": [24, 24, 24],
        "redaction_regions": [
            list(region)
            for region in (*_IDENTIFYING_HUD_REGIONS, *_IDENTIFYING_SCENE_REGIONS)
        ],
        "source_commit": "c41fe12a6df051e241c695b71b6859286e24c612",
        "source_path": "tests/fixtures/home_screen.png",
        "source_sha256": "279acc36eaf56c298c668ecb110d5001628c2ec5607f0f57604d8460bdb42611",
        "source_size": 4060294,
        "transformation": (
            "Replace every peripheral HUD region that can display player, clan, "
            "account, resource, notification, or action text and the in-scene "
            "account-associated identity label and badge with one opaque color; "
            "retain the non-identifying central scene and the sealed Home-builder "
            "template region."
        ),
    }


def test_copied_donor_assets_and_real_fixture_match_frozen_seals() -> None:
    root = Path(__file__).parents[1]
    expected = {
        "16_9/builder.png": "c48653fc4b0dd5f504d41903c4e12b2a31de96f3c1e8da0ba32e9eee9ae7a8b3",
        "16_9/gbuilder.png": "c696f96848e1be2d49710b625d5a77a7c0a3e40db91d25eb9b8be96340861094",
        "16_9/mbuilder.png": "f3317664be20289f8ac8d03feec05a81f7c6b0d04d8a8cbd16604e8025bb9b2d",
        "16_10/builder.png": "e37551f7c08fde1781ff977adcb0b7cb4041a913a511fc798cbae0cc633fa914",
        "16_10/gbuilder.png": "28a1a648464275e50704275b924904b3e058aa28dc6e9cbde1d57c1e5cd68412",
        "16_10/mbuilder.png": "aafb4af0a51081ba324bc3901a299524fd5020ed8dae60352c72113fd06cefd4",
    }
    assets = root / "src" / "clash_rush_rebuild" / "assets" / "basepilot_templates"
    assert {
        name: hashlib.sha256((assets / name).read_bytes()).hexdigest()
        for name in expected
    } == expected
    assert _FIXTURE.stat().st_size == 1_623_612
    assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == (
        "d5a9cdded32cf353da3473b23bb9015b7d0b3332ee8c28d6644fae3a9dc9aa84"
    )


def test_geometry_rejects_wrong_aspect_and_binding_disagreement() -> None:
    wrong_aspect = np.zeros((600, 600, 3), dtype=np.uint8)
    with pytest.raises(BasePilotRecognitionError, match="aspect"):
        BasePilotGeometry.from_frame(wrong_aspect)

    supported = np.zeros((720, 1280, 3), dtype=np.uint8)
    with pytest.raises(BasePilotRecognitionError, match="binding"):
        BasePilotGeometry.from_frame(supported, expected_size=(1281, 720))


def test_template_resolution_is_closed_and_missing_asset_fails_closed(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    vision = VisionService(BasePilotGeometry.from_frame(frame))
    with pytest.raises(BasePilotRecognitionError, match="allowlisted"):
        vision.find_template(frame, "attack.png")

    class MissingResource:
        def __truediv__(self, _part):
            return self

        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(vision_module, "files", lambda _package: MissingResource())
    with pytest.raises(BasePilotRecognitionError, match="unavailable"):
        vision.find_template(frame, "builder.png")


def test_builder_match_precedes_home_match(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    calls: list[str] = []

    def match(self, _frame, name, **_kwargs):
        calls.append(name)
        return (100, 100)

    monkeypatch.setattr(VisionService, "find_template", match)
    assert NoInputHomeDiagnosticController.detect_frame(frame) is HomeDiagnosticResult.BUILDER
    assert calls == ["mbuilder.png"]


def _binding(width: int = 640, height: int = 360) -> PlayerBinding:
    identity = ProcessIdentity(100, 9001)
    return PlayerBinding(identity, identity, 101, 102, width, height, "a" * 32)


def test_window_service_preserves_donor_bgra_to_bgr_capture_shape() -> None:
    binding = _binding()
    bgra = bytes([10, 20, 30, 255]) * (binding.width * binding.height)
    frame = WindowService(
        binding,
        lambda selected: (selected.width, selected.height, bgra),
    ).screenshot()

    assert frame.shape == (binding.height, binding.width, 3)
    assert frame.dtype == np.uint8
    assert frame[0, 0].tolist() == [10, 20, 30]


def test_window_service_rejects_malformed_owned_capture() -> None:
    binding = _binding()
    subject = WindowService(binding, lambda _selected: (1, 1, b"bad"))

    with pytest.raises(BasePilotCaptureError, match="failed"):
        subject.screenshot()


def test_capture_failure_drops_pixel_context_and_traceback_locals() -> None:
    binding = _binding()

    def fail(_selected):
        pixels = bytes(binding.width * binding.height * 4)
        raise RuntimeError(len(pixels))

    with pytest.raises(BasePilotCaptureError) as raised:
        WindowService(binding, fail).screenshot()

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    traceback = raised.value.__traceback__
    while traceback is not None:
        assert not any(
            isinstance(value, (bytes, bytearray, np.ndarray)) and len(value) > 1024
            for value in traceback.tb_frame.f_locals.values()
        )
        traceback = traceback.tb_next


def test_production_observation_clears_full_frame_after_recognition(monkeypatch) -> None:
    binding = _binding(1280, 720)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame.fill(123)
    subject = WindowService(
        binding,
        lambda _selected: (1280, 720, bytes(1280 * 720 * 4)),
    )
    monkeypatch.setattr(subject, "screenshot", lambda: frame)
    monkeypatch.setattr(
        NoInputHomeDiagnosticController,
        "_detect_village_type",
        lambda self, selected, **_kwargs: HomeDiagnosticResult.UNKNOWN,
    )

    assert NoInputHomeDiagnosticController(subject).observe() is HomeDiagnosticResult.UNKNOWN
    assert np.count_nonzero(frame) == 0


def test_production_observation_sanitizes_recognition_failure_traceback(monkeypatch) -> None:
    binding = _binding(1280, 720)
    frame = np.ones((720, 1280, 3), dtype=np.uint8)
    subject = WindowService(
        binding,
        lambda _selected: (1280, 720, bytes(1280 * 720 * 4)),
    )
    monkeypatch.setattr(subject, "screenshot", lambda: frame)
    monkeypatch.setattr(
        NoInputHomeDiagnosticController,
        "_detect_village_type",
        lambda self, selected, **_kwargs: (_ for _ in ()).throw(RuntimeError(selected)),
    )

    with pytest.raises(BasePilotRecognitionError) as raised:
        NoInputHomeDiagnosticController(subject).observe()

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert np.count_nonzero(frame) == 0
    traceback = raised.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get("__name__", "").startswith(
            "clash_rush_rebuild"
        ):
            assert not any(
                isinstance(value, np.ndarray)
                for value in traceback.tb_frame.f_locals.values()
            )
        traceback = traceback.tb_next
