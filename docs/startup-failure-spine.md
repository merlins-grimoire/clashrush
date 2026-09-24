# Startup diagnostic failure spine

Static diagnostic repair only. This change does not diagnose the previous live
failure, tune geometry or matching, issue approval, or authorize another live run.

## Complete donor seam retained

- Primary operational spine: NousResearch/hermes-agent PR #69076,
  https://github.com/NousResearch/hermes-agent/pull/69076,
  MIT, commit `c10c89f74637cc945e9840705e0209df29aa08c8`, tree
  `e10e7738c99c31e16452be7fd116517d9b04da17`. The already transplanted
  `diagnostic_child_job.run_owned_diagnostic_child`, permanent thread-owned
  CreateProcess wrapper, retained wait, Job-empty proof, stream/handle cleanup,
  `cli.main` diagnose-home parent/child orchestration and strict scalar/record
  parser are reused as a whole. No second process runner or logger is added.
  `tests/test_diagnostic_child_job.py` and `tests/test_cli.py` remain the complete
  donor-derived regression harnesses, extended by startup-specific matrices.
- Failure-envelope donor: BasePilot, https://github.com/efebolukbasi/BasePilot,
  MIT, commit `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, tree
  `0553306bfd30a32e31e427a0b77ff22c41f55901`,
  `app/core/bot.py:74-130` (production try/re-raise/finally) and
  `app/ui/qt/bot_controller.py:32-58` (worker catches only at presentation).
  Adapted across StartupDebugCycle, StartupGeometrySupervisor, the complete
  StartupDebugController loop and hidden CLI child. Raw messages, tracebacks,
  logger calls and GUI signal payloads are replaced by closed enum values.
  The donor does not supply this project's fault matrix: those tests are local
  adaptations of the complete existing diagnostic and startup test harnesses.
- First-party startup order remains pinned to ClashRush
  `949497bf0a543a43ec6ef39a8c897e366bc10362`, tree
  `12ded3ccdb9f27610d9f07fdc7a6ad5b9fd74cc0`; launch, geometry, stable frame,
  icon verification, evidence, input, recovery and stop retain their callers,
  templates and synthetic fixtures. See `startup-debug-transplant.md`.
- CoC_Bot's `(False, "error")` loses stage information; ClashAutomation's raw
  logs/screenshots and unbounded waits conflict with this diagnostic protocol.
  Neither is substituted for the compatible owned-child spine.

## Protocol and presentation

`startup-debug-one` is now the parent. It never imports/builds the startup native
cycle, consumes an approval or directly touches BlueStacks. It starts exactly one
`startup-debug-child` using the existing atomic owned-child runner. Only that
child composes the startup cycle and consumes the same one-shot STARTUP_DEBUG
approval. Both commands still require `--allow-private-full-frames`.

The hidden child's complete result protocol is:

| Status | stdout | stderr |
| --- | --- | --- |
| 0 | `HOME` plus one newline | empty |
| 1 | `BUILDER` plus one newline | empty |
| 1 | `UNKNOWN` plus one newline | empty |
| 2 | empty | exactly one StartupReason value plus one newline |

UNKNOWN/BUILDER are observations, not HOME success. The parent mirrors these
observation tuples. For failures it returns 1, emits no stdout, and emits one
canonical compact JSON record with exactly `classification`, `child_status`,
and `child_wait_completed`, using the existing strict invoking-harness parser.
No arbitrary exception text, extra lines, status/output mismatch, bool-as-int,
unknown stage, malformed UTF-8 or noncanonical record is forwarded.

Parent-only reasons are PARENT_OWNERSHIP, PARENT_SPAWN, PARENT_TIMEOUT,
PARENT_COMMUNICATION and PARENT_CLEANUP. They cannot be supplied by child stderr.
They carry null child_status rather than claiming a completed child protocol.
The legacy diagnose-home presentation remains compatible with its original
four stage labels; startup uses the expanded vocabulary.

The default parent timeout is 240 seconds (explicit bounded override 1–600).
This covers existing lifecycle readiness, the unchanged 60-second startup loop,
and owned cleanup. A timeout is failure, never permission to retry; termination
may leave blocked ACTIVE or an abandoned mutex requiring existing reconciliation.
The parent never clears those states. Child/grandchild processes inherit the
outer non-breakaway Job, in addition to the inner BlueStacks ownership Job.

The pipe reader uses binary mode and decodes strict UTF-8 on the guarded parent
thread. This preserves the donor text-mode CRLF-to-LF translation but avoids
CPython's Windows reader thread printing an uncaught decoding traceback. The
real harmless malformed-byte child regression verifies empty parent stderr.

## Internal failure envelope and precedence

StartupReason is a closed enum. It preserves every lifecycle BlockReason;
geometry binding, restore, bounds, resize, verify and post-geometry capture;
evidence initialization, initial capture/evidence, icon verification,
recognition, authorization, input, paired evidence and final evidence.
Lifecycle launch rollback captures the owner's existing stage directly, never
parses exception text or reads a private persisted record to infer it.

StartupFault is frozen and contains reason, a tuple of prior closed reasons and
an exact boolean completed-input fact. Each production envelope copies only
these values, drops the original exception, completes cleanup, then raises
outside the except block. Both __cause__ and __context__ are empty at that
boundary. The child converts only the final closed reason to its wire token.
Prior reasons are structural internal evidence, not concatenated strings.

Final-evidence failure supersedes the earlier controller reason, preserving it
and any completed-input fact. Stop/mutex failure supersedes the visit reason;
rollback failure preserves the failing launch stage. Parent cleanup similarly
retains a prior timeout/communication/spawn or exact child failure enum. A lost
post-action frame does not erase the delivered fact in the protected outcome
file and never creates replay authority. A used controller does not capture or
write another final frame.

## Four boundaries unchanged

1. Protected host mutex, exact one-instance binding, retained handles, private
   non-breakaway Jobs and authoritative stop/Job-empty/HWND proof.
2. Existing tree/state-byte-bound, expiring, one-shot approval before startup.
3. Existing protected ignored private evidence sink; no new persisted data,
   private paths, account names/tags, HWNDs or raw exceptions in console/records.
4. Existing closed action vocabulary, geometry/icon thresholds, caps and native
   revalidation; no gameplay, spending, unknown-screen or reward capability.

## Documentation and verification

Authoritative references checked before implementation:

- https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate
  — Popen.communicate, timeout and kill/communicate cleanup; text/binary pipes.
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
  — Managing Processes in Jobs, Managing a Process Tree that Uses Job Objects:
  child inheritance, non-breakaway ownership and nested Jobs.

DOC GAP: these API pages do not define a game's stage vocabulary or permission;
the pinned production seam and closed local contract define those properties.

Recorded RED: 17 parent/child/geometry/envelope tests failed against the old
collapse; 7 parent-reason tests failed for missing outcome fields; 10 controller
fault tests failed against STARTUP_FAILED/FINAL_EVIDENCE_FAILED; malformed UTF-8
failed with a real reader-thread warning promoted to an error; exact-type,
no-context, child-argument and reused-controller tests exposed protocol gaps;
parent cleanup initially lost the prior child reason. All were exercised before
the corresponding repair.

Independent review found a prelaunch enumeration gap: the second overlap check
inside LifecycleSupervisor (including snapshot close) occurred before the
rollback stage marker. Query/close regressions first reproduced STATE instead
of PLAYER_ENUMERATION. Its unchanged query/check/finally-close body is now a
named lifecycle seam wrapped only by the diagnostic supervisor's closed-stage
adapter; normal lifecycle behavior and cleanup remain unchanged.

Canonical and exact-export suite results, exported tree seal, privacy scan and
independent review verdict are recorded in the task handoff. Static PASS does
not establish the cause of the earlier live failure or authorize a live retry.
