"""Fault matrix through the complete copied startup loop, not helper-only tests."""
from types import SimpleNamespace
import pytest
from test_startup_debug import launcher
from test_startup_debug_boundaries import Clock
from clash_rush_rebuild.startup_debug import StartupDebugController
from clash_rush_rebuild.startup_failure import StartupFailure, StartupReason
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult as Result
from clash_rush_rebuild.input_authorization import InputAction


def fail(*a, **k):
    raise OSError("private-marker")


@pytest.mark.parametrize("stage,reason", [
    ("gate", StartupReason.AUTHORIZATION), ("capture", StartupReason.INITIAL_CAPTURE),
    ("initial", StartupReason.INITIAL_EVIDENCE), ("detect", StartupReason.ICON_VERIFY),
    ("classify", StartupReason.RECOGNITION), ("before", StartupReason.EVIDENCE_BEFORE),
    ("deliver", StartupReason.INPUT), ("after", StartupReason.POST_EVIDENCE),
    ("final", StartupReason.FINAL_EVIDENCE),
])
def test_controller_exact_fault_no_context_and_no_input_before_evidence(stage, reason):
    clock = Clock()
    events = []
    frame = launcher()
    def capture():
        if stage == "capture" and not events:
            events.append("capture-fault")
            fail()
        return frame.copy()
    def snapshot(kind, pixels):
        events.append(kind)
        if stage == kind:
            fail()
    def before(*a):
        if stage == "before":
            fail()
        events.append("before")
        return "nonce"
    def after(*a):
        events.append("after")
        if stage == "after":
            fail()
    def deliver(action, point, prepare, deadline):
        prepare(frame)
        if stage == "deliver":
            fail()
        events.append("input")
        return True
    evidence = SimpleNamespace(snapshot=snapshot, before=before, after=after)
    controller = StartupDebugController(capture=capture,
        classify=fail if stage == "classify" else lambda f: Result.HOME,
        detect=fail if stage == "detect" else lambda f: None if stage in {"classify", "final"} else (InputAction.STARTUP_CLOSE_PROMO, (0.5, 0.5)),
        deliver=deliver, evidence=evidence, gate=fail if stage == "gate" else lambda: True)
    with pytest.raises(StartupFailure) as caught:
        controller.run(timeout=1, wait=clock.wait, clock=clock.now)
    assert caught.value.reason is reason
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "private-marker" not in str(caught.value)
    if stage in {"gate", "capture", "initial", "detect", "classify", "before", "deliver"}:
        assert "input" not in events
    if stage == "after":
        assert caught.value.input_completed is True


def test_final_failure_preserves_original_and_completed_input():
    clock = Clock()
    frame = launcher()
    def deliver(a, p, prepare, deadline):
        prepare(frame)
        return True
    evidence = SimpleNamespace(before=lambda *a: "nonce", after=fail,
        snapshot=lambda kind, f: fail() if kind == "final" else None)
    controller = StartupDebugController(capture=lambda: frame.copy(), classify=lambda f: Result.UNKNOWN,
        detect=lambda f: (InputAction.STARTUP_CLOSE_PROMO, (0.5, 0.5)),
        deliver=deliver, evidence=evidence, gate=lambda: True)
    with pytest.raises(StartupFailure) as caught:
        controller.run(timeout=1, wait=clock.wait, clock=clock.now)
    assert caught.value.reason is StartupReason.FINAL_EVIDENCE
    assert caught.value.prior == (StartupReason.POST_EVIDENCE,)
    assert caught.value.input_completed is True


def test_reused_controller_never_recaptures_or_writes():
    events = []
    c = StartupDebugController(capture=lambda: events.append("capture"), classify=None,
        detect=None, deliver=None, evidence=None, gate=None)
    c.used = True
    with pytest.raises(StartupFailure):
        c.run()
    assert not events
