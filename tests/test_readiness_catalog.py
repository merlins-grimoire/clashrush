"""Public-synthetic R10 catalog admission tests; no private/game frames."""
from __future__ import annotations

from dataclasses import replace

import pytest

from clash_rush_rebuild import readiness_catalog as catalog


EXPECTED_PAIRS = {
    (screen, layout)
    for screen in ("HOME", "LOADING", "BUILDER")
    for layout in (
        "NOMINAL",
        "H_INSET_5",
        "H_INSET_10",
        "V_INSET_5",
        "V_INSET_10",
    )
}


def test_admitted_catalog_has_closed_r10_shape_and_provenance() -> None:
    admitted = catalog.ADMITTED_SYNTHETIC_CATALOG

    assert type(admitted) is catalog.AdmittedCatalog
    assert (admitted.width, admitted.height) == (640, 360)
    assert admitted.generation == "SYNTHETIC_PUBLIC_V1"
    assert len(admitted.entries) == 15
    assert {(entry.screen, entry.layout) for entry in admitted.entries} == EXPECTED_PAIRS
    assert all(len(entry.anchors) == 3 for entry in admitted.entries)
    assert admitted.provenance == catalog.PROVENANCE
    assert tuple(record.revision for record in admitted.provenance) == (
        "4ede1efd220ffc79a5b490cfd3788b44d2584da4",
        "a5c943afed0ed3b9abedbbc228b0889145ecaf24",
        "c41fe12a6df051e241c695b71b6859286e24c612",
    )
    assert all(record.license_id == "MIT" for record in admitted.provenance)


def test_catalog_exposes_only_closed_scalar_grammar() -> None:
    assert catalog.SCALAR_GRAMMAR == {
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


def test_templates_are_procedural_nonconstant_bgr_and_geometry_is_closed() -> None:
    admitted = catalog.ADMITTED_SYNTHETIC_CATALOG
    for entry in admitted.entries:
        for anchor in entry.anchors:
            rectangle = anchor.rectangle
            assert 8 <= rectangle.width <= 96
            assert 8 <= rectangle.height <= 96
            assert len(anchor.bgr) == rectangle.width * rectangle.height * 3
            for channel in range(3):
                assert len(set(anchor.bgr[channel::3])) > 1


def test_each_inset_is_exactly_derived_from_its_nominal_entry() -> None:
    admitted = catalog.ADMITTED_SYNTHETIC_CATALOG
    by_pair = {(entry.screen, entry.layout): entry for entry in admitted.entries}
    for screen in ("HOME", "LOADING", "BUILDER"):
        nominal = by_pair[(screen, "NOMINAL")]
        for layout in ("H_INSET_5", "H_INSET_10", "V_INSET_5", "V_INSET_10"):
            shifted = by_pair[(screen, layout)]
            assert tuple(anchor.rectangle for anchor in shifted.anchors) == tuple(
                catalog.transform_rectangle(anchor.rectangle, layout, 640, 360)
                for anchor in nominal.anchors
            )


def test_admission_rejects_forged_provenance_and_boundary_violations() -> None:
    candidate = catalog.synthetic_candidate()
    wrong_provenance = replace(
        candidate,
        provenance=(replace(candidate.provenance[0], license_id="UNKNOWN"),)
        + candidate.provenance[1:],
    )
    duplicate = replace(
        candidate,
        entries=candidate.entries[:-1] + (candidate.entries[0],),
    )
    first = candidate.entries[0]
    constant = replace(
        candidate,
        entries=(
            replace(
                first,
                anchors=(replace(first.anchors[0], bgr=b"\x01\x02\x03" * 256),)
                + first.anchors[1:],
            ),
        )
        + candidate.entries[1:],
    )

    for malformed in (wrong_provenance, duplicate, constant):
        with pytest.raises(catalog.CatalogAdmissionError, match="^CATALOG_INVALID$"):
            catalog.admit_catalog(malformed)


def test_provenance_fields_require_exact_public_scalar_values() -> None:
    class EqualToEverything:
        def __eq__(self, other: object) -> bool:
            return True

    candidate = catalog.synthetic_candidate()
    forged_record = replace(candidate.provenance[0], license_id=EqualToEverything())
    forged = replace(
        candidate,
        provenance=(forged_record,) + candidate.provenance[1:],
    )

    with pytest.raises(catalog.CatalogAdmissionError, match="^CATALOG_INVALID$"):
        catalog.admit_catalog(forged)


def test_rectangle_coordinates_reject_bool_and_out_of_frame_values() -> None:
    candidate = catalog.synthetic_candidate()
    first = candidate.entries[0]
    bad_rectangles = (
        replace(first.anchors[0].rectangle, x0=True),
        replace(first.anchors[0].rectangle, x1=641),
    )
    for rectangle in bad_rectangles:
        malformed = replace(
            candidate,
            entries=(
                replace(
                    first,
                    anchors=(replace(first.anchors[0], rectangle=rectangle),)
                    + first.anchors[1:],
                ),
            )
            + candidate.entries[1:],
        )
        with pytest.raises(catalog.CatalogAdmissionError, match="^CATALOG_INVALID$"):
            catalog.admit_catalog(malformed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", True),
        ("width", 639),
        ("height", 359),
        ("generation", "PRIVATE_REVISION"),
    ],
)
def test_admission_rejects_nonexact_or_open_catalog_scalars(field: str, value: object) -> None:
    candidate = replace(catalog.synthetic_candidate(), **{field: value})
    with pytest.raises(catalog.CatalogAdmissionError, match="^CATALOG_INVALID$"):
        catalog.admit_catalog(candidate)


def test_catalog_module_has_no_runtime_output_or_frame_derived_hash_surface() -> None:
    public_names = {name for name in dir(catalog) if not name.startswith("_")}
    assert public_names.isdisjoint(
        {
            "capture",
            "frame",
            "hash_frame",
            "log",
            "status",
            "write",
            "open",
            "print",
        }
    )
    assert not hasattr(catalog.AdmittedCatalog, "digest")
    assert not hasattr(catalog.AdmittedCatalog, "filename")
    assert not hasattr(catalog.AdmittedCatalog, "identifier")
