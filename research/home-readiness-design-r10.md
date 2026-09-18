# R10 design: single-frame positive-anchor constellation

Status: DESIGN ONLY; pending independent review. No production implementation,
calibration, live observation, or retry is authorized by this document.

## 1. Decision and evidence limit

Specify exactly one method: compare three spatially separated, positive static
UI anchors from the same owned transient frame against a closed catalog of
screen/layout constellations. A unique supported constellation supplies both a
screen-family observation and a layout observation. This is not a second HOME
gate, a polling algorithm, OCR, a color-threshold change, or an alternate capture
transport. Missing evidence yields UNKNOWN, never inferred loading.

The R9B observation `UNVERIFIED / DIMENSIONS_MATCH / MIXED_SAMPLED /
COLOR_ABSENT` remains UNKNOWN. Matching frame dimensions does not establish a
correct content viewport; mixed color does not establish a loaded game. This
new method adds semantic and spatial evidence rather than repeating those
counts. It cannot retroactively classify R9B, reconstruct its image, or prove a
static defect. No private image is opened to produce this design.

Smallest release-relevant exit: a separately implemented inert diagnostic can
report a supported source-screen signature versus a supported displaced-content
signature, or explicitly abstain, without reaching any gameplay authority.
It need not recognize every screen. A positive signature is evidence about the
captured content, not proof of a fresh game state, account identity, an enabled
button, army readiness, or safe action.

## 2. Donor-first inspection and reuse decision

Source, licenses, and tracked test listings below were re-inspected through local
immutable Git objects, not mutable donor files, image assets, or network access.
All three root LICENSE files contain the MIT grant. No code/assets are copied
in this documentation-only change.

| Pinned source | Complete seam and dependencies inspected | Decision and test evidence |
| --- | --- | --- |
| BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | `app/core/bot.py:223` `_update_config_size`, `:242` `_find_home_village_builder`, `:1428` `_home_screen_recovery`; `app/services/vision.py:185` `VisionService.find_template`, plus confidence variant at `:233`. Recovery captures, selects aspect assets, handles rewards/modal/input branches, then searches top-half builder templates. Matcher loads assets, scales them, fences a region, uses `TM_CCOEFF_NORMED`, restores region offsets, returns a center. | Largest compatible positive-anchor mechanic. Later implementation should adapt the region/shape validation and normalized correlation seam with attribution, not the recovery orchestrator. Replace file-loading/logging, first-hit selection, swallowed exceptions and mutable aspect selection with the closed in-memory catalog and complete competitor evaluation below. `tests/test_event_rewards.py` and `tests/test_upgrade_reads.py` are tracked; neither is a direct readiness/viewport test. Do not transplant rewards, clicks, sleeps or its HOME authority. |
| CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | `src/utils.py:496` `get_home_builders`, `:522` `start_coc`, `:458` `to_home_base`; `Frame_Handler.locate` matching/cached-frame path. Startup uses ADB, exit clicks, HOME/Builder HUD slash templates and location cache. | A slash alone is shared screen evidence, not a unique HOME constellation. Requires calibrated HUD/assets and optional OCR; caches, debug crop writes, sleeps and input conflict with the contract. No tracked `tests/` paths at this pin. Reject whole startup/caching seam; preserve the lesson to distinguish Home and Builder HUDs. |
| ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` | `utils/clash_base.py:93` `current_location` captures to a file; `utils/object_detection.py:43` `determine_base_location` tests a configured BGR pixel and writes an annotated copy; `tests/test_detection_robustness.py`. | Normalized geometry/channel-order and missing-image validation are useful, but one pixel and persisted captures cannot supply this evidence. Missing-file tests are substantive; screenshot-dependent robustness cases skip without local files. Reject capture/annotation and configured-pixel classification. |

Ranking: BasePilot's region-fenced positive-template primitive is the largest
compatible licensed recognition seam; neither other donor supplies complete
multi-anchor spatial discrimination with native ownership, scalar-only output,
and finite capture cancellation. Local additions are competitor/constellation
reduction and governance, not another recovery executor. Asset rights must be
verified separately: MIT source licensing does not establish redistribution
rights for game artwork. No donor test success count is claimed here.

## 3. Current production seam and integration constraints

Baseline is commit `85fb0d932bae2a7f6fb0adc8bd8ed2a53b10d1ad`, tree
`51fae5246290f68d9af532c9e0793cdbffb8da9d`.

- `home_source_diagnostic.py:observe_source` owns one returned evidence frame,
  sealed scalar output and pre/post validation; its caller ports must themselves
  be bounded. It does not provide the new semantic evidence or a native timeout.
- `lifecycle.py:LifecycleSupervisor.capture_owned` calls `capture_ready` before
  `capture_bgra`. In `win32_lifecycle_host.py:Win32LifecycleHost.capture_ready`,
  `_require_capture_binding` is followed by `capture_health`, which itself
  captures a frame (`capture.py:capture_health`). Thus one public capture call
  is not one native acquisition. A revalidator using `capture_ready` would add
  still more acquisitions. Do not claim a one-frame budget from mock call counts.
- `_require_capture_binding` checks HWND existence/visibility/root ancestry,
  retained process creation identities and private Job membership through the
  existing identity seam, exact client size, unlocked desktop and non-minimized
  root. Shape validation of `PlayerBinding` is not that ownership proof.
- `NativeLifecycleApi.capture_printwindow_bgra` uses synchronous `PrintWindow`
  flag 2, top-down 32-bit BI_RGB (B,G,R,X), a 64 MiB ceiling and independently
  guarded GDI cleanup. It has no enforceable native-call deadline today.
- `mvp_local_runtime.py:BgraGameplayRecognizer` and native visit ordering keep
  existing HOME/account/army gates. The R10 result must have no production
  recognizer, transaction, approval, input-constructor or Run caller.

A future adapter must preserve ownership and health checks but perform health
reduction on the same acquired frame, with no-capture pre/post revalidation.
It is a separately reviewed native composition change, not permission to bypass
`capture_ready` with a shape check or to edit sealed lifecycle start/stop.
The old R9B callable cannot simply be wrapped to satisfy this contract.

## 4. Closed input contract

Proposed boundary name: `observe_readiness_once`. This name is a specification,
not an existing exported API. Inputs are a service-owned binding snapshot,
an admitted catalog, a trusted monotonic clock and a trusted deadline-capable
owned-observation port. No arbitrary matcher callback, caller-selected ROI,
threshold, timeout, candidate list, path, image loader, or output sink is accepted.
Production dispatch must class-qualify or seal every adapter-reachable helper;
instance/subclass replacements do not become trusted capabilities.

External inputs are validated once before comparison/hash/serialization and
copied into exact immutable service values. Exact built-in integer means not
bool, numeric subclass or equality/hash-spoofing object. No attacker-controlled
property or serializer executes in the emission path. Service module code,
stdlib, and the admitted native capability are trusted; arbitrary interpreter
monkeypatching or a malicious port retaining pixels is outside this boundary.

Binding: exact `PlayerBinding`/`ProcessIdentity` shape and unchanged copied scalar
fields (including both identities, root/render HWNDs, dimensions, capture nonce)
plus actual retained-handle/Job authority under the lifecycle lease. None of
these private fields is serialized. Minimum geometry 640 by 360 and at most
64 MiB of BGRA bytes; R10 additionally requires an exact catalog geometry.

Frame: exact tuple `(width:int, height:int, pixels:bytes)`, dimensions exactly
matching the bound render client, exact `width*height*4` length, top-down BGRX;
ignore X. At most one evidence acquisition and one full-frame owner, ever.
Views may alias that allocation; no full-frame copy, grayscale copy, pyramid,
second transport, screenshot cache, hash or image sink. Native backing storage
is transient and cleared on conversion/cleanup; two observations never overlap.

### Catalog schema 1 (not yet populated or admitted)

One sealed service-owned catalog for exactly one render geometry and one game
UI/aspect revision. Revision is private configuration, not report content.
No fallback catalog or automatic resolution/locale/aspect selection.

Closed screen vocabulary: HOME, LOADING, BUILDER. Each requires exactly three
static, non-identifier-bearing anchors from distinct screen regions. HOME uses
Home-specific builder artwork, the Home attack control artwork and Home-specific
shop/control artwork; BUILDER uses their Builder-specific counterparts. LOADING
uses static game-logo artwork, a static loading-track surround and a distinct
static loading-scene motif. Dynamic fill, numbers, names, avatars, tags, chat,
resource amounts and account-specific scenery are excluded. These are proposed
anchor roles, not a claim that suitable unique assets have been calibrated.
If any role cannot be made discriminative across the negative corpus, the whole
catalog is unadmitted; do not weaken to two anchors or substitute generic color.

Closed layout vocabulary (render-normalized viewport boxes, half-open):

| Layout | x0,y0,x1,y1 |
| --- | --- |
| NOMINAL | 0,0,1,1 |
| H_INSET_5 | 0,0.05,1,0.95 |
| H_INSET_10 | 0,0.10,1,0.90 |
| V_INSET_5 | 0.05,0,0.95,1 |
| V_INSET_10 | 0.10,0,0.90,1 |

These are finite diagnostic hypotheses, not new allowed runtime viewports. No
continuous transform search, rotation, arbitrary translation or ROI repair.
Unsupported geometry remains UNKNOWN. Every catalog entry stores screen enum,
layout enum, three integer half-open render rectangles, and three exact bounded
BGR template byte arrays. Exactly one entry per screen/layout pair: 15 entries,
45 templates total. Each template width/height is 8..96 and matches its rectangle;
all fit the frame. No unknown/missing/duplicate entry, ignored competitor or
zero-variance template. Rectangles and expected template scaling are precomputed
at catalog admission using the declared viewport mapping and floor boundaries;
no runtime interpolation or resizing. Nominal anchor rectangles are explicit
calibration data, not invented here. Validate all entries against their nominal
rectangles/mapping. Require nonoverlapping rectangles within each constellation,
with at least two centers separated by 25% of frame width or height. An inset
entry additionally requires at least two centers shifted by more than 1% of
width or height from their nominal positions, respectively; otherwise reject
catalog admission rather than treating a tiny shift as a mismatch.

Admission requires separate calibration authority, exact-byte sealing, provenance,
asset-rights/privacy review, and negative/cross-screen coverage. Artifact digests
may bind reviewed static template bytes privately; never hash a captured frame
or emit any digest. This task neither reads images nor creates/adopts templates.
Synthetic generic templates will exercise the algorithm without private assets.
An absent/unadmitted catalog produces UNKNOWN/CATALOG_UNAVAILABLE before capture;
a present but invalid one produces UNKNOWN/CATALOG_INVALID, with no fallback.

## 5. Single observation, finite budget, and ownership sequence

Hard design bounds: one method invocation, at most one native frame acquisition,
one classifier observation, 45 patch correlations, no sleeps, polls or retries.
Evidence deadline is `t0 + 2_000_000_000` monotonic nanoseconds. All clock values
are exact nonnegative integers; a decreasing value is CLOCK_INVALID. At or after
the deadline no candidate can be accepted and no new stage can start. Clock is
read at entry, before/after every external operation and each patch correlation,
and before final serialization. No wall-clock or caller-extensible timeout.

1. Snapshot validated inputs and admit the catalog. Check the port's reviewed
   deadline/cancellation capability before native work. Missing capability is
   UNKNOWN/BOUND_UNAVAILABLE, zero acquisitions. A boolean supplied by an arbitrary
   caller is not proof of this capability.
2. Under the existing exclusive ownership lease, no-capture revalidation must
   freshly prove exact root/render creation identity, private Job membership,
   HWND existence/visibility/ancestry, unchanged client size/nonce, unlocked and
   non-minimized desktop. Compare copied binding scalars before/after callbacks.
3. Acquire once through that owned port within the remaining absolute budget.
   Reject malformed shape/bytes/dimensions; no normalization. Perform unchanged
   health reduction on this very frame. A near-black rejection is UNKNOWN, not
   a loading or viewport diagnosis. No health recapture.
4. Evaluate all catalog competitors as below, retaining only local bounded
   scalar scores. No early positive while any competitor remains unevaluated.
5. Release every frame/view/patch/native buffer and matcher scratch reference;
   only scalar candidate state may remain. Fresh no-capture binding revalidation
   must pass again before any positive is eligible for emission. A later failure
   discards the provisional result, never returns a partial observation.
6. Serialize from one local validated scalar snapshot with fixed keys. Clear
   catalog, binding and callback references before returning. No diagnostic
   property read or callback can replace the chosen result after the decision.

Important feasibility gate: current synchronous PrintWindow cannot be made
cancellable by checking the clock afterward, a Future timeout, or abandoning a
thread that still owns pixels/handles. Therefore the present native port is
BOUND_UNAVAILABLE for this design. Future implementation must separately prove
bounded native cancellation and cleanup (including a hung operation), or remain
inert; this design does not secretly select or authorize an alternate transport.
No claim of a working two-second native guarantee is made. CPU budget checks
also do not make Windows/Python a real-time system.

Cleanup gets a separate, nonrenewable one-second budget after evidence abortion
or completion. If cancellation/resource release cannot be proved by then, emit
no positive and return only UNKNOWN/CLEANUP_UNPROVEN if safe scalar emission is
possible. The supervising lease must stay blocked; never report successful stop,
release the fleet for another launch, or leave an untracked worker. A future
native design must prove that no pixel-owning work survives its terminal return;
if it cannot, it fails promotion, rather than calling an orphan a timeout.
Existing owner-approved outer lifecycle cleanup remains mandatory, separate from
this evidence budget; this diagnostic never invokes game input or safe-screen
navigation and cannot authorize launch, stop reconciliation or state changes.

## 6. Deterministic positive evidence and classification

For each of the 45 declared same-size BGR patches, compute normalized centered
correlation (the BasePilot/OpenCV `TM_CCOEFF_NORMED` primitive) at that one declared
rectangle. Per-channel mean centering; sum centered channel products over pixels
and channels, divided by the square root of the product of centered energies.
Reject zero energy, non-finite or out-of-range result as unscorable; never let a
constant template/patch score as perfect. An unscorable candidate makes the whole
observation UNKNOWN/UNSCORABLE, not an omitted competitor. No image-sized score
map is required; accumulate bounded numeric sums directly from the frame view.

Each constellation score is the minimum of its three anchor scores. Select a
unique highest constellation only when its minimum is at least 0.92 and exceeds
EVERY other constellation minimum by at least 0.08. Compare unrounded scores;
rounding is not used for acceptance. At equality, 0.92 and margin 0.08 accept;
anything below rejects. Ties reject. An exact shared slash/button, orange block,
black bars or partially matching HUD cannot supply three unique anchors.
The thresholds are frozen design parameters, not validated live sensitivity
claims. If calibration cannot meet them, revise/review the design; never tune in
response to a failed live attempt. All 15 candidates participate even if the
unchanged orange cue would pass or fail. No second detector is a fallback.

Classification after successful postvalidation and cleanup:

| Unique constellation | Classification | Source | Layout evidence |
| --- | --- | --- | --- |
| HOME / NOMINAL | HOME_SIGNATURE | HOME | NOMINAL |
| LOADING or BUILDER / NOMINAL | NON_HOME_SIGNATURE | LOADING or BUILDER | NOMINAL |
| Any supported screen / one inset layout | VIEWPORT_MISMATCH_SIGNATURE | That screen | OFFSET |
| None, ambiguity, error, unsupported/missing evidence | UNKNOWN | UNVERIFIED | UNVERIFIED |

VIEWPORT_MISMATCH_SIGNATURE means recognizable content coherently occupies a
supported non-nominal mapping, based on multiple anchors, not merely dark edges.
It does not locate a new click target, repair a viewport, distinguish emulator
scaling from capture composition, or prove that a displayed loading image is
currently progressing. A non-HOME inset retains both facts via source and layout;
neither silently wins over the other. No inset ID/coordinates leave the method.
LOADING means a positive loading-screen signature, not a claim that HOME will
arrive after waiting. BUILDER means observed non-HOME content, not launch success.
HOME_SIGNATURE does not assert overlay absence or authorize any existing gate.
A paused/stale full screenshot with a valid constellation can be indistinguishable
from fresh rendered content in a single PrintWindow result; ownership validation
cannot prove freshness. Report only the signature, not a causal diagnosis.
Unknown popup, emulator launcher, battle, crop/offset outside this finite catalog,
unseen language/skin, dark/gray/colorful images and absent anchors all abstain.

## 7. Public scalar grammar and exception containment

Return exactly one canonical compact JSON string (sorted keys, no whitespace,
ASCII enum literals). Exactly these keys, no extension map, nullable fields,
free text, floats or nested objects:

- `schema`: exact integer 3.
- `method`: literal `ANCHOR_CONSTELLATION_V1`.
- `classification`: HOME_SIGNATURE | NON_HOME_SIGNATURE |
  VIEWPORT_MISMATCH_SIGNATURE | UNKNOWN.
- `source`: HOME | LOADING | BUILDER | UNVERIFIED.
- `layout`: NOMINAL | OFFSET | UNVERIFIED.
- `observations`: exact integer 0 or 1; 1 only if one complete well-formed evidence
  frame was delivered, even if its reduction or later revalidation failed.
- `reason`: POSITIVE | CATALOG_UNAVAILABLE | CATALOG_INVALID | BOUND_UNAVAILABLE |
  BINDING_INVALID | BINDING_UNVERIFIED | CLOCK_INVALID | DEADLINE | CAPTURE_FAILED |
  FRAME_INVALID | HEALTH_UNVERIFIED | UNSCORABLE | NO_MATCH | AMBIGUOUS |
  REDUCTION_FAILED | CLEANUP_UNPROVEN.

POSITIVE requires one observation and one of the first three classifications
with the matching table combination. Every other reason requires UNKNOWN,
UNVERIFIED source/layout. NO_MATCH means best minimum below 0.92; AMBIGUOUS means
floor passed but uniqueness/margin failed. All error outputs erase provisional
screen/layout facts. Error precedence: CLEANUP_UNPROVEN overrides; otherwise
first failed stage in the numbered sequence wins, with deadline/clock validation
performed at each stage boundary before other results can be admitted. Failure
during serialization emits only a fixed `READINESS_DIAGNOSTIC_FAILED` exception
outside the original handler, never partially serialized JSON.

No frame bytes/hash, colors, crop data, raw scores, anchor/template identity,
window/process/account identity, handle, nonce, timestamps, machine path, UI
text/OCR, resource values, log exception message, stack or object repr appears.
No report authorizes input, approval, retry, network/Discord, or publication.

All pixel-bearing helpers clear references in finally, including active helper
locals, memoryviews, temporary patch arrays and malformed return aliases. Native
cleanup independently attempts restoration, bitmap/DC deletion and release even
when a prior cleanup step fails. Original exception cause/context chains and
ExceptionGroup members have unwound tracebacks cleared without formatting them;
new closed exceptions are raised outside the original handler. The method does
not retain exception objects or arbitrary exception args. Fault injection must
retain original exceptions externally to prove that service traceback locals
contain no frame after failure. Do not claim to erase copies deliberately held
by malicious capabilities or securely zero Python immutable bytes. Process-wide
crash-dump/swap policy is outside an application-level no-file-write guarantee.

## 8. Exact RED-test plan for separately authorized implementation

New proposed file: `tests/test_home_readiness_constellation.py`. It does not exist
in this slice. First tests fail for the absent API/catalog types; no production
stub is introduced to make this design appear implemented. Use public procedural
nonconstant generic motifs; synthetic geometry/layout is test data, not live
calibration. Inject owned ports/clock into the isolated test composition only.

| ID | Required input/fault and exact expectation |
| --- | --- |
| R01 | All 15 screen/layout combinations rendered from synthetic catalog: nominal HOME -> HOME_SIGNATURE; nominal LOADING/BUILDER -> NON_HOME_SIGNATURE; each inset -> VIEWPORT_MISMATCH_SIGNATURE with correct source, OFFSET; all POSITIVE, observations=1. |
| R02 | R9B scalar-equivalent dark chromatic attack region/bright surrounding scene, black, several grays, colorful non-HOME, natural dark borders, one/two anchors only, generic slash and orange rectangle -> UNKNOWN/NO_MATCH for fully scorable misses, UNSCORABLE for constant patches, or HEALTH_UNVERIFIED for unchanged health rejection; never a semantic guess. |
| R03 | One anchor moved independently, inconsistent transforms, unsupported translation/rotation, clipping, unseen scale/language/skin -> UNKNOWN. No best-effort relocation. |
| R04 | Correlation minimum 0.92 and margin 0.08 at exact representable boundary tests accept; just below each rejects. Exact ties and two plausible constellations below margin -> AMBIGUOUS; enumerate candidates in every ordering -> identical output. Use reducer scalar fixtures for precise numerical boundary tests and independent patch-formula tests. |
| R05 | Constant patch/template, NaN/infinity/out-of-range correlation, invalid/missing competitor -> UNSCORABLE or pre-capture CATALOG_INVALID, never positive from the remaining entries. |
| R06 | Nominal/inset Home with old orange predicate false still supplies diagnostic signature only; orange predicate true on a nonmatching scene supplies no R10 positive. HOME/account/army predicates and native input-construction ordering remain byte-for-byte behaviorally unchanged. |
| R07 | No catalog, wrong seal/geometry/revision, extra key, missing/duplicate scene/layout/anchor, bad bytes/rectangle/variance/spacing, identifier-bearing asset rejected at admission -> zero capture, appropriate catalog reason, no fallback. |
| R08 | Exact binding and frame type matrix: booleans, int/tuple/bytes subclasses, hostile equality/hash/properties, fake/subclass binding, deleted/mutated fields, nested identity alias mutation, malformed byte count/dimensions/oversize -> no positive, closed failure, no hostile value serialized. |
| R09 | Pre/post identity or creation-time change, reused HWND/PID, lost Job membership, ancestry/size/nonce change, minimized/locked desktop, lease loss; callback returns None/1/truthy object or raises -> no positive; pre-failure makes zero acquisitions, post-failure exactly one. |
| R10 | Instrument actual native acquisition and health reduction, not only public capture calls: at most one acquisition and one shared frame, exactly 45 patch correlations for a valid result, no cached frame, no prior result reuse on second invocation, no capture in either revalidation. |
| R11 | Fake monotonic entry/step/deadline boundary: equality with deadline rejects, regression/invalid type rejects, no stage starts after expiry, no sleeps, extra capture or renewal. Missing certified deadline capability rejects before native call. |
| R12 | Native operation hangs, cancellation fails, each GDI cleanup step fails independently: prove bounded retirement/no surviving pixel owner before promotion; no late positive, orphan thread/Future or released lease on failure. Existing synchronous PrintWindow intentionally fails this prerequisite until separately resolved. |
| R13 | Retained callback/reducer/serializer exceptions, cause/context chains, ExceptionGroups, malformed returns aliasing pixels, each early return and BaseException -> cleared service frame/view/native locals; pixels gone before postvalidation; no raw exception escapes. |
| R14 | Instance-shadowed public method and every reachable helper, fake matcher/result object, property replacement after decision, valid-looking mutable diagnostic -> cannot alter atomic emitted snapshot. Trusted module code is not adversarially replaced in this authority test. |
| R15 | Closed-key/type/cross-field serialization test for every result and failure; forbid identifiers, arbitrary strings, raw scores, coordinates, paths, nonfinite values, pixels or image hashes. Native handles and exception repr must never reach serializer. |
| R16 | Import/call-graph and sentinel tests: no input construction, gameplay, file write, network/Discord, OCR, sleep, launch, approval issue/consume, reconciliation, or image/hash sink reachable. No production caller is installed. |
| R17 | All existing source-diagnostic, HOME scalar/provenance, lifecycle/capture cleanup and native-runtime tests remain green; canonical `python -m pytest tests/` on exact blob-verified export. |

Calibration exit (separate authority): all catalog entries need positive and
cross-screen negative evidence including overlays/shared icons, real supported
UI revisions, and independently verified asset provenance. Synthetic tests prove
logic/ownership, not game-screen sensitivity. If the proposed LOADING triple or
another catalog member cannot meet the discriminative threshold, remain
CATALOG_UNAVAILABLE and return to design review, not a live tuning/retry loop.

## 9. Gates and deliverable

This card changes only this public-safe design document and freezes it in one
local commit/tree after tracked-blob privacy scanning. Its pre-created independent
design-review child reviews that exact immutable candidate. No implementation
card or live gate is implicitly approved by a design PASS.

Later work requires explicit owner authorization for static implementation and
any calibration/image access. Implementation must adapt/test the licensed seam,
prove the native deadline and one-acquisition prerequisites, add attribution for
actual copied source, run focused and canonical suites from the exact exported
tree, privacy-scan it and obtain independent exact-tree PASS. Current absence of
an admitted catalog or deadline-capable capture port is an explicit integration
prerequisite, not a fabricated success or permission to weaken the contract.

A live diagnostic would then need separate owner approval bound to the exact
reviewed candidate, action, canonical lifecycle state, expiry and one invocation;
its sealed normal lifecycle wrapper must own launch/bind/observe/stop and prove
postconditions. Do not issue that approval here. No fixed sleep, blind fallback,
input, gameplay, new retry, state mutation, network/Discord or publication is
part of this design task. Regardless of any future diagnostic signature, existing
HOME/account/army gates remain unchanged and independently mandatory.
