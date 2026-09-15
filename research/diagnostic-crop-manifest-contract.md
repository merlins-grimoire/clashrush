# Diagnostic crop manifest contract

## Scope and donor decision

This slice defines an immutable, version-1 crop manifest and validates only synthetic closed/numeric outputs. It does not capture frames, invoke OCR, persist pixels, access `private/` or `var/`, issue input, touch lifecycle state, send Discord/network traffic, or authorize gameplay or spending.

The pinned donor comparison used:

- CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` (MIT), `src/upgrader.py:62-128` and `src/utils.py:496-520,646-670,1572-1619`: separate normalized Home/Builder HUD regions, grayscale/high-contrast preprocessing, and slash-anchored builder/lab reads. Its ADB capture, unrestricted OCR strings, debug image writes, exception swallowing, and timeout loops are excluded.
- ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` (MIT), `utils/settings.py:148-177`: normalized coordinate scaling. Its mutable recursive configuration, first-window selection, input path, and pixel-coordinate representation are excluded.
- BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (MIT), `app/core/upgrade_menu.py:1-52`: bounded numeric costs and normalized HUD/menu fences. Its free-form labels, full-frame debug persistence, and gameplay coupling are excluded.

No donor code is copied. The local implementation keeps only the reviewed coordinate/preprocessing concepts and adds the required sealed source-state, dimension, grammar, and output boundaries, so no new third-party notice is required.

## Coordinate and crop contract

A normalized ROI is `(left, top, right, bottom)` in integer millionths of the positively identified render surface. The exact scale is `1_000_000`; floats, NaN, infinity, raw screen pixels, and caller-selected ROIs have no API path. Every entry also caps the resulting crop width and height.

| Crop ID | Required source state | Normalized ROI | Maximum dimensions | Preprocessing | Output grammar |
|---|---|---:|---:|---|---|
| `HOME_RESOURCE_BALANCES` | `HOME_VILLAGE` | `(800000,0,960000,300000)` | `512x384` | `GRAYSCALE_HIGH_CONTRAST_240` | `RESOURCE_TRIPLE` |
| `HOME_BUILDER_COUNT` | `HOME_VILLAGE` | `(490000,40000,545000,80000)` | `128x64` | `GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED` | `BUILDER_FRACTION` |
| `BUILDER_BUILDER_COUNT` | `BUILDER_VILLAGE` | `(565000,40000,620000,80000)` | `128x64` | `GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED` | `BUILDER_FRACTION` |
| `HOME_LAB_STATUS` | `HOME_VILLAGE` | `(368000,40000,410000,80000)` | `128x64` | `GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED` | `AVAILABILITY` |
| `BUILDER_LAB_STATUS` | `BUILDER_VILLAGE` | `(448000,40000,485000,80000)` | `128x64` | `GRAYSCALE_HIGH_CONTRAST_200_SLASH_ANCHORED` | `AVAILABILITY` |

`resolve_diagnostic_crop` accepts exact closed `CropId` and `SourceState` members. It rejects text lookalikes, source mismatches, forged values, and unknown IDs. `DiagnosticCropSpec` construction is sealed, and `require_manifest_crop` accepts only the exact immutable manifest entries, so an arbitrary ROI cannot be introduced by constructing an equal-looking value.

`validate_crop_dimensions` accepts positive exact integers only and rejects either dimension above the entry-specific cap. `bool`, floats, strings, and zero/negative values are rejected.

The canonical ASCII JSON representation of the schema version, coordinate scale, and every ordered field is SHA-256 sealed by `diagnostic_crop_manifest_seal`. Any reviewed content change produces a different seal.

## Output grammar

OCR engines remain outside this boundary. They must interpret their private transient text before calling `validate_diagnostic_output`; raw OCR text is never accepted or retained.

- `RESOURCE_TRIPLE`: exact tuple `(gold, elixir, dark_elixir)`; each value is an exact integer from `0` through `2_000_000_000`.
- `BUILDER_FRACTION`: exact tuple `(free, total)`; exact integers with `0 <= free <= total <= 10` and `total >= 1`.
- `AVAILABILITY`: exact closed `ClosedOutput` member `AVAILABLE | BUSY | UNKNOWN`. The same words supplied as strings are rejected.
- Confidence is a separate exact integer in millionths from `0` through `1_000_000`; floats, booleans, and text are rejected.

Validated output is detached into immutable scalar storage. `require_pristine_diagnostic_output` rebuilds it through the grammar and rejects forged tuple storage, grammar substitutions, and unrestricted text without reflecting rejected content into exceptions.

## Verification

`tests/test_diagnostic_crop_manifest.py` proves exact manifest contents, source-state binding, normalized ROI and maximum-dimension bounds, deterministic sealing, sealed construction, forged-spec rejection, output detachment, forged-output rejection, numeric boundaries, closed availability values, confidence bounds, and rejection of arbitrary ROIs and raw OCR strings. Tests use only synthetic values and no pixels or private identifiers.
