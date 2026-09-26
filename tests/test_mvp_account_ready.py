from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from clash_rush_rebuild.input_authorization import InputAction
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.mvp_account_ready import (
    AccountReady,
    AccountReadinessController,
    AccountReadinessError,
    PrivateBootstrapLocator,
    ReadinessVisualProfile,
    TemplateSpec,
    load_private_visual_profile,
)


BINDING = PlayerBinding(
    ProcessIdentity(100, 200),
    ProcessIdentity(100, 200),
    10,
    11,
    640,
    360,
    "1" * 32,
)
TAG = "#SYNTHETIC"
NOW = datetime.fromtimestamp(1_788_894_243, UTC)


def _template(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(7, 7, 3), dtype=np.uint8)


def _frame(*entries: tuple[np.ndarray, int, int]) -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    for template, x, y in entries:
        height, width = template.shape[:2]
        frame[y : y + height, x : x + width] = template
    return frame


class Input:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def click(self, binding, x, y, *, action):
        assert binding == BINDING
        self.events.append(("click", round(x, 3), round(y, 3), action))
        return True

    def drag(self, binding, x0, y0, x1, y1, *, action):
        assert binding == BINDING
        self.events.append(("drag", x0, y0, x1, y1, action))
        return True


class StartupInput:
    def __init__(self) -> None:
        self.events: list[tuple[float, float]] = []

    def click_continue(self, binding, x, y):
        assert binding == BINDING
        self.events.append((x, y))
        return True


def _bootstrap_locator(tmp_path: Path, template: np.ndarray) -> PrivateBootstrapLocator:
    root = tmp_path / "private" / "readiness" / "bootstrap"
    root.mkdir(parents=True)
    ok, encoded = cv2.imencode(".png", template)
    assert ok
    payload = encoded.tobytes()
    (root / "launcher.png").write_bytes(payload)
    (root / "profile.json").write_text(json.dumps({
        "schema": 1,
        "controls": {
            "launcher": {
                "file": "launcher.png",
                "file_sha256": hashlib.sha256(payload).hexdigest(),
                "pixel_sha256": hashlib.sha256(template.tobytes()).hexdigest(),
                "roi_ppm": [0, 0, 1_000_000, 1_000_000],
                "threshold_ppm": 990_000,
            }
        },
    }), encoding="utf-8")
    return PrivateBootstrapLocator(tmp_path)


def test_private_bootstrap_locator_loads_and_locates_digest_sealed_launcher(
    tmp_path: Path,
) -> None:
    launcher = _template(91)
    locator = _bootstrap_locator(tmp_path, launcher)
    frame = _frame((launcher, 120, 80))

    located = locator(frame, "launcher")

    assert located.x == pytest.approx((120 + 3) / 639)
    assert located.y == pytest.approx((80 + 3) / 359)
    (tmp_path / "private" / "readiness" / "bootstrap" / "launcher.png").write_bytes(
        b"changed"
    )
    with pytest.raises(AccountReadinessError, match="bootstrap evidence unavailable"):
        PrivateBootstrapLocator(tmp_path)


def test_private_bootstrap_locator_rejects_a_junction_escape(tmp_path: Path) -> None:
    outside_project = tmp_path / "outside-project"
    _bootstrap_locator(outside_project, _template(95))
    outside = outside_project / "private" / "readiness" / "bootstrap"
    readiness = tmp_path / "private" / "readiness"
    readiness.mkdir(parents=True)
    target = readiness / "bootstrap"
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks unavailable")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions unavailable")

    with pytest.raises(AccountReadinessError, match="path escaped"):
        PrivateBootstrapLocator(tmp_path)


def test_complete_real_matcher_trace_emits_account_ready_without_attack_requests() -> None:
    templates = {name: _template(index) for index, name in enumerate((
        "home", "settings_button", "settings", "more_button", "more",
        "export", "more_close", "settings_close",
    ), start=1)}
    profile = ReadinessVisualProfile(
        profile_id="ordinary-card-v1",
        specs=tuple(
            TemplateSpec(name, template, hashlib.sha256(template.tobytes()).hexdigest(), 0.99, (0.0, 0.0, 1.0, 1.0))
            for name, template in templates.items()
        ),
    )
    frames = iter([
        _frame((templates["home"], 4, 4)),
        _frame((templates["home"], 4, 4), (templates["settings_button"], 80, 70)),
        _frame((templates["settings"], 4, 4), (templates["more_button"], 40, 70)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4), (templates["export"], 60, 55)),
        _frame((templates["more"], 4, 4), (templates["more_close"], 75, 5)),
        _frame((templates["settings"], 4, 4), (templates["settings_close"], 75, 5)),
        _frame((templates["home"], 4, 4)),
    ])
    input_port = Input()
    startup_input = StartupInput()
    reads = iter([
        "old clipboard",
        json.dumps({"tag": TAG, "timestamp": 1_788_894_243, "buildings": []}),
    ])
    clears: list[str] = []
    waits: list[float] = []
    controller = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=lambda: next(frames),
        profile=profile,
        startup_input=startup_input,
        export_input=input_port,
        find_continue=lambda _frame: None,
        live_gate=lambda: True,
        clipboard_read=lambda: next(reads),
        clipboard_clear=lambda: clears.append("clear"),
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=lambda: 1.0,
        wait=waits.append,
    )

    result = controller.run(deadline=10.0)

    assert result == AccountReady("2" * 32, "ordinary-card-v1")
    assert startup_input.events == []
    assert [event[-1] for event in input_port.events] == [
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
        InputAction.ACCOUNT_EXPORT_NAVIGATION,
    ]
    assert not any(
        event[-1] in {InputAction.ATTACK_NAVIGATION, InputAction.TROOP_DEPLOYMENT}
        for event in input_port.events
    )
    assert clears == ["clear", "clear", "clear"]
    assert waits == [0.75, 0.75, 0.15, 0.15, 0.15, 0.75, 0.75, 0.75]
    assert input_port.events[5][1:3] == (round(63.5 / 640, 3), round(58.5 / 360, 3))


def test_private_profile_loader_freezes_exact_manifest_and_asset_bytes(tmp_path: Path) -> None:
    private = tmp_path / "private" / "readiness"
    private.mkdir(parents=True)
    entries = {}
    for index, name in enumerate(sorted({
        "home", "settings_button", "settings", "more_button", "more",
        "export", "more_close", "settings_close",
    }), start=1):
        template = _template(index)
        ok, encoded = cv2.imencode(".png", template)
        assert ok
        asset = private / f"{name}.png"
        asset.write_bytes(encoded.tobytes())
        entries[name] = {
            "file": asset.name,
            "file_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            "pixel_sha256": hashlib.sha256(template.tobytes()).hexdigest(),
            "threshold_ppm": 990_000,
            "roi_ppm": [0, 0, 1_000_000, 1_000_000],
        }
    manifest = private / "profile.json"
    manifest.write_text(json.dumps({
        "schema": 1,
        "profile_id": "ordinary-card-v1",
        "templates": entries,
    }), encoding="utf-8")

    profile = load_private_visual_profile(tmp_path, manifest)

    assert profile.profile_id == "ordinary-card-v1"
    assert profile.spec("home").template.flags.writeable is False

    (private / "home.png").write_bytes(b"changed")
    try:
        load_private_visual_profile(tmp_path, manifest)
    except Exception as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("changed asset must be rejected")


def _profile() -> tuple[ReadinessVisualProfile, dict[str, np.ndarray]]:
    names = (
        "home", "settings_button", "settings", "more_button", "more",
        "export", "more_close", "settings_close",
    )
    templates = {name: _template(index + 20) for index, name in enumerate(names)}
    return ReadinessVisualProfile(
        "ordinary-card-v1",
        tuple(TemplateSpec(
            name, template, hashlib.sha256(template.tobytes()).hexdigest(),
            0.99, (0.0, 0.0, 1.0, 1.0),
        ) for name, template in templates.items()),
    ), templates


def _launcher_controller(
    frames: list[np.ndarray], locator: PrivateBootstrapLocator
) -> tuple[AccountReadinessController, Input]:
    profile, _templates = _profile()
    inputs = Input()
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=iter(frames).__next__,
        profile=profile,
        startup_input=StartupInput(),
        export_input=inputs,
        find_continue=lambda _frame: None,
        locate_bootstrap=locator,
        live_gate=lambda: True,
        clipboard_read=lambda: "",
        clipboard_clear=lambda: None,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=lambda: 1.0,
        wait=lambda _seconds: None,
    )
    return subject, inputs


def test_three_fresh_stable_launcher_frames_click_once_then_reach_home(
    tmp_path: Path,
) -> None:
    profile, templates = _profile()
    launcher = _template(92)
    locator = _bootstrap_locator(tmp_path, launcher)
    launcher_frame = _frame((launcher, 120, 80))
    subject, inputs = _launcher_controller(
        [launcher_frame.copy() for _ in range(3)]
        + [_frame(), _frame((templates["home"], 4, 4))],
        locator,
    )
    subject._profile = profile

    subject._reach_home(10.0)

    assert [event[-1] for event in inputs.events] == [
        InputAction.STARTUP_LAUNCH_GAME
    ]


@pytest.mark.parametrize("mode", ["unstable", "no-match"])
def test_unstable_or_unmatched_launcher_emits_no_click(
    tmp_path: Path, mode: str,
) -> None:
    launcher = _template(93)
    locator = _bootstrap_locator(tmp_path, launcher)
    first = _frame((launcher, 120, 80))
    second = first.copy()
    if mode == "unstable":
        second[:, :20] = 255
    else:
        first.fill(0)
    subject, inputs = _launcher_controller([first, second], locator)

    with pytest.raises(AccountReadinessError):
        subject._reach_home(10.0)

    assert inputs.events == []


def test_repeated_launcher_after_one_click_fails_without_second_click(
    tmp_path: Path,
) -> None:
    launcher = _template(94)
    locator = _bootstrap_locator(tmp_path, launcher)
    launcher_frame = _frame((launcher, 120, 80))
    subject, inputs = _launcher_controller(
        [launcher_frame.copy() for _ in range(4)], locator
    )

    with pytest.raises(AccountReadinessError, match="launcher cap exhausted"):
        subject._reach_home(10.0)

    assert [event[-1] for event in inputs.events] == [
        InputAction.STARTUP_LAUNCH_GAME
    ]


def _successful_frames(templates: dict[str, np.ndarray]):
    return [
        _frame((templates["home"], 4, 4)),
        _frame((templates["home"], 4, 4), (templates["settings_button"], 80, 70)),
        _frame((templates["settings"], 4, 4), (templates["more_button"], 40, 70)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4)),
        _frame((templates["more"], 4, 4), (templates["export"], 60, 55)),
        _frame((templates["more"], 4, 4), (templates["more_close"], 75, 5)),
        _frame((templates["settings"], 4, 4), (templates["settings_close"], 75, 5)),
        _frame((templates["home"], 4, 4)),
    ]


def _controller(frames, *, clear=lambda: None, monotonic=lambda: 1.0):
    profile, templates = _profile()
    inputs = Input()
    startup = StartupInput()
    reads = iter(["old", json.dumps({
        "tag": TAG, "timestamp": 1_788_894_243, "buildings": [],
    })])
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=iter(frames).__next__,
        profile=profile,
        startup_input=startup,
        export_input=inputs,
        find_continue=lambda _frame: None,
        live_gate=lambda: True,
        clipboard_read=reads.__next__,
        clipboard_clear=clear,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=monotonic,
        wait=lambda _seconds: None,
    )
    return subject, inputs, startup, templates


def test_missing_settings_destination_denies_every_later_gesture() -> None:
    profile, templates = _profile()
    frames = [
        _frame((templates["home"], 4, 4)),
        _frame((templates["home"], 4, 4), (templates["settings_button"], 80, 70)),
        _frame((templates["home"], 4, 4), (templates["more_button"], 40, 70)),
    ]
    subject, inputs, _startup, _templates = _controller(frames)
    subject._profile = profile

    try:
        subject.run(deadline=10.0)
    except AccountReadinessError as exc:
        assert "settings evidence" in str(exc)
    else:
        raise AssertionError("wrong destination must fail")
    assert len(inputs.events) == 1


def test_final_positive_home_is_sufficient_after_identity_bound_export() -> None:
    profile, templates = _profile()
    subject, inputs, _startup, _ = _controller(_successful_frames(templates))
    subject._profile = profile

    result = subject.run(deadline=10.0)

    assert type(result) is AccountReady
    assert all(event[-1] is InputAction.ACCOUNT_EXPORT_NAVIGATION for event in inputs.events)


def test_clipboard_cleanup_fault_overrides_an_otherwise_complete_proof() -> None:
    profile, templates = _profile()
    clears = 0

    def clear() -> None:
        nonlocal clears
        clears += 1
        if clears == 2:
            raise OSError("synthetic private clipboard detail")

    subject, _inputs, _startup, _ = _controller(
        _successful_frames(templates), clear=clear
    )
    subject._profile = profile
    try:
        subject.run(deadline=10.0)
    except AccountReadinessError as exc:
        assert str(exc) == "clipboard cleanup failed"
    else:
        raise AssertionError("cleanup fault must fail")


@pytest.mark.parametrize("timestamp_offset", [-121, 31])
def test_stale_or_future_export_never_returns_account_ready(timestamp_offset: int) -> None:
    profile, templates = _profile()
    subject, _inputs, _startup, _ = _controller(_successful_frames(templates))
    subject._profile = profile
    reads = iter([
        "old",
        json.dumps({
            "tag": TAG,
            "timestamp": int(NOW.timestamp()) + timestamp_offset,
            "buildings": [],
        }),
    ])
    subject._clipboard_read = reads.__next__

    with pytest.raises(AccountReadinessError, match="world export proof failed"):
        subject.run(deadline=10.0)


def test_startup_continue_cap_exhaustion_never_retries_the_click() -> None:
    profile, templates = _profile()
    startup = StartupInput()
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=iter([_frame(), _frame()]).__next__,
        profile=profile,
        startup_input=startup,
        export_input=Input(),
        find_continue=lambda _frame: SimpleNamespace(x=0.5, y=0.5),
        live_gate=lambda: True,
        clipboard_read=lambda: "",
        clipboard_clear=lambda: None,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=lambda: 1.0,
        wait=lambda _seconds: None,
    )

    try:
        subject.run(deadline=10.0)
    except AccountReadinessError as exc:
        assert "cap exhausted" in str(exc)
    else:
        raise AssertionError("second Continue evidence must fail")
    assert startup.events == [(0.5, 0.5)]


def test_deadline_crossing_during_capture_discards_the_frame() -> None:
    profile, _templates = _profile()
    calls = iter([9.0, 10.0])
    private_frame = np.ones((360, 640, 3), dtype=np.uint8)
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=lambda: private_frame,
        profile=profile,
        startup_input=StartupInput(),
        export_input=Input(),
        find_continue=lambda _frame: None,
        live_gate=lambda: True,
        clipboard_read=lambda: "",
        clipboard_clear=lambda: None,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=calls.__next__,
        wait=lambda _seconds: None,
    )

    try:
        subject.run(deadline=10.0)
    except AccountReadinessError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("deadline crossing must fail")
    assert not np.any(private_frame)


def test_malformed_capture_is_zeroed_in_traceback_before_failure() -> None:
    profile, _templates = _profile()
    private_frame = np.ones((360, 640, 3), dtype=np.float32)
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=lambda: private_frame,
        profile=profile,
        startup_input=StartupInput(),
        export_input=Input(),
        find_continue=lambda _frame: None,
        live_gate=lambda: True,
        clipboard_read=lambda: "",
        clipboard_clear=lambda: None,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=lambda: 1.0,
        wait=lambda _seconds: None,
    )

    with pytest.raises(AccountReadinessError) as caught:
        subject.run(deadline=10.0)
    trace = caught.value.__traceback__
    while trace:
        if trace.tb_frame.f_code.co_name == "_capture_frame":
            retained = trace.tb_frame.f_locals.get("frame")
            assert retained is None or not np.any(retained)
        trace = trace.tb_next
    assert not np.any(private_frame)


def test_deadline_crossing_during_matcher_discards_the_match() -> None:
    profile, templates = _profile()
    calls = iter([9.0, 10.0])
    subject, _inputs, _startup, _ = _controller([], monotonic=calls.__next__)
    subject._profile = profile

    try:
        subject._match(
            _frame((templates["home"], 4, 4)), "home", 10.0
        )
    except AccountReadinessError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("matcher deadline crossing must fail")


def test_deadline_crossing_at_native_gate_emits_no_input() -> None:
    profile, _templates = _profile()
    calls = iter([9.0, 10.0])
    subject, inputs, _startup, _ = _controller([], monotonic=calls.__next__)
    subject._profile = profile

    try:
        subject._click(SimpleNamespace(x=0.5, y=0.5), 10.0)
    except AccountReadinessError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("native gate deadline crossing must fail")
    assert inputs.events == []


@pytest.mark.parametrize("color", [(0, 0, 255), (0, 255, 0)])
def test_plain_red_or_green_wrong_screen_never_authorizes_a_control(color) -> None:
    profile, _templates = _profile()
    decoy = np.zeros((360, 640, 3), dtype=np.uint8)
    decoy[:] = color
    calls = iter([1.0] * 8 + [10.0])
    subject, inputs, startup, _ = _controller([decoy], monotonic=calls.__next__)
    subject._profile = profile

    with pytest.raises(AccountReadinessError, match="deadline"):
        subject.run(deadline=10.0)
    assert inputs.events == []
    assert startup.events == []
