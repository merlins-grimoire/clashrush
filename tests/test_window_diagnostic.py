from __future__ import annotations

import json

import pytest

from clash_rush_rebuild.window_diagnostic import (
    Cardinality,
    DiagnosticReason,
    GeometryClass,
    RenderDiagnostic,
    RenderSelectionResult,
    RenderWindowFact,
    RootDiagnostic,
    RootSelectionResult,
    RootWindowFact,
    WindowDiagnosticRecord,
    evaluate_window_inventory,
    select_render_window,
    select_root_window,
    serialize_window_diagnostic,
)


def _root(**changes: object) -> RootWindowFact:
    values: dict[str, object] = {
        "visible": True,
        "title_matches": True,
        "ancestry_matches": True,
        "identity_matches": True,
    }
    values.update(changes)
    return RootWindowFact(**values)  # type: ignore[arg-type]


def _render(**changes: object) -> RenderWindowFact:
    values: dict[str, object] = {
        "visible": True,
        "ancestry_matches": True,
        "in_private_job": True,
        "identity_matches": True,
        "client_size": (1280, 720),
    }
    values.update(changes)
    return RenderWindowFact(**values)  # type: ignore[arg-type]


def _evaluate(
    roots: tuple[RootWindowFact, ...] = (_root(),),
    renders: tuple[RenderWindowFact, ...] = (_render(),),
    *,
    roots_complete: bool = True,
    renders_complete: bool = True,
) -> WindowDiagnosticRecord:
    return evaluate_window_inventory(
        roots,
        renders,
        roots_complete=roots_complete,
        renders_complete=renders_complete,
    )


def test_root_selection_result_distinguishes_zero_one_and_multiple_roots() -> None:
    zero = select_root_window((), complete=True)
    one = select_root_window((_root(title_matches=False), _root()), complete=True)
    multiple = select_root_window((_root(), _root()), complete=True)

    assert zero == RootSelectionResult(
        DiagnosticReason.ROOT_NONE,
        RootDiagnostic(*(Cardinality.ZERO,) * 5),
        None,
    )
    assert one.reason is DiagnosticReason.BINDING_READY
    assert one.selected_index == 1
    assert multiple.reason is DiagnosticReason.ROOT_MULTIPLE
    assert multiple.selected_index is None


def test_render_selection_accepts_child_owner_when_exactly_inside_private_job() -> None:
    result = select_render_window(
        (_render(in_private_job=True, identity_matches=False),), complete=True
    )

    assert result.reason is DiagnosticReason.BINDING_READY
    assert result.selected_index == 0
    assert result.selected_size == (1280, 720)
    assert result.diagnostic.job_ownership is Cardinality.ONE
    assert result.diagnostic.identity is Cardinality.ZERO
    assert result.diagnostic.geometry is Cardinality.ONE


def test_render_selection_result_tracks_visibility_and_ancestry_changes() -> None:
    hidden = select_render_window((_render(visible=False),), complete=True)
    visible = select_render_window(
        (_render(client_size=(640, 360)), _render()), complete=True
    )
    detached = select_render_window(
        (_render(ancestry_matches=False),), complete=True
    )

    assert hidden.reason is DiagnosticReason.RENDER_NOT_VISIBLE
    assert visible.reason is DiagnosticReason.BINDING_READY
    assert visible.selected_index == 1
    assert visible.selected_size == (1280, 720)
    assert detached.reason is DiagnosticReason.RENDER_ANCESTRY_MISMATCH


def test_render_selection_result_rejects_equal_area_candidates() -> None:
    result = select_render_window(
        (
            _render(client_size=(1280, 720)),
            _render(client_size=(960, 960)),
        ),
        complete=True,
    )

    assert result == RenderSelectionResult(
        DiagnosticReason.RENDER_EQUAL_LARGEST,
        RenderDiagnostic(
            *(Cardinality.MULTIPLE,) * 6,
            GeometryClass.VALID,
        ),
        None,
        None,
    )


def test_positive_inventory_is_reduced_to_closed_structural_facts() -> None:
    record = _evaluate(
        (_root(), _root(title_matches=False)),
        (_render(client_size=(640, 360)), _render(client_size=(1280, 720))),
    )

    assert record.reason is DiagnosticReason.BINDING_READY
    assert record.root.initial is Cardinality.MULTIPLE
    assert record.root.visible is Cardinality.MULTIPLE
    assert record.root.title_match is Cardinality.ONE
    assert record.root.ancestry is Cardinality.ONE
    assert record.root.identity is Cardinality.ONE
    assert record.render is not None
    assert record.render.initial is Cardinality.MULTIPLE
    assert record.render.visible is Cardinality.MULTIPLE
    assert record.render.ancestry is Cardinality.MULTIPLE
    assert record.render.job_ownership is Cardinality.MULTIPLE
    assert record.render.identity is Cardinality.MULTIPLE
    assert record.render.geometry is Cardinality.MULTIPLE
    assert record.render.geometry_class is GeometryClass.VALID


@pytest.mark.parametrize(
    ("roots", "reason"),
    [
        ((), DiagnosticReason.ROOT_NONE),
        ((_root(visible=False),), DiagnosticReason.ROOT_NOT_VISIBLE),
        ((_root(title_matches=False),), DiagnosticReason.ROOT_TITLE_MISMATCH),
        ((_root(ancestry_matches=False),), DiagnosticReason.ROOT_ANCESTRY_MISMATCH),
        ((_root(identity_matches=False),), DiagnosticReason.ROOT_IDENTITY_MISMATCH),
        ((_root(), _root()), DiagnosticReason.ROOT_MULTIPLE),
    ],
)
def test_root_reasons_are_closed_and_ordered(
    roots: tuple[RootWindowFact, ...], reason: DiagnosticReason
) -> None:
    record = _evaluate(roots)

    assert record.reason is reason
    assert record.render is None


@pytest.mark.parametrize(
    ("renders", "reason", "geometry_class"),
    [
        ((), DiagnosticReason.RENDER_NONE, GeometryClass.NOT_EVALUATED),
        ((_render(visible=False),), DiagnosticReason.RENDER_NOT_VISIBLE, GeometryClass.NOT_EVALUATED),
        ((_render(ancestry_matches=False),), DiagnosticReason.RENDER_ANCESTRY_MISMATCH, GeometryClass.NOT_EVALUATED),
        ((_render(in_private_job=False),), DiagnosticReason.RENDER_OUTSIDE_JOB, GeometryClass.NOT_EVALUATED),
        ((_render(client_size=(639, 360)),), DiagnosticReason.RENDER_GEOMETRY_TOO_SMALL, GeometryClass.TOO_SMALL),
        ((_render(client_size=(0, 720)),), DiagnosticReason.RENDER_GEOMETRY_INVALID, GeometryClass.INVALID),
        ((_render(), _render()), DiagnosticReason.RENDER_EQUAL_LARGEST, GeometryClass.VALID),
    ],
)
def test_render_reasons_and_geometry_are_closed(
    renders: tuple[RenderWindowFact, ...],
    reason: DiagnosticReason,
    geometry_class: GeometryClass,
) -> None:
    record = _evaluate(renders=renders)

    assert record.reason is reason
    assert record.render is not None
    assert record.render.geometry_class is geometry_class


def test_rejected_unrelated_windows_do_not_fail_a_successful_stage() -> None:
    roots = (
        _root(visible=False),
        _root(title_matches=False),
        _root(ancestry_matches=False),
        _root(identity_matches=False),
        _root(),
    )
    renders = (
        _render(visible=False),
        _render(ancestry_matches=False),
        _render(in_private_job=False),
        _render(identity_matches=False, client_size=(640, 360)),
        _render(client_size=(200, 200)),
        _render(),
    )

    assert _evaluate(roots, renders).reason is DiagnosticReason.BINDING_READY


def test_strict_largest_render_wins_without_exposing_geometry() -> None:
    record = _evaluate(renders=(_render(client_size=(640, 360)), _render()))

    assert record.reason is DiagnosticReason.BINDING_READY
    serialized = serialize_window_diagnostic(record)
    assert "1280" not in serialized
    assert "720" not in serialized
    assert "640" not in serialized
    assert "360" not in serialized


@pytest.mark.parametrize(
    ("roots", "renders", "roots_complete", "renders_complete", "reason"),
    [
        ((_root(),) * 4097, (_render(),), True, True, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(),), (_render(),) * 4097, True, True, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(),), (_render(),), False, True, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(),), (_render(),), True, False, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(visible=1),), (_render(),), True, True, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(),), (_render(in_private_job="yes"),), True, True, DiagnosticReason.NATIVE_FACT_INVALID),
        ((_root(),), (_render(client_size=(4097, 4097)),), True, True, DiagnosticReason.RENDER_GEOMETRY_INVALID),
    ],
)
def test_incomplete_overflowing_or_invalid_facts_collapse_to_one_reason(
    roots: tuple[RootWindowFact, ...],
    renders: tuple[RenderWindowFact, ...],
    roots_complete: bool,
    renders_complete: bool,
    reason: DiagnosticReason,
) -> None:
    record = _evaluate(
        roots,
        renders,
        roots_complete=roots_complete,
        renders_complete=renders_complete,
    )

    assert record.reason is reason
    assert record.root.initial in set(Cardinality)


def test_root_failure_does_not_require_or_validate_unobserved_children() -> None:
    record = _evaluate((), (_render(client_size=(0, 0)),), renders_complete=False)

    assert record.reason is DiagnosticReason.ROOT_NONE
    assert record.render is None


def test_complete_enumeration_permutations_do_not_change_the_record() -> None:
    roots = (_root(title_matches=False), _root())
    renders = (_render(client_size=(640, 360)), _render())

    expected = _evaluate(roots, renders)
    assert _evaluate(tuple(reversed(roots)), tuple(reversed(renders))) == expected


def test_serialization_whitelists_only_schema_and_closed_diagnostic_values() -> None:
    forbidden = (
        "PRIVATE WINDOW TITLE",
        "HWND-987654",
        "PID-424242",
        "PIXEL-CANARY",
        r"C:\\private\\installation",
        "PRIVATE_INSTANCE_ALPHA",
    )
    record = _evaluate(
        (_root(), _root(title_matches=False)),
        (_render(), _render(client_size=(640, 360))),
    )
    payload = serialize_window_diagnostic(record)
    decoded = json.loads(payload)

    assert decoded["schema_version"] == 1
    assert decoded["command_outcome"] == "BINDING_OBSERVED"
    assert decoded["diagnostic"]["reason"] == "BINDING_READY"
    assert set(decoded) == {"schema_version", "command_outcome", "diagnostic"}
    assert not any(value in payload for value in forbidden)
    assert not any(key in payload.lower() for key in ("hwnd", "pid", "pixel", "path", "instance"))


def test_serializer_rejects_non_record_objects_without_reflecting_them() -> None:
    class SecretObject:
        def __repr__(self) -> str:
            return (
                "PRIVATE WINDOW TITLE HWND-987654 PID-424242 PIXEL-CANARY "
                r"C:\private\installation PRIVATE_INSTANCE_ALPHA"
            )

    with pytest.raises(TypeError, match="diagnostic record required") as error:
        serialize_window_diagnostic(SecretObject())  # type: ignore[arg-type]

    rendered = str(error.value)
    assert "PRIVATE WINDOW TITLE" not in rendered
    assert "PID-424242" not in rendered
    assert "PRIVATE_INSTANCE_ALPHA" not in rendered


@pytest.mark.parametrize("field", ["reason", "root", "render"])
def test_serializer_rejects_malformed_nested_values_without_leaking_them(
    field: str,
) -> None:
    class SecretValue:
        value = (
            "PRIVATE WINDOW TITLE HWND-987654 PID-424242 PIXEL-CANARY "
            r"C:\private\installation PRIVATE_INSTANCE_ALPHA"
        )

    root = RootDiagnostic(*(Cardinality.ONE,) * 5)
    render = RenderDiagnostic(
        *(Cardinality.ONE,) * 6,
        GeometryClass.VALID,
    )
    values: dict[str, object] = {
        "reason": DiagnosticReason.BINDING_READY,
        "root": root,
        "render": render,
    }
    if field == "reason":
        values["reason"] = SecretValue()
    elif field == "root":
        values["root"] = RootDiagnostic(
            SecretValue(),  # type: ignore[arg-type]
            *(Cardinality.ONE,) * 4,
        )
    else:
        values["render"] = RenderDiagnostic(
            *(Cardinality.ONE,) * 6,
            SecretValue(),  # type: ignore[arg-type]
        )
    malformed = WindowDiagnosticRecord(**values)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="diagnostic record required") as error:
        serialize_window_diagnostic(malformed)

    rendered = str(error.value)
    assert "PRIVATE WINDOW TITLE" not in rendered
    assert "PID-424242" not in rendered
    assert "PRIVATE_INSTANCE_ALPHA" not in rendered
