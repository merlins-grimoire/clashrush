"""Closed one-click Welcome Back startup seam for account readiness.

The bounded capture -> detect -> click -> settle shape is adapted from
ClashAutomation commit c41fe12a6df051e241c695b71b6859286e24c612,
``utils/base_actions.py:179-236`` and ``utils/object_detection.py:168-265``.
Only the local exact Welcome Back/Okay detector is admitted.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .input_authorization import InputAction
from .mvp_account_ready import AccountReadinessError
from .startup_debug import popup_position


def _time(monotonic: Callable[[], float], deadline: float) -> float:
    try:
        value = monotonic()
    except BaseException as exc:
        raise AccountReadinessError("startup clock failed") from exc
    if (
        type(value) not in (int, float)
        or type(value) is bool
        or not math.isfinite(float(value))
        or float(value) >= deadline
    ):
        raise AccountReadinessError("startup deadline expired")
    return float(value)


def await_account_home(
    *,
    capture: Callable[[], np.ndarray],
    home_verified: Callable[[np.ndarray], bool],
    startup_input_factory: Callable[[], object],
    font_path: str | Path,
    deadline: float,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
    popup_detector: Callable[..., tuple[float, float] | None] = popup_position,
) -> bool:
    """Accept Home, or dismiss one exact Okay and then poll only for Home."""
    if type(deadline) not in (int, float) or type(deadline) is bool or not math.isfinite(float(deadline)):
        raise AccountReadinessError("startup deadline is malformed")
    clicked = False
    while True:
        _time(monotonic, float(deadline))
        try:
            frame = capture()
        except BaseException as exc:
            raise AccountReadinessError("startup capture failed") from exc
        try:
            if type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
                raise AccountReadinessError("startup frame is malformed")
            if home_verified(frame) is True:
                return True
            if clicked:
                point = None
            else:
                point = popup_detector(frame, InputAction.STARTUP_OKAY, font_path)
        except AccountReadinessError:
            raise
        except BaseException as exc:
            raise AccountReadinessError("startup classification failed") from exc
        finally:
            if type(frame) is np.ndarray and frame.flags.writeable:
                frame.fill(0)
            frame = None
        if clicked:
            _time(monotonic, float(deadline))
            wait(0.25)
            continue
        if point is None:
            raise AccountReadinessError("exact Welcome Back popup unavailable")
        startup_input = startup_input_factory()
        clicked = True
        try:
            sent = startup_input.click_okay(point)
        except BaseException as exc:
            raise AccountReadinessError("startup input unavailable") from exc
        if sent is not True:
            raise AccountReadinessError("startup input failed")
        _time(monotonic, float(deadline))
        wait(0.25)


__all__ = ["await_account_home"]
