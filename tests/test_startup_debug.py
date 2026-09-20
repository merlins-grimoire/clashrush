"""Synthetic startup contracts; launcher fixture adapted from first-party donor."""

import cv2
import numpy as np
import pytest
from PIL import Image

from clash_rush_rebuild.input_authorization import InputAction, InputAuthorization
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult as Result
from clash_rush_rebuild.startup_debug import (
    LAUNCH_SIGNATURE,
    LAUNCH_X,
    LAUNCH_Y,
    DebugEvidence,
    StartupDebugController,
    StartupDebugError,
    launcher_position,
    popup_position,
)


def launcher():
    frame = np.zeros((720, 1280, 3), np.uint8)
    x0, x1 = int((LAUNCH_X - 0.028) * 1280), int((LAUNCH_X + 0.028) * 1280)
    y0, y1 = int((LAUNCH_Y - 0.028) * 720), int((LAUNCH_Y + 0.028) * 720)
    colors = np.frombuffer(bytes.fromhex(LAUNCH_SIGNATURE), np.uint8).reshape(4, 4, 3)
    for row in range(4):
        for col in range(4):
            frame[
                y0 + (y1 - y0) * row // 4 : y0 + (y1 - y0) * (row + 1) // 4,
                x0 + (x1 - x0) * col // 4 : x0 + (x1 - x0) * (col + 1) // 4,
            ] = colors[row, col]
    return frame


def test_reviewed_launcher_guard_positive_and_changed_negative():
    frame = launcher()
    assert launcher_position(frame) == (LAUNCH_X, LAUNCH_Y)
    assert launcher_position(np.zeros_like(frame)) is None
    frame[:] = 255
    assert launcher_position(frame) is None


def test_popup_unique_components_and_color_fences():
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[40:75, 1180:1215] = (0, 0, 255)
    assert popup_position(frame, InputAction.STARTUP_CLOSE_PROMO) is None
    cv2.line(frame, (1188, 48), (1206, 66), (255, 255, 255), 5)
    cv2.line(frame, (1188, 66), (1206, 48), (255, 255, 255), 5)
    assert popup_position(frame, InputAction.STARTUP_CLOSE_PROMO) is not None
    frame[40:75, 1230:1265] = (0, 0, 255)
    with pytest.raises(StartupDebugError):
        popup_position(frame, InputAction.STARTUP_CLOSE_PROMO)
    frame[:] = 0
    frame[520:560, 580:660] = (0, 255, 0)
    # Color alone is NOT permission to click Claim/Collect/a purchase.
    assert popup_position(frame, InputAction.STARTUP_OKAY) is None


def test_closed_debug_authority_does_not_expand_old_purposes():
    debug = InputAuthorization.startup_debug(lambda: True)
    allowed = {
        InputAction.STARTUP_LAUNCH_GAME,
        InputAction.STARTUP_CLOSE_PROMO,
        InputAction.STARTUP_OKAY,
        InputAction.STARTUP_CONTINUE,
    }
    for action in InputAction:
        if action in allowed:
            debug.require(action)
        else:
            with pytest.raises(RuntimeError):
                debug.require(action)
    for action in allowed - {InputAction.STARTUP_CONTINUE}:
        with pytest.raises(RuntimeError):
            InputAuthorization.monitored_attack(lambda: True).require(action)
        with pytest.raises(RuntimeError):
            InputAuthorization.startup_continue_only(lambda: True).require(action)
    with pytest.raises(RuntimeError):
        InputAuthorization.startup_debug(lambda: 1).require(InputAction.STARTUP_OKAY)


def test_evidence_pair_crosshair_initial_final_and_exclusive(tmp_path):
    seals = []
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: seals.append((p, d)))
    frame = np.zeros((80, 100, 3), np.uint8)
    evidence.snapshot("initial", frame)
    nonce = evidence.before(InputAction.STARTUP_CLOSE_PROMO, frame, (0.5, 0.5))
    evidence.after(nonce, frame, True)
    evidence.snapshot("final", frame)
    before = np.asarray(Image.open(evidence.directory / f"{nonce}-before.png"))
    after = np.asarray(Image.open(evidence.directory / f"{nonce}-after.png"))
    assert tuple(before[round(0.5 * 79), round(0.5 * 99)]) == (255, 0, 0)
    assert not after.any() and not frame.any()
    assert all(p.parent == evidence.directory or d for p, d in seals)
    with pytest.raises(StartupDebugError):
        evidence.after(nonce, frame, True)
    with pytest.raises(StartupDebugError):
        evidence.snapshot("initial", frame)
    evidence.delete()
    assert not evidence.directory.exists()


def test_dacl_failure_creates_no_frame(tmp_path):
    def fail(p, d):
        raise OSError("synthetic")

    with pytest.raises(StartupDebugError):
        DebugEvidence(tmp_path, seal=fail)
    assert not list(tmp_path.rglob("*.png"))


def test_write_failure_prevents_delivery(tmp_path, monkeypatch):
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)
    monkeypatch.setattr(evidence, "_write", lambda *a: (_ for _ in ()).throw(OSError()))
    clicks = []
    subject = StartupDebugController(
        capture=launcher,
        classify=lambda f: Result.UNKNOWN,
        detect=lambda f: (InputAction.STARTUP_LAUNCH_GAME, (LAUNCH_X, LAUNCH_Y)),
        deliver=lambda *a: clicks.append(a) or True,
        evidence=evidence,
        gate=lambda: True,
    )
    with pytest.raises(StartupDebugError):
        subject.run(timeout=1, wait=lambda s: None)
    assert not clicks


def test_stale_revalidation_prevents_click(tmp_path):
    evidence = DebugEvidence(tmp_path, seal=lambda p, d: None)
    captures = iter(
        [launcher(), launcher(), launcher(), np.zeros((720, 1280, 3), np.uint8)]
    )
    clicks = []

    def capture():
        return next(captures, np.zeros((720, 1280, 3), np.uint8))

    subject = StartupDebugController(
        capture=capture,
        classify=lambda f: Result.UNKNOWN,
        detect=lambda f: (
            (InputAction.STARTUP_LAUNCH_GAME, launcher_position(f))
            if launcher_position(f)
            else None
        ),
        deliver=lambda *a: clicks.append(a) or True,
        evidence=evidence,
        gate=lambda: True,
    )
    subject.run(timeout=0.1, wait=lambda s: None)
    assert not clicks


def test_diagnostic_cli_requires_explicit_full_frame_opt_in():
    from clash_rush_rebuild.cli import main

    assert (
        main(["startup-debug-one", "--project-root", "unused", "--slots", "unused"])
        == 2
    )


def test_debug_cycle_has_distinct_durable_approval():
    from clash_rush_rebuild.approval_reconciliation import ApprovalAction
    from clash_rush_rebuild.cycle import StartupDebugCycle

    assert StartupDebugCycle._approval_action is ApprovalAction.STARTUP_DEBUG
