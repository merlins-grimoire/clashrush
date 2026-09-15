from __future__ import annotations

import pytest

from clash_rush_rebuild.diagnostic_crop_manifest import (
    ClosedOutput,
    CropId,
    DiagnosticCropManifestError,
    DiagnosticCropSpec,
    DiagnosticOutput,
    OutputGrammar,
    Preprocessing,
    SourceState,
    diagnostic_crop_manifest,
    diagnostic_crop_manifest_seal,
    require_manifest_crop,
    require_pristine_diagnostic_output,
    resolve_diagnostic_crop,
    validate_crop_dimensions,
    validate_diagnostic_output,
)


def test_manifest_resolves_only_a_sealed_crop_for_its_exact_source_state() -> None:
    spec = resolve_diagnostic_crop(CropId.HOME_BUILDER_COUNT, SourceState.HOME_VILLAGE)

    assert spec.crop_id == "HOME_BUILDER_COUNT"
    assert spec.source_state == "HOME_VILLAGE"
    assert spec.normalized_roi == (490_000, 40_000, 545_000, 80_000)

    with pytest.raises(DiagnosticCropManifestError):
        resolve_diagnostic_crop("HOME_BUILDER_COUNT", SourceState.HOME_VILLAGE)  # type: ignore[arg-type]


def test_manifest_binds_every_crop_field_and_has_a_stable_content_seal() -> None:
    specs = diagnostic_crop_manifest()

    assert type(specs) is tuple
    assert tuple(spec.crop_id for spec in specs) == (
        "HOME_RESOURCE_BALANCES",
        "HOME_BUILDER_COUNT",
        "BUILDER_BUILDER_COUNT",
        "HOME_LAB_STATUS",
        "BUILDER_LAB_STATUS",
    )
    assert all(
        len(spec.normalized_roi) == 4
        and 0 <= spec.normalized_roi[0] < spec.normalized_roi[2] <= 1_000_000
        and 0 <= spec.normalized_roi[1] < spec.normalized_roi[3] <= 1_000_000
        and 0 < spec.maximum_dimensions[0] <= 512
        and 0 < spec.maximum_dimensions[1] <= 384
        and spec.preprocessing in {member.value for member in Preprocessing}
        and spec.output_grammar in {member.value for member in OutputGrammar}
        for spec in specs
    )
    seal = diagnostic_crop_manifest_seal()
    assert type(seal) is str
    assert len(seal) == 64
    assert set(seal) <= set("0123456789abcdef")
    assert seal == diagnostic_crop_manifest_seal()


def test_arbitrary_roi_and_forged_manifest_entries_are_rejected() -> None:
    with pytest.raises(DiagnosticCropManifestError, match="sealed"):
        DiagnosticCropSpec(
            "HOME_BUILDER_COUNT",
            "HOME_VILLAGE",
            (0, 0, 1_000_000, 1_000_000),
            (128, 64),
            "GRAYSCALE_HIGH_CONTRAST_200",
            "BUILDER_FRACTION",
        )

    approved = diagnostic_crop_manifest()[1]
    forged = tuple.__new__(DiagnosticCropSpec, tuple(approved))
    assert forged == approved
    with pytest.raises(DiagnosticCropManifestError, match="manifest-approved"):
        require_manifest_crop(forged)


@pytest.mark.parametrize(
    ("crop_id", "state", "roi", "maximum", "preprocessing", "grammar"),
    [
        (
            CropId.HOME_RESOURCE_BALANCES,
            SourceState.HOME_VILLAGE,
            (800_000, 0, 960_000, 300_000),
            (512, 384),
            Preprocessing.GRAYSCALE_HIGH_CONTRAST_240,
            OutputGrammar.RESOURCE_TRIPLE,
        ),
        (
            CropId.BUILDER_BUILDER_COUNT,
            SourceState.BUILDER_VILLAGE,
            (565_000, 40_000, 620_000, 80_000),
            (128, 64),
            Preprocessing.GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED,
            OutputGrammar.BUILDER_FRACTION,
        ),
        (
            CropId.BUILDER_LAB_STATUS,
            SourceState.BUILDER_VILLAGE,
            (448_000, 40_000, 485_000, 80_000),
            (128, 64),
            Preprocessing.GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED,
            OutputGrammar.AVAILABILITY,
        ),
    ],
)
def test_resolved_specs_are_exact_manifest_snapshots(
    crop_id: CropId,
    state: SourceState,
    roi: tuple[int, int, int, int],
    maximum: tuple[int, int],
    preprocessing: Preprocessing,
    grammar: OutputGrammar,
) -> None:
    spec = resolve_diagnostic_crop(crop_id, state)

    assert spec.normalized_roi == roi
    assert spec.maximum_dimensions == maximum
    assert spec.preprocessing == preprocessing.value
    assert spec.output_grammar == grammar.value
    assert require_manifest_crop(spec) is spec


def test_crop_resolution_requires_the_exact_declared_source_state() -> None:
    with pytest.raises(DiagnosticCropManifestError, match="source state"):
        resolve_diagnostic_crop(
            CropId.HOME_BUILDER_COUNT,
            SourceState.BUILDER_VILLAGE,
        )


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 1), (1, 0), (129, 64), (128, 65), (True, 64), (128, 64.0)],
)
def test_crop_dimensions_are_positive_exact_integers_within_the_manifest_cap(
    width: object, height: object
) -> None:
    with pytest.raises(DiagnosticCropManifestError, match="dimensions"):
        validate_crop_dimensions(CropId.HOME_BUILDER_COUNT, width, height)  # type: ignore[arg-type]

    assert validate_crop_dimensions(CropId.HOME_BUILDER_COUNT, 128, 64) == (128, 64)


@pytest.mark.parametrize(
    ("crop_id", "value", "expected"),
    [
        (CropId.HOME_RESOURCE_BALANCES, (0, 1, 2_000_000_000), (0, 1, 2_000_000_000)),
        (CropId.HOME_BUILDER_COUNT, (0, 5), (0, 5)),
        (CropId.BUILDER_BUILDER_COUNT, (2, 2), (2, 2)),
        (CropId.HOME_LAB_STATUS, ClosedOutput.AVAILABLE, "AVAILABLE"),
        (CropId.BUILDER_LAB_STATUS, ClosedOutput.UNKNOWN, "UNKNOWN"),
    ],
)
def test_outputs_are_reduced_to_closed_or_bounded_numeric_values(
    crop_id: CropId, value: object, expected: object
) -> None:
    output = validate_diagnostic_output(crop_id, value, confidence_ppm=900_000)

    assert output.crop_id == crop_id.value
    assert output.value == expected
    assert output.confidence_ppm == 900_000


@pytest.mark.parametrize(
    ("crop_id", "value"),
    [
        (CropId.HOME_RESOURCE_BALANCES, "100 200 300"),
        (CropId.HOME_RESOURCE_BALANCES, (1, 2)),
        (CropId.HOME_RESOURCE_BALANCES, (1, 2, 2_000_000_001)),
        (CropId.HOME_BUILDER_COUNT, "1/5"),
        (CropId.HOME_BUILDER_COUNT, (6, 5)),
        (CropId.HOME_BUILDER_COUNT, (True, 5)),
        (CropId.HOME_LAB_STATUS, "AVAILABLE"),
        (CropId.HOME_LAB_STATUS, object()),
    ],
)
def test_raw_ocr_text_and_values_outside_the_declared_grammar_are_rejected(
    crop_id: CropId, value: object
) -> None:
    with pytest.raises(DiagnosticCropManifestError, match="output"):
        validate_diagnostic_output(crop_id, value, confidence_ppm=500_000)


@pytest.mark.parametrize("confidence", [-1, 1_000_001, True, 0.5, "900000"])
def test_confidence_is_a_bounded_numeric_value_not_text(confidence: object) -> None:
    with pytest.raises(DiagnosticCropManifestError, match="confidence"):
        validate_diagnostic_output(
            CropId.HOME_BUILDER_COUNT,
            (1, 5),
            confidence_ppm=confidence,  # type: ignore[arg-type]
        )


def test_output_boundary_rebuilds_validated_values_and_rejects_forged_storage() -> None:
    output = validate_diagnostic_output(
        CropId.HOME_BUILDER_COUNT,
        (1, 5),
        confidence_ppm=750_000,
    )

    rebuilt = require_pristine_diagnostic_output(output)
    assert rebuilt == output
    assert rebuilt is not output

    forged = tuple.__new__(
        DiagnosticOutput,
        ("HOME_BUILDER_COUNT", "BUILDER_FRACTION", "1/5 raw OCR", 750_000),
    )
    with pytest.raises(DiagnosticCropManifestError, match="output"):
        require_pristine_diagnostic_output(forged)

    with pytest.raises(DiagnosticCropManifestError, match="validation"):
        DiagnosticOutput("HOME_BUILDER_COUNT", "BUILDER_FRACTION", (1, 5), 750_000)


@pytest.mark.parametrize(
    "boundary",
    [
        "value",
        "crop_id",
        "confidence",
        "rebuild",
        "resolve_crop",
        "resolve_source",
        "dimensions_crop",
        "forged_spec",
        "spec_constructor",
        "output_constructor",
    ],
)
def test_rejected_raw_ocr_is_not_retained_by_exception_tracebacks(boundary: str) -> None:
    canary = "RAW-OCR-PRIVATE-CANARY"
    try:
        if boundary == "value":
            validate_diagnostic_output(
                CropId.HOME_BUILDER_COUNT,
                canary,
                confidence_ppm=750_000,
            )
        elif boundary == "crop_id":
            validate_diagnostic_output(canary, (1, 5), confidence_ppm=750_000)  # type: ignore[arg-type]
        elif boundary == "confidence":
            validate_diagnostic_output(
                CropId.HOME_BUILDER_COUNT,
                (1, 5),
                confidence_ppm=canary,  # type: ignore[arg-type]
            )
        elif boundary == "rebuild":
            forged = tuple.__new__(
                DiagnosticOutput,
                ("HOME_BUILDER_COUNT", "BUILDER_FRACTION", canary, 750_000),
            )
            require_pristine_diagnostic_output(forged)
        elif boundary == "resolve_crop":
            resolve_diagnostic_crop(canary, SourceState.HOME_VILLAGE)  # type: ignore[arg-type]
        elif boundary == "resolve_source":
            resolve_diagnostic_crop(CropId.HOME_BUILDER_COUNT, canary)  # type: ignore[arg-type]
        elif boundary == "dimensions_crop":
            validate_crop_dimensions(canary, 1, 1)  # type: ignore[arg-type]
        elif boundary == "forged_spec":
            forged_spec = tuple.__new__(
                DiagnosticCropSpec,
                (canary, "HOME_VILLAGE", (0, 0, 1, 1), (1, 1), "x", "x"),
            )
            require_manifest_crop(forged_spec)
        elif boundary == "spec_constructor":
            DiagnosticCropSpec(canary, canary, (0, 0, 1, 1), (1, 1), canary, canary)
        else:
            DiagnosticOutput(canary, canary, canary, 1)
    except DiagnosticCropManifestError as error:
        assert error.__cause__ is None
        assert error.__context__ is None
        traceback = error.__traceback__
        while traceback is not None:
            if (
                traceback.tb_frame.f_globals.get("__name__")
                == "clash_rush_rebuild.diagnostic_crop_manifest"
            ):
                assert canary not in repr(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next
    else:
        pytest.fail("raw OCR was accepted")
