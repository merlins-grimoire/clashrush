"""Pure, bounded, non-identifying window-inventory diagnostics.

The pinned donor inventories expose raw titles, HWNDs, classes, and dimensions. This
module intentionally adapts none of those representations: callers reduce native
facts to exact booleans and geometry before this immutable diagnostic boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

_MAX_WINDOWS = 4096
_MIN_WIDTH = 640
_MIN_HEIGHT = 360
_MAX_CAPTURE_BYTES = 64 * 1024 * 1024


class Cardinality(str, Enum):
    ZERO = "ZERO"
    ONE = "ONE"
    MULTIPLE = "MULTIPLE"


class GeometryClass(str, Enum):
    NOT_EVALUATED = "NOT_EVALUATED"
    INVALID = "INVALID"
    TOO_SMALL = "TOO_SMALL"
    VALID = "VALID"


class DiagnosticReason(str, Enum):
    NATIVE_FACT_INVALID = "NATIVE_FACT_INVALID"
    ROOT_NONE = "ROOT_NONE"
    ROOT_NOT_VISIBLE = "ROOT_NOT_VISIBLE"
    ROOT_TITLE_MISMATCH = "ROOT_TITLE_MISMATCH"
    ROOT_ANCESTRY_MISMATCH = "ROOT_ANCESTRY_MISMATCH"
    ROOT_IDENTITY_MISMATCH = "ROOT_IDENTITY_MISMATCH"
    ROOT_MULTIPLE = "ROOT_MULTIPLE"
    RENDER_NONE = "RENDER_NONE"
    RENDER_NOT_VISIBLE = "RENDER_NOT_VISIBLE"
    RENDER_ANCESTRY_MISMATCH = "RENDER_ANCESTRY_MISMATCH"
    RENDER_OUTSIDE_JOB = "RENDER_OUTSIDE_JOB"
    RENDER_IDENTITY_MISMATCH = "RENDER_IDENTITY_MISMATCH"
    RENDER_GEOMETRY_TOO_SMALL = "RENDER_GEOMETRY_TOO_SMALL"
    RENDER_GEOMETRY_INVALID = "RENDER_GEOMETRY_INVALID"
    RENDER_EQUAL_LARGEST = "RENDER_EQUAL_LARGEST"
    BINDING_READY = "BINDING_READY"


@dataclass(frozen=True, slots=True)
class RootWindowFact:
    visible: bool
    title_matches: bool
    ancestry_matches: bool
    identity_matches: bool


@dataclass(frozen=True, slots=True)
class RenderWindowFact:
    visible: bool
    ancestry_matches: bool
    in_private_job: bool
    identity_matches: bool
    client_size: tuple[int, int]


@dataclass(frozen=True, slots=True)
class RootDiagnostic:
    initial: Cardinality
    visible: Cardinality
    title_match: Cardinality
    ancestry: Cardinality
    identity: Cardinality


@dataclass(frozen=True, slots=True)
class RenderDiagnostic:
    initial: Cardinality
    visible: Cardinality
    ancestry: Cardinality
    job_ownership: Cardinality
    identity: Cardinality
    geometry: Cardinality
    geometry_class: GeometryClass


@dataclass(frozen=True, slots=True)
class WindowDiagnosticRecord:
    reason: DiagnosticReason
    root: RootDiagnostic
    render: RenderDiagnostic | None


def _cardinality(count: int) -> Cardinality:
    if count == 0:
        return Cardinality.ZERO
    if count == 1:
        return Cardinality.ONE
    return Cardinality.MULTIPLE


def _empty_root() -> RootDiagnostic:
    return RootDiagnostic(*(Cardinality.ZERO,) * 5)


def _invalid_record() -> WindowDiagnosticRecord:
    return WindowDiagnosticRecord(
        DiagnosticReason.NATIVE_FACT_INVALID,
        _empty_root(),
        None,
    )


def _root_report(counts: tuple[int, int, int, int, int]) -> RootDiagnostic:
    return RootDiagnostic(*(_cardinality(value) for value in counts))


def _render_report(
    counts: tuple[int, int, int, int, int, int], geometry_class: GeometryClass
) -> RenderDiagnostic:
    return RenderDiagnostic(
        *(_cardinality(value) for value in counts),
        geometry_class,
    )


def _root_facts_valid(roots: tuple[RootWindowFact, ...]) -> bool:
    return all(
        type(fact) is RootWindowFact
        and all(
            type(value) is bool
            for value in (
                fact.visible,
                fact.title_matches,
                fact.ancestry_matches,
                fact.identity_matches,
            )
        )
        for fact in roots
    )


def _render_filters_valid(renders: tuple[RenderWindowFact, ...]) -> bool:
    return all(
        type(fact) is RenderWindowFact
        and all(
            type(value) is bool
            for value in (
                fact.visible,
                fact.ancestry_matches,
                fact.in_private_job,
                fact.identity_matches,
            )
        )
        for fact in renders
    )


def _geometry(fact: RenderWindowFact) -> tuple[GeometryClass, int]:
    size = fact.client_size
    if (
        type(size) is not tuple
        or len(size) != 2
        or any(type(value) is not int or value <= 0 for value in size)
    ):
        return GeometryClass.INVALID, 0
    width, height = size
    area = width * height
    if area * 4 > _MAX_CAPTURE_BYTES:
        return GeometryClass.INVALID, 0
    if width < _MIN_WIDTH or height < _MIN_HEIGHT:
        return GeometryClass.TOO_SMALL, area
    return GeometryClass.VALID, area


def evaluate_window_inventory(
    roots: tuple[RootWindowFact, ...],
    renders: tuple[RenderWindowFact, ...],
    *,
    roots_complete: bool,
    renders_complete: bool,
) -> WindowDiagnosticRecord:
    """Reduce one complete root/child inventory to bounded structural facts."""
    if (
        type(roots) is not tuple
        or type(roots_complete) is not bool
        or not roots_complete
        or len(roots) > _MAX_WINDOWS
        or not _root_facts_valid(roots)
    ):
        return _invalid_record()

    root_counts = [len(roots)]
    root_report = _root_report((root_counts[0], 0, 0, 0, 0))
    if not roots:
        return WindowDiagnosticRecord(DiagnosticReason.ROOT_NONE, root_report, None)

    survivors = roots
    root_filters = (
        (lambda fact: fact.visible, DiagnosticReason.ROOT_NOT_VISIBLE),
        (lambda fact: fact.title_matches, DiagnosticReason.ROOT_TITLE_MISMATCH),
        (lambda fact: fact.ancestry_matches, DiagnosticReason.ROOT_ANCESTRY_MISMATCH),
        (lambda fact: fact.identity_matches, DiagnosticReason.ROOT_IDENTITY_MISMATCH),
    )
    for predicate, reason in root_filters:
        survivors = tuple(fact for fact in survivors if predicate(fact))
        root_counts.append(len(survivors))
        if not survivors:
            root_counts.extend([0] * (5 - len(root_counts)))
            return WindowDiagnosticRecord(
                reason,
                _root_report(tuple(root_counts)),  # type: ignore[arg-type]
                None,
            )
    root_report = _root_report(tuple(root_counts))  # type: ignore[arg-type]
    if len(survivors) > 1:
        return WindowDiagnosticRecord(
            DiagnosticReason.ROOT_MULTIPLE, root_report, None
        )

    if (
        type(renders) is not tuple
        or type(renders_complete) is not bool
        or not renders_complete
        or len(renders) > _MAX_WINDOWS
        or not _render_filters_valid(renders)
    ):
        return WindowDiagnosticRecord(
            DiagnosticReason.NATIVE_FACT_INVALID, root_report, None
        )

    render_counts = [len(renders)]
    if not renders:
        render_report = _render_report(
            (0, 0, 0, 0, 0, 0), GeometryClass.NOT_EVALUATED
        )
        return WindowDiagnosticRecord(
            DiagnosticReason.RENDER_NONE, root_report, render_report
        )

    render_survivors = renders
    render_filters = (
        (lambda fact: fact.visible, DiagnosticReason.RENDER_NOT_VISIBLE),
        (lambda fact: fact.ancestry_matches, DiagnosticReason.RENDER_ANCESTRY_MISMATCH),
        (lambda fact: fact.in_private_job, DiagnosticReason.RENDER_OUTSIDE_JOB),
        (lambda fact: fact.identity_matches, DiagnosticReason.RENDER_IDENTITY_MISMATCH),
    )
    for predicate, reason in render_filters:
        render_survivors = tuple(fact for fact in render_survivors if predicate(fact))
        render_counts.append(len(render_survivors))
        if not render_survivors:
            render_counts.extend([0] * (6 - len(render_counts)))
            return WindowDiagnosticRecord(
                reason,
                root_report,
                _render_report(
                    tuple(render_counts),  # type: ignore[arg-type]
                    GeometryClass.NOT_EVALUATED,
                ),
            )

    classified = tuple(_geometry(fact) for fact in render_survivors)
    if any(kind is GeometryClass.INVALID for kind, _area in classified):
        render_counts.append(0)
        return WindowDiagnosticRecord(
            DiagnosticReason.RENDER_GEOMETRY_INVALID,
            root_report,
            _render_report(
                tuple(render_counts),  # type: ignore[arg-type]
                GeometryClass.INVALID,
            ),
        )

    valid_areas = tuple(
        area for kind, area in classified if kind is GeometryClass.VALID
    )
    render_counts.append(len(valid_areas))
    if not valid_areas:
        return WindowDiagnosticRecord(
            DiagnosticReason.RENDER_GEOMETRY_TOO_SMALL,
            root_report,
            _render_report(
                tuple(render_counts),  # type: ignore[arg-type]
                GeometryClass.TOO_SMALL,
            ),
        )

    render_report = _render_report(
        tuple(render_counts),  # type: ignore[arg-type]
        GeometryClass.VALID,
    )
    largest = max(valid_areas)
    if sum(area == largest for area in valid_areas) != 1:
        return WindowDiagnosticRecord(
            DiagnosticReason.RENDER_EQUAL_LARGEST, root_report, render_report
        )
    return WindowDiagnosticRecord(
        DiagnosticReason.BINDING_READY, root_report, render_report
    )


def serialize_window_diagnostic(record: WindowDiagnosticRecord) -> str:
    """Serialize only an exact diagnostic record through an explicit allowlist."""
    if type(record) is not WindowDiagnosticRecord or type(record.reason) is not DiagnosticReason:
        raise TypeError("diagnostic record required")
    root = record.root
    if type(root) is not RootDiagnostic or any(
        type(value) is not Cardinality
        for value in (
            root.initial,
            root.visible,
            root.title_match,
            root.ancestry,
            root.identity,
        )
    ):
        raise TypeError("diagnostic record required")
    render = record.render
    if render is not None and (
        type(render) is not RenderDiagnostic
        or any(
            type(value) is not Cardinality
            for value in (
                render.initial,
                render.visible,
                render.ancestry,
                render.job_ownership,
                render.identity,
                render.geometry,
            )
        )
        or type(render.geometry_class) is not GeometryClass
    ):
        raise TypeError("diagnostic record required")
    diagnostic: dict[str, object] = {
        "reason": record.reason.value,
        "root": {
            "initial": root.initial.value,
            "visible": root.visible.value,
            "title_match": root.title_match.value,
            "ancestry": root.ancestry.value,
            "identity": root.identity.value,
        },
    }
    if render is not None:
        diagnostic["render"] = {
            "initial": render.initial.value,
            "visible": render.visible.value,
            "ancestry": render.ancestry.value,
            "job_ownership": render.job_ownership.value,
            "identity": render.identity.value,
            "geometry": render.geometry.value,
            "geometry_class": render.geometry_class.value,
        }
    return json.dumps(
        {
            "schema_version": 1,
            "command_outcome": "BINDING_OBSERVED",
            "diagnostic": diagnostic,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
