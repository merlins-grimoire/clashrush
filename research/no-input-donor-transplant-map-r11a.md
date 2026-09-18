# R11A no-input donor transplant map

Status: frozen implementation map only. Base tree: `43a878f09129f6a70c7f1fa7d0a518c70af21a66`.

## Decision

Use BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (tree `0553306bfd30a32e31e427a0b77ff22c41f55901`, MIT) as the ONE primary operational spine. Copy its memory-returning capture/controller shape and complete Home-vs-Builder template-recognition seam. The only secondary donor material is ClashAutomation's tracked real Home frame for differential tests. Named-instance launch, exact HWND/process binding, and stop/absence proof remain the already-reviewed local lifecycle; no donor has a compatible ownership boundary.

Confidence: 0.90. This is the shortest donor-led seam because BasePilot already composes an in-memory screenshot with positive village templates, while the other donors either persist frames or use ADB.

The installed diagnostic has one flow (the parent bounds the entire child process; the child owns the lifecycle Job):

`diagnose-home parent --timeout -> diagnose-home-child -> NoInputDiagnosticCycle.visit_once() [mutex -> registry/state/absence -> exact one-shot launch approval -> LifecycleSupervisor.start(slot) -> BasePilot-shaped WindowService.screenshot() -> BasePilot-shaped _detect_village_type(frame) -> HOME|BUILDER|UNKNOWN -> LifecycleSupervisor.stop(binding, None) -> mutex release]`

It emits exactly one scalar token. Only `HOME` is a successful Home result. `BUILDER` and `UNKNOWN` are nonzero, fail-closed results. The composition exposes no input, gameplay, spending, network, Discord, credential, or screenshot-write port.

## Exact transplant inventory

### Primary spine: BasePilot

Copy these production elements together; do not substitute a new recognizer or controller abstraction.

| Source at pinned commit | Exact element | Donor production callers inventoried | R11B disposition |
|---|---|---|---|
| `app/services/window.py:68-85` | `WindowService` construction contract | Bot receives `window` and calls `window.screenshot()` throughout `app/core/bot.py` | Preserve class name and `screenshot()` interface; constructor accepts the local exact binding plus owned-capture callback instead of discovering a window. |
| `app/services/window.py:295-350` | `WindowService.screenshot` | Direct callers in `app/core/bot.py`: lines 315, 341, 428, 436, 484, 570, 582, 600, 628, 687, 720, 747, 794, 826, 902, 938, 951, 976, 1005, 1064, 1100, 1139, 1254, 1265, 1339, 1388, 1406, 1419, 1432, 1529, 1540, 1573, 1746, 1776, 1794, 1848, 1861, 1890 | Copy the complete memory-returning screenshot seam, then apply only the lifecycle/privacy adapter below. The diagnostic invokes it once per recognition attempt. |
| `app/services/vision.py:130-148` | `_template_scale_xy`, `_resize_template_for_screen` | `find_template` | Copy unchanged except inject the selected aspect/template root rather than global UI configuration. |
| `app/services/vision.py:152-164` | `bottom_half_region`, `top_half_region` | Bot recognition and action callers | Copy both helpers as one geometry seam; the diagnostic uses `top_half_region`. |
| `app/services/vision.py:184-226` | `VisionService.find_template` | All BasePilot template callers; selected Home seam callers are below | Copy matching, scaling, ROI offsets, threshold and `(x, y)/(None, None)` contract. Remove exception swallowing only to make malformed production evidence fail closed. |
| `app/config.py:7-27` | `ASPECT_16_10`, `ASPECT_16_9`, `_ASPECT_TOLERANCE`, `ASPECT_BASELINE`, `resolve_aspect_key` | `Config.set_aspect_for_screen_size`; selected diagnostic configuration | Copy these pure constants/resolver. Preserve authored baselines `2560x1440` and `2560x1600` and the donor's nearest-aspect/tolerance behavior. Do not copy the singleton, disk JSON, UI probing, or logging. |
| `app/config.py:159-184,218-232` | `_apply_aspect`, `set_aspect_for_screen_size`, `set_target_size`, `set_target_size_from_frame` behavior | `Bot._update_config_size`; template scaling/path selection | Preserve this dependency as immutable per-frame `BasePilotGeometry.from_frame(frame)`: exact frame shape selects one supported aspect and its baseline once. Unsupported geometry raises the typed recognition error; there is no mutable singleton or `data.json`. |
| `app/utils/common.py:79-86` | `get_template_path` behavior | `VisionService.find_template` | Preserve `templates/<aspect>/<name>` selection, but resolve through `importlib.resources` under the installed package and a closed three-name allowlist. No donor checkout/CWD/user-data fallback. |
| `app/core/bot.py:18` | `_HOME_VILLAGE_BUILDER_TEMPLATES = ('builder.png', 'gbuilder.png')` | `_find_home_village_builder` | Copy exactly. |
| `app/core/bot.py:223-225` | `_update_config_size` | `_detect_village_type` line 1576 | Copy the call position and responsibility. Adapt it only to bind the immutable `BasePilotGeometry` used by both scaling and template lookup for this frame. |
| `app/core/bot.py:242-248` | `_find_home_village_builder` | `_maybe_upgrade_walls` line 638; `_home_screen_recovery` line 1476; `_wake_home_and_wait_for_attack` line 1558; `_detect_village_type` line 1581 | Copy exactly for the selected read-only caller. The three input/action callers are inventoried but excluded because importing them would create forbidden authority. |
| `app/core/bot.py:1570-1584` | `_detect_village_type` | `_ensure_correct_village` line 1609 | Copy its complete read-only decision sequence. Replace the inputful caller with the diagnostic composition root, not a second decision algorithm. |
| `templates/16_9/{builder,gbuilder,mbuilder}.png` and `templates/16_10/{builder,gbuilder,mbuilder}.png` | Positive Home and Builder production templates | Above template calls | Copy all six assets with their relative aspect layout. No generated stand-ins. |

Asset seals, in the order above:

- 16:9: `builder` `c48653fc4b0dd5f504d41903c4e12b2a31de96f3c1e8da0ba32e9eee9ae7a8b3`; `gbuilder` `c696f96848e1be2d49710b625d5a77a7c0a3e40db91d25eb9b8be96340861094`; `mbuilder` `f3317664be20289f8ac8d03feec05a81f7c6b0d04d8a8cbd16604e8025bb9b2d`.
- 16:10: `builder` `e37551f7c08fde1781ff977adcb0b7cb4041a913a511fc798cbae0cc633fa914`; `gbuilder` `28a1a648464275e50704275b924904b3e058aa28dc6e9cbde1d57c1e5cd68412`; `mbuilder` `aafb4af0a51081ba324bc3901a299524fd5020ed8dae60352c72113fd06cefd4`.

### Secondary evidence: ClashAutomation

Copy `tests/fixtures/home_screen.png` from `c41fe12a6df051e241c695b71b6859286e24c612` (tree `b549901e7ca8b871a17265517d730f0b3c84a618`, MIT), size 4,060,294 bytes, SHA-256 `279acc36eaf56c298c668ecb110d5001628c2ec5607f0f57604d8460bdb42611`. It is a tracked real 1728x1080 Home capture, not a generated stand-in.

Copy/adapt the fail-closed assertions from `tests/test_detection_robustness.py:31-35,61-73` and the tracked-fixture pattern from `tests/test_vision_and_config.py:93-115,152-167`. Add a differential assertion that the copied BasePilot recognizer returns `HOME` on this real donor frame. BasePilot has no tracked test for `_detect_village_type`; do not invent a donor test count.

Synthetic arrays may test malformed frames, wrong aspect, missing templates, ambiguous matches, and exception cleanup. They cannot satisfy the production exit gate. Production promotion requires the installed composition to classify a fresh lifecycle-owned real BlueStacks frame as `HOME` and then prove stop/absence; a donor fixture, mock, replay, or synthetic frame is never that evidence.

## Existing local lifecycle boundary (adapter only)

Reuse, do not edit or bypass:

- `src/clash_rush_rebuild/lifecycle.py:235-370` - `LifecycleSupervisor.start`, exact slot authorization, owned Job, process identity, exact window binding and readiness.
- `src/clash_rush_rebuild/lifecycle.py:372-393` - `capture_owned`, binding revalidation and exact BGRA size/type.
- `src/clash_rush_rebuild/lifecycle.py:406-427` plus the remainder of `stop` - authoritative Job termination and stop proof.
- `src/clash_rush_rebuild/win32_lifecycle_host.py:326-424` - bounded `PrintWindow(..., 2)` BGRA capture and independently guarded GDI cleanup.
- `src/clash_rush_rebuild/win32_lifecycle_host.py:727-765` - identity/HWND/geometry/desktop revalidation and capture readiness.

The mutex-owning production adapter is `NoInputDiagnosticCycle` in `src/clash_rush_rebuild/cycle.py`, composed by `build_no_input_diagnostic_cycle` in `src/clash_rush_rebuild/cli.py`. It is a minimal specialization of `InertCycle.visit_once` (`cycle.py:81-117`) and `build_inert_cycle` (`cli.py:63-108`), not a competing lifecycle framework. Copy that method's exact ordering and refusal paths: acquire the real `Win32Runtime` protected mutex before registry discovery; on abandonment perform only the existing read-only state/player observation and refuse; require the exact ordered five-slot registry; load canonical state including transition-guard validation; require exact `Ready`; prove zero pre-existing players; and select only `slots[state.next_slot]`.

Still under that same lease and before process creation, load canonical state bytes and call `OneShotApprovalService.validate_then_consume(ApprovalAction.LAUNCH_WINDOW_BINDING_DIAGNOSTIC, _candidate_tree(project), state, state_bytes)` using `PrivateApprovalStorage` (`approval_reconciliation.py:48-50,528-641`; `cli.py:111-141`). A CLI boolean or an `AcquiredMutexLease` object is not authorization and cannot replace acquisition/validation. Any mismatch before durable consumption creates no process; every failure after consumption leaves it consumed. Then create the existing supervisor exactly as `build_inert_cycle` does, call sealed `start`, run only the read-only observation hook, and call sealed `stop(binding, None)` in `finally` whenever `start` returned. Recognition failure cannot skip stop. Stop failure, unproved `READY`, or lease-release failure emits no success; release failure permanently disables the process exactly as `InertCycle` does. `HOME` is printed only after stop/absence proof and successful mutex release. Tests transplant/extend `tests/test_cycle.py:114-181,184-253` and `tests/test_cli.py:41-73,110-146` to assert this full event order, abandonment/ACTIVE/guard/overlap/approval refusal, stop-on-observation-error, and release-failure suppression.

CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` is MIT and already supplied the named-instance concepts now attributed in `THIRD_PARTY_NOTICES.md`. Its `src/utils.py:847-976` is not copied again: `BlueStacks_Manager.start` uses detached `subprocess.Popen`, PID/cache state, fuzzy executable search and snapshot polling, which conflict with the retained-handle suspended launch, private Job assignment, and exact stop proof above. This concrete ownership incompatibility mandates the local reviewed adapter; it does not authorize reinvention.

## Line-by-line adaptation boundary

Only these changes to the copied primary seam are allowed:

1. `window.py:72-85`: retain `WindowService` and its capture responsibility; replace title/class discovery with constructor arguments `(binding, capture_owned)`. Reject non-exact `PlayerBinding` and non-callable capture. Do not copy `_resolve_hwnd`, enumeration, selection stores, or fallback discovery.
2. `window.py:295-298`: retain `screenshot()` with no path argument; remove rediscovery. Revalidate only by calling the supplied `LifecycleSupervisor.capture_owned(binding)`.
3. `window.py:300-347`: replace raw Win32 allocation with that returned `(width, height, bgra_bytes)`; preserve the donor's BGRA-to-BGR ndarray result using `numpy.frombuffer(...).reshape(...)` plus `cv2.cvtColor(..., COLOR_BGRA2BGR)`. Require exact dimensions and byte length. Pillow is not retained. Clear BGRA bytes and arrays in `finally`; never call PIL save, OpenCV write, logging with pixels, or any debug-output helper.
4. `window.py:348-352`: replace catch-and-return-None with a bounded local recognition error after clearing frame-bearing locals. Never preserve frame bytes in exception text, cause, context, traceback locals, audit, or console.
5. `config.py:7-27,159-184,218-232` and `bot.py:223-225`: copy the pure aspect constants/resolver and preserve `_update_config_size` at the start of `_detect_village_type`. Replace the mutable `Config` singleton with immutable `BasePilotGeometry(aspect_key, ref_width, ref_height, width, height)` constructed once from the exact frame; reject non-exact ndarray shape, unsupported aspect, nonpositive size, or disagreement with the lifecycle binding. Never default to `16_10`, reload `data.json`, or probe a UI window.
6. `common.py:79-86`: replace global `Config()` and `get_resource_path` with `importlib.resources.files("clash_rush_rebuild") / "assets" / "basepilot_templates" / aspect_key / template_name`; accept exactly `builder.png`, `gbuilder.png`, or `mbuilder.png`, require a regular packaged resource, and do not search the CWD, donor checkout, user directories, or network.
7. `vision.py:130-148`: preserve donor scaling against the immutable geometry's exact authored baseline. Reject unsupported aspect or out-of-bounds template geometry.
8. `vision.py:152-164`: copy unchanged.
9. `vision.py:184-220`: preserve `TM_CCOEFF_NORMED`, scaled template, ROI handling, offsets, threshold and center calculation. Resolve only packaged copied assets. Do not add OCR, fallback coordinates, broad filesystem search, or downloaded assets.
10. `vision.py:221-226`: replace broad exception swallowing with fail-closed typed error; no image/debug persistence.
11. `bot.py:18,223-225,242-248,1570-1584`: preserve template order, geometry update position, and Builder-before-Home decision order. Host these methods in a donor-derived read-only diagnostic controller with no input member. Convert only final donor strings to the closed scalar `HOME|BUILDER|UNKNOWN`.
12. `cycle.py:81-117`: add `NoInputDiagnosticCycle` by preserving the existing mutex/registry/state/absence/supervisor sequence verbatim and inserting only exact launch-approval consumption before `start` and a read-only `observe(supervisor, binding)` between sealed `start`/`stop`. Always stop in `finally`; defer scalar publication until lease release succeeds. No observer callback may run before authorization or after stop.
13. `cli.py:63-108,111-141,215-281,284-371`: add `build_no_input_diagnostic_cycle`, private `diagnose-home-child`, and public `diagnose-home`. The child is the sole lifecycle composition and emits only the closed scalar after the cycle returns. The public command starts that installed child with `subprocess.run(..., timeout=<bounded constant>, check=False, capture_output=True, text=True)`, accepts only one exact scalar and expected status, and terminates/kills the child on timeout. No custom messages, pipe protocol, daemon, shared memory, or second lifecycle supervisor.
14. Timeout: bound the whole installed diagnostic child. Terminating it closes its private kill-on-close Job. A killed/uncertain run remains fail-closed `ACTIVE` for the separately reviewed reconciliation path; the parent never repairs state or prints `HOME` from partial stdout.

Destination is fixed: `basepilot_window.py` (donor `WindowService` shape), `basepilot_vision.py` (geometry, resolver, `VisionService` subset), `no_input_home_diagnostic.py` (donor-derived read-only controller and closed result), the two named adapters in existing `cycle.py`/`cli.py`, and `src/clash_rush_rebuild/assets/basepilot_templates/{16_9,16_10}/` for the six PNGs. No other local production code is in scope. In particular, do not transplant BasePilot input, popup recovery, account switching, UI configuration, random waits, debug persistence, or action orchestration.

## Installed-package and dependency boundary

- Add runtime dependencies `numpy>=2.2` and `opencv-python>=4.12`, the exact retained BasePilot subset. Do not add/import Pillow, PySide6, pytesseract, pywin32, donor logger, mutable `Config`, `ensure_dir`, input, OCR, or UI modules. Existing native Win32 bindings remain local `ctypes` code.
- Add setuptools package data for `assets/basepilot_templates/16_9/*.png` and `assets/basepilot_templates/16_10/*.png`. Source-tree-relative and donor-checkout paths are forbidden.
- Copy the six templates and ClashAutomation fixture to `tests/fixtures/donor/`; the production classifier must never resolve the test fixture. Copy the selected assertions into `tests/test_no_input_home_diagnostic.py`; extend `tests/test_cycle.py` and `tests/test_cli.py` for composition.
- Build/install the wheel into a clean temporary environment outside the source tree. With the source checkout and donor checkouts excluded from `PYTHONPATH` and the working directory elsewhere, assert `importlib.resources` finds all six packaged assets with the recorded hashes, classify the copied real Home fixture through the installed production modules, and run the installed CLI with no BlueStacks to prove fail-closed nonzero/no scalar success. A source-tree-only pass is not acceptance.

## Rejected alternatives

- ClashAutomation as spine: `utils/game_window_controller.py:200-272` always creates directories and writes a bitmap, falls back to restore/foreground plus flag-0 capture, and `utils/clash_base.py:93-109` writes every location frame. Its `determine_base_location` at `utils/object_detection.py:43-96` is a single pixel and writes an annotation. Adapting this would replace more of the complete seam than BasePilot and violate memory-only privacy.
- CoC_Bot as spine: capture/input and app state are ADB/minitouch based, and the repository has no automated test tree. Its detached launch and ADB stop cannot prove local Job ownership or absence.
- BasePilot whole `Bot`: it is input-capable and its recovery callers click. Only its complete read-only capture/template/village sub-seam is selected; importing the orchestrator would violate explicit no-input authority.

These are incompatible transport, privacy, ownership, or authority boundaries. They require the selected licensed seam plus narrow adapters, not a greenfield replacement.

## License and executable R11B gate

- Copy BasePilot's full `LICENSE` text into `THIRD_PARTY_NOTICES.md`, naming the pinned commit, copied functions and six assets. CRLF working-copy SHA-256: `fca1e84e519fb317c8b47e823bddb7f438b466d875c4904c9fa33f7570ce7613`.
- Preserve ClashAutomation's full MIT text for the copied fixture and test adaptation. CRLF working-copy SHA-256: `9221d77ee228d838307ca40da8b59c5ccd090b4ef8405d1cbe439d3399c4e949`.
- Retain the existing CoC_Bot notice. CRLF working-copy SHA-256: `2942402036365ceb18f26e1c7b4d9be1ae080b2cdd95264f3370f350e2c89726`.

R11B order is mandatory: copy this full seam/assets/tests and notices first; write RED compatibility/differential tests; make only the adaptations numbered above; run focused tests, canonical `pytest tests/`, exact-export tests, an installed CLI no-BlueStacks fail-closed probe, provenance/privacy scans, and independent exact-tree review. No live BlueStacks action is authorized by this map.