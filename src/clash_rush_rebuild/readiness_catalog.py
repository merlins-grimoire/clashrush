"""Admitted public-synthetic catalog for the inert R10 readiness seam.

This module contains no captured or game-derived pixels. Templates are generated
from a fixed arithmetic motif and admitted only when the complete closed catalog,
public donor provenance, geometry, and scalar vocabulary match this revision.
It performs no capture, file I/O, logging, Status emission, or hashing.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


SCREENS = ("HOME", "LOADING", "BUILDER")
LAYOUTS = (
    "NOMINAL",
    "H_INSET_5",
    "H_INSET_10",
    "V_INSET_5",
    "V_INSET_10",
)
GENERATION = "SYNTHETIC_PUBLIC_V1"

SCALAR_GRAMMAR: Mapping[str, tuple[object, ...]] = MappingProxyType(
    {
        "schema": (3,),
        "method": ("ANCHOR_CONSTELLATION_V1",),
        "classification": (
            "HOME_SIGNATURE",
            "NON_HOME_SIGNATURE",
            "VIEWPORT_MISMATCH_SIGNATURE",
            "UNKNOWN",
        ),
        "source": ("HOME", "LOADING", "BUILDER", "UNVERIFIED"),
        "layout": ("NOMINAL", "OFFSET", "UNVERIFIED"),
        "observations": (0, 1),
        "reason": (
            "POSITIVE",
            "CATALOG_UNAVAILABLE",
            "CATALOG_INVALID",
            "BOUND_UNAVAILABLE",
            "BINDING_INVALID",
            "BINDING_UNVERIFIED",
            "CLOCK_INVALID",
            "DEADLINE",
            "CAPTURE_FAILED",
            "FRAME_INVALID",
            "HEALTH_UNVERIFIED",
            "UNSCORABLE",
            "NO_MATCH",
            "AMBIGUOUS",
            "REDUCTION_FAILED",
            "CLEANUP_UNPROVEN",
        ),
    }
)


class CatalogAdmissionError(ValueError):
    """The complete public-synthetic catalog failed closed admission."""


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    donor: str
    revision: str
    tree: str
    source_paths: tuple[str, ...]
    license_id: str
    attribution: str
    use: str


PROVENANCE = (
    ProvenanceRecord(
        donor="BasePilot",
        revision="4ede1efd220ffc79a5b490cfd3788b44d2584da4",
        tree="0553306bfd30a32e31e427a0b77ff22c41f55901",
        source_paths=("app/services/vision.py:185-267",),
        license_id="MIT",
        attribution="Copyright (c) 2026 Efe Bolukbasi",
        use="normalized-centered-correlation-and-region-bounds",
    ),
    ProvenanceRecord(
        donor="CoC_Bot",
        revision="a5c943afed0ed3b9abedbbc228b0889145ecaf24",
        tree="d79368fbe550036f1542883f18434e318016b279",
        source_paths=("src/utils.py:496-520", "src/utils.py:1572-1665"),
        license_id="MIT",
        attribution="Copyright (c) 2026 m24842",
        use="distinct-home-builder-bounded-geometry-evidence",
    ),
    ProvenanceRecord(
        donor="ClashAutomation",
        revision="c41fe12a6df051e241c695b71b6859286e24c612",
        tree="b549901e7ca8b871a17265517d730f0b3c84a618",
        source_paths=(
            "utils/game_window_controller.py:225-237",
            "utils/object_detection.py:43-57",
        ),
        license_id="MIT",
        attribution="Copyright (c) 2026 Caleb Welsh",
        use="render-target-bgr-order-and-bounds-evidence",
    ),
)


@dataclass(frozen=True, slots=True)
class Rectangle:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class Anchor:
    rectangle: Rectangle
    bgr: bytes


@dataclass(frozen=True, slots=True)
class Constellation:
    screen: str
    layout: str
    anchors: tuple[Anchor, Anchor, Anchor]


@dataclass(frozen=True, slots=True)
class CatalogCandidate:
    width: int
    height: int
    generation: str
    entries: tuple[Constellation, ...]
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True, slots=True)
class AdmittedCatalog:
    width: int
    height: int
    generation: str
    entries: tuple[Constellation, ...]
    provenance: tuple[ProvenanceRecord, ...]


_WIDTH = 640
_HEIGHT = 360
_NOMINAL_RECTANGLES = (
    Rectangle(48, 40, 64, 56),
    Rectangle(304, 72, 320, 88),
    Rectangle(544, 288, 560, 304),
)


def _invalid() -> None:
    raise CatalogAdmissionError("CATALOG_INVALID")


def _validated_rectangle(
    rectangle: object,
    frame_width: object,
    frame_height: object,
) -> tuple[Rectangle, int, int]:
    """Snapshot a bounded rectangle before performing coordinate arithmetic."""
    try:
        if (
            type(rectangle) is not Rectangle
            or type(frame_width) is not int
            or type(frame_height) is not int
            or (frame_width, frame_height) != (_WIDTH, _HEIGHT)
        ):
            _invalid()
        x0 = rectangle.x0
        y0 = rectangle.y0
        x1 = rectangle.x1
        y1 = rectangle.y1
        if any(type(value) is not int for value in (x0, y0, x1, y1)):
            _invalid()
        rectangle_width = x1 - x0
        rectangle_height = y1 - y0
        if (
            not 8 <= rectangle_width <= 96
            or not 8 <= rectangle_height <= 96
            or x0 < 0
            or y0 < 0
            or x1 > frame_width
            or y1 > frame_height
        ):
            _invalid()
        return Rectangle(x0, y0, x1, y1), rectangle_width, rectangle_height
    except CatalogAdmissionError:
        raise
    except BaseException:
        _invalid()


def _mapped(value: int, extent: int, inset: int) -> int:
    return (extent * inset + value * (100 - 2 * inset)) // 100


def transform_rectangle(
    rectangle: Rectangle,
    layout: str,
    width: int,
    height: int,
) -> Rectangle:
    """Apply one closed R10 viewport hypothesis with floor boundaries."""
    if (
        type(rectangle) is not Rectangle
        or type(layout) is not str
        or layout not in LAYOUTS
        or type(width) is not int
        or type(height) is not int
    ):
        _invalid()
    rectangle, _, _ = _validated_rectangle(rectangle, width, height)
    if layout == "NOMINAL":
        return rectangle
    inset = 5 if layout.endswith("_5") else 10
    if layout.startswith("H_"):
        transformed = Rectangle(
            rectangle.x0,
            _mapped(rectangle.y0, height, inset),
            rectangle.x1,
            _mapped(rectangle.y1, height, inset),
        )
    else:
        transformed = Rectangle(
            _mapped(rectangle.x0, width, inset),
            rectangle.y0,
            _mapped(rectangle.x1, width, inset),
            rectangle.y1,
        )
    transformed, _, _ = _validated_rectangle(transformed, width, height)
    return transformed


def _motif(screen_index: int, anchor_index: int, width: int, height: int) -> bytes:
    values = bytearray()
    seed = 29 + screen_index * 53 + anchor_index * 17
    for y in range(height):
        for x in range(width):
            values.extend(
                (
                    (seed + x * 11 + y * 3) % 256,
                    (seed * 3 + x * 5 + y * 13) % 256,
                    (seed * 7 + x * 17 + y * 7) % 256,
                )
            )
    return bytes(values)


def synthetic_candidate() -> CatalogCandidate:
    """Create the sole public procedural candidate; accepts no external pixels."""
    entries: list[Constellation] = []
    for screen_index, screen in enumerate(SCREENS):
        for layout in LAYOUTS:
            anchors: list[Anchor] = []
            for anchor_index, nominal in enumerate(_NOMINAL_RECTANGLES):
                rectangle = transform_rectangle(nominal, layout, _WIDTH, _HEIGHT)
                anchors.append(
                    Anchor(
                        rectangle,
                        _motif(
                            screen_index,
                            anchor_index,
                            rectangle.width,
                            rectangle.height,
                        ),
                    )
                )
            entries.append(Constellation(screen, layout, tuple(anchors)))
    return CatalogCandidate(
        _WIDTH,
        _HEIGHT,
        GENERATION,
        tuple(entries),
        PROVENANCE,
    )


def _has_channel_variance(values: bytes) -> bool:
    for channel in range(3):
        first = values[channel]
        if not any(
            values[offset + channel] != first
            for offset in range(0, len(values), 3)
        ):
            return False
    return True


def _overlap(left: Rectangle, right: Rectangle) -> bool:
    return (
        left.x0 < right.x1
        and right.x0 < left.x1
        and left.y0 < right.y1
        and right.y0 < left.y1
    )


def _widely_separated(
    rectangles: tuple[Rectangle, ...], width: int, height: int
) -> bool:
    for index, left in enumerate(rectangles):
        for right in rectangles[index + 1 :]:
            center_dx_twice = abs((left.x0 + left.x1) - (right.x0 + right.x1))
            center_dy_twice = abs((left.y0 + left.y1) - (right.y0 + right.y1))
            if center_dx_twice * 2 >= width or center_dy_twice * 2 >= height:
                return True
    return False


def _meaningfully_shifted(
    nominal: tuple[Rectangle, ...],
    shifted: tuple[Rectangle, ...],
    width: int,
    height: int,
) -> bool:
    shifted_count = 0
    for original, current in zip(nominal, shifted, strict=True):
        center_dx_twice = abs((original.x0 + original.x1) - (current.x0 + current.x1))
        center_dy_twice = abs((original.y0 + original.y1) - (current.y0 + current.y1))
        if center_dx_twice * 50 > width or center_dy_twice * 50 > height:
            shifted_count += 1
    return shifted_count >= 2


def _provenance_is_exact(provenance: object) -> bool:
    if type(provenance) is not tuple or len(provenance) != len(PROVENANCE):
        return False
    for supplied, expected in zip(provenance, PROVENANCE, strict=True):
        if type(supplied) is not ProvenanceRecord:
            return False
        fields = (
            supplied.donor,
            supplied.revision,
            supplied.tree,
            supplied.license_id,
            supplied.attribution,
            supplied.use,
        )
        if any(type(value) is not str for value in fields):
            return False
        if (
            type(supplied.source_paths) is not tuple
            or any(type(path) is not str for path in supplied.source_paths)
            or supplied != expected
        ):
            return False
    return True


def admit_catalog(candidate: object) -> AdmittedCatalog:
    """Validate and copy the complete closed public-synthetic catalog."""
    try:
        if type(candidate) is not CatalogCandidate:
            _invalid()
        width = candidate.width
        height = candidate.height
        generation = candidate.generation
        entries = candidate.entries
        provenance = candidate.provenance
        if (
            type(width) is not int
            or type(height) is not int
            or (width, height) != (_WIDTH, _HEIGHT)
            or width * height * 4 > 64 * 1024 * 1024
            or type(generation) is not str
            or generation != GENERATION
            or not _provenance_is_exact(provenance)
            or type(entries) is not tuple
            or len(entries) != 15
        ):
            _invalid()

        expected_pairs = {
            (screen, layout) for screen in SCREENS for layout in LAYOUTS
        }
        found: dict[tuple[str, str], Constellation] = {}
        for entry in entries:
            if (
                type(entry) is not Constellation
                or type(entry.screen) is not str
                or type(entry.layout) is not str
                or type(entry.anchors) is not tuple
                or len(entry.anchors) != 3
            ):
                _invalid()
            pair = (entry.screen, entry.layout)
            if pair not in expected_pairs or pair in found:
                _invalid()
            rectangles: list[Rectangle] = []
            validated_anchors: list[Anchor] = []
            for anchor_index, anchor in enumerate(entry.anchors):
                if (
                    type(anchor) is not Anchor
                    or type(anchor.rectangle) is not Rectangle
                    or type(anchor.bgr) is not bytes
                ):
                    _invalid()
                rectangle, rectangle_width, rectangle_height = _validated_rectangle(
                    anchor.rectangle,
                    width,
                    height,
                )
                expected_length = rectangle_width * rectangle_height * 3
                if (
                    len(anchor.bgr) != expected_length
                    or not _has_channel_variance(anchor.bgr)
                ):
                    _invalid()
                expected_bytes = _motif(
                    SCREENS.index(entry.screen),
                    anchor_index,
                    rectangle_width,
                    rectangle_height,
                )
                if anchor.bgr != expected_bytes:
                    _invalid()
                rectangles.append(rectangle)
                validated_anchors.append(Anchor(rectangle, bytes(anchor.bgr)))
            rectangle_tuple = tuple(rectangles)
            if (
                any(
                    _overlap(left, right)
                    for index, left in enumerate(rectangle_tuple)
                    for right in rectangle_tuple[index + 1 :]
                )
                or not _widely_separated(rectangle_tuple, width, height)
            ):
                _invalid()
            found[pair] = Constellation(
                entry.screen,
                entry.layout,
                tuple(validated_anchors),
            )

        if set(found) != expected_pairs:
            _invalid()
        for screen in SCREENS:
            nominal = tuple(
                anchor.rectangle
                for anchor in found[(screen, "NOMINAL")].anchors
            )
            if nominal != _NOMINAL_RECTANGLES:
                _invalid()
            for layout in LAYOUTS[1:]:
                shifted = tuple(
                    anchor.rectangle for anchor in found[(screen, layout)].anchors
                )
                expected_shift = tuple(
                    transform_rectangle(rectangle, layout, width, height)
                    for rectangle in nominal
                )
                if shifted != expected_shift or not _meaningfully_shifted(
                    nominal, shifted, width, height
                ):
                    _invalid()

        copied_entries = tuple(
            found[(entry.screen, entry.layout)]
            for entry in entries
        )
        return AdmittedCatalog(
            width,
            height,
            generation,
            copied_entries,
            PROVENANCE,
        )
    except CatalogAdmissionError:
        raise
    except BaseException:
        _invalid()


ADMITTED_SYNTHETIC_CATALOG = admit_catalog(synthetic_candidate())
