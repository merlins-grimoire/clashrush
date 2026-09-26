"""Closed one-click Welcome Back startup seam for account readiness.

The bounded capture -> detect -> click -> settle shape is adapted from
ClashAutomation commit c41fe12a6df051e241c695b71b6859286e24c612,
``utils/base_actions.py:179-236`` and ``utils/object_detection.py:168-265``.
Only the local exact Welcome Back/Okay detector is admitted.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

import numpy as np

from .input_authorization import InputAction
from .mvp_account_ready import AccountReadinessError
from .startup_debug import popup_position


class StartupInputPhase(StrEnum):
    NO_GESTURE = "NO_GESTURE"
    STARTED_UNCERTAIN = "STARTED_UNCERTAIN"
    COMPLETED = "COMPLETED"


def _tag_startup_error(
    error: AccountReadinessError, phase: StartupInputPhase
) -> AccountReadinessError:
    error.startup_input_phase = phase.value
    return error


def _phase_from_exception(
    error: BaseException, default: StartupInputPhase
) -> StartupInputPhase:
    value = getattr(error, "startup_input_phase", None)
    try:
        return StartupInputPhase(value)
    except (TypeError, ValueError):
        return default


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
) -> StartupInputPhase:
    """Accept Home, or dismiss one exact Okay and return its gesture phase."""
    if (
        type(deadline) not in (int, float)
        or type(deadline) is bool
        or not math.isfinite(float(deadline))
    ):
        raise _tag_startup_error(
            AccountReadinessError("startup deadline is malformed"),
            StartupInputPhase.NO_GESTURE,
        )
    clicked = False
    input_phase = StartupInputPhase.NO_GESTURE
    while True:
        try:
            _time(monotonic, float(deadline))
        except AccountReadinessError as exc:
            raise _tag_startup_error(exc, input_phase)
        try:
            frame = capture()
        except BaseException as exc:
            raise _tag_startup_error(
                AccountReadinessError("startup capture failed"), input_phase
            ) from exc
        try:
            if (
                type(frame) is not np.ndarray
                or frame.dtype != np.uint8
                or frame.ndim != 3
                or frame.shape[2] != 3
                or frame.size == 0
            ):
                raise _tag_startup_error(
                    AccountReadinessError("startup frame is malformed"), input_phase
                )
            if home_verified(frame) is True:
                return input_phase
            if clicked:
                point = None
            else:
                point = popup_detector(
                    frame, InputAction.STARTUP_OKAY, font_path
                )
        except AccountReadinessError:
            raise
        except BaseException as exc:
            raise _tag_startup_error(
                AccountReadinessError("startup classification failed"), input_phase
            ) from exc
        finally:
            if type(frame) is np.ndarray and frame.flags.writeable:
                frame.fill(0)
            frame = None
        if clicked or point is None:
            try:
                _time(monotonic, float(deadline))
                wait(0.25)
            except AccountReadinessError as exc:
                raise _tag_startup_error(exc, input_phase)
            except BaseException as exc:
                raise _tag_startup_error(
                    AccountReadinessError("startup wait failed"), input_phase
                ) from exc
            continue
        try:
            startup_input = startup_input_factory()
        except BaseException as exc:
            raise _tag_startup_error(
                AccountReadinessError("startup input unavailable"), input_phase
            ) from exc
        clicked = True
        try:
            sent = startup_input.click_okay(point)
        except BaseException as exc:
            input_phase = _phase_from_exception(
                exc, StartupInputPhase.STARTED_UNCERTAIN
            )
            raise _tag_startup_error(
                AccountReadinessError("startup input unavailable"), input_phase
            ) from exc
        if sent is not True:
            raise _tag_startup_error(
                AccountReadinessError("startup input failed"), input_phase
            )
        input_phase = StartupInputPhase.COMPLETED
        try:
            _time(monotonic, float(deadline))
            wait(0.25)
        except AccountReadinessError as exc:
            raise _tag_startup_error(exc, input_phase)
        except BaseException as exc:
            raise _tag_startup_error(
                AccountReadinessError("startup wait failed"), input_phase
            ) from exc


__all__ = ["StartupInputPhase", "await_account_home"]
