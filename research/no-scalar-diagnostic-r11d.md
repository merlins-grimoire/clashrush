# R11D no-scalar diagnostic investigation

Status: static investigation only. Candidate: commit `d64b0fe0d6d18ab338afa7e9dfc0a8ff3749eda2`, tree `455300713a193c7de3c1d818ebcf9e551b4a10c7`. No second live diagnostic is authorized.

## Finding

The exact runtime failure cannot be recovered from the retained evidence. The public `diagnose-home` boundary intentionally converts every timeout, child exception, unexpected child status/output pair, and nonempty child stderr into the same outer result: exit 1 with empty stdout (`src/clash_rush_rebuild/cli.py:454-471`). It neither forwards nor persists the child's return code, stdout, or stderr when validation fails. The run retained only that outer exit and the absence of a forwarded scalar.

One cause can be excluded: the 120-second parent timeout did not fire. The Kanban execution log records the approval command plus the one diagnostic command returning in 18.9 seconds. All other listed causes remain observationally merged.

Confidence: 0.97 that the exact cause was destroyed at the parent subprocess boundary; 0.74 that the failure occurred after launch and during capture/recognition, because approval was consumed and final lifecycle state was canonical `READY`, but the evidence does not prove that ordering and does not justify a stronger claim.

## Timeline (EDT)

- 08:05:46: run 43 began on the sealed candidate.
- Before execution: exact commit/tree, clean worktree, canonical `READY(slot 3)`, `STOPPED` local control, absent transition guard, exact five-slot registry, and zero player processes/windows were revalidated. One exact-tree launch approval was issued.
- One invocation ran: `uv run --frozen clash-rush-rebuild diagnose-home --project-root . --slots private/slots.json --timeout-seconds 120`.
- The invocation returned exit 1 with no forwarded scalar. No retry, relaunch, or recapture occurred.
- Immediate postcheck: approval consumed; canonical `READY(slot 3)`; guard absent; local control `STOPPED`; three consecutive complete player/window observations were zero/zero; no newly timestamped image file was found.
- 08:10:51: run 43 completed.
- 08:14:27: independent postcheck classified `DIAGNOSTIC_FAILED_NO_SCALAR` and declined cleanup/privacy PASS because the retained contract did not identify the failure stage or provide per-run retirement evidence.

## Donor and caller trace

The selected production seam remains BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (tree `0553306bfd30a32e31e427a0b77ff22c41f55901`, MIT):

- `app/services/window.py:68-85,295-350`: `WindowService.screenshot()` returns an in-memory frame; donor production callers are the screenshot calls throughout `app/core/bot.py`, including `_detect_village_type` at `bot.py:1570-1584`.
- `app/services/vision.py:130-164,184-226`: aspect scaling, top-half geometry, template matching, and `(x, y)/(None, None)` result shape.
- `app/core/bot.py:223-225,242-248,1570-1584`: update geometry, search `builder.png`/`gbuilder.png`, search `mbuilder.png` first, and classify Builder before Home. Its production caller is `_ensure_correct_village` at `bot.py:1603-1619`; inputful recovery callers remain excluded.
- Local donor-preserving adapters are `basepilot_window.py:39-89`, `basepilot_vision.py:125-240`, and `no_input_home_diagnostic.py:30-120`. `cli.py:150-155` composes their unchanged `screenshot()`/`observe()` interfaces inside `NoInputDiagnosticCycle`.

ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` (tree `b549901e7ca8b871a17265517d730f0b3c84a618`, MIT) is supporting differential evidence, not the runtime spine:

- `utils/game_window_controller.py:200-272` exposes capture failures through logging/`None`, but always creates directories and writes a bitmap, with a restore/foreground and flag-0 fallback. Those behaviors remain incompatible with the memory-only diagnostic.
- `utils/clash_base.py:93-109` calls that capture and `determine_base_location`, then returns `Builder`, `Home`, or `Unknown`.
- `utils/object_detection.py:43-96` returns two booleans and logs missing/out-of-bounds evidence, but uses one configured pixel and writes an annotated image. Its tracked real Home fixture and missing-image assertions remain appropriate differential tests; its persisted diagnostic path is not suitable for production reuse.

No donor change or replacement helper framework is warranted. The defect is in the local process/evidence adapter after the donor seam.

## Classified hypotheses

| Hypothesis | Classification | Evidence |
|---|---|---|
| Parent timeout | Excluded | The combined execution returned in 18.9 seconds, far below the configured 120-second timeout. |
| Launch/admission failure before durable `ACTIVE` | Possible | Approval was consumed, but a failure between consumption and durable launch can leave `READY`; raw child stderr was discarded. |
| Lifecycle launch failure after durable `ACTIVE` | Unlikely, not excluded | Reviewed rollback normally leaves blocked `ACTIVE`, while postcheck found canonical `READY`. A failure before `ACTIVE`, or an unretained exceptional path, remains possible. |
| Capture failure | Plausible | `WindowService.screenshot()` converts all capture/conversion exceptions to one pixel-safe `BasePilotCaptureError`; `NoInputHomeDiagnosticController.observe()` then converts every exception to generic `BasePilotRecognitionError`. |
| Recognition/template/geometry failure | Plausible | Recognition errors are likewise collapsed to the same generic observation error; no stage token survives. |
| Valid `BUILDER`/`UNKNOWN` scalar contaminated by stderr | Possible | The parent rejects any nonempty stderr even when `(returncode, stdout)` is otherwise valid, and discards both streams. |
| Malformed/extra scalar or unexpected child return code | Possible | The parent accepts only three exact pairs and silently maps every other pair to exit 1. |
| Cleanup failure | Unlikely, not excluded | `NoInputDiagnosticCycle` always calls `stop` after a returned binding and a stop failure normally prevents `READY`; mutex-release failure can occur after `READY` and suppress all scalar output. |
| Successful observation followed by mutex-release failure | Possible | `cycle.py:200-205` raises after setting the child disabled; final lifecycle can already be `READY`. |

## Retrospective evidence boundary

The retained evidence supports:

- exactly one outer invocation and no retry;
- no 120-second timeout;
- consumed exact-tree approval;
- final canonical `READY(slot 3)`, absent transition guard, and `STOPPED` local control;
- repeated post-run absence of player processes and configured windows;
- no image file with a recognized image extension newly timestamped beneath the checked private runtime roots;
- unchanged clean candidate tree.

The code plus normal return of synchronous `subprocess.run` imply that, if the child was successfully spawned, it had exited and its capture pipes had reached EOF before the outer command returned. This is useful design evidence, but it is not a retained per-run handle/Job assertion.

The retained evidence does not support:

- the nested child's raw return code, stdout, stderr, exception type, traceback, or last completed stage;
- whether a process was created, a window bound, a frame captured, or recognition began;
- whether a valid scalar existed but was rejected solely because stderr was nonempty;
- a per-run assertion that the nested child handle was retired, or a separately recorded Job-empty fact for that helper process;
- proof that no bytes with unrecognized extensions or no transient pixel-bearing object ever existed. The image suffix/time scan is supporting evidence only.

The information was destroyed in two places: `NoInputHomeDiagnosticController.observe()` erases capture-versus-recognition exception identity, and `diagnose-home` erases invalid child status/stdout/stderr details. Post-run state cannot reconstruct either loss.

## Smallest donor-preserving contract change

Keep the BasePilot `WindowService.screenshot()`, `VisionService.find_template()`, controller decision order, `HomeDiagnosticResult`, whole-command child process, and existing lifecycle Job/kill-on-close ownership. Add only a closed, sanitized failure taxonomy at the local adapters:

1. Preserve `BasePilotCaptureError` versus `BasePilotRecognitionError` through `NoInputHomeDiagnosticController.observe()`; do not include exception text, frame data, paths, window titles, or identifiers.
2. In `NoInputDiagnosticCycle`, wrap only the local lifecycle stages with an exact enum: `LAUNCH`, `CAPTURE`, `RECOGNITION`, `CLEANUP`. A `stop` or mutex-release failure must override an earlier observation failure because cleanup uncertainty is the decisive result.
3. In `diagnose-home-child`, retain stdout as exactly `HOME|BUILDER|UNKNOWN` on scalar outcomes. On failure, emit exactly one allowlisted token to stderr and return a documented nonzero child status. No traceback or raw exception text may cross the boundary.
4. In `diagnose-home`, classify `TimeoutExpired` as `TIMEOUT`; a recognized child failure token as its stage; any other nonempty stderr as `UNEXPECTED_STDERR`; and every invalid status/stdout pair as `SCALAR_PARSE`. Never echo raw stderr. Public stdout remains empty on every failure.
5. Have the invoking run harness retain only the sanitized classification, outer/child status, elapsed duration, and a boolean that synchronous child wait completed. Continue using `subprocess.run` and lifecycle kill-on-close semantics; do not add a daemon, pipe protocol, shared memory, second Job architecture, or custom IPC.

This distinguishes timeout, launch, capture, recognition, stderr, cleanup, and scalar-parse outcomes while changing no donor algorithm or donor-facing interface.

## Bounded static implementation and test proposal

One future implementation slice, with no live action, should be limited to the local evidence adapters and their tests:

- Production: `no_input_home_diagnostic.py`, `cycle.py`, and `cli.py` only.
- Tests: extend `test_no_input_home_diagnostic.py`, `test_cycle.py`, and `test_cli.py` only.
- RED matrix: inject one failure at approval/prelaunch, lifecycle start, capture, geometry/template recognition, lifecycle stop, mutex release, timeout, valid scalar plus stderr, unknown stderr, extra stdout, malformed scalar, and mismatched return code.
- Assertions: exact classification; stdout remains closed to the three scalar values; stderr is one allowlisted token or empty; raw exception/private text never appears; stop runs after every returned binding; cleanup overrides observation; timeout returns only after child termination; no input/capture persistence/network port is introduced.
- GREEN verification: focused tests, canonical `pytest tests/`, exact exported-tree tests, static prohibited-surface/privacy scans, and independent exact-tree review.
- Promotion boundary: static PASS authorizes no BlueStacks launch. Any later diagnostic requires a new exact-tree, one-shot owner approval and a separate cleanup/outcome gate.

Existing focused evidence was rerun during this investigation: the five CLI cases covering timeout, partial output, unexpected stderr, and invalid status/output combinations pass. They confirm the collapse behavior; they do not recover run 43's lost child evidence.
