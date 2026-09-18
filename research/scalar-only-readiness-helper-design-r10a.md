# R10A design: scalar-only private readiness helper

Status: DESIGN ONLY; pending independent same-card review. This document does
not implement or launch a helper, capture pixels, admit a catalog, authorize
BlueStacks, or add a production caller.

## 1. Frozen decision and boundary

Use one architecture only: a short-lived Windows helper process privately owns
one `PrintWindow(PW_RENDERFULLCONTENT)` frame, performs health reduction and the
complete R10 three-anchor/15-constellation reduction inside that process, clears
its service-owned pixel aliases, and writes exactly one fixed 24-byte closed
scalar record to a one-shot bounded named-pipe channel. Full-frame bytes never
cross the process boundary. The parent never maps, receives, hashes, logs, or
otherwise accesses pixels.

The parent creates the helper suspended with handle inheritance disabled,
immediately records ownership of both returned process/thread handles, assigns
the process to a new private kill-on-close Job, prepares the fixed request and
duplicated target proof handles, and resumes only after those steps succeed.
The parent accepts a helper scalar only after exact record/EOF validation, exact
zero process exit, helper Job active-process count zero, exact target binding
postvalidation under the still-held lifecycle lease, and successful closure of
both IPC endpoints and all helper handles. Any ambiguity returns the existing
public `UNKNOWN/BOUND_UNAVAILABLE` scalar. Forced retirement can establish safe
cleanup but can never rehabilitate evidence or make a helper record acceptable.

This replaces, rather than wraps or repairs, the pixel-returning candidate at
commit `550fa136442c52794b29264513bd4dffc8e91883`, tree
`c7317e22ab36b050cb15fabe50f68ff67d77a4ab`. None of that candidate's shared
frame transport, parent reducer, helper controller, certification token, or
mutable runtime composition is reused. Its catalog/reducer arithmetic and
public scalar vocabulary remain specification evidence only and must be
reimplemented behind this process boundary from the accepted R10 contract.

No alternate transport, fallback capture, retry, polling loop, foreground
restore, parent-side matcher, or second architecture is permitted.

## 2. Exact donor evidence, license, attribution, and exclusions

The following immutable local Git objects were inspected. Each root `LICENSE`
is MIT. No donor code or assets are copied by this design-only change.

| Donor pin | Exact evidence used | Allowed lesson | Rejected behavior |
| --- | --- | --- | --- |
| BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, tree `0553306bfd30a32e31e427a0b77ff22c41f55901`, Copyright (c) 2026 Efe Bolukbasi | `app/services/vision.py:185-220` validates region bounds and template/search dimensions before `cv2.matchTemplate(..., TM_CCOEFF_NORMED)` and returns a region-adjusted center; `:233-267` exposes the confidence variant. `app/core/bot.py:242-247`, `:1547-1563`, and `:1575-1581` show distinct Home/Builder topology evidence in callers. | Behavioral window/topology evidence and the normalized centered-correlation primitive. An implementation adapting code must reproduce the full MIT notice in `THIRD_PARTY_NOTICES.md`, name this commit/path, and describe local changes. | File image loading, mutable resize/aspect selection, first-hit authority, broad exception swallowing, logs, rewards, recovery, sleeps, clicks, and any game artwork. Source MIT does not establish artwork redistribution rights. |
| CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, tree `d79368fbe550036f1542883f18434e318016b279`, Copyright (c) 2026 m24842 | `src/utils.py:496-520` uses a bounded HUD crop and slash template; `:522-559` combines it with startup; `:1572-1665` shows normalized crop geometry plus cached-frame and matcher behavior. | Transport-neutral lesson that Home and Builder require distinct bounded geometry/evidence, and that template dimensions must be checked against the frame. | ADB startup/framebuffer/screenshot transport, `TEMP_CACHE`/`cached_frame`, frame copies, debug writes, OCR, timeout loops, sleeps, input, broad exception handling, and location authority. No code is transplanted. |
| ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612`, tree `b549901e7ca8b871a17265517d730f0b3c84a618`, Copyright (c) 2026 Caleb Welsh | `utils/game_window_controller.py:225-237` chooses the exact render child when present and calls `PrintWindow(target_hwnd, ..., 2)`; `utils/object_detection.py:43-57` fails closed on missing image/out-of-range geometry and confirms OpenCV BGR ordering. | MIT donor evidence only for exact render HWND selection, `PrintWindow` flag 2, BGR order, and bounds checks. An implementation adapting code must include the full MIT notice, exact commit/path, and local changes. | Screenshot files, bitmap save/readback, mutable title-only selection, parent-window fallback, restore/foreground, sleep, flag-0 retry, one-pixel classification, annotations, and configured coordinates. |

BasePilot supplies the largest compatible recognition primitive, but no donor
supplies the required helper ownership, bounded retirement, sealed Win32
composition, or scalar-only process protocol. Those governance and lifecycle
pieces are local. The helper bundle has no admitted production catalog until a
separate asset-rights/privacy/calibration review installs an exact closed
catalog. Absence of that catalog is `CATALOG_UNAVAILABLE` before helper launch.

## 3. Closed protocol

### 3.1 Fixed parent request

The parent constructs one exact built-in request record from service-owned
snapshots; callers cannot supply a path, command line, pipe name, timeout, ROI,
threshold, matcher, output sink, or native adapter. The bounded request contains
only fixed-width scalars:

- protocol version and catalog generation enum;
- root/render HWND values;
- root/render PIDs and creation `FILETIME` values;
- render width/height and capture nonce;
- lifecycle lease generation;
- duplicated child-valid handles for the retained root process, retained render
  process, and target private Job;
- one random invocation nonce used only to bind the request to the one-shot pipe.

The one-shot named-pipe name and nonce are generated internally and passed on
the exact helper command line. They are never logged or emitted. The request
has a compile-time size ceiling and exact field offsets; short, long, duplicate,
trailing, subclassed, non-integer, boolean-as-integer, reserved-nonzero, or
second requests fail before capture. There is no variable-length field.

The helper executable/package owns exactly one reviewed catalog table per
allowed generation. It does not accept catalog/template bytes or paths over
IPC, auto-select another generation, or load caller-named assets. Until a
separately reviewed helper bundle contains an admitted catalog, the parent
returns `CATALOG_UNAVAILABLE` without process creation. A future private bundle
may contain the reviewed templates; this design neither creates nor packages
those assets.

### 3.2 Fixed helper response

The only helper-to-parent payload is exactly 24 bytes:

| Offset | Width | Meaning |
| --- | ---: | --- |
| 0 | 4 | ASCII magic `R10S` |
| 4 | 1 | internal protocol version `1` |
| 5 | 1 | public schema `3` |
| 6 | 1 | method enum `1` = `ANCHOR_CONSTELLATION_V1` |
| 7 | 1 | closed classification enum |
| 8 | 1 | closed source enum |
| 9 | 1 | closed layout enum |
| 10 | 1 | observations, exactly `0` or `1` |
| 11 | 1 | closed reason enum |
| 12 | 4 | reserved, all zero |
| 16 | 8 | terminal marker ASCII `R10S-END` |

The enum/cross-field table is exactly the accepted R10 public schema-3 grammar.
There are no floats, scores, coordinates, dimensions, times, identifiers,
handles, nonces, paths, exception data, image data, hash, checksum, digest,
length extension, or free text. Exactly 24 bytes followed by EOF is required;
short, oversized, trailing, duplicate, malformed, or unterminated output is
ignored. The parent serializes accepted enums into canonical schema-3 JSON.
For any parent/helper ownership or proof failure, the parent constructs only the
fixed `UNKNOWN/UNVERIFIED/UNVERIFIED/0/BOUND_UNAVAILABLE` result locally; it does
not trust a helper failure record to prove cleanup.

The helper may write a valid negative semantic record (`NO_MATCH`, `AMBIGUOUS`,
`UNSCORABLE`, `HEALTH_UNVERIFIED`) only after the same pixel teardown and binding
postvalidation required for a positive. It writes at most once. It closes its
pipe before a successful exact-zero exit. No logs, stdout/stderr payload,
filesystem frame, frame digest, crash dump, or alternate channel is permitted.

## 4. Sealed production composition

Production and test composition are deliberately separate.

Production code exposes one module-level entry point whose dispatch graph is a
closed list of module-private functions. It creates the Win32 API table itself;
no caller-supplied runtime, protocol, fake, subclass, callback, clock object, or
identity helper can enter production. Module monkeypatching remains outside the
accepted threat model.

Every reachable production call is either:

1. a direct module-level helper call, or
2. a class-qualified call on an exact service-owned final type, followed only by
   class-qualified calls down to a module-owned immutable Win32 function table.

No production method calls `self.some_helper`, `getattr`, a Protocol method, or
an instance attribute containing a callable. No subclass is accepted. Exact
built-in status values are reduced immediately: Win32 integer statuses require
`type(value) is int` before comparison, so `True`/`False`, integer subclasses,
and equality-spoofing objects cannot prove resume, wait, exit, or Job state.
Win32 booleans require `type(value) is bool`. Exact handle validity is checked
before use. The trusted monotonic source is created internally and invoked by a
module-level/class-qualified path; each exact nonnegative integer sample must be
greater than or equal to the prior sample.

Synthetic tests use a separate fake state-machine model with the same scalar
stage/result vocabulary. A fake cannot satisfy the production capability type
or be passed through the production entry point. Concrete Win32 ABI tests patch
only the module-owned syscall table at the documented test seam and assert the
real production call graph; they do not certify arbitrary injected objects.

This closes the parked candidate's transitive instance-shadow paths in
`win32_runtime.py:487-493,496,505,523,545,549,555,565,568,572,578`,
`win32_lifecycle_host.py:854-883`, and
`win32_primitives.py:266-276` rather than adding another outer token.

## 5. Ownership state machine

States are linear and closed:

`INERT -> TARGET_PREVALIDATED -> IPC_READY -> HELPER_CREATED_SUSPENDED ->`
`HELPER_JOB_ASSIGNED -> REQUEST_READY -> RESUMED -> RECORD_OR_ABORT ->`
`TERMINAL_PROOF -> TARGET_POSTVALIDATED -> ACCEPTED`

Any transition failure goes to `RETIRE`, then `CLOSED` or `UNPROVEN`. Only
`ACCEPTED` returns a helper record. `RETIRE` never returns to the success path.

### S0 INERT and target prevalidation

Under the existing host-wide lifecycle/input mutex and active lifecycle lease,
the parent takes an exact immutable scalar snapshot. Fresh retained-handle proof
must establish root/render process identity, target private-Job membership,
exact HWND existence/visibility/root ancestry, render client geometry, unchanged
capture nonce, unlocked desktop, and non-minimized root. All native statuses are
strictly typed. Failure returns `BOUND_UNAVAILABLE` with no helper creation.

### S1 IPC and private Job preparation

The parent creates one one-shot named pipe with fixed ACL and fixed request/
response limits, then creates a private Job and sets kill-on-close with neither
breakaway flag. No pixel storage is created. Any failure closes each acquired
resource independently and returns `BOUND_UNAVAILABLE`.

### S2 suspended creation and immediate ownership

The exact helper executable is created suspended with `bInheritHandles=False`
and no shell. As soon as `CreateProcessW` returns, the parent records the exact
process handle, thread handle, PID, and creation `FILETIME` in its owned session
before any duplicate, grant, pipe, clock, or cancellation operation. Invalid
records trigger independent process/Job retirement and handle closure attempts.
No second helper can start while a session owns or retains unresolved resources.

The parent assigns the helper to the private kill-on-close Job and proves exact
membership while it is still suspended. It then duplicates only the three
target proof handles into the child, constructs and freezes the fixed request
bytes, and seals the named-pipe server against a second connection. It resumes
only after those preparations. `ResumeThread` must be an exact int with prior
suspend count one. The thread handle is closed independently. The resumed
helper connects once; only then does the parent write the already-frozen request
and reject any second connection. Failure at any point enters `RETIRE`;
assignment is always before resume and no request field can change afterward.

### S3 helper prevalidation, capture, and reduction

The helper parses the exact request into immutable built-in scalars. It verifies
its invocation nonce and independently proves, through the duplicated retained
handles and target Job handle:

- root/render PIDs and creation times;
- both processes' target-Job membership;
- exact root/render HWND ownership, visibility and ancestry;
- exact render geometry and capture nonce binding;
- unlocked/non-minimized desktop and held lease generation.

The helper then performs exactly one lowest-level `PrintWindow` call with the
exact render HWND and flag 2 into helper-private GDI memory. It creates no Python
full-frame `bytes`; bounded views may alias the one native allocation. On that
same allocation it performs health reduction and all 45 correlations, retaining
only bounded scalar accumulators and the provisional closed result. No second
capture, cache, image-sized derivative, screenshot file, hash/digest, logging,
or output occurs.

In a `finally`-owned cleanup sequence, restoration, bitmap/DC deletion and DC
release are attempted independently. Every service-owned native buffer, view,
patch alias, and exception traceback/cause/context/ExceptionGroup reference is
cleared before continuing. This is reference/lifetime containment, not a claim
of secure erasure of immutable Python data, swap, or crash dumps.

After pixels are gone, the helper repeats the complete duplicated-handle,
target-Job, window, geometry, nonce, desktop, and lease proof. It then closes
all duplicated target handles independently. Only after those mandatory closes
succeed may it encode and write the 24-byte scalar. It writes once, closes its
pipe endpoint, and exits exactly zero. A mandatory cleanup failure, changed
binding, native exception, malformed result, or output failure suppresses
acceptance and exits nonzero; it cannot leave a success marker that the parent
may accept.

### S4 parent terminal proof and acceptance

The parent reads at most 25 bytes, requiring exactly the 24-byte record and EOF.
It does not parse into public enums until all terminal proofs succeed. Under one
trusted nonrenewable absolute evidence deadline it requires:

1. helper process wait reports exact `WAIT_OBJECT_0` (exact int, not bool);
2. `GetExitCodeProcess` reports exact integer zero;
3. helper private Job active-process count reports exact integer zero;
4. the pipe has reached EOF and both helper/parent IPC endpoints close;
5. helper thread/process/Job handles close independently;
6. the full target binding proof from S0 passes again under the same lease.

Only then is the record parsed and cross-field validated. If the process needed
forced retirement, exited nonzero/unknown, the Job was nonempty/unknown, IPC did
not close, any handle close failed, the target changed, or the clock became
invalid, the record is discarded and `BOUND_UNAVAILABLE` is returned.

### S5 deadline, cancellation, failure, and retirement

The parent samples one internal monotonic clock at entry and around every
external operation. The evidence deadline remains `t0 + 2_000_000_000 ns`; it
is never renewed. Equality expires. A separate cleanup deadline is frozen once
at retirement entry as the earlier of the standing total terminal bound and
`cleanup_entry + 1_000_000_000 ns`; it is never renewed.

Clock regression/exception, cancellation, deadline, wait/query failure, pipe
failure, or proof failure sets an irreversible `evidence_invalid` bit. After the
parent owns a process, retirement attempts do not depend on another successful
clock/cancellation/proof call:

- attempt `TerminateJobObject` once if a Job exists;
- attempt `TerminateProcess` once if a process handle exists, even if Job
  termination failed or raised;
- attempt nonblocking/bounded process wait, exact exit query, and Job active
  count independently, even if time is invalid;
- attempt pipe cancellation/closure and every thread/process/Job/duplicated
  handle close independently.

A fault in one attempt cannot skip another. The implementation may use only
native operations whose individual boundedness is separately certified; an
uncertified operation makes the production capability unavailable before
launch. Invalid time changes acceptance to `BOUND_UNAVAILABLE` but does not set
all later native timeouts to an invalid sentinel or suppress termination.
Unresolved resources remain attached to a quarantined owned session and keep
the lifecycle lease/fleet blocked; they are never represented as released.
Even if retirement later proves exit and Job empty, the helper record remains
inadmissible because retirement recovery is not clean evidence completion.

## 6. The five final-review defects are design transitions

| Final defect from parked candidate review | Required transition/test in this design |
| --- | --- |
| 1. Transitive instance shadowing fabricated assignment/wait/close, host binding, and identity proof. | Section 4 production entry rejects injected/subclass objects; every S0-S5 native/identity call is module-level or class-qualified through the immutable internal table. Shadow every formerly reachable helper and require no shadow invocation plus `BOUND_UNAVAILABLE`. Maps R08/R09/R12/R14/R16. |
| 2. A frame survived forced retirement without exact zero-exit proof. | S4 requires clean (non-retirement) wait, exact zero exit, Job empty, EOF/close, and postvalidation. S5 permanently invalidates evidence before retirement. Test wait false->true and exit `None`/nonzero/zero after termination; all discard the record. Maps R11/R12/R15. |
| 3. Shared-frame failure left an outstanding parent owner, open IPC, and accessible pixels. | There is no shared frame or parent pixel type. S3's sole native allocation exists only inside the helper and is destroyed before scalar write; killing the Job retires its address space. Parent protocol tests prove no response field/API can carry pixel-sized data and no shared memory/file is created. Maps R08/R10/R12/R13/R15/R16. |
| 4. Clock failure caused retirement calls to be skipped. | S5 makes Job and process termination unconditional independent attempts after ownership; clock invalidity only poisons acceptance. A receive/cancel path that regresses/raises the clock must still record both termination attempts, wait/query, pipe close, and handle closes. Maps R11/R12/R13/R15. |
| 5. `False == WAIT_OBJECT_0` was accepted. | Section 4 and S4 validate exact int before comparison at every native integer boundary. Test bool and int subclasses for wait/resume/exit/active count; none proves success. Maps R08/R11/R12/R14/R15. |

## 7. Transition and R01-R17 verification map

Implementation starts with failing tests for the architecture below. Public
fixtures are procedural and synthetic. Tests may launch only a harmless
synthetic helper specifically built for the test; they may not launch
BlueStacks, call real `PrintWindow`, or use private/game pixels.

| Row | Required proof in the scalar-only architecture |
| --- | --- |
| R01 | Inside-helper reducer tests cover all 15 synthetic screen/layout pairs and exact schema-3 fields; protocol tests encode/decode the corresponding 24-byte records only after S3 cleanup. |
| R02 | Helper reducer covers the complete negative corpus (R9B-equivalent chromatic/dark scene, black/grays/colorful non-HOME, borders, one/two anchors, slash/orange) with only the frozen negative reasons. No frame leaves helper fixtures. |
| R03 | Independent shifts, inconsistent transforms, unsupported translation/rotation, clipping, scale/language/skin all abstain; no relocation or fallback exists. |
| R04 | Independent formula/differential tests cover 0.92/0.08 inclusive boundaries, adjacent representable values, ties, plausible competitors, and all orderings before scalar encoding. |
| R05 | Catalog admission and helper reduction reject per-channel constants, invalid/missing competitors, nonfinite/out-of-range scores, and prove exactly 45 correlations for valid input. |
| R06 | Sentinel/call-graph tests prove no production caller, HOME/account/army/input change, and no orange predicate authority; helper output remains diagnostic only. |
| R07 | Parent rejects missing/unadmitted bundle before launch; helper rejects wrong generation, invalid closed catalog cardinality/geometry/provenance/variance without fallback. Catalog is internally owned, never caller/IPC supplied. |
| R08 | Exact fixed request/record type/length/reserved/cross-field matrix plus hostile scalar/bool/subclass/deleted-field tests. Parent API contains no frame/pixel parameter or return. |
| R09 | S0 and helper S3 pre/post plus parent S4 postvalidation cover retained identities, PID/HWND reuse, target Job membership, ancestry/geometry/nonce, desktop, lease, and mutation at every external boundary. |
| R10 | Instrument the actual helper's lowest capture seam: one flag-2 call, one helper-private native allocation, health plus 45 correlations on it, zero shared mappings/full-frame IPC/files/digests/logs, no parent pixel access, no reuse across invocations. |
| R11 | Exact monotonic boundary/regression/equality tests at every S0-S5 external stage; no new stage after expiry, no renewal/sleep/retry. After ownership, invalid clock still permits and records every mandatory retirement attempt. |
| R12 | Full state-machine matrix: Job/create/set-limit, suspended creation, immediate ownership, assignment-before-resume, request/duplicate/resume/thread-close, pipe short/long/trailing/crash/hang, zero/nonzero/unknown exit, nonempty Job, EOF/close, cancellation, each termination/wait/query/close fault, combined faults, retained unresolved ownership, and harmless concrete synthetic helper. No forced-retirement path accepts evidence. |
| R13 | In-helper fault injection retains original exceptions externally and probes GDI/reducer/encoder/combined cleanup failures, BaseException, cause/context/ExceptionGroup; success is suppressed and service-owned aliases are cleared. Parent never obtains the allocation; process retirement proves no surviving pixel owner. |
| R14 | Shadow every formerly reachable runtime/host/identity helper and every new class method; production dispatch never invokes shadows. Fake model remains type-incompatible with production. Atomic record snapshot cannot be changed by a later property/callback because none is called. |
| R15 | Enumerate all 24-byte enum/cross-field combinations and canonical JSON output; reject bool/status subclasses, reserved bytes, extra data, malformed terminal marker, nonzero/unknown exit, and forbidden fields. Any S0-S5 ambiguity is locally generated `BOUND_UNAVAILABLE`. |
| R16 | Reachable import/call-graph and syscall allowlist prove no input/gameplay, emulator launch, approval/reconciliation, OCR, network/Discord, screenshot file, image sink, shared memory, frame hash/digest, stdout/stderr log, sleep, retry, or production caller. Allowed IPC/process/Job/GDI calls are explicit. |
| R17 | Run focused tests, existing readiness/lifecycle/native tests, canonical `python -m pytest tests/`, exact blob-verified clean-export canonical tests, and tracked-tree privacy/secret/image-blob scans. Record real counts and skips. |

The implementation test matrix must additionally enumerate every state edge and
show its expected terminal state (`ACCEPTED`, `CLOSED/BOUND_UNAVAILABLE`, or
`UNPROVEN/BOUND_UNAVAILABLE`). Test names or a coverage-label cardinality check
are not evidence.

## 8. Rejected alternatives

- Repairing the parked shared-memory/pixel-returning helper: rejected because it
  preserves parent pixel access and the failed ownership surface.
- Shared memory, memory-mapped files, inherited DIBs, sockets carrying frames,
  filesystem screenshots, frame digests, or parent-side template reduction:
  rejected because they create another frame owner/alias or pixel transport.
- Thread/Future timeout around synchronous `PrintWindow`: rejected because a
  timed-out worker can retain native pixels/handles.
- Donor ADB/framebuffer/cached-frame transport: rejected for transport,
  privacy, persistence, and authority conflicts.
- ClashAutomation foreground restore, flag-0 fallback, title/parent fallback,
  screenshot write/readback, or one-pixel classification: rejected as mutable,
  unbounded, persistent, or insufficient evidence.
- A second helper/retry or capture-health call: rejected by the one-acquisition
  and nonrenewable-deadline contract.
- Arbitrary injected production ports with certification tokens: rejected;
  production capability is the closed internal composition, not a caller claim.
- Accepting a scalar after forced retirement: rejected because retirement proves
  cleanup only, not orderly worker completion or trustworthy evidence.

## 9. Delivery and authority gates

This card changes only this public-safe design document and freezes one clean
local commit/tree. Independent same-card review must verify the exact commit and
all five defect mappings before implementation starts. The pre-created R10B
implementation card remains dependency-gated until this design is complete.

Implementation must use RED/GREEN against Sections 6-7, preserve applicable MIT
attribution, run focused/canonical/exact-export/privacy gates, freeze one clean
commit/tree, and obtain independent same-card PASS. Synthetic helper success is
not native `PrintWindow` compatibility, catalog calibration, live readiness, or
Run authority.

Production remains `BOUND_UNAVAILABLE` unless the exact helper binary, Win32
ABI, catalog bundle, bounded native operation set, and full terminal state
machine are independently promoted. Any future real/private image access,
helper launch against BlueStacks, gameplay/input, calibration, production
caller, live run, Discord/network action, push, or publication requires its own
explicit gate and is outside this design.
