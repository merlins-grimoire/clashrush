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
from clash_rush_rebuild.input_authorization import (
    InputAction,
    InputAuthorizationError,
    InputPurpose,
)
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
        "16_9/attack.png": "08f218ae58050bb45bc74adc0e599221d85aeec307025cd0c8a49c71a42b535a",
        "16_10/builder.png": "e37551f7c08fde1781ff977adcb0b7cb4041a913a511fc798cbae0cc633fa914",
        "16_10/gbuilder.png": "28a1a648464275e50704275b924904b3e058aa28dc6e9cbde1d57c1e5cd68412",
        "16_10/mbuilder.png": "aafb4af0a51081ba324bc3901a299524fd5020ed8dae60352c72113fd06cefd4",
        "16_10/attack.png": "f15446a19fbbe76771dd4568b5db370cfbba0ec86c1a73ec5198c0869c64328f",
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


def test_geometry_adapts_unsupported_live_aspect_without_magic_tolerance() -> None:
    unsupported_aspect = np.zeros((1000, 1728, 3), dtype=np.uint8)

    geometry = BasePilotGeometry.from_frame(unsupported_aspect)

    assert geometry.aspect_key == "16_10"
    assert geometry.width == 1728
    assert geometry.height == 1000
    assert geometry.adaptation_reason == "UNSUPPORTED_GEOMETRY"


def test_unsupported_live_aspect_retains_positive_home_evidence() -> None:
    frame = cv2.imread(str(_FIXTURE), cv2.IMREAD_COLOR)
    assert frame is not None
    unsupported_aspect = cv2.resize(frame, (1728, 1000))

    assert (
        NoInputHomeDiagnosticController.detect_frame(unsupported_aspect)
        is HomeDiagnosticResult.HOME
    )


def test_geometry_still_rejects_binding_disagreement() -> None:
    supported = np.zeros((720, 1280, 3), dtype=np.uint8)
    with pytest.raises(BasePilotRecognitionError, match="binding"):
        BasePilotGeometry.from_frame(supported, expected_size=(1281, 720))


def test_template_resolution_is_closed_and_missing_asset_fails_closed(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    vision = VisionService(BasePilotGeometry.from_frame(frame))
    with pytest.raises(BasePilotRecognitionError, match="allowlisted"):
        vision.find_template(frame, "shop.png")

    class MissingResource:
        def __truediv__(self, _part):
            return self

        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(vision_module, "files", lambda _package: MissingResource())
    with pytest.raises(BasePilotRecognitionError, match="unavailable"):
        vision.find_template(frame, "builder.png")


def test_donor_attack_text_survives_current_ui_scale() -> None:
    root = Path(__file__).parents[1]
    template = cv2.imread(
        str(root / "src" / "clash_rush_rebuild" / "assets" / "basepilot_templates" / "16_10" / "attack.png"),
        cv2.IMREAD_COLOR,
    )
    assert template is not None
    scaled = cv2.resize(template, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    frame = np.zeros((1050, 1920, 3), dtype=np.uint8)
    frame[900 : 900 + scaled.shape[0], 30 : 30 + scaled.shape[1]] = scaled

    assert NoInputHomeDiagnosticController.detect_home_attack(frame) is True


def test_donor_attack_text_rejects_non_home_frame() -> None:
    frame = np.zeros((1050, 1920, 3), dtype=np.uint8)

    assert NoInputHomeDiagnosticController.detect_home_attack(frame) is False


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


def test_production_diagnostic_owns_an_explicit_empty_input_capability() -> None:
    binding = _binding(1280, 720)
    window = WindowService(
        binding,
        lambda _selected: (1280, 720, bytes(1280 * 720 * 4)),
    )

    subject = NoInputHomeDiagnosticController(window)

    assert subject._input_authorization.purpose is InputPurpose.NO_INPUT_DIAGNOSTIC
    for action in InputAction:
        with pytest.raises(InputAuthorizationError, match="not authorized"):
            subject._input_authorization.require(action)


def test_production_observation_preserves_sanitized_capture_failure_stage(
    monkeypatch,
) -> None:
    binding = _binding(1280, 720)
    subject = WindowService(
        binding,
        lambda _selected: (1280, 720, bytes(1280 * 720 * 4)),
    )
    monkeypatch.setattr(
        subject,
        "screenshot",
        lambda: (_ for _ in ()).throw(BasePilotCaptureError("private capture detail")),
    )

    with pytest.raises(BasePilotCaptureError) as raised:
        NoInputHomeDiagnosticController(subject).observe()

    assert str(raised.value) == "Home capture failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


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

    assert str(raised.value) == "Home recognition failed"
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


def _controller_for_frames(
    monkeypatch,
    frames: list[np.ndarray],
) -> NoInputHomeDiagnosticController:
    binding = _binding(frames[0].shape[1], frames[0].shape[0])
    window = WindowService(
        binding,
        lambda _selected: (
            binding.width,
            binding.height,
            bytes(binding.width * binding.height * 4),
        ),
    )
    queue = iter(frames)
    monkeypatch.setattr(window, "screenshot", lambda: next(queue))
    return NoInputHomeDiagnosticController(window)


def _advancing_clock(step: float = 1.0):
    current = -step

    def now() -> float:
        nonlocal current
        current += step
        return current

    return now


def test_popup_loading_no_match_polls_then_settles_to_unknown(monkeypatch) -> None:
    frames = [np.zeros((720, 1280, 3), dtype=np.uint8) for _ in range(3)]
    subject = _controller_for_frames(monkeypatch, frames)

    result = subject.wait_for_readiness(
        timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=_advancing_clock(),
        wait=lambda _seconds: None,
    )

    assert result is HomeDiagnosticResult.UNKNOWN
    assert subject.observation_reasons == ("NO_MATCH", "NO_MATCH", "TIMEOUT")


def test_delayed_home_readiness_uses_later_settled_frame(monkeypatch) -> None:
    home = cv2.imread(str(_FIXTURE), cv2.IMREAD_COLOR)
    assert home is not None
    loading = np.zeros_like(home)
    subject = _controller_for_frames(monkeypatch, [loading, home])

    result = subject.wait_for_readiness(
        timeout_seconds=5,
        poll_interval_seconds=1,
        monotonic=_advancing_clock(),
        wait=lambda _seconds: None,
    )

    assert result is HomeDiagnosticResult.HOME
    assert subject.observation_reasons == ("NO_MATCH",)
    assert np.count_nonzero(loading) == 0
    assert np.count_nonzero(home) == 0


def test_unavailable_template_is_sanitized_unknown_and_preserved(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    class MissingResource:
        def __truediv__(self, _part):
            return self

        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(vision_module, "files", lambda _package: MissingResource())
    subject = _controller_for_frames(monkeypatch, [frame])

    assert subject.observe() is HomeDiagnosticResult.UNKNOWN
    assert subject.observation_reasons == ("TEMPLATE_UNAVAILABLE",)


def test_malformed_template_is_sanitized_unknown_and_preserved(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    class MalformedResource:
        def __truediv__(self, _part):
            return self

        def is_file(self) -> bool:
            return True

        def read_bytes(self) -> bytes:
            return b"not-an-image"

    monkeypatch.setattr(vision_module, "files", lambda _package: MalformedResource())
    subject = _controller_for_frames(monkeypatch, [frame])

    assert subject.observe() is HomeDiagnosticResult.UNKNOWN
    assert subject.observation_reasons == ("TEMPLATE_MALFORMED",)


def test_opencv_failure_is_sanitized_unknown_and_preserved(monkeypatch) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    monkeypatch.setattr(
        vision_module.cv2,
        "matchTemplate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private pixels")),
    )
    subject = _controller_for_frames(monkeypatch, [frame])

    assert subject.observe() is HomeDiagnosticResult.UNKNOWN
    assert subject.observation_reasons == ("OPENCV_FAILURE",)


def test_timeout_clears_every_captured_frame(monkeypatch) -> None:
    frames = [np.full((720, 1280, 3), 17, dtype=np.uint8) for _ in range(2)]
    subject = _controller_for_frames(monkeypatch, frames)
    monkeypatch.setattr(
        subject,
        "_detect_village_type",
        lambda _frame, **_kwargs: HomeDiagnosticResult.UNKNOWN,
    )

    assert subject.wait_for_readiness(
        timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=_advancing_clock(),
        wait=lambda _seconds: None,
    ) is HomeDiagnosticResult.UNKNOWN
    assert all(np.count_nonzero(frame) == 0 for frame in frames)
