"""Donor-derived no-input Home-vs-Builder diagnostic controller.

Copied and adapted from BasePilot ``app/core/bot.py:18,223-225,242-248,
1570-1584`` at commit 4ede1efd220ffc79a5b490cfd3788b44d2584da4
(MIT). No BasePilot input or action caller is present.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from enum import StrEnum

import numpy as np

from .basepilot_vision import (
    BasePilotGeometry,
    BasePilotRecognitionError,
    RecognitionReason,
    VisionService,
)
from .basepilot_window import BasePilotCaptureError, WindowService
from .input_authorization import InputAuthorization

_HOME_VILLAGE_BUILDER_TEMPLATES = ("builder.png", "gbuilder.png")


class HomeDiagnosticResult(StrEnum):
    HOME = "HOME"
    BUILDER = "BUILDER"
    UNKNOWN = "UNKNOWN"


def _raise_observation_error() -> None:
    raise BasePilotRecognitionError(
        RecognitionReason.INVALID_EVIDENCE,
        "Home recognition failed",
    ) from None


def _raise_capture_error() -> None:
    raise BasePilotCaptureError("Home capture failed") from None


class NoInputHomeDiagnosticController:
    """The copied BasePilot read-only controller seam with closed scalar output."""

    def __init__(self, window: WindowService) -> None:
        if type(window) is not WindowService:
            raise BasePilotRecognitionError(
                RecognitionReason.INVALID_EVIDENCE,
                "exact donor-derived window service required",
            )
        self.window = window
        self._input_authorization = InputAuthorization.no_input_diagnostic()
        self._geometry: BasePilotGeometry | None = None
        self.vision: VisionService | None = None
        self._observation_reasons: list[RecognitionReason] = []

    @property
    def observation_reasons(self) -> tuple[str, ...]:
        return tuple(reason.value for reason in self._observation_reasons)

    def _record_reason(self, reason: RecognitionReason) -> None:
        if type(reason) is RecognitionReason:
            self._observation_reasons.append(reason)

    def _update_config_size(
        self,
        frame: np.ndarray,
        *,
        expected_size: tuple[int, int] | None = None,
    ) -> None:
        self._geometry = BasePilotGeometry.from_frame(
            frame,
            expected_size=expected_size,
        )
        if self._geometry.adaptation_reason is not None:
            self._record_reason(self._geometry.adaptation_reason)
        self.vision = VisionService(self._geometry)

    def _find_home_village_builder(
        self,
        frame: np.ndarray,
        region: tuple[int, int, int, int],
    ) -> tuple[int | None, int | None]:
        if type(self.vision) is not VisionService:
            raise BasePilotRecognitionError(
                RecognitionReason.INVALID_EVIDENCE,
                "frame geometry was not initialized",
            )
        for name in _HOME_VILLAGE_BUILDER_TEMPLATES:
            x, y = self.vision.find_template(frame, name, region=region)
            if not x:
                continue
            return (x, y)
        return (None, None)

    def _detect_village_type(
        self,
        frame: np.ndarray,
        *,
        expected_size: tuple[int, int] | None = None,
    ) -> HomeDiagnosticResult:
        if type(frame) is not np.ndarray or frame.size == 0:
            return HomeDiagnosticResult.UNKNOWN
        self._update_config_size(frame, expected_size=expected_size)
        if type(self.vision) is not VisionService:
            raise BasePilotRecognitionError(
                RecognitionReason.INVALID_EVIDENCE,
                "frame geometry was not initialized",
            )
        top_region = VisionService.top_half_region(frame)
        builder_x, _builder_y = self.vision.find_template(
            frame,
            "mbuilder.png",
            region=top_region,
        )
        if builder_x:
            return HomeDiagnosticResult.BUILDER
        home_x, _home_y = self._find_home_village_builder(frame, top_region)
        if home_x:
            return HomeDiagnosticResult.HOME
        self._record_reason(RecognitionReason.NO_MATCH)
        return HomeDiagnosticResult.UNKNOWN

    @classmethod
    def detect_frame(cls, frame: np.ndarray) -> HomeDiagnosticResult:
        """Fixture/differential entry through the copied production recognizer."""
        subject = object.__new__(cls)
        subject.window = None
        subject._geometry = None
        subject.vision = None
        subject._observation_reasons = []
        return subject._detect_village_type(frame)

    def observe(self) -> HomeDiagnosticResult:
        frame: np.ndarray | None = None
        result: HomeDiagnosticResult | None = None
        capture_failed = False
        recognition_failed = False
        try:
            try:
                frame = self.window.screenshot()
            except BaseException:
                capture_failed = True
            if not capture_failed:
                try:
                    result = self._detect_village_type(
                        frame,
                        expected_size=(
                            self.window._binding.width,
                            self.window._binding.height,
                        ),
                    )
                except BasePilotRecognitionError as exc:
                    self._record_reason(exc.reason)
                    result = HomeDiagnosticResult.UNKNOWN
                except BaseException:
                    recognition_failed = True
        except BaseException:
            recognition_failed = True
        finally:
            if type(frame) is np.ndarray:
                frame.fill(0)
            frame = None
        if capture_failed:
            result = None
            _raise_capture_error()
        if recognition_failed:
            result = None
            _raise_observation_error()
        if type(result) is not HomeDiagnosticResult:
            _raise_observation_error()
        return result

    def wait_for_readiness(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> HomeDiagnosticResult:
        """Poll memory-only frames within one CoC_Bot-style startup budget."""
        if (
            type(timeout_seconds) not in (int, float)
            or type(timeout_seconds) is bool
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
            or type(poll_interval_seconds) not in (int, float)
            or type(poll_interval_seconds) is bool
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
            or not callable(monotonic)
            or not callable(wait)
        ):
            _raise_observation_error()
        started = monotonic()
        if type(started) not in (int, float) or not math.isfinite(float(started)):
            _raise_observation_error()
        deadline = float(started) + float(timeout_seconds)
        captured = False
        while True:
            try:
                result = self.observe()
                captured = True
            except BasePilotCaptureError:
                self._record_reason(RecognitionReason.CAPTURE_FAILURE)
                result = HomeDiagnosticResult.UNKNOWN
            if result in (HomeDiagnosticResult.HOME, HomeDiagnosticResult.BUILDER):
                return result
            now = monotonic()
            if type(now) not in (int, float) or not math.isfinite(float(now)):
                _raise_observation_error()
            if float(now) >= deadline:
                self._record_reason(RecognitionReason.TIMEOUT)
                if not captured:
                    _raise_capture_error()
                return HomeDiagnosticResult.UNKNOWN
            wait(min(float(poll_interval_seconds), deadline - float(now)))
