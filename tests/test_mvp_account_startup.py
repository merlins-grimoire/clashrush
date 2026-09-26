from __future__ import annotations

import numpy as np
import pytest

from clash_rush_rebuild import mvp_local_native
from clash_rush_rebuild.input_authorization import InputAction, InputAuthorization
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.mvp_account_startup import await_account_home
from clash_rush_rebuild.mvp_account_ready import AccountReadinessError


def _frame(marker: int) -> np.ndarray:
    return np.full((72, 128, 3), marker, dtype=np.uint8)


BINDING = PlayerBinding(
    ProcessIdentity(100, 200), ProcessIdentity(100, 200),
    10, 11, 1280, 720, "0" * 32,
)


def test_welcome_back_gets_one_okay_click_then_home_and_zeros_frames() -> None:
    frames = [_frame(1), _frame(2)]
    captured: list[np.ndarray] = []
    events: list[object] = []

    def capture() -> np.ndarray:
        frame = frames.pop(0)
        captured.append(frame)
        return frame

    class StartupInput:
        def click_okay(self, expected_point):
            events.append(("click", expected_point))
            return True

    result = await_account_home(
        capture=capture,
        home_verified=lambda frame: bool(frame[0, 0, 0] == 2),
        popup_detector=lambda frame, action, font: (
            events.append(("detect", action, font)) or (0.5, 0.75)
        ),
        startup_input_factory=lambda: events.append("construct") or StartupInput(),
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        wait=lambda seconds: events.append(("wait", seconds)),
    )

    assert result is True
    assert events == [
        ("detect", InputAction.STARTUP_OKAY, "private-font"),
        "construct",
        ("click", (0.5, 0.75)),
        ("wait", 0.25),
    ]
    assert len(captured) == 2
    assert all(not np.any(frame) for frame in captured)


def test_normal_home_constructs_no_startup_input_and_zeros_frame() -> None:
    frame = _frame(2)

    assert await_account_home(
        capture=lambda: frame,
        home_verified=lambda candidate: bool(candidate[0, 0, 0] == 2),
        popup_detector=lambda *_args: pytest.fail("popup detector must not run on Home"),
        startup_input_factory=lambda: pytest.fail("startup input must not be constructed"),
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        wait=lambda _seconds: None,
    ) is True
    assert not np.any(frame)


@pytest.mark.parametrize("screen", ["unknown", "Continue", "launcher", "loading"])
def test_non_welcome_startup_screen_emits_no_input(screen: str) -> None:
    frame = _frame(1)

    with pytest.raises(AccountReadinessError, match="Welcome Back"):
        await_account_home(
            capture=lambda: frame,
            home_verified=lambda _frame: False,
            popup_detector=lambda *_args: None,
            startup_input_factory=lambda: pytest.fail(f"input on {screen}"),
            font_path="private-font",
            deadline=10.0,
            monotonic=lambda: 1.0,
            wait=lambda _seconds: None,
        )
    assert not np.any(frame)


@pytest.mark.parametrize("failure", [RuntimeError("ambiguous"), ValueError("malformed")])
def test_ambiguous_or_malformed_popup_emits_no_input(failure: BaseException) -> None:
    frame = _frame(1)

    with pytest.raises(AccountReadinessError, match="classification"):
        await_account_home(
            capture=lambda: frame,
            home_verified=lambda _frame: False,
            popup_detector=lambda *_args: (_ for _ in ()).throw(failure),
            startup_input_factory=lambda: pytest.fail("input must not be constructed"),
            font_path="private-font",
            deadline=10.0,
            monotonic=lambda: 1.0,
            wait=lambda _seconds: None,
        )
    assert not np.any(frame)


def test_native_okay_click_revalidates_fresh_target_and_has_no_callback_after_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    final_frame = np.full(
        (BINDING.height, BINDING.width, 3), 7, dtype=np.uint8
    )

    class User32:
        def ShowWindow(self, *_args): events.append("show"); return True
        def BringWindowToTop(self, *_args): events.append("top"); return True
        def SetForegroundWindow(self, *_args): events.append("foreground-set"); return True
        def GetForegroundWindow(self): events.append("foreground-get"); return 10
        def GetAncestor(self, *_args): events.append("ancestor"); return 10
        def ClientToScreen(self, _hwnd, _point): events.append("map"); return True
        def SetCursorPos(self, _x, _y): events.append("cursor-set"); return True
        def GetCursorPos(self, point):
            events.append("cursor-get")
            point._obj.x = round(0.5 * (BINDING.width - 1))
            point._obj.y = round(0.75 * (BINDING.height - 1))
            return True
        def mouse_event(self, flag, *_args): events.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    monkeypatch.setattr(mvp_local_native.time, "sleep", lambda _seconds: None)
    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=lambda binding: events.append(("safety", binding)),
        authorization=InputAuthorization.account_startup(
            lambda: events.append("authorize") or True
        ),
        capture=lambda: events.append("capture") or final_frame,
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: events.append("clock") or 1.0,
        detector=lambda frame, action, font: (
            events.append(("proof", action, font, int(frame[0, 0, 0])))
            or (0.5, 0.75)
        ),
    )

    assert subject.click_okay((0.5, 0.75)) is True
    proof_index = next(
        i for i, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "proof"
    )
    for event in ("show", "top", "foreground-set", "map", "cursor-set"):
        assert events.index(event) < proof_index
    assert not any(
        event in {"authorize", "clock"}
        for event in events[proof_index + 1:]
        if isinstance(event, str)
    )
    assert not any(
        isinstance(event, tuple) and event[0] == "safety"
        for event in events[proof_index + 1:]
    )
    assert events.count(0x0002) == 1
    assert events.count(0x0004) == 1
    assert subject.click_okay((0.5, 0.75)) is False
    assert not np.any(final_frame)


@pytest.mark.parametrize("fresh", [None, (0.7, 0.75), (0.5, 0.9)])
def test_native_okay_rejects_disappeared_or_moved_fresh_target(
    monkeypatch: pytest.MonkeyPatch, fresh,
) -> None:
    mouse: list[int] = []
    frame = np.ones((BINDING.height, BINDING.width, 3), np.uint8)

    class User32:
        def ShowWindow(self, *_args): return True
        def BringWindowToTop(self, *_args): return True
        def SetForegroundWindow(self, *_args): return True
        def GetForegroundWindow(self): return 10
        def GetAncestor(self, *_args): return 10
        def ClientToScreen(self, _hwnd, _point): return True
        def SetCursorPos(self, _x, _y): return True
        def mouse_event(self, flag, *_args): mouse.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=lambda _binding: None,
        authorization=InputAuthorization.account_startup(lambda: True),
        capture=lambda: frame,
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        detector=lambda *_args: fresh,
    )

    assert subject.click_okay((0.5, 0.75)) is False
    assert mouse == []
    assert not np.any(frame)


@pytest.mark.parametrize("blocked", ["revoked", "deadline", "binding"])
def test_native_okay_rechecks_authority_deadline_and_binding_before_final_capture(
    monkeypatch: pytest.MonkeyPatch, blocked: str,
) -> None:
    events: list[str] = []

    class User32:
        def mouse_event(self, flag, *_args): events.append(str(flag))

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    def safety(_binding):
        events.append("safety")
        if blocked == "binding":
            raise RuntimeError("changed")

    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=safety,
        authorization=InputAuthorization.account_startup(
            lambda: events.append("authorization") or blocked != "revoked"
        ),
        capture=lambda: events.append("capture") or pytest.fail("capture forbidden"),
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: events.append("clock") or (10.0 if blocked == "deadline" else 1.0),
        detector=lambda *_args: pytest.fail("detector forbidden"),
    )

    assert subject.click_okay((0.5, 0.75)) is False
    assert "capture" not in events
    assert "2" not in events and "4" not in events


@pytest.mark.parametrize("blocked", ["foreground", "cursor"])
def test_native_okay_rejects_foreground_or_cursor_change_after_proof(
    monkeypatch: pytest.MonkeyPatch, blocked: str,
) -> None:
    mouse: list[int] = []
    frame = np.ones((BINDING.height, BINDING.width, 3), np.uint8)
    foreground_reads = 0

    class User32:
        def ShowWindow(self, *_args): return True
        def BringWindowToTop(self, *_args): return True
        def SetForegroundWindow(self, *_args): return True
        def GetForegroundWindow(self):
            nonlocal foreground_reads
            foreground_reads += 1
            return 99 if blocked == "foreground" and foreground_reads == 2 else 10
        def GetAncestor(self, hwnd, *_args): return int(hwnd)
        def ClientToScreen(self, _hwnd, _point): return True
        def SetCursorPos(self, _x, _y): return True
        def GetCursorPos(self, point):
            point._obj.x = 0 if blocked == "cursor" else round(0.5 * (BINDING.width - 1))
            point._obj.y = 0 if blocked == "cursor" else round(0.75 * (BINDING.height - 1))
            return True
        def mouse_event(self, flag, *_args): mouse.append(flag)

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=lambda _binding: None,
        authorization=InputAuthorization.account_startup(lambda: True),
        capture=lambda: frame,
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        detector=lambda *_args: (0.5, 0.75),
    )

    assert subject.click_okay((0.5, 0.75)) is False
    assert mouse == []
    assert not np.any(frame)


def test_native_okay_mouse_up_failure_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mouse: list[int] = []
    frame = np.ones((BINDING.height, BINDING.width, 3), np.uint8)

    class User32:
        def ShowWindow(self, *_args): return True
        def BringWindowToTop(self, *_args): return True
        def SetForegroundWindow(self, *_args): return True
        def GetForegroundWindow(self): return 10
        def GetAncestor(self, *_args): return 10
        def ClientToScreen(self, _hwnd, _point): return True
        def SetCursorPos(self, _x, _y): return True
        def GetCursorPos(self, point):
            point._obj.x = round(0.5 * (BINDING.width - 1))
            point._obj.y = round(0.75 * (BINDING.height - 1))
            return True
        def mouse_event(self, flag, *_args):
            mouse.append(flag)
            if flag == 0x0004:
                raise RuntimeError("cleanup failure")

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    sleep_calls = 0

    def sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise RuntimeError("primary failure")

    monkeypatch.setattr(mvp_local_native.time, "sleep", sleep)
    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=lambda _binding: None,
        authorization=InputAuthorization.account_startup(lambda: True),
        capture=lambda: frame,
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        detector=lambda *_args: (0.5, 0.75),
    )

    with pytest.raises(RuntimeError, match="primary failure") as raised:
        subject.click_okay((0.5, 0.75))
    assert mouse == [0x0002, 0x0004]
    assert raised.value.__notes__ == ["mouse-up cleanup also failed"]


def test_native_okay_down_exception_still_attempts_one_up_and_preserves_down_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mouse: list[int] = []
    frame = np.ones((BINDING.height, BINDING.width, 3), np.uint8)

    class User32:
        def ShowWindow(self, *_args): return True
        def BringWindowToTop(self, *_args): return True
        def SetForegroundWindow(self, *_args): return True
        def GetForegroundWindow(self): return 10
        def GetAncestor(self, *_args): return 10
        def ClientToScreen(self, _hwnd, _point): return True
        def SetCursorPos(self, _x, _y): return True
        def GetCursorPos(self, point):
            point._obj.x = round(0.5 * (BINDING.width - 1))
            point._obj.y = round(0.75 * (BINDING.height - 1))
            return True
        def mouse_event(self, flag, *_args):
            mouse.append(flag)
            if flag == 0x0002:
                raise RuntimeError("down failure")
            raise RuntimeError("up failure")

    monkeypatch.setattr(mvp_local_native.ctypes, "WinDLL", lambda *_a, **_k: User32())
    monkeypatch.setattr(mvp_local_native, "_configure_input_signatures", lambda _api: None)
    subject = mvp_local_native.Win32AccountStartupInput(
        BINDING,
        safety_check=lambda _binding: None,
        authorization=InputAuthorization.account_startup(lambda: True),
        capture=lambda: frame,
        font_path="private-font",
        deadline=10.0,
        monotonic=lambda: 1.0,
        detector=lambda *_args: (0.5, 0.75),
    )

    with pytest.raises(RuntimeError, match="down failure") as raised:
        subject.click_okay((0.5, 0.75))
    assert mouse == [0x0002, 0x0004]
    assert raised.value.__notes__ == ["mouse-up cleanup also failed"]

