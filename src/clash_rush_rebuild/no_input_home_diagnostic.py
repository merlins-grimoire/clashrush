"""Donor-derived no-input Home-vs-Builder diagnostic controller.

Copied and adapted from BasePilot ``app/core/bot.py:18,223-225,242-248,
1570-1584`` at commit 4ede1efd220ffc79a5b490cfd3788b44d2584da4
(MIT). No BasePilot input or action caller is present.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np

from .basepilot_vision import BasePilotGeometry, BasePilotRecognitionError, VisionService
from .basepilot_window import BasePilotCaptureError, WindowService

_HOME_VILLAGE_BUILDER_TEMPLATES = ("builder.png", "gbuilder.png")


class HomeDiagnosticResult(StrEnum):
    HOME = "HOME"
    BUILDER = "BUILDER"
    UNKNOWN = "UNKNOWN"


def _raise_observation_error() -> None:
    raise BasePilotRecognitionError("Home recognition failed") from None


def _raise_capture_error() -> None:
    raise BasePilotCaptureError("Home capture failed") from None


class NoInputHomeDiagnosticController:
    """The copied BasePilot read-only controller seam with closed scalar output."""

    def __init__(self, window: WindowService) -> None:
        if type(window) is not WindowService:
            raise BasePilotRecognitionError("exact donor-derived window service required")
        self.window = window
        self._geometry: BasePilotGeometry | None = None
        self.vision: VisionService | None = None

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
        self.vision = VisionService(self._geometry)

    def _find_home_village_builder(
        self,
        frame: np.ndarray,
        region: tuple[int, int, int, int],
    ) -> tuple[int | None, int | None]:
        if type(self.vision) is not VisionService:
            raise BasePilotRecognitionError("frame geometry was not initialized")
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
            raise BasePilotRecognitionError("frame geometry was not initialized")
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
        return HomeDiagnosticResult.UNKNOWN

    @classmethod
    def detect_frame(cls, frame: np.ndarray) -> HomeDiagnosticResult:
        """Fixture/differential entry through the copied production recognizer."""
        subject = object.__new__(cls)
        subject.window = None
        subject._geometry = None
        subject.vision = None
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
