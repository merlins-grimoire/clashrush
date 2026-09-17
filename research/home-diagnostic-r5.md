# Initial HOME reassessment: diagnostics-only candidate

## Decision and evidence boundary

The preceding bounded visit failed with `HOME_NOT_VERIFIED` before native-input construction and before any transaction INTENT. Its separate postcheck established executed=false and confirmed=false, with owned stop complete. This static slice does not re-observe or modify runtime state.

No available evidence proves that the current detector's ROI, thresholds, byte order, or normalization caused the negative. Do not broaden the detector or introduce a blind fallback. Add scalar diagnostics to the exact existing observation. This is not a fix for the live false negative and does not establish that the initial screen was HOME.

A plausible composition issue remains: `LifecycleSupervisor.start` waits for window binding and render health, not for positive game HOME. `run_native_mvp_visit` makes one immediate HOME observation after start. A healthy loading screen, emulator launcher, popup, or different village can therefore stop the visit. The diagnostics below cannot identify those semantic screens; a later separately authorized observation must inform any further change. No waiting loop, extra capture, startup input, or retry is added here.

## Pinned donor inventory and non-reuse decisions

All referenced donor checkouts were clean at the stated revisions; source and licenses were inspected locally without network access.

| Candidate | Revision and license | Complete relevant seam | Compatibility decision |
| --- | --- | --- | --- |
| BasePilot | `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, MIT | `app/core/bot.py:_home_screen_recovery`, `_find_home_village_builder`, `_update_config_size`; template service and aspect-profile assets; caller recovery includes reward handling, modal dismissal, surrender, return-home, and reconnect | Largest positive template/recovery seam, but not drop-in: requires calibrated template/aspect coverage and exposes input/reward authority before HOME. No direct recovery test was found in its `tests/` directory; upgrade-reading tests are not startup proof. Do not copy the complete recovery executor into this no-input gate. |
| CoC_Bot | `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, MIT | `src/utils.py:get_home_builders`, `to_home_base`, `start_coc`; frame handler, slash template, OCR, location cache, ADB/input startup | The HUD slash score is a separate positive signal but depends on different calibrated ROI/assets. Complete startup performs ADB and blind exit/zoom/navigation input. Not compatible with memory-only native lifecycle and initial no-input gate. |
| ClashAutomation | `c41fe12a6df051e241c695b71b6859286e24c612`, MIT | `utils/clash_base.py:current_location` -> file capture -> `utils/object_detection.py:determine_base_location`; settings-scaled pixel geometry, BGR targets, annotation persistence; `tests/test_detection_robustness.py` missing-image checks | Single calibrated-pixel classification and persisted full captures/annotations are incompatible. Its normalized geometry, channel-order and missing-image lessons already apply locally; not justification for a replacement detector. |
| First-party historical runner | `949497bf0a543a43ec6ef39a8c897e366bc10362`, no third-party source-license claim | `src/clash_rush/m9_live.py:_home_attack_btn_visible` lines 372-380; PIL RGB crop -> OpenCV HSV mask -> mask mean; M9/native composition described in `docs/mvp2-local-donor-manifest.md` | Existing local donor predicate is preserved, not promoted as a new tactical authority. Historical caller uses a different composition and does not prove readiness of this native launch. |

No new third-party code or assets are copied by this slice.

## Full local seam audit

- `NativeLifecycleApi.capture_printwindow_bgra` captures the exact render HWND with PrintWindow flag 2. A negative DIB height makes scanlines top-down; 32-bit BI_RGB provides B,G,R,X. X/alpha is ignored by the color detector. Native capture remains capped at 64 MiB and retains its independently guarded GDI cleanup.
- `LifecycleSupervisor.capture_owned` revalidates owned binding and capture readiness; it rejects changed dimensions or malformed byte length before the gameplay recognizer receives a frame. This slice does not alter those ownership or capture checks.
- The recognizer validates exact tuple, integer geometry, exact bytes and width*height*4 length. It uses the bound render dimensions, not root-window/chrome dimensions.
- HOME ROI stays `(0.035, 0.90, 0.085, 0.97)`, with integer-truncated, half-open bounds. At synthetic 1280x720 this is x=[44,108), y=[648,698): 3200 pixels.
- The existing OpenCV-compatible 8-bit HSV arithmetic is extracted unchanged into `_hsv` for reuse by diagnostics and `_orange`. Predicate remains H=5..30 inclusive, S>=100, V>=120.
- `_fraction` still divides matching pixels by ROI pixels, not channel elements. The HOME decision remains exactly `_fraction(...) > 0.30`. At the synthetic reference geometry, 960 matches reject and 961 accept.
- Diagnostics do not supply the HOME boolean. The existing gate independently evaluates the same immutable frame, with no recapture or optional fallback.
- `run_native_mvp_visit` still consumes state-bound approval before lifecycle launch, checks HOME before constructing input or exporting account state, and retains exact account/army gates and one bounded attack. Its existing finally still attempts owned stop and lease release. The external wrapper still owns STOPPED/read-back and approval cleanup.

## Diagnostic output contract

`BgraGameplayRecognizer.home_diagnostic` is None before observation and after a failed validation. Successful reduction stores one frozen scalar snapshot; each `recognize` call clears any prior snapshot before validation.

A valid negative initial HOME observation raises `RuntimeSafetyError` with the unchanged `HOME_NOT_VERIFIED` prefix followed by canonical compact JSON:

- `schema`: fixed integer 1.
- `reason`: one of `ROI_EMPTY`, `COLOR_ABSENT`, `FRACTION_LOW`, `HOME_POSITIVE`.
- `width`, `height`: validated render dimensions.
- `left`, `top`, `right`, `bottom`: half-open detector ROI bounds.
- `roi_pixels`: denominator.
- `hue_pixels`, `saturation_pixels`, `value_pixels`: independent counts satisfying each unchanged component threshold.
- `orange_pixels`: count satisfying the joint predicate; the fraction is exactly this count divided by `roi_pixels`.

Malformed frames instead raise fixed reasons `FRAME_SHAPE_INVALID`, `FRAME_GEOMETRY_MISMATCH`, or `FRAME_BYTES_INVALID`, without echoing malformed values. Native lifecycle failures that occur before recognition keep their existing failure behavior; diagnostics do not bypass them to inspect an unsafe capture.

Only already validated bounded integer geometry/counts and fixed reason/schema values leave the reduction. No pixel samples, frame hashes, image paths, account references, tags, native handles, window titles, or OCR enter the report. The reducer and recognizer clear frame locals in finally; rejected frames are removed before raising. No file or network sink is added. The current external wrapper lets the sanitized HOME exception reach its existing error output after cleanup. The general CLI retains its generic error output; no broad exception-printing change is introduced.

These numbers distinguish malformed geometry/bytes, empty ROI, lack of eligible hue/saturation/value, and insufficient joint fraction. They do not establish which UI screen was shown, whether the ROI is correctly calibrated for a changed UI, or whether a threshold should be changed. Component counts individually never authorize HOME.

## Static verification and next gate

Public synthetic coverage in `tests/test_home_diagnostics.py` exercises strict fraction boundaries, exact denominator, component/joint masks, BGRA red/blue reversal, ignored alpha, top-down ROI location, multiple bound geometries, malformed shape/geometry/bytes, stale-report removal, transient-local cleanup, and the real native composition through injected ports. Native negative-path tests prove one capture, no input construction, and owned stop/release; positive-path tests reach an inert input-constructor sentinel only after HOME.

Existing runtime, lifecycle, approval, world-export, and external-wrapper tests remain required. Exact-tree export, per-blob verification, full static suite, and privacy scan precede the local candidate commit. The dependent independent review must inspect the immutable candidate and external wrapper seal before any separately authorized run. No live conclusion or retry authority follows from these synthetic tests.
