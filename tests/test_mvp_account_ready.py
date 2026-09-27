from __future__ import annotations

import hashlib
import json
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
    ReadinessVisualProfile,
    TemplateSpec,
    _match_template,
    load_private_visual_profile,
    require_manual_home_frame,
)
from clash_rush_rebuild.no_input_home_diagnostic import (
    HomeDiagnosticResult,
    NoInputHomeDiagnosticController,
)
from clash_rush_rebuild.win32_state_io import (
    FILE_ATTRIBUTE_NORMAL,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_OPEN_REPARSE_POINT,
    GENERIC_READ,
    OPEN_EXISTING,
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


def test_private_control_match_adapts_across_donor_current_ui_scale_range() -> None:
    template = _template(99)
    scaled = cv2.resize(template, (6, 6), interpolation=cv2.INTER_AREA)
    frame = _frame((scaled, 80, 70))
    spec = TemplateSpec(
        "settings_button",
        template,
        hashlib.sha256(template.tobytes()).hexdigest(),
        0.90,
        (0.0, 0.0, 1.0, 1.0),
    )

    match = _match_template(frame, spec)

    assert match is not None
    assert match.confidence >= 0.90
    assert match.x == pytest.approx(83 / 640)
    assert match.y == pytest.approx(73 / 360)


def test_private_control_match_rejects_evidence_below_donor_scale_floor() -> None:
    template = _template(100)
    scaled = cv2.resize(template, (3, 3), interpolation=cv2.INTER_AREA)
    frame = _frame((scaled, 80, 70))
    spec = TemplateSpec(
        "settings_button",
        template,
        hashlib.sha256(template.tobytes()).hexdigest(),
        0.99,
        (0.0, 0.0, 1.0, 1.0),
    )

    assert _match_template(frame, spec) is None


def test_private_control_match_rejects_degenerate_resized_candidate() -> None:
    template = np.zeros((3, 3, 3), dtype=np.uint8)
    template[1, 1] = 255
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    spec = TemplateSpec(
        "settings_button",
        template,
        hashlib.sha256(template.tobytes()).hexdigest(),
        0.99,
        (0.0, 0.0, 1.0, 1.0),
    )

    assert _match_template(frame, spec) is None


class Input:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def click(self, binding, x, y, *, action, pre_input_check=None):
        assert binding == BINDING
        if pre_input_check is not None and pre_input_check() is not True:
            return False
        self.events.append(("click", round(x, 3), round(y, 3), action))
        return True

    def drag(self, binding, x0, y0, x1, y1, *, action):
        assert binding == BINDING
        self.events.append(("drag", x0, y0, x1, y1, action))
        return True


class PrivateProfileFileApi:
    def __init__(self, root: Path) -> None:
        self.files = {
            str(path.resolve(strict=True)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        self.handles: dict[int, str] = {}
        self.offsets: dict[int, int] = {}
        self.calls: list[tuple[object, ...]] = []
        self.final_path_override: str | None = None
        self.attributes = FILE_ATTRIBUTE_NORMAL

    def create_file(self, path, access, share, creation, flags):
        self.calls.append(("open", path, access, share, creation, flags))
        handle = len(self.handles) + 1
        self.handles[handle] = path
        self.offsets[handle] = 0
        return handle

    def final_path(self, handle):
        return self.final_path_override or self.handles[handle]

    def handle_attributes(self, _handle):
        return self.attributes

    def read_file(self, handle, size):
        path = self.handles[handle]
        offset = self.offsets[handle]
        chunk = self.files[path][offset : offset + size]
        self.offsets[handle] += len(chunk)
        self.calls.append(("read", handle, size))
        return chunk

    def close_handle(self, handle):
        self.calls.append(("close", handle))
        return True


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
        export_input=input_port,
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


def _visual_profile_packet(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    private = tmp_path / "private" / "readiness"
    private.mkdir(parents=True)
    entries = {}
    payloads: dict[str, bytes] = {}
    for index, name in enumerate(sorted({
        "home", "settings_button", "settings", "more_button", "more",
        "export", "more_close", "settings_close",
    }), start=1):
        template = _template(index)
        ok, encoded = cv2.imencode(".png", template)
        assert ok
        payload = encoded.tobytes()
        asset = private / f"{name}.png"
        asset.write_bytes(payload)
        payloads[str(asset.resolve(strict=True))] = payload
        entries[name] = {
            "file": asset.name,
            "file_sha256": hashlib.sha256(payload).hexdigest(),
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
    payloads[str(manifest.resolve(strict=True))] = manifest.read_bytes()
    return manifest, payloads


def test_private_profile_loader_freezes_exact_manifest_and_asset_bytes(tmp_path: Path) -> None:
    manifest, _payloads = _visual_profile_packet(tmp_path)
    private = manifest.parent

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


@pytest.mark.parametrize("fault", ["final-path", "reparse", "manifest-oversize", "asset-oversize"])
def test_private_profile_handle_bound_reads_reject_swaps_reparse_and_oversize(
    tmp_path: Path, fault: str,
) -> None:
    manifest, _payloads = _visual_profile_packet(tmp_path)
    api = PrivateProfileFileApi(manifest.parent)
    if fault == "final-path":
        api.final_path_override = str(tmp_path / "outside" / "profile.json")
    elif fault == "reparse":
        api.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    elif fault == "manifest-oversize":
        api.files[str(manifest.resolve(strict=True))] = b"x" * 1_000_001
    else:
        asset = next(path for path in api.files if path.endswith("home.png"))
        api.files[asset] = b"x" * 4_000_001

    with pytest.raises(AccountReadinessError):
        load_private_visual_profile(tmp_path, manifest, file_api=api)

    assert [call[0] for call in api.calls].count("close") == len(api.handles)
    assert all(
        call[2:] == (GENERIC_READ, 0, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)
        for call in api.calls
        if call[0] == "open"
    )


def test_private_profile_rejects_oversized_png_header_before_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _payloads = _visual_profile_packet(tmp_path)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (10_000).to_bytes(4, "big")
        + (10_000).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    asset = manifest.parent / "home.png"
    asset.write_bytes(payload)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["templates"]["home"]["file_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    api = PrivateProfileFileApi(manifest.parent)
    original_decode = cv2.imdecode

    def guarded_decode(buffer, flags):
        if bytes(buffer) == payload:
            raise AssertionError("oversized header must be rejected before decode")
        return original_decode(buffer, flags)

    monkeypatch.setattr(cv2, "imdecode", guarded_decode)

    with pytest.raises(AccountReadinessError, match="narrow PNG"):
        load_private_visual_profile(tmp_path, manifest, file_api=api)

    assert [call[0] for call in api.calls].count("close") == len(api.handles)


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


@pytest.mark.parametrize(
    ("include_profile_home", "diagnostic", "accepted"),
    [
        (True, HomeDiagnosticResult.HOME, True),
        (False, HomeDiagnosticResult.HOME, False),
        (True, HomeDiagnosticResult.BUILDER, False),
        (True, HomeDiagnosticResult.UNKNOWN, False),
    ],
)
def test_manual_home_frame_requires_sealed_profile_and_donor_attack_proof(
    monkeypatch: pytest.MonkeyPatch,
    include_profile_home: bool,
    diagnostic: HomeDiagnosticResult,
    accepted: bool,
) -> None:
    profile, templates = _profile()
    frame = (
        _frame((templates["home"], 4, 4))
        if include_profile_home
        else _frame()
    )
    monkeypatch.setattr(
        NoInputHomeDiagnosticController,
        "detect_home_attack",
        classmethod(lambda _cls, _frame: diagnostic is HomeDiagnosticResult.HOME),
    )

    if accepted:
        require_manual_home_frame(frame, profile)
    else:
        with pytest.raises(AccountReadinessError, match="first frame"):
            require_manual_home_frame(frame, profile)


def _successful_frames(templates: dict[str, np.ndarray]):
    return [
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
    reads = iter(["old", json.dumps({
        "tag": TAG, "timestamp": 1_788_894_243, "buildings": [],
    })])
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=iter(frames).__next__,
        profile=profile,
        export_input=inputs,
        live_gate=lambda: True,
        clipboard_read=reads.__next__,
        clipboard_clear=clear,
        expected_tag_sha256=hashlib.sha256(TAG.encode()).hexdigest(),
        wall_clock=lambda: NOW,
        monotonic=monotonic,
        wait=lambda _seconds: None,
    )
    return subject, inputs, None, templates


def test_missing_settings_destination_denies_every_later_gesture() -> None:
    profile, templates = _profile()
    frames = [
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


def test_clipboard_cleanup_failure_preserves_primary_readiness_error() -> None:
    primary = RuntimeError("synthetic primary readiness failure")
    clears = 0

    def clear() -> None:
        nonlocal clears
        clears += 1
        if clears == 2:
            raise OSError("synthetic clipboard cleanup failure")

    subject, _inputs, _startup, _templates = _controller([], clear=clear)
    subject._capture_frame = lambda _deadline: (_ for _ in ()).throw(primary)

    with pytest.raises(RuntimeError) as caught:
        subject.run(deadline=10.0)

    assert caught.value is primary
    assert caught.value.__notes__ == ["clipboard cleanup also failed"]


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


def test_deadline_crossing_during_capture_discards_the_frame() -> None:
    profile, _templates = _profile()
    calls = iter([9.0, 10.0])
    private_frame = np.ones((360, 640, 3), dtype=np.uint8)
    subject = AccountReadinessController(
        binding=BINDING,
        run_nonce="2" * 32,
        capture=lambda: private_frame,
        profile=profile,
        export_input=Input(),
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
        export_input=Input(),
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
