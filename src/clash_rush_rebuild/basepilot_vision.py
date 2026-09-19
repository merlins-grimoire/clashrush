"""BasePilot-derived aspect geometry and template matching.

Copied and adapted from BasePilot ``app/config.py``, ``app/utils/common.py``,
and ``app/services/vision.py:130-226`` at commit
4ede1efd220ffc79a5b490cfd3788b44d2584da4 (MIT).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib.resources import files

import cv2
import numpy as np

ASPECT_16_10 = "16_10"
ASPECT_16_9 = "16_9"
_ASPECT_TOLERANCE = 0.03
ASPECT_BASELINE: dict[str, tuple[int, int]] = {
    ASPECT_16_9: (2560, 1440),
    ASPECT_16_10: (2560, 1600),
}
_ALLOWED_TEMPLATES = frozenset({"builder.png", "gbuilder.png", "mbuilder.png"})


class BasePilotRecognitionError(RuntimeError):
    """Copied donor recognition evidence is malformed or unavailable."""


def resolve_aspect_key(width: int, height: int) -> str | None:
    """Return the donor aspect key, preserving its tolerance and nearest match."""
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        return None
    ratio = width / height
    distance_16_9 = abs(ratio - 1.77778)
    distance_16_10 = abs(ratio - 1.6)
    if min(distance_16_9, distance_16_10) > _ASPECT_TOLERANCE:
        return None
    if distance_16_9 < distance_16_10:
        return ASPECT_16_9
    return ASPECT_16_10


@dataclass(frozen=True, slots=True)
class BasePilotGeometry:
    aspect_key: str
    ref_width: int
    ref_height: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            type(self.aspect_key) is not str
            or self.aspect_key not in ASPECT_BASELINE
            or type(self.ref_width) is not int
            or type(self.ref_height) is not int
            or (self.ref_width, self.ref_height) != ASPECT_BASELINE[self.aspect_key]
            or type(self.width) is not int
            or type(self.height) is not int
            or self.width <= 0
            or self.height <= 0
            or resolve_aspect_key(self.width, self.height) != self.aspect_key
        ):
            raise BasePilotRecognitionError("supported immutable frame geometry required")

    @classmethod
    def from_frame(
        cls,
        frame: np.ndarray,
        *,
        expected_size: tuple[int, int] | None = None,
    ) -> BasePilotGeometry:
        if (
            type(frame) is not np.ndarray
            or frame.dtype != np.uint8
            or frame.ndim != 3
            or frame.shape[2] != 3
            or frame.size == 0
        ):
            raise BasePilotRecognitionError("exact nonempty BGR frame required")
        height, width = frame.shape[:2]
        if (
            expected_size is not None
            and (
                type(expected_size) is not tuple
                or len(expected_size) != 2
                or any(type(value) is not int for value in expected_size)
                or expected_size != (width, height)
            )
        ):
            raise BasePilotRecognitionError("frame disagrees with lifecycle binding")
        aspect_key = resolve_aspect_key(width, height)
        if aspect_key is None:
            raise BasePilotRecognitionError("capture aspect is unsupported")
        ref_width, ref_height = ASPECT_BASELINE[aspect_key]
        return cls(aspect_key, ref_width, ref_height, width, height)


def _load_template(geometry: BasePilotGeometry, template_name: str) -> np.ndarray:
    if type(geometry) is not BasePilotGeometry:
        raise BasePilotRecognitionError("immutable frame geometry required")
    if type(template_name) is not str or template_name not in _ALLOWED_TEMPLATES:
        raise BasePilotRecognitionError("template name is not allowlisted")
    resource = (
        files("clash_rush_rebuild")
        / "assets"
        / "basepilot_templates"
        / geometry.aspect_key
        / template_name
    )
    if not resource.is_file():
        raise BasePilotRecognitionError("packaged template is unavailable")
    payload = resource.read_bytes()
    encoded = np.frombuffer(payload, dtype=np.uint8)
    template = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    encoded = None
    payload = b""
    if type(template) is not np.ndarray or template.size == 0:
        raise BasePilotRecognitionError("packaged template is malformed")
    return template


class VisionService:
    """Copied BasePilot template-matching subset bound to immutable geometry."""

    def __init__(self, geometry: BasePilotGeometry) -> None:
        if type(geometry) is not BasePilotGeometry:
            raise BasePilotRecognitionError("immutable frame geometry required")
        self._geometry = geometry

    def _template_scale_xy(self, screen_img: np.ndarray) -> tuple[float, float]:
        height, width = screen_img.shape[:2]
        if (width, height) != (self._geometry.width, self._geometry.height):
            raise BasePilotRecognitionError("frame geometry changed")
        return (
            width / self._geometry.ref_width,
            height / self._geometry.ref_height,
        )

    def _resize_template_for_screen(
        self, template: np.ndarray, screen_img: np.ndarray
    ) -> np.ndarray:
        scale_x, scale_y = self._template_scale_xy(screen_img)
        if abs(scale_x - 1) < 0.001 and abs(scale_y - 1) < 0.001:
            return template
        template_height, template_width = template.shape[:2]
        new_width = max(1, int(round(template_width * scale_x)))
        new_height = max(1, int(round(template_height * scale_y)))
        interpolation = cv2.INTER_AREA if min(scale_x, scale_y) < 1 else cv2.INTER_CUBIC
        return cv2.resize(
            template,
            (new_width, new_height),
            interpolation=interpolation,
        )

    @staticmethod
    def bottom_half_region(screen_img: np.ndarray) -> tuple[int, int, int, int]:
        height, width = screen_img.shape[:2]
        y0 = height // 2
        return (0, y0, width, height - y0)

    @staticmethod
    def top_half_region(screen_img: np.ndarray) -> tuple[int, int, int, int]:
        height, width = screen_img.shape[:2]
        y1 = height // 2
        return (0, 0, width, y1)

    def find_template(
        self,
        screen_img: np.ndarray,
        template_name: str,
        threshold: float = 0.8,
        region: tuple[int, int, int, int] | None = None,
        *,
        scale_template: bool = True,
    ) -> tuple[int | None, int | None]:
        """Preserve BasePilot's match, scaling, ROI, and center contract."""
        if type(screen_img) is not np.ndarray or screen_img.dtype != np.uint8:
            raise BasePilotRecognitionError("exact BGR screen image required")
        if (
            type(threshold) not in (int, float)
            or type(threshold) is bool
            or not math.isfinite(float(threshold))
            or not 0 <= float(threshold) <= 1
            or type(scale_template) is not bool
        ):
            raise BasePilotRecognitionError("template policy is malformed")
        template = _load_template(self._geometry, template_name)
        search_img: np.ndarray | None = None
        result: np.ndarray | None = None
        try:
            if scale_template:
                template = self._resize_template_for_screen(template, screen_img)
            if region is not None:
                if (
                    type(region) is not tuple
                    or len(region) != 4
                    or any(type(value) is not int for value in region)
                ):
                    raise BasePilotRecognitionError("template region is malformed")
                x, y, width, height = region
                screen_height, screen_width = screen_img.shape[:2]
                if (
                    x < 0
                    or y < 0
                    or width <= 0
                    or height <= 0
                    or x + width > screen_width
                    or y + height > screen_height
                ):
                    raise BasePilotRecognitionError("template region is out of bounds")
                search_img = screen_img[y : y + height, x : x + width]
                offset_x, offset_y = x, y
            else:
                search_img = screen_img
                offset_x, offset_y = 0, 0
            template_height, template_width = template.shape[:2]
            search_height, search_width = search_img.shape[:2]
            if template_width > search_width or template_height > search_height:
                return (None, None)
            result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_location = cv2.minMaxLoc(result)
            if max_value < threshold:
                return (None, None)
            center_x = offset_x + max_location[0] + template_width // 2
            center_y = offset_y + max_location[1] + template_height // 2
            return (center_x, center_y)
        except BasePilotRecognitionError:
            raise
        except BaseException:
            template = None
            search_img = None
            result = None
            raise BasePilotRecognitionError("template recognition failed") from None
        finally:
            template = None
            search_img = None
            result = None
