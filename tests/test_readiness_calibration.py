from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from clash_rush_rebuild.input_authorization import InputAction
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.readiness_calibration import (
    CalibrationError,
    CalibrationResult,
    LocatedControl,
    PrivateBootstrapLocator,
    ReadinessCalibrationController,
)


BINDING = PlayerBinding(
    ProcessIdentity(100, 200), ProcessIdentity(100, 200),
    10, 11, 1280, 720, "1" * 32,
)
NAMES = {
    "home", "settings_button", "settings", "more_button", "more",
    "export", "more_close", "settings_close", "ordinary_card",
}
EXPECTED = {
    "settings_button": (0.9547, 0.7271),
    "more_button": (0.5110, 0.8340),
    "export": (0.7058, 0.6288),
    "more_close": (0.802, 0.119),
    "settings_close": (0.802, 0.119),
    "ordinary_card": (0.15, 0.45),
}


def _located(name: str) -> LocatedControl:
    x, y = EXPECTED[name]
    return LocatedControl(
        x, y, (max(0, x - 0.01), max(0, y - 0.01), x + 0.01, y + 0.01)
    )


def _frame(code: int) -> np.ndarray:
    return np.random.default_rng(code).integers(
        0, 256, size=(720, 1280, 3), dtype=np.uint8
    )


class Input:
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self.events = events

    def click(self, binding, x, y, *, action):
        assert binding == BINDING
        self.events.append(("click", x, y, action))
        return True

    def drag(self, binding, x0, y0, x1, y1, *, action):
        assert binding == BINDING
        self.events.append(("drag", x0, y0, x1, y1, action))
        return True


def _subject(
    tmp_path: Path, *, frames=None, prove=None, clear=None, locate=None,
    use_default_nonce=False
):
    events: list[tuple[object, ...]] = []
    supplied = iter(frames or [frame for i in range(10, 21) for frame in (_frame(i), _frame(i).copy())])
    states: list[str] = []

    def proof(frame: np.ndarray, state: str) -> bool:
        states.append(state)
        return True if prove is None else prove(frame, state)

    kwargs = dict(
        project_root=tmp_path,
        binding=BINDING,
        capture=supplied.__next__,
        input_port=Input(events),
        prove_state=proof,
        locate_control=locate or (lambda _frame, name: _located(name)),
        live_gate=lambda: True,
        clipboard_read=lambda: "fresh export",
        clipboard_clear=clear or (lambda: None),
        monotonic=lambda: 1.0,
        wait=lambda _seconds: None,
        seal_private=lambda _path, _directory: None,
    )
    if not use_default_nonce:
        kwargs["nonce_factory"] = lambda: "2" * 32
    subject = ReadinessCalibrationController(**kwargs)
    return subject, events, states


def _bootstrap(tmp_path: Path, controls: dict[str, tuple[np.ndarray, list[int]]]) -> None:
    root = tmp_path / "private" / "readiness" / "bootstrap"
    root.mkdir(parents=True)
    entries = {}
    for name, (pixels, roi) in controls.items():
        ok, encoded = cv2.imencode(".png", pixels)
        assert ok
        payload = encoded.tobytes()
        (root / f"{name}.png").write_bytes(payload)
        entries[name] = {
            "file": f"{name}.png",
            "file_sha256": hashlib.sha256(payload).hexdigest(),
            "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
            "roi_ppm": roi,
            "threshold_ppm": 900_000,
        }
    (root / "profile.json").write_text(
        json.dumps({"schema": 1, "controls": entries}, separators=(",", ":")),
        encoding="utf-8",
    )


def test_private_bootstrap_locator_is_digest_sealed_and_missing_controls_fail_closed(
    tmp_path: Path,
) -> None:
    template = _frame(50)[100:140, 200:250].copy()
    _bootstrap(tmp_path, {"launcher": (template, [100_000, 100_000, 600_000, 600_000])})
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[100:140, 200:250] = template
    locator = PrivateBootstrapLocator(tmp_path)

    located = locator(frame, "launcher")

    assert located.center == pytest.approx((224.5 / 1279, 119.5 / 719))
    assert located.bounds == pytest.approx((200 / 1280, 100 / 720, 250 / 1280, 140 / 720))
    with pytest.raises(CalibrationError, match="more_button bootstrap"):
        locator(frame, "more_button")


def test_missing_reviewed_more_control_fails_before_more_click_and_retains_checkpoint(
    tmp_path: Path,
) -> None:
    def locate(_frame: np.ndarray, name: str) -> LocatedControl:
        if name == "more_button":
            raise CalibrationError("more_button bootstrap evidence unavailable")
        return _located(name)

    subject, events, _states = _subject(tmp_path, locate=locate)

    with pytest.raises(CalibrationError, match="more_button bootstrap"):
        subject.run(deadline=60.0)

    assert [event[0] for event in events] == ["click"]
    assert list((tmp_path / "private" / "readiness" / "calibration").rglob("*.png"))
    assert not (tmp_path / "private" / "readiness" / "profile.json").exists()


def test_each_published_template_requires_two_stable_samples(tmp_path: Path) -> None:
    frames = [frame for i in range(10, 21) for frame in (_frame(i), _frame(i).copy())]
    frames[1] = _frame(99)
    subject, events, _states = _subject(tmp_path, frames=frames)

    with pytest.raises(CalibrationError, match="stable samples"):
        subject.run(deadline=60.0)

    assert events == []
    assert not (tmp_path / "private" / "readiness" / "profile.json").exists()


def test_clicks_use_injected_semantic_location_not_donor_coordinate_or_self_crop(
    tmp_path: Path,
) -> None:
    point = (0.955, 0.727)

    def locate(_frame: np.ndarray, name: str) -> LocatedControl:
        control = _located(name)
        return LocatedControl(point[0], point[1], control.bounds) if name == "settings_button" else control

    subject, events, _states = _subject(tmp_path, locate=locate)

    subject.run(deadline=60.0)

    assert events[0][1:3] == point


def test_ordinary_card_publication_uses_reviewed_detected_bounds(tmp_path: Path) -> None:
    frames = [frame for i in range(10, 21) for frame in (_frame(i), _frame(i).copy())]
    subject, _events, _states = _subject(tmp_path, frames=frames)

    subject.run(deadline=60.0)

    pixels = cv2.imread(str(tmp_path / "private" / "readiness" / "ordinary_card.png"))
    assert pixels.shape[:2] == (15, 25)


def test_success_publishes_exact_nine_digest_sealed_private_templates(tmp_path: Path) -> None:
    subject, events, states = _subject(tmp_path)

    result = subject.run(deadline=60.0)

    assert result == CalibrationResult("readiness-s003-" + "2" * 12)
    readiness = tmp_path / "private" / "readiness"
    assert {path.name for path in readiness.glob("*.png")} == {
        f"{name}.png" for name in NAMES
    }
    manifest = json.loads((readiness / "profile.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema", "profile_id", "templates"}
    assert manifest["schema"] == 1
    assert manifest["profile_id"] == result.profile_id
    assert set(manifest["templates"]) == NAMES
    for name, entry in manifest["templates"].items():
        assert set(entry) == {
            "file", "file_sha256", "pixel_sha256", "threshold_ppm", "roi_ppm"
        }
        payload = (readiness / entry["file"]).read_bytes()
        pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        assert hashlib.sha256(payload).hexdigest() == entry["file_sha256"]
        assert hashlib.sha256(pixels.tobytes()).hexdigest() == entry["pixel_sha256"]
        assert max(pixels.shape[:2]) <= 320
    assert not (readiness / "calibration" / ("2" * 32)).exists()
    assert states == [state for state in (
        "home", "settings", "more", "more", "more", "more",
        "more", "settings", "home",
    ) for _ in range(2)]
    assert [event[0] for event in events] == [
        "click", "click", "drag", "drag", "drag", "click", "click", "click"
    ]
    assert all(event[-1] is InputAction.ACCOUNT_EXPORT_NAVIGATION for event in events)


def test_default_nonce_factory_produces_one_valid_private_run_id(tmp_path: Path) -> None:
    subject, _events, _states = _subject(tmp_path, use_default_nonce=True)

    result = subject.run(deadline=60.0)

    assert result.profile_id.startswith("readiness-s003-")


def test_failed_positive_state_gate_publishes_no_profile_or_templates(tmp_path: Path) -> None:
    subject, events, _states = _subject(
        tmp_path,
        prove=lambda _frame, state: state != "settings",
    )

    with pytest.raises(CalibrationError, match="settings evidence"):
        subject.run(deadline=60.0)

    readiness = tmp_path / "private" / "readiness"
    assert not (readiness / "profile.json").exists()
    assert list(readiness.glob("*.png")) == []
    assert len(events) == 1


def test_export_must_replace_the_cleared_clipboard_before_profile_publication(
    tmp_path: Path,
) -> None:
    subject, events, _states = _subject(tmp_path)
    subject._clipboard_read = lambda: ""

    with pytest.raises(CalibrationError, match="export proof"):
        subject.run(deadline=60.0)

    readiness = tmp_path / "private" / "readiness"
    assert not (readiness / "profile.json").exists()
    assert list(readiness.glob("*.png")) == []
    assert [event[0] for event in events] == [
        "click", "click", "drag", "drag", "drag", "click"
    ]


def test_failure_always_clears_clipboard_and_never_emits_attack_action(tmp_path: Path) -> None:
    clears: list[str] = []
    frames = [_frame(10), _frame(11), _frame(12)]
    subject, events, _states = _subject(
        tmp_path,
        frames=frames,
        clear=lambda: clears.append("clear"),
    )

    with pytest.raises(CalibrationError):
        subject.run(deadline=60.0)

    assert clears == ["clear", "clear"]
    assert all(
        event[-1] not in {InputAction.ATTACK_NAVIGATION, InputAction.TROOP_DEPLOYMENT}
        for event in events
    )


def test_final_clipboard_cleanup_failure_prevents_profile_publication(tmp_path: Path) -> None:
    clears = 0

    def clear() -> None:
        nonlocal clears
        clears += 1
        if clears == 3:
            raise OSError("synthetic private clipboard detail")

    subject, _events, _states = _subject(tmp_path, clear=clear)

    with pytest.raises(CalibrationError, match="clipboard cleanup failed"):
        subject.run(deadline=60.0)

    readiness = tmp_path / "private" / "readiness"
    assert not (readiness / "profile.json").exists()
    assert list(readiness.glob("*.png")) == []


def test_publication_checks_deadline_and_removes_all_preprofile_assets(
    tmp_path: Path,
) -> None:
    subject, _events, _states = _subject(tmp_path)
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 1.0 if calls <= 12 else 60.0

    subject._monotonic = clock
    crops = {
        name: _frame(index)[0:20, 0:20].copy()
        for index, name in enumerate(NAMES)
    }

    with pytest.raises(CalibrationError, match="deadline"):
        subject._publish(crops, "2" * 32, 60.0)

    readiness = tmp_path / "private" / "readiness"
    assert not (readiness / "profile.json").exists()
    assert list(readiness.glob("*.png")) == []
    assert list(readiness.glob(".publish-*")) == []
    assert list(readiness.glob(".profile-*")) == []


def test_project_path_must_resolve_and_artifacts_cannot_escape_private_readiness(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(CalibrationError, match="private readiness path"):
        ReadinessCalibrationController(
            project_root=missing,
            binding=BINDING,
            capture=lambda: _frame(1),
            input_port=Input([]),
            prove_state=lambda _frame, _state: True,
            locate_control=lambda _frame, name: _located(name),
            live_gate=lambda: True,
            clipboard_read=lambda: "fresh export",
            clipboard_clear=lambda: None,
            monotonic=lambda: 1.0,
            wait=lambda _seconds: None,
        )
