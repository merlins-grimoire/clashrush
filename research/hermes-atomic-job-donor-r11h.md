# R11H Hermes atomic-Job donor compatibility review

Status: static, read-only donor review. Local base commit `91ed9c317411be3b24ece20eb0156807e97b1d3a`, tree `ad9555478dc0915c680625622c0c96a21513f593`. No BlueStacks launch, donor live-process test, capture, input, lifecycle mutation, approval issuance, retry, push, publication, or production/test-code edit was performed.

## Verdict

**PASS as a bounded implementation-and-test donor, not as a direct drop-in.** Confidence: **0.88**.

Hermes PR #69076 provides the largest compatible licensed seam for atomically placing a `subprocess.Popen` child into a Windows Job before it can execute: permanent `_winapi.CreateProcess` wrapper, caller-thread ownership gate, `CREATE_SUSPENDED -> AssignProcessToJobObject -> ResumeThread`, singleton/install locking, raw-handle cleanup on resume failure, caller wiring, and 23 focused tests. That seam is materially better than designing another process framework.

The donor's production policy is intentionally incompatible in five places and must be adapted rather than copied unchanged: it uses one process-wide Job, fails open on Job setup/assignment/signature drift, does not retain a diagnostic-specific process handle or prove wait/Job-empty cleanup, logs free-form operational warnings, and leaves timeout cleanup to callers. A transplant is acceptable only with the thin fail-closed, per-diagnostic ownership and bounded-communication adaptations in this report.

## Immutable provenance and license

- Upstream repository: `https://github.com/NousResearch/hermes-agent.git`.
- PR: `https://github.com/NousResearch/hermes-agent/pull/69076`.
- Fork branch: `https://github.com/Sora-bluesky/hermes-agent/tree/fix/issue-69033`.
- Exact head commit: `c10c89f74637cc945e9840705e0209df29aa08c8`.
- Exact tree: `e10e7738c99c31e16452be7fd116517d9b04da17`.
- Parent: `d4625b593d4fe4829802d050571229b80efd6f25`; parent tree `c6eeae8549f3a729b48eb632736e64dcd70cd64d`.
- `refs/pull/69076/head` and the fork's `refs/heads/fix/issue-69033` both resolved to the exact head during this review. `refs/pull/69076/merge` resolved to `270c5f1d64f48003b3631f03a900ec41d512a1f6`.
- GitHub's PR API reported `state=open`, `merged=false`, one commit, and head `c10c89f...`; an independent `merge-base --is-ancestor` check returned false against fetched upstream `main`. The donor is therefore **open and unmerged**, not upstream-released behavior.
- Root `LICENSE` is the full MIT License, copyright 2025 Nous Research. Immutable Git blob: `75410e73319c72cd3e991a501c5455eb78f38375`; LF byte-stream SHA-256: `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`.
- A transplant must preserve that copyright and MIT permission/disclaimer in `THIRD_PARTY_NOTICES.md`, identify PR #69076 and the exact commit/tree, and enumerate copied/adapted code and tests.

## Exact donor change and callers

The one-commit PR changes exactly five paths (`1178` insertions, `18` deletions):

| Immutable path | Git blob | LF SHA-256 | Role / production callers |
|---|---|---|---|
| `hermes_cli/_subprocess_compat.py` | `6e4c00756d96ef7e08fb91a7fafcfca593ce4670` | `2e702892635dcfee9b016eb0ebd6b3f6db34ad976cae2ff26c4966abb424d830` | New Job singleton, one-time private `_winapi.CreateProcess` wrapper, thread-local owner gate, atomic create/assign/resume, resume-failure termination/handle close, and `spawn_bash_with_kill_on_exit`. |
| `tools/environments/base_output.py` | `065e84ccdc18506c5dae7cb77fec51066fe3b6bd` | `fb56e0078eb605a7b7a16f7ad61d74d5621902ee6fe5074bb94166f160f2963f` | `_popen_bash` calls the wrapper; this shared path serves container/remote shell backends. |
| `tools/environments/local.py` | `5195a9738ff99bd1d34cafc794ea4ecfea669b6f` | `93c266a0face0f0b329388b76133ec86ff9210503cfd2ffbabc68252ae974d96` | `LocalEnvironment._run_bash` calls the wrapper for foreground local shells. |
| `tools/process_registry.py` | `13358598188f94370e19d258e99ace52a9c07eb6` | `61cbac2a16daba9b89bf7bee96432503abcf9cc642d20f5e7d6f8ee3323a2f89` | `ProcessRegistry.spawn_local(..., use_pty=False)` calls the wrapper for local background pipe mode; winpty remains a separate path. |
| `tests/test_windows_terminal_kill_on_exit_job.py` | `ac702a70b144b835f266168b4422e6b7d9136aff` | `abfb45865c21fe6b6c9ec24d98a485e1b4cb0f5ba3ee316ce3e5f2a36fcc81ee` | 23 focused contract/integration tests. |

The reusable seam is `hermes_cli/_subprocess_compat.py:578-771` together with the focused tests that prove that seam. The three Hermes shell callers are inventory evidence, not destination code for Clash Rush: the diagnostic has one explicit parent-to-child call site in `src/clash_rush_rebuild/cli.py`, so importing the donor's generic shell/backend wiring would widen scope without adding ownership proof.

## 23-test suite verification

The immutable test blob contains exactly 23 top-level `test_*` functions; both AST enumeration and `pytest --collect-only` on the project Python 3.13.5 environment independently collected 23 tests. Coverage groups are:

- 4 wrapper/gate lifecycle tests: POSIX pass-through, unavailable-Job pass-through, owner set/clear, and exception clear.
- 7 CreateProcess-wrapper tests: unrelated pass-through, atomic order, assignment-failure resume, resume-failure termination, raw-handle closes, secondary termination warning, and signature drift.
- 3 install tests: idempotence, concurrent first callers, and real-Windows reload safety.
- 4 singleton/warning tests: singleton, unavailable warning, concurrent singleton creation, and warning-once behavior.
- 3 caller-wiring tests: shared `_popen_bash`, local `_run_bash`, and background `spawn_local` pipe mode.
- 2 real Windows process tests: child/grandchild death on Job close and unrelated-thread survival.

The PR body still says 22, but the exact head contains 23; the later contributor comment and prior Windows review both report 23. A prior native Windows review on pre-rebase head `370563542a...` reported `23 passed in 3.92s` plus three paired non-PTY/PTY owner-death trials. That commit is not an ancestor of the folded final head. Its receipt is useful provenance only, not test evidence for `c10c89f...`.

This review did **not** execute the two process-spawning Windows tests because the card prohibits process launch/live retry. A non-live attempted focused run through the local project's dependency environment was also not accepted as evidence: donor `tests/conftest.py` failed setup on missing `yaml` before every selected test. No dependency was installed and no donor file was changed. The immutable suite's count, structure, callers, and prior native receipt are verified; execution of the transplanted tests remains an implementation-card gate.

## Behavioral comparison

| Property | Hermes donor | Reviewed local lifecycle | Diagnostic-child requirement / disposition |
|---|---|---|---|
| Atomic first instruction | Adds `CREATE_SUSPENDED`, assigns the returned raw process handle, then resumes. | `LifecycleSupervisor.start` creates suspended, derives identity, assigns, proves `IsProcessInJob`, requires resume count exactly `1`. | **Copy donor ordering and tests.** It closes the race in which the child starts descendants before assignment. |
| Ownership gate | Permanent process-wide `_winapi` wrapper; inert unless current thread's `threading.local().job` is set around one factory call. | No stdlib patch; direct native `CreateProcessW` under one supervisor and host-wide lifecycle mutex. | **Adapt to one exact diagnostic spawn context.** Keep thread isolation but use a non-nestable exact context/token and refuse pre-existing ownership instead of overwriting it. |
| Job lifetime | One singleton Job for all terminal shells; held until Hermes exits. | One private unnamed Job per launched player/run. | **Use one private unnamed Job per diagnostic child.** Never put the child in the BlueStacks lifecycle Job and never share it process-wide. |
| Job setup failure | Fails open and warns once. | Fails closed; durable `ACTIVE` remains blocking. | **Fail closed before child creation.** No fallback `subprocess.run`, no unowned spawn. |
| Assignment failure | Warns and resumes the unassigned child. | Treats assignment as uncertain; independently attempts Job and retained-process termination, then proves wait/Job-empty/absence. | **Fail closed.** Do not resume after assignment failure; terminate through both available authorities, wait, and refuse success if any proof is missing. |
| Signature/version drift | If `_winapi.CreateProcess` is not exactly 9 positional arguments, skips ownership and spawns normally. | Uses declared `ctypes` ABI and exact result validation. | **Fail closed before calling the real function.** Runtime self-check must require the supported 9-positional signature; Python 3.11 is the production target. |
| Resume result/failure | Treats a non-raising pywin32 `ResumeThread` call as success; on exception calls `TerminateProcess`, best-effort closes `hp/ht`, then re-raises. | Requires exact prior suspend count `1`; rollback waits on retained process and proves Job active count `0`. | Preserve donor cleanup intent but use the existing local exact resume-count and retained-handle proof semantics. A termination attempt alone is insufficient. |
| Normal cleanup | Singleton Job kills all members only when its last handle closes; no per-child wait/Job-empty record. | Explicit `TerminateJobObject`, wait original retained process, prove Job empty/HWND invalid/player absent, then close handles. | Diagnostic cleanup must terminate its private Job, wait retained diagnostic handle, prove Job active count `0`, and close all handles before reporting `child_wait_completed=true`. |
| Timeout / pipe drain | The wrapper does not own communication. Standard Windows `subprocess.run` catches `TimeoutExpired`, kills only the direct child, then performs an unbounded second `communicate()` before returning control. | Lifecycle termination is explicit and does not depend on draining inherited pipes. | **Replace only the diagnostic parent's `subprocess.run` call with direct donor-wrapped `Popen` plus bounded communication.** Catch the first timeout, terminate the private Job before any second drain, prove wait/Job-empty, then permit only a separately bounded drain. |
| Output | Free-form warnings may include exception traceback on secondary termination failure. | Closed reason enums and sanitized records. | Emit only the existing closed diagnostic failure JSON/scalars. No raw exception, path, command, PID, handle, or traceback. |
| Scope isolation | Thread-local gate prevents concurrent unrelated threads from being swept; single-level and explicitly non-reentrant. | Exact owned run object and host-wide mutex prevent overlap. | Keep unrelated Popen calls untouched. Reject nesting/reentrancy and clear the exact owner in `finally`; do not use a broad process-global “next spawn” flag. |

## CPython 3.11/3.13 and private `_winapi` risk

The project runtime contract is Python 3.11. The live host's Python 3.11.16 and the project's `uv run --frozen` Python 3.13.5 both expose `_winapi.CreateProcess` as exactly nine positional-only parameters, matching the donor's index `5` creation-flags assumption. This confirms current shape compatibility, not API stability.

Risks that remain mandatory implementation gates:

1. `subprocess._winapi` is private and may change without compatibility guarantees. The donor mutates its process-global `CreateProcess` attribute permanently. Any transplant must be isolated in a small Windows-only module, pin exact supported interpreter minors, self-check the callable/signature before installation, and fail closed for the diagnostic if unsupported.
2. The donor's reload marker is stored as function attributes on the installed wrapper. Preserve the idempotence/reload tests, but do not claim this makes third-party monkeypatch composition safe. Refuse an unknown existing wrapper rather than wrapping it or silently bypassing ownership.
3. CPython 3.13.5's known constructor seam remains: `_winapi.CreateProcess` can succeed, the wrapper can assign and resume, and then `_close_pipe_fds` can raise before `Popen` publishes `_child_created`, PID, or its process-handle wrapper. Python 3.11 has the same nine-argument call shape, but the implementation card must verify the exact 3.11 control flow used in production rather than infer safety from shape alone.
4. The donor **does close the escape-from-Job race** in that seam: by the time the original `CreateProcess` returns to CPython, the child has been assigned before resume, so any descendant inherits the Job. It **does not close the retirement/evidence gap**: its process-wide Job stays open, it retains no diagnostic-owned wait handle outside CPython, and an outer `Popen` constructor exception still provides no proof that the child exited or was waited.
5. Therefore the transplant must capture a diagnostic-owned duplicate/retained process handle and exact creation identity inside the wrapper immediately after successful create and before returning to CPython. On any later `Popen`/pipe-cleanup exception, the parent adapter must terminate the private Job, wait that retained handle, prove Job empty, close all handles, and only then emit `CLEANUP` with truthful wait evidence. If duplication, publication, termination, wait, Job-empty query, or handle close cannot be proved, preserve the existing blocking `CLEANUP/null/false`; never infer absence from `_child_created`, exception type, PID, or traceback locals.
6. Standard `subprocess.run(..., timeout=...)` cannot remain on this owned path. On Windows it handles the first timeout internally, kills only the direct process, and performs a second unbounded `communicate()` before the adapter can close the Job. A descendant retaining stdout/stderr can therefore hang the caller indefinitely. Use direct `Popen` under the donor-derived owner gate, call `communicate(timeout=<whole-command remainder>)` once, and on timeout terminate the private Job immediately. Only after retained-handle wait and Job-empty proof may the adapter attempt a second `communicate(timeout=<small cleanup bound>)`; a failed bounded drain closes streams and remains a sanitized cleanup failure. No unbounded wait, drain, context-manager exit, or destructor path may sit between timeout detection and authoritative Job termination.

## Concrete copy/adaptation manifest

The implementation card must start by copying the donor seam and tests, then apply only these adaptations.

### Copy first

1. From `hermes_cli/_subprocess_compat.py:658-772`, copy the constants, one-time install lock/marker pattern, thread-local gate, atomic create/assign/resume order, and raw-handle resume-failure cleanup into a new narrow destination module such as `src/clash_rush_rebuild/diagnostic_child_job.py`.
2. From `hermes_cli/_subprocess_compat.py:627-655`, copy the Job creation/`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` algorithm, but implement it through the project's existing `NativeWin32Api`/`Win32Runtime` ctypes backend and instantiate per diagnostic invocation rather than as a singleton. Do not add pywin32 or change the lockfile.
3. Adapt into `tests/test_diagnostic_child_job.py` the donor tests at `tests/test_windows_terminal_kill_on_exit_job.py:80-520` covering owner lifetime, unrelated pass-through, atomic order, resume failure, raw-handle closure, unexpected signature, install idempotence/concurrency/reload; and `:744-910` covering real Job inheritance and unrelated-thread isolation.
4. Add the full MIT text and exact provenance/copy list to `THIRD_PARTY_NOTICES.md` before promotion.

### Thin required adaptations

1. Replace `_kill_on_exit_job` with an exact per-invocation owner record containing the private Job handle, a retained/duplicated process handle, exact PID plus creation time, thread handle state, assignment state, and a one-shot ownership token. No process-wide Job and no unrelated child capture.
2. Replace fail-open branches for unavailable native-Job support, Job creation, assignment, or signature drift with typed fail-closed results. The diagnostic child must not spawn unless the existing ctypes ownership instrumentation is ready.
3. Require exact non-nested ownership. If the current thread already owns a spawn or an unknown `_winapi.CreateProcess` wrapper is installed, create no child and return the closed cleanup classification.
4. After `CreateProcess`, publish the retained diagnostic process identity before any operation that can return control to CPython. Prove Job membership before resume and require exact `ResumeThread` prior count `1`.
5. Replace only `diagnose-home`'s standard `subprocess.run` call with direct donor-wrapped `subprocess.Popen` and an absolute monotonic deadline. Do not use `subprocess.run`, `Popen.__enter__/__exit__`, or an unbounded second `communicate` on this path. On timeout, terminate the private Job before any drain; wait the retained process and prove Job empty within finite bounds; then allow at most one separately bounded cleanup drain.
6. On assignment uncertainty, resume failure, wrapper failure, `Popen` constructor failure, timeout, communicate failure, context-exit-equivalent stream cleanup failure, or normal completion cleanup, attempt the authoritative private-Job termination regardless of earlier query failure; independently use the retained process handle where uncertainty requires it; wait with a finite bound; require Job active count `0`; and close streams/thread/process/Job handles through independently guarded cleanup steps.
7. Preserve current public diagnostic transport. Output may contain only the existing closed classification, exact JSON scalar types, and truthful `child_wait_completed`; never expose command, path, PID, handle, identity, raw exception, traceback, stdout/stderr beyond the existing allowlist, or private identifiers.
8. Keep `src/clash_rush_rebuild/lifecycle.py` and `win32_runtime.py` behavior sealed. Add only narrowly required ctypes primitives such as retained-handle duplication if absent, with their own ABI tests; do not alter `LifecycleSupervisor.start`, place the diagnostic child in the BlueStacks Job, or add pywin32.
9. Wire only the production `diagnose-home` child invocation in `src/clash_rush_rebuild/cli.py`; do not transplant Hermes's generic shell callers, process registry, Job singleton, warning logger, or terminal-backend behavior.

### Required new differential tests

- Python 3.11 exact `_winapi` signature accepted; kwargs, wrong arity, Python-shape fake, and unknown prior wrapper rejected before child creation.
- `CreateProcess` succeeds and the simulated CPython pipe cleanup raises: private Job terminated, retained process waited, Job empty, every handle closed, no unrelated process affected, and only truthful closed output emitted.
- The diagnostic child immediately creates a descendant that inherits and retains stdout/stderr, then exceeds the parent deadline: the first `communicate` times out, private-Job termination occurs before any second drain, child and descendant exit, retained process wait and Job-empty proof finish within bounds, and the cleanup drain is itself bounded. Mutating the implementation back to `subprocess.run` must make this test fail or hang under an outer test watchdog.
- Retained-handle duplication/publication failure before resume; assignment raises after possibly succeeding; resume returns `0`, `2`, `0xFFFFFFFF`, bool, float, or raises; each follows the correct fail-closed rollback matrix.
- Job termination, retained-process termination, wait, Job-empty query, and each handle-close failure are independently injected; no exception can skip the other cleanup authority, and unproved cleanup remains `CLEANUP/null/false`.
- Same-thread later Popen, concurrent other-thread Popen, nested diagnostic spawn, reload, and unknown monkeypatch composition never sweep an unrelated process.
- Real Windows Python 3.11 test: immediate child/grandchild inheritance, normal completion, forced parent-side constructor cleanup failure, finite wait, Job empty, and no leftover process. This is static-review-authorized test scope only; executing it requires the implementation card's exact gate.

## Promotion boundary

This PASS authorizes creation/release of **one bounded static donor-transplant implementation card** using the manifest above. It does not authorize BlueStacks, a diagnostic run, process launch, capture, lifecycle reconciliation/mutation, approval issuance, input/gameplay, retry, push, or publication. Implementation promotion requires copied/adapted tests, focused plus canonical `pytest tests/`, exact-export/privacy gates, a clean committed tree, and independent exact-tree PASS before any separately approved live action.
