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


@dataclass(frozen=True, slots=True)
class RootSelectionResult:
    reason: DiagnosticReason
    diagnostic: RootDiagnostic
    selected_index: int | None


@dataclass(frozen=True, slots=True)
class RenderSelectionResult:
    reason: DiagnosticReason
    diagnostic: RenderDiagnostic
    selected_index: int | None
    selected_size: tuple[int, int] | None


def _cardinality(count: int) -> Cardinality:
    if count == 0:
        return Cardinality.ZERO
    if count == 1:
        return Cardinality.ONE
    return Cardinality.MULTIPLE


def _empty_root() -> RootDiagnostic:
    return RootDiagnostic(*(Cardinality.ZERO,) * 5)


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


def select_root_window(
    roots: tuple[RootWindowFact, ...], *, complete: bool
) -> RootSelectionResult:
    """Select one exact root while retaining only its tuple position internally."""
    if (
        type(roots) is not tuple
        or type(complete) is not bool
        or not complete
        or len(roots) > _MAX_WINDOWS
        or not _root_facts_valid(roots)
    ):
        return RootSelectionResult(
            DiagnosticReason.NATIVE_FACT_INVALID, _empty_root(), None
        )

    counts = [len(roots)]
    report = _root_report((counts[0], 0, 0, 0, 0))
    if not roots:
        return RootSelectionResult(DiagnosticReason.ROOT_NONE, report, None)

    survivors = tuple(enumerate(roots))
    filters = (
        (lambda fact: fact.visible, DiagnosticReason.ROOT_NOT_VISIBLE),
        (lambda fact: fact.title_matches, DiagnosticReason.ROOT_TITLE_MISMATCH),
        (lambda fact: fact.ancestry_matches, DiagnosticReason.ROOT_ANCESTRY_MISMATCH),
        (lambda fact: fact.identity_matches, DiagnosticReason.ROOT_IDENTITY_MISMATCH),
    )
    for predicate, reason in filters:
        survivors = tuple(
            (index, fact) for index, fact in survivors if predicate(fact)
        )
        counts.append(len(survivors))
        if not survivors:
            counts.extend([0] * (5 - len(counts)))
            return RootSelectionResult(
                reason,
                _root_report(tuple(counts)),  # type: ignore[arg-type]
                None,
            )

    report = _root_report(tuple(counts))  # type: ignore[arg-type]
    if len(survivors) > 1:
        return RootSelectionResult(DiagnosticReason.ROOT_MULTIPLE, report, None)
    return RootSelectionResult(
        DiagnosticReason.BINDING_READY, report, survivors[0][0]
    )


def select_render_window(
    renders: tuple[RenderWindowFact, ...], *, complete: bool
) -> RenderSelectionResult:
    """Select a unique strict-largest exact render from a complete child inventory."""
    if (
        type(renders) is not tuple
        or type(complete) is not bool
        or not complete
        or len(renders) > _MAX_WINDOWS
        or not _render_filters_valid(renders)
    ):
        return RenderSelectionResult(
            DiagnosticReason.NATIVE_FACT_INVALID,
            _render_report((0, 0, 0, 0, 0, 0), GeometryClass.NOT_EVALUATED),
            None,
            None,
        )

    counts = [len(renders)]
    if not renders:
        return RenderSelectionResult(
            DiagnosticReason.RENDER_NONE,
            _render_report((0, 0, 0, 0, 0, 0), GeometryClass.NOT_EVALUATED),
            None,
            None,
        )

    survivors = tuple(enumerate(renders))
    filters = (
        (lambda fact: fact.visible, DiagnosticReason.RENDER_NOT_VISIBLE),
        (lambda fact: fact.ancestry_matches, DiagnosticReason.RENDER_ANCESTRY_MISMATCH),
        (lambda fact: fact.in_private_job, DiagnosticReason.RENDER_OUTSIDE_JOB),
        (lambda fact: fact.identity_matches, DiagnosticReason.RENDER_IDENTITY_MISMATCH),
    )
    for predicate, reason in filters:
        survivors = tuple(
            (index, fact) for index, fact in survivors if predicate(fact)
        )
        counts.append(len(survivors))
        if not survivors:
            counts.extend([0] * (6 - len(counts)))
            return RenderSelectionResult(
                reason,
                _render_report(
                    tuple(counts),  # type: ignore[arg-type]
                    GeometryClass.NOT_EVALUATED,
                ),
                None,
                None,
            )

    classified = tuple(
        (index, fact, *_geometry(fact)) for index, fact in survivors
    )
    if any(kind is GeometryClass.INVALID for _index, _fact, kind, _area in classified):
        counts.append(0)
        return RenderSelectionResult(
            DiagnosticReason.RENDER_GEOMETRY_INVALID,
            _render_report(
                tuple(counts),  # type: ignore[arg-type]
                GeometryClass.INVALID,
            ),
            None,
            None,
        )

    valid = tuple(
        (index, fact, area)
        for index, fact, kind, area in classified
        if kind is GeometryClass.VALID
    )
    counts.append(len(valid))
    if not valid:
        return RenderSelectionResult(
            DiagnosticReason.RENDER_GEOMETRY_TOO_SMALL,
            _render_report(
                tuple(counts),  # type: ignore[arg-type]
                GeometryClass.TOO_SMALL,
            ),
            None,
            None,
        )

    report = _render_report(
        tuple(counts),  # type: ignore[arg-type]
        GeometryClass.VALID,
    )
    largest_area = max(area for _index, _fact, area in valid)
    largest = tuple(candidate for candidate in valid if candidate[2] == largest_area)
    if len(largest) != 1:
        return RenderSelectionResult(
            DiagnosticReason.RENDER_EQUAL_LARGEST, report, None, None
        )
    selected_index, selected_fact, _area = largest[0]
    return RenderSelectionResult(
        DiagnosticReason.BINDING_READY,
        report,
        selected_index,
        selected_fact.client_size,
    )


def evaluate_window_inventory(
    roots: tuple[RootWindowFact, ...],
    renders: tuple[RenderWindowFact, ...],
    *,
    roots_complete: bool,
    renders_complete: bool,
) -> WindowDiagnosticRecord:
    """Reduce one complete root/child inventory to bounded structural facts."""
    root = select_root_window(roots, complete=roots_complete)
    if root.reason is not DiagnosticReason.BINDING_READY:
        return WindowDiagnosticRecord(root.reason, root.diagnostic, None)

    render = select_render_window(renders, complete=renders_complete)
    if render.reason is DiagnosticReason.NATIVE_FACT_INVALID:
        return WindowDiagnosticRecord(render.reason, root.diagnostic, None)
    return WindowDiagnosticRecord(render.reason, root.diagnostic, render.diagnostic)


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
