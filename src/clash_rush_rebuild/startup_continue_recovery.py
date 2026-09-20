"""CoC_Bot-derived startup Continue recovery with closed local boundaries.

The control flow and text-template matcher are adapted from CoC_Bot
``src/utils.py:335-352,522-580,1631-1716`` at pinned commit
``a5c943afed0ed3b9abedbbc228b0889145ecaf24`` (MIT). Font bytes,
captured pixels, Android-debug transport, update/network calls, and broad popup
actions are absent.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .lifecycle import PlayerBinding


class StartupContinueError(RuntimeError):
    """One closed startup-recovery boundary failed."""


class StartupContinueResult(StrEnum):
    HOME = "HOME"
    BUILDER = "BUILDER"
    UPDATE_REQUIRED = "UPDATE_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ContinueMatch:
    x: float
    y: float
    confidence: float


class ContinueInputPort(Protocol):
    def click_continue(
        self, binding: PlayerBinding, x: float, y: float
    ) -> bool: ...


class CapturePort(Protocol):
    def screenshot(self) -> np.ndarray: ...


def render_text(
    text: str,
    font_path: Path,
    font_size: int,
    color: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    """Render the donor text template in memory from an operator font."""
    try:
        path = Path(font_path)
        if (
            type(text) is not str
            or text not in {"Continue", "Update", "UPDATE"}
            or type(font_size) is not int
            or not 1 <= font_size <= 128
            or not path.is_file()
        ):
            raise ValueError
        font = ImageFont.truetype(str(path), font_size)
        temporary = Image.new("RGB", (1, 1))
        bbox = ImageDraw.Draw(temporary).textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0:
            raise ValueError
        rendered = Image.new("RGB", (width, height), (0, 0, 0))
        ImageDraw.Draw(rendered).text(
            (-bbox[0], -bbox[1]), text, font=font, fill=color
        )
        return np.asarray(rendered).copy()
    except BaseException:
        raise StartupContinueError("FONT_UNAVAILABLE") from None


def _locate(
    template: np.ndarray,
    frame: np.ndarray,
    *,
    threshold: float,
) -> ContinueMatch | None:
    if (
        type(template) is not np.ndarray
        or type(frame) is not np.ndarray
        or template.size == 0
        or frame.size == 0
    ):
        return None
    selected_template = template
    selected_frame = frame
    if selected_template.ndim == 3:
        selected_template = cv2.cvtColor(selected_template, cv2.COLOR_BGR2GRAY)
    if selected_frame.ndim == 3:
        selected_frame = cv2.cvtColor(selected_frame, cv2.COLOR_BGR2GRAY)
    height, width = selected_template.shape[:2]
    frame_height, frame_width = selected_frame.shape[:2]
    if height > frame_height or width > frame_width:
        return None
    response = cv2.matchTemplate(
        selected_frame, selected_template, cv2.TM_CCOEFF_NORMED
    )
    _minimum, confidence, _minimum_location, location = cv2.minMaxLoc(response)
    if not math.isfinite(float(confidence)) or float(confidence) <= threshold:
        return None
    return ContinueMatch(
        (location[0] + width / 2) / frame_width,
        (location[1] + height / 2) / frame_height,
        float(confidence),
    )


def batch_locate(
    templates: tuple[np.ndarray, ...],
    frame: np.ndarray,
    *,
    threshold: float = 0.7,
) -> tuple[ContinueMatch | None, ...]:
    """Preserve CoC_Bot's one-result-per-template batch contract."""
    if type(templates) is not tuple:
        raise StartupContinueError("TEMPLATE_MALFORMED") from None
    try:
        return tuple(
            _locate(template, frame, threshold=threshold) for template in templates
        )
    except BaseException:
        raise StartupContinueError("RECOGNITION_FAILED") from None


def _scaled_text_templates(
    text: str, font_path: Path, frame_height: int
) -> tuple[np.ndarray, ...]:
    if type(frame_height) is not int or frame_height <= 0:
        raise StartupContinueError("TEMPLATE_MALFORMED") from None
    scale = frame_height / 720.0
    templates = []
    for donor_size in range(25, 31):
        rendered = render_text(text, font_path, donor_size)
        if scale != 1.0:
            rendered = cv2.resize(
                rendered,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_NEAREST,
            )
        templates.append(rendered)
    return tuple(templates)


def find_continue(
    frame: np.ndarray, font_path: Path, frame_height: int
) -> ContinueMatch | None:
    matches = batch_locate(
        _scaled_text_templates("Continue", font_path, frame_height), frame
    )
    accepted = tuple(match for match in matches if match is not None)
    return max(accepted, key=lambda match: match.confidence) if accepted else None


def find_update(frame: np.ndarray, font_path: Path, frame_height: int) -> bool:
    for text in ("Update", "UPDATE"):
        if any(
            match is not None
            for match in batch_locate(
                _scaled_text_templates(text, font_path, frame_height), frame
            )
        ):
            return True
    return False


class StartupContinueController:
    """Bounded donor startup order with exactly one Continue attempt."""

    def __init__(
        self,
        *,
        binding: PlayerBinding,
        window: CapturePort,
        input_port: ContinueInputPort,
        font_path: Path,
        classify_village: Callable[[np.ndarray], StartupContinueResult],
        find_continue: Callable[[np.ndarray, Path, int], ContinueMatch | None] = find_continue,
        find_update: Callable[[np.ndarray, Path, int], bool] = find_update,
    ) -> None:
        if (
            type(binding) is not PlayerBinding
            or not callable(getattr(window, "screenshot", None))
            or not callable(getattr(input_port, "click_continue", None))
            or not callable(classify_village)
            or not callable(find_continue)
            or not callable(find_update)
        ):
            raise StartupContinueError("BOUNDARY_MALFORMED") from None
        self._binding = binding
        self._window = window
        self._input = input_port
        self._font_path = Path(font_path)
        self._classify_village = classify_village
        self._find_continue = find_continue
        self._find_update = find_update

    def run(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
        settle_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> StartupContinueResult:
        values = (timeout_seconds, poll_interval_seconds, settle_seconds)
        if any(
            type(value) not in (int, float)
            or type(value) is bool
            or not math.isfinite(float(value))
            or value <= 0
            for value in values
        ) or not callable(monotonic) or not callable(wait):
            raise StartupContinueError("BOUNDARY_MALFORMED") from None
        started = monotonic()
        if type(started) not in (int, float) or not math.isfinite(float(started)):
            raise StartupContinueError("CLOCK_FAILED") from None
        deadline = float(started) + float(timeout_seconds)
        clicked = False
        while True:
            frame: np.ndarray | None = None
            result = StartupContinueResult.UNKNOWN
            match: ContinueMatch | None = None
            update_required = False
            try:
                frame = self._window.screenshot()
                result = self._classify_village(frame)
                if result not in (
                    StartupContinueResult.HOME,
                    StartupContinueResult.BUILDER,
                    StartupContinueResult.UNKNOWN,
                ):
                    raise ValueError
                if result is StartupContinueResult.UNKNOWN:
                    update_required = self._find_update(
                        frame, self._font_path, self._binding.height
                    ) is True
                    if not update_required:
                        match = self._find_continue(
                            frame, self._font_path, self._binding.height
                        )
            except StartupContinueError:
                raise
            except BaseException:
                raise StartupContinueError("RECOGNITION_FAILED") from None
            finally:
                if type(frame) is np.ndarray:
                    frame.fill(0)
                frame = None
            if result in (
                StartupContinueResult.HOME,
                StartupContinueResult.BUILDER,
            ):
                return result
            if update_required:
                return StartupContinueResult.UPDATE_REQUIRED
            if match is not None and not clicked:
                try:
                    delivered = self._input.click_continue(
                        self._binding, match.x, match.y
                    )
                except BaseException:
                    delivered = False
                if delivered is not True:
                    raise StartupContinueError("INPUT_FAILED") from None
                clicked = True
                wait(float(settle_seconds))
            now = monotonic()
            if type(now) not in (int, float) or not math.isfinite(float(now)):
                raise StartupContinueError("CLOCK_FAILED") from None
            if float(now) >= deadline:
                return StartupContinueResult.UNKNOWN
            if match is None or clicked:
                wait(min(float(poll_interval_seconds), deadline - float(now)))


__all__ = [
    "ContinueMatch",
    "StartupContinueController",
    "StartupContinueError",
    "StartupContinueResult",
    "batch_locate",
    "find_continue",
    "find_update",
    "render_text",
]