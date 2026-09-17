# R9B: inert source-readiness / viewport evidence

## Scope and decision

This slice adds only `home_source_diagnostic.observe_source(binding, capture_owned,
revalidate)` and synthetic tests. Existing recognition, native composition,
HOME thresholds/ROI, lifecycle, approval, input, and the sealed external runner
are unchanged. There is no CLI/launcher, production call site, wait loop, retry,
image sink, file/network write, or gameplay authority. The caller receives an
immutable canonical JSON string, not a Recognition or action intent.

Recovered R8 evidence is width=1501, height=805, ROI=(52,724)-(127,780),
roi_pixels=4200, hue_pixels=0, saturation_pixels=4200, value_pixels=0,
orange_pixels=0. This proves a dark chromatic ROI, not HOME, loading, a capture
failure, or a misplaced viewport. V=max(B,G,R) makes channel swapping unable to
repair zero value matches. Three different synthetic scenes can share those
ROI counts; these are counterexamples, not reconstructions of a private image.

One observation is the smallest useful extension: compare the unchanged ROI
with bounded sampled surrounding content. Do not introduce multi-observation
waiting merely to guess whether darkness is loading. A one-shot sample can
separate a dimension mismatch, suspected dark borders around bright content,
a globally dark sample, and an existing positive HOME cue. It cannot reliably
identify the semantic source screen or conclusively diagnose the live failure.
Unknown remains UNVERIFIED. Static design review passed this bounded contract
before implementation; downstream exact-tree review is still required.

## Donor-first inventory

Re-inspected source and LICENSE from immutable local Git objects, with no network:

| Donor / MIT revision | Complete seam, callers and dependencies | Tests / decision |
| --- | --- | --- |
| BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | `app/core/bot.py:_home_screen_recovery` captures, updates aspect profile, handles rewards/popups/surrender/return/reconnect, then calls `_find_home_village_builder` with top-half template service; `_update_config_size` depends on config/aspect assets | Tracked tests are event rewards and upgrade reads, not startup/viewport diagnosis. Largest positive recovery seam, but input/reward authority and absent calibrated template coverage conflict with this inert boundary. Do not copy executor or assets. |
| CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | `src/utils.py:start_coc` invokes ADB startup, exit clicks and cached Frame_Handler, then `get_home_builders`; `to_home_base` falls through to zoom/swipes/boat input. Builder slash requires calibrated HUD ROI, template and optional OCR | No tracked tests. Complete seam conflicts with native ownership, memory-only evidence, no-input scope and cache freshness. Do not transplant slash test without its calibration/callers. |
| ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` | `utils/clash_base.py:current_location` delegates file captures to `utils/object_detection.py:determine_base_location`; configured BGR pixel targets and annotated-image writes | `tests/test_detection_robustness.py` verifies missing-file fail-closed behavior; screenshot-dependent tests can skip. Its normalized geometry and BGR lessons apply, but file capture/annotations and one configured color pixel do not provide the required memory-only readiness seam. |
| Existing local seam (baseline `d9c1085526247b2659e4598751c1e385cbcc71d4`) | `BgraGameplayRecognizer._diagnose_home` -> `_home_payload` and the OpenCV-compatible `_hsv`; `LifecycleSupervisor.capture_owned` already checks ownership and capture readiness | Reuse this largest compatible reduction unchanged. Existing diagnostic/provenance/seal/runtime tests remain the differential regression suite. The historical first-party HOME color predicate is not promoted as a new tactical authority. |

No third-party code/assets copied and no new dependency. The small grid reducer
is local diagnostic-only code because none of the three complete donor seams
provides this allowed behavior. Existing donor attribution remains unchanged.

## API and trust boundary

- Exact PlayerBinding, validated shape and minimum 640x360 geometry; native
  64 MiB BGRA ceiling retained. Shape checks do not prove ownership.
- Injected capture and revalidation ports are trusted capabilities. A future
  independently reviewed native composition must supply the owned lifecycle
  capture seam and fresh exact-binding/desktop readiness checks, under its lease.
  No lambda returning True is a valid production revalidation implementation.
- Revalidate immediately before the single capture and after releasing the frame.
  Both results must be exact True. Snapshot binding fields and reject callback
  replacement/mutation; the owned port remains the actual identity authority.
- Validate exact tuple, exact integer bound geometry, exact bytes and byte length.
  Geometry mismatch is a closed SOURCE_GEOMETRY_MISMATCH failure, never normalized
  or accepted as another resolution.
- Reuse the unchanged HOME reducer once. Add exactly 576 HSV sample evaluations.
  Bounded pixels plus one capture; no recapture, retained source, iteration,
  sleep, or temporal readiness inference. Injected ports own their finite
  operation deadlines; this synchronous function cannot interrupt a hung port.
  TimeoutError is sanitized and never retried. The conditional multi-observation
  deadline contract is not applicable because there is no multi-observation mode.
- Code/stdlib are trusted, as in R7; this is not a sandbox against arbitrary
  module monkeypatches, stack introspection, or a malicious capture capability.
  No mutable caller-owned diagnostic object crosses emission.

## Closed scalar grammar (schema 2)

All schema-1 scalar fields are preserved: reason, width, height, left, top, right,
bottom, roi_pixels, hue_pixels, saturation_pixels, value_pixels, orange_pixels.
The HOME predicate remains H=5..30, S>=100, V>=120, joint fraction strictly >0.30.
`schema` is 2 for this independent API; native schema-1 errors remain unchanged.

Additional fields:

- `geometry`: DIMENSIONS_MATCH or LETTERBOX_SUSPECT. Matching dimensions never
  means a calibrated viewport or correct capture content has been proven.
- `readiness`: HOME_CUE_PRESENT only when the existing strict predicate passes
  and geometry is not suspect; otherwise UNVERIFIED. Neither value authorizes
  gameplay, proves account/army readiness, nor replaces the existing gate.
- `content`: BLACK_SAMPLED, LOW_CHROMA_SAMPLED, DARK_CHROMATIC_SAMPLED, MIXED_SAMPLED.
  In precedence order: all sampled V<=8; no sampled S>=100; no sampled V>=120
  with some S>=100; everything else. These describe samples, never all pixels
  or semantic loading. Low chroma is not synonymous with exact gray.
- `sample_pixels`=576; sample_black, sample_bright, sample_chromatic in [0,576].
- `top_dark`, `bottom_dark` in [0,32]; `left_dark`, `right_dark` in [0,18].
- `center_pixels`=144; `center_bright` in [0,144].

The sample centers are x=(2*column+1)*width//64 for columns [0,31] and
y=(2*row+1)*height//36 for rows [0,17]. Dark means V<120. Opposed outer
sample rows both entirely dark, or opposed outer columns both entirely dark,
plus >72 bright central samples (columns [8,23], rows [4,12]) yields
LETTERBOX_SUSPECT. Natural scenes can share that signature; narrow bars and
unsampled content may be missed. No crop/ROI is moved and no false-negative
repair is inferred. The center is a fixed grid subset, not detected geometry.

Closed failure strings: SOURCE_BINDING_INVALID, SOURCE_BINDING_UNVERIFIED,
SOURCE_CAPTURE_FAILED, SOURCE_SHAPE_INVALID, SOURCE_GEOMETRY_MISMATCH,
SOURCE_BYTES_INVALID, SOURCE_REDUCTION_FAILED. No callback messages are relayed.
No raw color, sample map, pixels, identity, label, path, handle, OCR, or hash
enters the output. Serialization is atomic before exposing anything to callers.

## Frame lifetime and proof limits

Only one full-frame bytes reference is owned at a time; helper references alias
that same bytes object without image copying. Reducers clear frame/pixels in
finally. Observation clears frame before the postvalidation callback and clears
binding/port references before raising. Failure clears unwound tracebacks,
including cause/context chains and exception groups, and emits a new closed
exception outside the handler. Python immutable bytes cannot be securely zeroed;
this releases owned references, not copies deliberately retained by a caller.

Synthetic tests cover recovered dark chromatic ROI vs bright surroundings,
loading-colored/non-HOME, valid HOME, both letterbox axes and conflicting HOME
cue, black/gray and brightness limits, BGRA/alpha, exact geometry/bytes, strict
center-majority boundary, exact revalidation results, binding alias mutation,
callback timeout/failure/chains/groups, reduction failure, traceback cleanup,
no previous observation reuse, no pixels at postvalidation, bounded work,
closed output keys and absence of native/IO imports. They do not validate a
private live screen, detection sensitivity, or prove a loading-screen template.

## Exit gate

Focused tests and canonical `pytest tests/` must pass against a blob-verified
exact staged export with PYTHONPATH explicitly bound to that export. Privacy
scan precedes local commit. The dependent review card evaluates the immutable
commit/tree. No static PASS authorizes capture, launch, integration into Run,
retry, input, gameplay, network/Discord or publication.
