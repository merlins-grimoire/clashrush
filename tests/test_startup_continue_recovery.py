from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from clash_rush_rebuild.input_authorization import (
    InputAction,
    InputAuthorization,
    InputAuthorizationError,
    InputPurpose,
)
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.startup_continue_recovery import (
    ContinueMatch,
    StartupContinueController,
    StartupContinueError,
    StartupContinueResult,
    batch_locate,
    render_text,
)


def _binding(width: int = 640, height: int = 360) -> PlayerBinding:
    identity = ProcessIdentity(100, 9001)
    return PlayerBinding(identity, identity, 101, 102, width, height, "a" * 32)


class FakeWindow:
    def __init__(self, binding: PlayerBinding, frames: list[np.ndarray]) -> None:
        self.binding = binding
        self.frames = iter(frames)
        self.captured: list[np.ndarray] = []

    def screenshot(self) -> np.ndarray:
        frame = next(self.frames)
        self.captured.append(frame)
        return frame


class FakeInput:
    def __init__(self, binding: PlayerBinding, *, result: bool = True) -> None:
        self.binding = binding
        self.result = result
        self.calls: list[tuple[PlayerBinding, float, float]] = []

    def click_continue(self, binding: PlayerBinding, x: float, y: float) -> bool:
        self.calls.append((binding, x, y))
        if binding != self.binding:
            raise StartupContinueError("BINDING_CHANGED")
        return self.result


def _clock(step: float = 1.0):
    current = -step

    def now() -> float:
        nonlocal current
        current += step
        return current

    return now


def _controller(
    frames: list[np.ndarray],
    observations: list[StartupContinueResult],
    matches: list[ContinueMatch | None],
    *,
    input_port: FakeInput | None = None,
) -> tuple[StartupContinueController, FakeInput]:
    binding = _binding(frames[0].shape[1], frames[0].shape[0])
    window = FakeWindow(binding, frames)
    selected_input = input_port or FakeInput(binding)
    observation_queue = iter(observations)
    match_queue = iter(matches)
    subject = StartupContinueController(
        binding=binding,
        window=window,
        input_port=selected_input,
        font_path=Path("private-font.ttf"),
        classify_village=lambda _frame: next(observation_queue),
        find_continue=lambda _frame, _font, _height: next(match_queue),
        find_update=lambda _frame, _font, _height: False,
    )
    return subject, selected_input


def test_donor_batch_locate_returns_exact_normalized_template_center() -> None:
    frame = np.zeros((360, 640), dtype=np.uint8)
    template = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    frame[20:22, 50:52] = template

    assert batch_locate((template,), frame, threshold=0.99) == (
        ContinueMatch(51 / 640, 21 / 360, 1.0),
    )


def test_render_text_is_memory_only_and_requires_a_private_existing_font(tmp_path: Path) -> None:
    missing = tmp_path / "missing.ttf"
    with pytest.raises(StartupContinueError, match="FONT_UNAVAILABLE"):
        render_text("Continue", missing, 25)
    assert list(tmp_path.iterdir()) == []


def test_continue_absent_times_out_without_input_and_clears_frames() -> None:
    frames = [np.full((360, 640, 3), 17, dtype=np.uint8) for _ in range(2)]
    subject, input_port = _controller(
        frames,
        [StartupContinueResult.UNKNOWN, StartupContinueResult.UNKNOWN],
        [None, None],
    )

    result = subject.run(
        timeout_seconds=2,
        poll_interval_seconds=1,
        settle_seconds=0.25,
        monotonic=_clock(),
        wait=lambda _seconds: None,
    )

    assert result is StartupContinueResult.UNKNOWN
    assert input_port.calls == []
    assert all(np.count_nonzero(frame) == 0 for frame in frames)


def test_continue_present_clicks_detected_center_once_then_reobserves_home() -> None:
    frames = [np.ones((360, 640, 3), dtype=np.uint8) for _ in range(2)]
    subject, input_port = _controller(
        frames,
        [StartupContinueResult.UNKNOWN, StartupContinueResult.HOME],
        [ContinueMatch(0.25, 0.75, 0.9)],
    )

    result = subject.run(
        timeout_seconds=5,
        poll_interval_seconds=1,
        settle_seconds=0.25,
        monotonic=_clock(),
        wait=lambda _seconds: None,
    )

    assert result is StartupContinueResult.HOME
    assert input_port.calls == [(_binding(), 0.25, 0.75)]
    assert all(np.count_nonzero(frame) == 0 for frame in frames)


def test_delayed_continue_then_multiple_settled_frames_never_replays_click() -> None:
    frames = [np.ones((360, 640, 3), dtype=np.uint8) for _ in range(4)]
    subject, input_port = _controller(
        frames,
        [
            StartupContinueResult.UNKNOWN,
            StartupContinueResult.UNKNOWN,
            StartupContinueResult.UNKNOWN,
            StartupContinueResult.BUILDER,
        ],
        [None, ContinueMatch(0.4, 0.6, 0.8), ContinueMatch(0.4, 0.6, 0.8)],
    )

    result = subject.run(
        timeout_seconds=8,
        poll_interval_seconds=1,
        settle_seconds=0.25,
        monotonic=_clock(),
        wait=lambda _seconds: None,
    )

    assert result is StartupContinueResult.BUILDER
    assert len(input_port.calls) == 1


def test_update_required_is_closed_blocker_with_zero_input() -> None:
    frame = np.ones((360, 640, 3), dtype=np.uint8)
    binding = _binding()
    window = FakeWindow(binding, [frame])
    input_port = FakeInput(binding)
    subject = StartupContinueController(
        binding=binding,
        window=window,
        input_port=input_port,
        font_path=Path("private-font.ttf"),
        classify_village=lambda _frame: StartupContinueResult.UNKNOWN,
        find_continue=lambda _frame, _font, _height: None,
        find_update=lambda _frame, _font, _height: True,
    )

    assert subject.run(
        timeout_seconds=5,
        poll_interval_seconds=1,
        settle_seconds=0.25,
        monotonic=_clock(),
        wait=lambda _seconds: None,
    ) is StartupContinueResult.UPDATE_REQUIRED
    assert input_port.calls == []
    assert np.count_nonzero(frame) == 0


def test_unknown_popup_never_receives_input() -> None:
    frames = [np.ones((360, 640, 3), dtype=np.uint8) for _ in range(2)]
    subject, input_port = _controller(
        frames,
        [StartupContinueResult.UNKNOWN, StartupContinueResult.UNKNOWN],
        [None, None],
    )
    assert subject.run(
        timeout_seconds=2,
        poll_interval_seconds=1,
        settle_seconds=0.25,
        monotonic=_clock(),
        wait=lambda _seconds: None,
    ) is StartupContinueResult.UNKNOWN
    assert input_port.calls == []


def test_moved_binding_and_input_exception_fail_closed_and_cleanup_pixels() -> None:
    frame = np.ones((360, 640, 3), dtype=np.uint8)
    moved = FakeInput(_binding(width=641))
    subject, _input_port = _controller(
        [frame],
        [StartupContinueResult.UNKNOWN],
        [ContinueMatch(0.5, 0.5, 0.9)],
        input_port=moved,
    )

    with pytest.raises(StartupContinueError, match="INPUT_FAILED") as caught:
        subject.run(
            timeout_seconds=5,
            poll_interval_seconds=1,
            settle_seconds=0.25,
            monotonic=_clock(),
            wait=lambda _seconds: None,
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert np.count_nonzero(frame) == 0


def test_startup_continue_authority_is_separate_and_one_purpose_only() -> None:
    authorization = InputAuthorization.startup_continue_only(lambda: True)
    assert authorization.purpose is InputPurpose.STARTUP_CONTINUE_ONLY
    authorization.require(InputAction.STARTUP_CONTINUE)
    with pytest.raises(InputAuthorizationError, match="not authorized"):
        authorization.require(InputAction.ATTACK_NAVIGATION)


def test_expired_live_gate_rejects_continue_action() -> None:
    authorization = InputAuthorization.startup_continue_only(lambda: False)
    with pytest.raises(InputAuthorizationError, match="not authorized"):
        authorization.require(InputAction.STARTUP_CONTINUE)
