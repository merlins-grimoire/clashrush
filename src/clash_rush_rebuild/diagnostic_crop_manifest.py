"""Sealed definitions for bounded diagnostic image crops.

Coordinates are integer millionths of a positively bound render surface.  Consumers
resolve a crop by closed ID and source state; no public API accepts a caller ROI.
"""

import hashlib
import json
from enum import StrEnum
from typing import Self

_NORMALIZED_SCALE = 1_000_000
_MAX_RESOURCE_VALUE = 2_000_000_000
_MAX_BUILDERS = 10
_MAX_CONFIDENCE = 1_000_000
_SPEC_TOKEN = object()
_OUTPUT_TOKEN = object()


class DiagnosticCropManifestError(ValueError):
    """A diagnostic crop or output is outside the sealed manifest."""


class CropId(StrEnum):
    HOME_RESOURCE_BALANCES = "HOME_RESOURCE_BALANCES"
    HOME_BUILDER_COUNT = "HOME_BUILDER_COUNT"
    BUILDER_BUILDER_COUNT = "BUILDER_BUILDER_COUNT"
    HOME_LAB_STATUS = "HOME_LAB_STATUS"
    BUILDER_LAB_STATUS = "BUILDER_LAB_STATUS"


class SourceState(StrEnum):
    HOME_VILLAGE = "HOME_VILLAGE"
    BUILDER_VILLAGE = "BUILDER_VILLAGE"


class Preprocessing(StrEnum):
    GRAYSCALE_HIGH_CONTRAST_240 = "GRAYSCALE_HIGH_CONTRAST_240"
    GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED = (
        "GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED"
    )


class OutputGrammar(StrEnum):
    RESOURCE_TRIPLE = "RESOURCE_TRIPLE"
    BUILDER_FRACTION = "BUILDER_FRACTION"
    AVAILABILITY = "AVAILABILITY"


class ClosedOutput(StrEnum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    UNKNOWN = "UNKNOWN"


_CROP_ID_VOCABULARY = tuple((member, member.value) for member in CropId)
_SOURCE_STATE_VOCABULARY = tuple((member, member.value) for member in SourceState)
_CLOSED_OUTPUT_VOCABULARY = tuple((member, member.value) for member in ClosedOutput)


def _closed_member_text(
    value: object,
    expected_type: type[object],
    vocabulary: tuple[tuple[object, str], ...],
    label: str,
) -> str:
    if type(value) is not expected_type:
        raise DiagnosticCropManifestError(f"{label} must be a closed value")
    for member, text in vocabulary:
        if value is member:
            if (
                type(object.__getattribute__(member, "_name_")) is not str
                or object.__getattribute__(member, "_name_") != text
                or type(object.__getattribute__(member, "_value_")) is not str
                or object.__getattribute__(member, "_value_") != text
            ):
                raise DiagnosticCropManifestError(f"{label} closed value is corrupted")
            return text
    raise DiagnosticCropManifestError(f"{label} must be a closed value")


class DiagnosticCropSpec(tuple[object, ...]):
    """One immutable manifest entry; callers cannot construct new entries."""

    __slots__ = ()

    def __new__(
        cls,
        crop_id: str,
        source_state: str,
        normalized_roi: tuple[int, int, int, int],
        maximum_dimensions: tuple[int, int],
        preprocessing: str,
        output_grammar: str,
        *,
        _token: object | None = None,
    ) -> Self:
        if _token is not _SPEC_TOKEN:
            crop_id = None  # type: ignore[assignment]
            source_state = None  # type: ignore[assignment]
            normalized_roi = None  # type: ignore[assignment]
            maximum_dimensions = None  # type: ignore[assignment]
            preprocessing = None  # type: ignore[assignment]
            output_grammar = None  # type: ignore[assignment]
            _token = None
            raise DiagnosticCropManifestError("diagnostic crop specifications are sealed")
        return tuple.__new__(
            cls,
            (
                crop_id,
                source_state,
                normalized_roi,
                maximum_dimensions,
                preprocessing,
                output_grammar,
            ),
        )

    @property
    def crop_id(self) -> str:
        return self[0]  # type: ignore[return-value]

    @property
    def source_state(self) -> str:
        return self[1]  # type: ignore[return-value]

    @property
    def normalized_roi(self) -> tuple[int, int, int, int]:
        return self[2]  # type: ignore[return-value]

    @property
    def maximum_dimensions(self) -> tuple[int, int]:
        return self[3]  # type: ignore[return-value]

    @property
    def preprocessing(self) -> str:
        return self[4]  # type: ignore[return-value]

    @property
    def output_grammar(self) -> str:
        return self[5]  # type: ignore[return-value]


class DiagnosticOutput(tuple[object, ...]):
    """A detached closed/numeric value suitable for a bounded debug view."""

    __slots__ = ()

    def __new__(
        cls,
        crop_id: str,
        grammar: str,
        value: object,
        confidence_ppm: int,
        *,
        _token: object | None = None,
    ) -> Self:
        if _token is not _OUTPUT_TOKEN:
            crop_id = None  # type: ignore[assignment]
            grammar = None  # type: ignore[assignment]
            value = None
            confidence_ppm = None  # type: ignore[assignment]
            _token = None
            raise DiagnosticCropManifestError("diagnostic outputs require validation")
        return tuple.__new__(cls, (crop_id, grammar, value, confidence_ppm))

    @property
    def crop_id(self) -> str:
        return self[0]  # type: ignore[return-value]

    @property
    def grammar(self) -> str:
        return self[1]  # type: ignore[return-value]

    @property
    def value(self) -> object:
        return self[2]

    @property
    def confidence_ppm(self) -> int:
        return self[3]  # type: ignore[return-value]


def _spec(
    crop_id: str,
    source_state: str,
    roi: tuple[int, int, int, int],
    maximum: tuple[int, int],
    preprocessing: str,
    grammar: str,
) -> DiagnosticCropSpec:
    left, top, right, bottom = roi
    if not (
        all(type(value) is int for value in roi)
        and 0 <= left < right <= _NORMALIZED_SCALE
        and 0 <= top < bottom <= _NORMALIZED_SCALE
        and all(type(value) is int and value > 0 for value in maximum)
    ):
        raise RuntimeError("invalid built-in diagnostic crop specification")
    return DiagnosticCropSpec(
        crop_id,
        source_state,
        roi,
        maximum,
        preprocessing,
        grammar,
        _token=_SPEC_TOKEN,
    )


_MANIFEST = (
    _spec(
        "HOME_RESOURCE_BALANCES",
        "HOME_VILLAGE",
        (800_000, 0, 960_000, 300_000),
        (512, 384),
        "GRAYSCALE_HIGH_CONTRAST_240",
        "RESOURCE_TRIPLE",
    ),
    _spec(
        "HOME_BUILDER_COUNT",
        "HOME_VILLAGE",
        (490_000, 40_000, 545_000, 80_000),
        (128, 64),
        "GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED",
        "BUILDER_FRACTION",
    ),
    _spec(
        "BUILDER_BUILDER_COUNT",
        "BUILDER_VILLAGE",
        (565_000, 40_000, 620_000, 80_000),
        (128, 64),
        "GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED",
        "BUILDER_FRACTION",
    ),
    _spec(
        "HOME_LAB_STATUS",
        "HOME_VILLAGE",
        (368_000, 40_000, 410_000, 80_000),
        (128, 64),
        "GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED",
        "AVAILABILITY",
    ),
    _spec(
        "BUILDER_LAB_STATUS",
        "BUILDER_VILLAGE",
        (448_000, 40_000, 485_000, 80_000),
        (128, 64),
        "GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED",
        "AVAILABILITY",
    ),
)


def _canonical_manifest_bytes() -> bytes:
    payload = {
        "coordinate_scale": _NORMALIZED_SCALE,
        "schema_version": 1,
        "specs": [
            {
                "crop_id": spec.crop_id,
                "maximum_dimensions": list(spec.maximum_dimensions),
                "normalized_roi": list(spec.normalized_roi),
                "output_grammar": spec.output_grammar,
                "preprocessing": spec.preprocessing,
                "source_state": spec.source_state,
            }
            for spec in _MANIFEST
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")


_MANIFEST_SEAL = hashlib.sha256(_canonical_manifest_bytes()).hexdigest()


def diagnostic_crop_manifest() -> tuple[DiagnosticCropSpec, ...]:
    """Return the immutable, version-1 ordered crop manifest."""
    return _MANIFEST


def diagnostic_crop_manifest_seal() -> str:
    """Return the canonical SHA-256 seal for all manifest content."""
    return _MANIFEST_SEAL


def _spec_for_crop(crop_id: CropId) -> DiagnosticCropSpec:
    crop_text = _closed_member_text(crop_id, CropId, _CROP_ID_VOCABULARY, "crop id")
    for spec in _MANIFEST:
        if spec.crop_id == crop_text:
            return spec
    raise DiagnosticCropManifestError("crop id is not manifest-approved")


def require_manifest_crop(spec: DiagnosticCropSpec) -> DiagnosticCropSpec:
    """Reject forged or caller-constructed crop specifications, including arbitrary ROIs."""
    approved = type(spec) is DiagnosticCropSpec and any(
        spec is item for item in _MANIFEST
    )
    if not approved:
        spec = None  # type: ignore[assignment]
        raise DiagnosticCropManifestError("crop specification is not manifest-approved")
    return spec


def resolve_diagnostic_crop(
    crop_id: CropId, source_state: SourceState
) -> DiagnosticCropSpec:
    """Resolve one approved crop only for its positively identified source state."""
    try:
        spec = _spec_for_crop(crop_id)
        source_text = _closed_member_text(
            source_state, SourceState, _SOURCE_STATE_VOCABULARY, "source state"
        )
        error_message = (
            "" if source_text == spec.source_state else "crop source state does not match the manifest"
        )
    except DiagnosticCropManifestError as error:
        spec = None
        error_message = str(error)
    crop_id = None  # type: ignore[assignment]
    source_state = None  # type: ignore[assignment]
    if spec is None or error_message:
        raise DiagnosticCropManifestError(error_message) from None
    return spec


def validate_crop_dimensions(crop_id: CropId, width: int, height: int) -> tuple[int, int]:
    """Require positive exact dimensions no larger than the crop-specific cap."""
    try:
        spec = _spec_for_crop(crop_id)
        valid = (
            type(width) is int
            and type(height) is int
            and width > 0
            and height > 0
            and width <= spec.maximum_dimensions[0]
            and height <= spec.maximum_dimensions[1]
        )
        result = (width, height) if valid else None
        error_message = "crop dimensions exceed the manifest"
    except DiagnosticCropManifestError as error:
        result = None
        error_message = str(error)
    crop_id = None  # type: ignore[assignment]
    width = None  # type: ignore[assignment]
    height = None  # type: ignore[assignment]
    if result is None:
        raise DiagnosticCropManifestError(error_message) from None
    return result


def _numeric_tuple(
    value: object, length: int, upper_bound: int
) -> tuple[int, ...] | None:
    if type(value) is not tuple or len(value) != length:
        return None
    if any(type(item) is not int or item < 0 or item > upper_bound for item in value):
        return None
    return tuple(value)


def _validated_output(
    crop_id: CropId, value: object, confidence_ppm: int
) -> tuple[DiagnosticOutput | None, str]:
    try:
        spec = _spec_for_crop(crop_id)
    except DiagnosticCropManifestError:
        return None, "diagnostic output violates the manifest grammar"
    if (
        type(confidence_ppm) is not int
        or confidence_ppm < 0
        or confidence_ppm > _MAX_CONFIDENCE
    ):
        return None, "diagnostic confidence is outside its numeric bound"

    normalized: object | None = None
    if spec.output_grammar == "RESOURCE_TRIPLE":
        normalized = _numeric_tuple(value, 3, _MAX_RESOURCE_VALUE)
    elif spec.output_grammar == "BUILDER_FRACTION":
        builders = _numeric_tuple(value, 2, _MAX_BUILDERS)
        if builders is not None and builders[1] >= 1 and builders[0] <= builders[1]:
            normalized = builders
    elif spec.output_grammar == "AVAILABILITY":
        try:
            normalized = _closed_member_text(
                value,
                ClosedOutput,
                _CLOSED_OUTPUT_VOCABULARY,
                "diagnostic output",
            )
        except DiagnosticCropManifestError:
            normalized = None

    if normalized is None:
        return None, "diagnostic output violates the manifest grammar"
    return (
        DiagnosticOutput(
            spec.crop_id,
            spec.output_grammar,
            normalized,
            confidence_ppm,
            _token=_OUTPUT_TOKEN,
        ),
        "",
    )


def validate_diagnostic_output(
    crop_id: CropId, value: object, *, confidence_ppm: int
) -> DiagnosticOutput:
    """Detach OCR interpretation without retaining rejected raw text in tracebacks."""
    try:
        result, error_message = _validated_output(crop_id, value, confidence_ppm)
    except DiagnosticCropManifestError:
        result = None
        error_message = "diagnostic output violates the manifest grammar"
    crop_id = None  # type: ignore[assignment]
    value = None
    confidence_ppm = None  # type: ignore[assignment]
    if result is None:
        raise DiagnosticCropManifestError(error_message) from None
    return result


def _rebuild_diagnostic_output(output: DiagnosticOutput) -> DiagnosticOutput | None:
    if type(output) is not DiagnosticOutput or len(output) != 4:
        return None
    crop_text, grammar, value, confidence = tuple(output)
    if type(crop_text) is not str or type(grammar) is not str:
        return None

    member: CropId | None = None
    spec: DiagnosticCropSpec | None = None
    for candidate, canonical in _CROP_ID_VOCABULARY:
        if crop_text == canonical:
            member = candidate  # type: ignore[assignment]
            spec = _spec_for_crop(member)
            break
    if member is None or spec is None or grammar != spec.output_grammar:
        return None

    candidate_value = value
    if grammar == "AVAILABILITY":
        candidate_value = None
        if type(value) is str:
            for closed_member, canonical in _CLOSED_OUTPUT_VOCABULARY:
                if value == canonical:
                    candidate_value = closed_member
                    break
    rebuilt, _ = _validated_output(
        member,
        candidate_value,
        confidence,  # type: ignore[arg-type]
    )
    return rebuilt


def require_pristine_diagnostic_output(output: DiagnosticOutput) -> DiagnosticOutput:
    """Rebuild validated storage without retaining rejected raw text in tracebacks."""
    try:
        rebuilt = _rebuild_diagnostic_output(output)
    except DiagnosticCropManifestError:
        rebuilt = None
    output = None  # type: ignore[assignment]
    if rebuilt is None:
        raise DiagnosticCropManifestError("diagnostic output requires validation") from None
    return rebuilt