from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from test_startup_debug import launcher

from clash_rush_rebuild.input_authorization import InputAction as Action
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult as Result
from clash_rush_rebuild.startup_debug import (
    DebugEvidence,
    StartupDebugController,
    StartupDebugError,
)


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def wait(self, seconds):
        self.value += seconds


@pytest.mark.parametrize("result", [Result.HOME, Result.BUILDER, Result.UNKNOWN])
def test_no_action_has_initial_and_final_even_on_timeout(tmp_path, result):
    frame = launcher()
    clock = Clock()
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)
    calls = []
    actual = StartupDebugController(
        capture=lambda: frame.copy(),
        classify=lambda f: result,
        detect=lambda f: None,
        deliver=lambda *a: calls.append(a),
        evidence=evidence,
        gate=lambda: True,
    ).run(timeout=1, wait=clock.wait, clock=clock.now)
    assert actual is result and not calls
    assert {p.name for p in evidence.directory.iterdir()} == {
        "initial.png",
        "final.png",
    }


def test_real_detector_orders_red_before_okay_before_continue(monkeypatch):
    import clash_rush_rebuild.startup_debug as mod

    frame = launcher()
    monkeypatch.setattr(mod, "launcher_position", lambda f: None)
    monkeypatch.setattr(mod, "find_update", lambda *a: False)
    calls = []

    def popup(f, action, font):
        calls.append(action)
        return (0.5, 0.5)

    monkeypatch.setattr(mod, "popup_position", popup)
    monkeypatch.setattr(
        mod, "find_continue", lambda *a: pytest.fail("lower priority Continue")
    )
    assert mod.StartupDetector("generic")(frame)[0] is Action.STARTUP_CLOSE_PROMO
    assert calls == [Action.STARTUP_CLOSE_PROMO]
    monkeypatch.setattr(
        mod,
        "popup_position",
        lambda f, action, font: (0.5, 0.5) if action is Action.STARTUP_OKAY else None,
    )
    assert mod.StartupDetector("generic")(frame)[0] is Action.STARTUP_OKAY


def test_complete_order_caps_and_initial_final_pairs(tmp_path):
    actions = [
        Action.STARTUP_LAUNCH_GAME,
        Action.STARTUP_CLOSE_PROMO,
        Action.STARTUP_OKAY,
        Action.STARTUP_CONTINUE,
    ]
    state = {"n": 0}
    frame = launcher()
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)
    clock = Clock()

    def detect(f):
        return (actions[state["n"]], (0.5, 0.5)) if state["n"] < 4 else None

    def deliver(action, point, prepare, deadline):
        assert deadline > clock.now()
        prepare(frame)
        state["n"] += 1
        return True

    controller = StartupDebugController(
        capture=lambda: frame.copy(),
        classify=lambda f: Result.HOME,
        detect=detect,
        deliver=deliver,
        evidence=evidence,
        gate=lambda: True,
    )
    assert controller.run(timeout=60, wait=clock.wait, clock=clock.now) is Result.HOME
    assert len(list(evidence.directory.glob("*-before.png"))) == 4
    assert len(list(evidence.directory.glob("*-after.png"))) == 4
    assert (evidence.directory / "initial.png").exists()
    assert (evidence.directory / "final.png").exists()
    kinds = {
        json.loads(p.read_bytes())["action"]
        for p in evidence.directory.glob("*-intent.json")
    }
    assert kinds == {a.value for a in actions}
    with pytest.raises(StartupDebugError):
        controller.run()


@pytest.mark.parametrize(
    "action,limit",
    [
        (Action.STARTUP_LAUNCH_GAME, 1),
        (Action.STARTUP_CLOSE_PROMO, 3),
        (Action.STARTUP_OKAY, 1),
        (Action.STARTUP_CONTINUE, 1),
    ],
)
def test_persistent_control_reaches_cap_without_replay(tmp_path, action, limit):
    frame = launcher()
    clock = Clock()
    clicks = []
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)

    def deliver(a, p, prepare, deadline):
        prepare(frame)
        clicks.append(a)
        return True

    result = StartupDebugController(
        capture=lambda: frame.copy(),
        classify=lambda f: Result.UNKNOWN,
        detect=lambda f: (action, (0.5, 0.5)),
        deliver=deliver,
        evidence=evidence,
        gate=lambda: True,
    ).run(timeout=60, wait=clock.wait, clock=clock.now)
    assert result is Result.UNKNOWN and len(clicks) == limit


@pytest.mark.parametrize("fault", ["seal", "write"])
def test_before_fault_blocks_actual_delivery(tmp_path, fault, monkeypatch):
    frame = launcher()
    clock = Clock()
    clicks = []
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)
    original = evidence._write

    def write(name, raw):
        if name.endswith("-before.png"):
            raise OSError()
        return original(name, raw)

    def seal(path, directory):
        if path.name.endswith("-before.png"):
            raise OSError()

    if fault == "write":
        monkeypatch.setattr(evidence, "_write", write)
    else:
        monkeypatch.setattr(evidence, "_seal", seal)

    def deliver(a, p, prepare, deadline):
        prepare(frame)
        clicks.append(a)
        return True

    with pytest.raises(StartupDebugError):
        StartupDebugController(
            capture=lambda: frame.copy(),
            classify=lambda f: Result.UNKNOWN,
            detect=lambda f: (Action.STARTUP_LAUNCH_GAME, (0.5, 0.5)),
            deliver=deliver,
            evidence=evidence,
            gate=lambda: True,
        ).run(timeout=60, wait=clock.wait, clock=clock.now)
    assert not clicks


def test_native_last_seam_and_cleanup(monkeypatch):
    from test_startup_continue_integration import BINDING

    import clash_rush_rebuild.startup_debug_native as mod

    frame = launcher()
    events = []
    cursor = [0, 0]
    moved = [False]
    clock = Clock()

    class Api:
        def ClientToScreen(self, hwnd, p):
            if moved[0]:
                p._obj.x += 1
            return 1

        def SetCursorPos(self, x, y):
            cursor[:] = [x, y]
            return 1

        def GetForegroundWindow(self):
            return BINDING.root_hwnd

        def GetAncestor(self, h, n):
            return BINDING.root_hwnd

        def mouse_event(self, kind, *args):
            events.append(kind)

    api = Api()

    def get_cursor(p):
        p._obj.x, p._obj.y = cursor
        return 1

    api.GetCursorPos = get_cursor

    class Native:
        def __init__(self, *a):
            self._user32 = api

        def _require_binding(self, b):
            pass

        def _foreground(self):
            return True

    monkeypatch.setattr(mod, "Win32BoundInput", Native)
    monkeypatch.setattr(mod.time, "sleep", clock.wait)
    monkeypatch.setattr(mod.time, "monotonic", clock.now)
    action = Action.STARTUP_CLOSE_PROMO
    point = (0.5, 0.5)

    def subject():
        return mod.StartupNativeInput(
            BINDING,
            lambda: frame.copy(),
            lambda f: (action, point),
            SimpleNamespace(verify=lambda: None),
            lambda: True,
            lambda b: None,
        )

    def prepare(f):
        events.append("before")

    assert subject()(action, point, prepare, 60) is True
    assert events == ["before", 2, 4]
    events.clear()

    def stale(f):
        clock.value += 3

    assert subject()(action, point, stale, 60) is False
    assert not events

    def move(f):
        cursor[0] += 1

    assert subject()(action, point, move, 60) is False
    assert not events

    def window_move(f):
        moved[0] = True

    assert subject()(action, point, window_move, 60) is False
    assert not events


def test_retention_cap_and_protected_native_dacl(tmp_path):
    # Exercise the real platform DACL implementation with generic generated pixels.
    for _ in range(3):
        evidence = DebugEvidence(tmp_path)
        evidence.snapshot("initial", np.zeros((80, 100, 3), np.uint8))
        evidence.verify()
    with pytest.raises(StartupDebugError):
        DebugEvidence(tmp_path)


def test_debug_approval_cleanup_uses_existing_cycle(monkeypatch):
    from test_startup_continue_integration import _cycle

    from clash_rush_rebuild.cycle import StartupDebugCycle

    events = []
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle
    assert cycle.visit_once() is Result.HOME
    assert any(s.startswith("approval:STARTUP_DEBUG:") for s in events)
    assert events[-2:] == ["stop", "mutex:release"]
    events = []
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle

    def failure(*a):
        raise StartupDebugError("synthetic")

    cycle._recover = failure
    with pytest.raises(RuntimeError):
        cycle.visit_once()
    assert events[-2:] == ["stop", "mutex:release"]


def test_okay_label_is_bound_to_component_and_welcome_context(monkeypatch):
    from PIL import Image, ImageDraw, ImageFont

    import clash_rush_rebuild.startup_debug as mod

    fonts = {size: ImageFont.load_default(size=size) for size in range(20, 49, 2)}
    monkeypatch.setattr(ImageFont, "truetype", lambda path, size: fonts[size])
    image = Image.new("RGB", (1280, 720))
    draw = ImageDraw.Draw(image)
    draw.rectangle((535, 520, 745, 575), fill=(0, 255, 0))
    draw.text((565, 524), "Okay", font=fonts[32], fill="white")
    draw.text((420, 200), "Welcome Back", font=fonts[32], fill="white")
    frame = np.asarray(image)[:, :, ::-1].copy()
    assert mod.popup_position(frame, Action.STARTUP_OKAY, "generic") is not None
    # Same green component, no approved title: a reward/purchase is not Okay.
    frame[150:350] = 0
    assert mod.popup_position(frame, Action.STARTUP_OKAY, "generic") is None


def test_non_welcome_frame_ignores_multiple_green_loading_components(monkeypatch):
    import clash_rush_rebuild.startup_debug as mod

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[510:550, 500:550] = (0, 255, 0)
    frame[510:550, 650:700] = (0, 255, 0)
    requested: list[str] = []

    def no_context(_frame, text, _font):
        requested.append(text)
        return False

    monkeypatch.setattr(mod, "_text_present", no_context)

    assert mod.popup_position(frame, Action.STARTUP_OKAY, "generic") is None
    assert requested == ["Welcome Back"]


def test_after_capture_failure_preserves_completed_input_fact(tmp_path):
    frame = launcher()
    clock = Clock()
    state = {"clicked": False}
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)

    def capture():
        if state["clicked"]:
            raise OSError("synthetic capture failure")
        return frame.copy()

    def deliver(a, p, prepare, deadline):
        prepare(frame)
        state["clicked"] = True
        return True

    with pytest.raises((StartupDebugError, OSError)):
        StartupDebugController(
            capture=capture,
            classify=lambda f: Result.UNKNOWN,
            detect=lambda f: (Action.STARTUP_CLOSE_PROMO, (0.5, 0.5)),
            deliver=deliver,
            evidence=evidence,
            gate=lambda: True,
        ).run(timeout=60, wait=clock.wait, clock=clock.now)
    outcomes = list(evidence.directory.glob("*-outcome.json"))
    assert (
        len(outcomes) == 1 and json.loads(outcomes[0].read_bytes())["delivered"] is True
    )
