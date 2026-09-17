"""Synthetic one-shot source diagnostics; no capture transport or private assets."""
from __future__ import annotations

import json

import pytest

from clash_rush_rebuild import home_source_diagnostic as source
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.mvp_local_runtime import BgraGameplayRecognizer, RuntimeSafetyError


BINDING = PlayerBinding(ProcessIdentity(100, 200), ProcessIdentity(100, 200),
                        10, 11, 1501, 805, "0" * 32)


def frame(color=(80, 20, 0), *, home=False, bars=None, alpha=0):
    width, height = BINDING.width, BINDING.height
    pixels = bytearray(bytes((*color, alpha)) * (width * height))
    if bars is not None:
        for y in range(height):
            for x in range(width):
                if ((bars == "horizontal" and (y < height // 10 or y >= height * 9 // 10))
                        or (bars == "vertical" and (x < width // 10 or x >= width * 9 // 10))):
                    start = (y * width + x) * 4
                    pixels[start:start + 4] = bytes((0, 0, 0, alpha))
    if home:
        for y in range(int(height * .90), int(height * .97)):
            for x in range(int(width * .035), int(width * .085)):
                start = (y * width + x) * 4
                pixels[start:start + 4] = bytes((0, 100, 220, alpha))
    return width, height, bytes(pixels)


def observe(value, *, valid=True):
    events = []
    def revalidate(binding):
        assert binding is BINDING
        events.append("validate")
        return valid
    def capture(binding):
        assert binding is BINDING
        events.append("capture")
        return value
    result = source.observe_source(BINDING, capture, revalidate)
    assert events == ["validate", "capture", "validate"]
    return json.loads(result)


def test_recovered_r8_dark_chromatic_is_not_mislabelled_loading_or_home():
    result = observe(frame())
    assert result["schema"] == 2
    assert result["readiness"] == "UNVERIFIED"
    assert result["geometry"] == "DIMENSIONS_MATCH"
    assert result["content"] == "DARK_CHROMATIC_SAMPLED"
    assert (result["width"], result["height"], result["roi_pixels"]) == (1501, 805, 4200)
    assert (result["hue_pixels"], result["saturation_pixels"],
            result["value_pixels"], result["orange_pixels"]) == (0, 4200, 0, 0)
    assert result["reason"] == "COLOR_ABSENT"
    assert result["sample_pixels"] == 576
    assert result["sample_bright"] == 0
    assert result["sample_chromatic"] == 576


@pytest.mark.parametrize("alpha", [0, 255])
def test_valid_home_preserves_existing_cue_and_alpha_is_ignored(alpha):
    value = frame((60, 180, 60), home=True, alpha=alpha)
    result = observe(value)
    assert result["readiness"] == "HOME_CUE_PRESENT"
    assert result["reason"] == "HOME_POSITIVE"
    assert result["orange_pixels"] == 4200
    assert result["geometry"] == "DIMENSIONS_MATCH"
    recognizer = BgraGameplayRecognizer(BINDING, lambda _: value, account_verified=False)
    assert recognizer.recognize(BINDING, "synthetic").home is True


@pytest.mark.parametrize("color,content", [
    ((0, 0, 0), "BLACK_SAMPLED"),
    ((8, 8, 8), "BLACK_SAMPLED"),
    ((9, 9, 9), "LOW_CHROMA_SAMPLED"),
    ((80, 80, 80), "LOW_CHROMA_SAMPLED"),
    ((200, 200, 200), "LOW_CHROMA_SAMPLED"),
    ((255, 255, 255), "LOW_CHROMA_SAMPLED"),
    ((180, 90, 10), "MIXED_SAMPLED"),  # synthetic loading-colored scene, not HOME
])
def test_black_gray_and_loading_remain_unverified(color, content):
    result = observe(frame(color))
    assert result["content"] == content
    assert result["readiness"] == "UNVERIFIED"
    assert result["geometry"] == "DIMENSIONS_MATCH"


@pytest.mark.parametrize("bars", ["horizontal", "vertical"])
def test_letterbox_is_suspect_not_automatic_roi_relocation(bars):
    result = observe(frame((0, 100, 220), bars=bars))
    assert result["geometry"] == "LETTERBOX_SUSPECT"
    assert result["readiness"] == "UNVERIFIED"
    assert result["orange_pixels"] == 0
    assert result["center_bright"] == result["center_pixels"]


def test_letterbox_suspicion_takes_precedence_even_over_home_cue():
    result = observe(frame((60, 180, 60), home=True, bars="horizontal"))
    assert result["reason"] == "HOME_POSITIVE"
    assert result["geometry"] == "LETTERBOX_SUSPECT"
    assert result["readiness"] == "UNVERIFIED"


def test_channel_swap_does_not_repair_dark_roi_or_authorize_bright_blue():
    assert observe(frame((0, 20, 80)))["readiness"] == "UNVERIFIED"
    assert observe(frame((220, 100, 0)))["readiness"] == "UNVERIFIED"
    assert observe(frame((0, 100, 220)))["readiness"] == "HOME_CUE_PRESENT"


@pytest.mark.parametrize("value,reason", [
    ((1500, 805, b"synthetic-private"), "SOURCE_GEOMETRY_MISMATCH"),
    ((True, 805, b"synthetic-private"), "SOURCE_GEOMETRY_MISMATCH"),
    ((1501, 805, b"synthetic-private"), "SOURCE_BYTES_INVALID"),
    ((1501, 805, bytearray(b"synthetic-private")), "SOURCE_BYTES_INVALID"),
    ([1501, 805, b"synthetic-private"], "SOURCE_SHAPE_INVALID"),
])
def test_invalid_frame_closed_errors_and_cleanup(value, reason):
    with pytest.raises(RuntimeSafetyError) as caught:
        source.observe_source(BINDING, lambda _: value, lambda _: True)
    assert str(caught.value) == reason
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert_clean_traceback(caught.value)


def assert_clean_traceback(error):
    trace = error.__traceback__
    while trace:
        if trace.tb_frame.f_globals.get("__name__") in {
            source.__name__, "clash_rush_rebuild.mvp_local_runtime",
        }:
            values = trace.tb_frame.f_locals
            for name in ("frame", "pixels", "capture_owned", "revalidate", "binding"):
                assert values.get(name) is None
            assert not any(type(v) in (bytes, bytearray) for v in values.values())
        trace = trace.tb_next


@pytest.mark.parametrize("invalid", [False, None, 1, "true", object()])
@pytest.mark.parametrize("stage", [0, 1])
def test_revalidation_is_exact_and_each_observation_fresh(invalid, stage):
    events = []
    values = iter([invalid] if stage == 0 else [True, invalid])
    def capture(_):
        events.append("capture")
        return frame(home=True)
    with pytest.raises(RuntimeSafetyError) as caught:
        source.observe_source(BINDING, capture, lambda _: next(values))
    assert str(caught.value) == "SOURCE_BINDING_UNVERIFIED"
    assert events == ([] if stage == 0 else ["capture"])
    assert_clean_traceback(caught.value)


def test_callback_failure_clears_retained_traceback_and_private_message():
    errors = []
    def capture(_):
        pixels = frame()[2]
        try:
            raise ValueError("synthetic-private")
        except ValueError as inner:
            outer = ExceptionGroup("synthetic-private", [inner])
            errors.extend([inner, outer])
            raise outer from inner
    with pytest.raises(RuntimeSafetyError) as caught:
        source.observe_source(BINDING, capture, lambda _: True)
    assert str(caught.value) == "SOURCE_CAPTURE_FAILED"
    assert caught.value.__context__ is None
    assert_clean_traceback(caught.value)
    for error in errors:
        trace = error.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_name == "capture":
                assert trace.tb_frame.f_locals == {}
            trace = trace.tb_next


def test_reducer_failure_clears_frame_and_emits_no_original_exception(monkeypatch):
    def fail(frame):
        raise ValueError("synthetic-private")
    monkeypatch.setattr(source, "_sample_content", fail)
    with pytest.raises(RuntimeSafetyError) as caught:
        source.observe_source(BINDING, lambda _: frame(), lambda _: True)
    assert str(caught.value) == "SOURCE_REDUCTION_FAILED"
    assert caught.value.__context__ is None
    assert_clean_traceback(caught.value)


def test_only_closed_scalar_keys_and_values_escape():
    result = observe(frame(home=True))
    assert set(result) == {
        "schema", "reason", "width", "height", "left", "top", "right", "bottom",
        "roi_pixels", "hue_pixels", "saturation_pixels", "value_pixels", "orange_pixels",
        "geometry", "readiness", "content", "sample_pixels", "sample_black",
        "sample_bright", "sample_chromatic", "top_dark", "bottom_dark", "left_dark",
        "right_dark", "center_bright", "center_pixels",
    }
    for key, value in result.items():
        assert type(value) in (str, int)
        if type(value) is int:
            assert 0 <= value <= 64 * 1024 * 1024
    assert result["center_pixels"] == 144


def test_no_previous_observation_or_capture_is_cached():
    values = iter([frame(home=True), frame()])
    events = []
    def validate(_):
        events.append("validate")
        return True
    def capture(_):
        events.append("capture")
        return next(values)
    first = source.observe_source(BINDING, capture, validate)
    second = source.observe_source(BINDING, capture, validate)
    assert json.loads(first)["readiness"] == "HOME_CUE_PRESENT"
    assert json.loads(second)["readiness"] == "UNVERIFIED"
    assert events == ["validate", "capture", "validate"] * 2


def test_same_r8_roi_counts_can_have_different_surrounding_content():
    width, height, pixels = frame()
    mixed = bytearray(bytes((0, 100, 220, 0)) * (width * height))
    for y in range(724, 780):
        start, end = (y * width + 52) * 4, (y * width + 127) * 4
        mixed[start:end] = pixels[start:end]
    dark = observe((width, height, pixels))
    bright = observe((width, height, bytes(mixed)))
    for key in ("roi_pixels", "hue_pixels", "saturation_pixels", "value_pixels", "orange_pixels"):
        assert dark[key] == bright[key]
    assert dark["sample_bright"] == 0
    assert bright["sample_bright"] > 0
    assert bright["readiness"] == dark["readiness"] == "UNVERIFIED"
    assert bright["content"] == "MIXED_SAMPLED"


@pytest.mark.parametrize("center_count,suspect", [(72, False), (73, True)])
def test_bar_suspicion_requires_strict_majority_bright_center(center_count, suspect):
    width, height = BINDING.width, BINDING.height
    pixels = bytearray(width * height * 4)
    count = 0
    for row in range(4, 13):
        for column in range(8, 24):
            if count < center_count:
                y, x = (2 * row + 1) * height // 36, (2 * column + 1) * width // 64
                start = (y * width + x) * 4
                pixels[start:start + 4] = bytes((0, 100, 220, 0))
                count += 1
    result = observe((width, height, bytes(pixels)))
    assert (result["geometry"] == "LETTERBOX_SUSPECT") is suspect
    assert result["center_bright"] == center_count
    assert result["readiness"] == "UNVERIFIED"


@pytest.mark.parametrize("color,bright", [((0, 20, 119), 0), ((0, 20, 120), 576)])
def test_sample_brightness_boundary_is_unchanged_hsv_value(color, bright):
    result = observe(frame(color))
    assert result["sample_bright"] == bright


@pytest.mark.parametrize("binding", [None, object(), True])
def test_nonexact_binding_rejected_before_ports(binding):
    def forbidden(_):
        pytest.fail("port must not be invoked")
    with pytest.raises(RuntimeSafetyError, match="^SOURCE_BINDING_INVALID$"):
        source.observe_source(binding, forbidden, forbidden)


@pytest.mark.parametrize("field,value", [("width", True), ("height", 359), ("width", 1000000)])
def test_invalid_mutated_binding_rejected_before_capture(field, value):
    binding = PlayerBinding(BINDING.identity, BINDING.render_identity,
                            10, 11, 1501, 805, "0" * 32)
    object.__setattr__(binding, field, value)
    events = []
    with pytest.raises(RuntimeSafetyError, match="^SOURCE_BINDING_INVALID$"):
        source.observe_source(binding, lambda _: events.append("capture"), lambda _: True)
    assert events == []


@pytest.mark.parametrize("stage", ["before", "capture", "after"])
def test_callback_binding_alias_mutation_invalidates_report(stage):
    binding = PlayerBinding(BINDING.identity, BINDING.render_identity,
                            10, 11, 1501, 805, "0" * 32)
    validations = 0
    def mutate():
        object.__setattr__(binding, "render_hwnd", 12)
    def validate(_):
        nonlocal validations
        validations += 1
        if (stage == "before" and validations == 1) or (stage == "after" and validations == 2):
            mutate()
        return True
    def capture(_):
        if stage == "capture":
            mutate()
        return frame(home=True)
    with pytest.raises(RuntimeSafetyError, match="^SOURCE_BINDING_UNVERIFIED$") as caught:
        source.observe_source(binding, capture, validate)
    assert_clean_traceback(caught.value)


def test_owned_capture_timeout_has_no_retry_or_diagnostic_fallback():
    calls = []
    def capture(_):
        calls.append("capture")
        pixels = frame()[2]
        raise TimeoutError("synthetic-private")
    with pytest.raises(RuntimeSafetyError, match="^SOURCE_CAPTURE_FAILED$") as caught:
        source.observe_source(BINDING, capture, lambda _: True)
    assert calls == ["capture"]
    assert caught.value.__context__ is None
    assert_clean_traceback(caught.value)


@pytest.mark.parametrize("which", ["identity", "render_identity"])
def test_nested_identity_alias_mutation_invalidates_report(which):
    binding = PlayerBinding(ProcessIdentity(100, 200), ProcessIdentity(100, 200),
                            10, 11, 1501, 805, "0" * 32)
    def capture(_):
        object.__setattr__(getattr(binding, which), "creation_time_100ns", 201)
        return frame(home=True)
    with pytest.raises(RuntimeSafetyError, match="^SOURCE_BINDING_UNVERIFIED$"):
        source.observe_source(binding, capture, lambda _: True)


def test_postvalidation_runs_with_no_owned_frame_reference():
    import inspect
    validations = 0
    def validate(_):
        nonlocal validations
        validations += 1
        if validations == 2:
            caller = inspect.currentframe().f_back
            assert caller.f_locals["frame"] is None
        return True
    source.observe_source(BINDING, lambda _: frame(home=True), validate)
    assert validations == 2


def test_single_capture_has_bounded_home_and_grid_work(monkeypatch):
    original = BgraGameplayRecognizer._hsv
    count = 0
    def hsv(*channels):
        nonlocal count
        count += 1
        return original(*channels)
    monkeypatch.setattr(BgraGameplayRecognizer, "_hsv", hsv)
    observe(frame())
    assert count == 4200 + 576


def test_inert_module_has_no_live_composition_or_io_imports():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(source.__file__).read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"__future__", "collections.abc", "lifecycle", "mvp_local_runtime"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
