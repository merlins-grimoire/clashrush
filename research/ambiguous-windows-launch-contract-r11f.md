# R11F ambiguous Windows launch evidence contract

Status: frozen design only. Base commit `9bc356325bd39170ea7469fb2b486ff52d5b5661`. No production/test edit or live diagnostic is authorized.

## Decision

Keep BasePilot capture/window/recognition and the standard whole-command `subprocess.run` transport unchanged. Remove every inference based on exception type, traceback frames, `Popen._child_created`, or any other private stdlib state.

The revised rule is deliberately asymmetric:

- Any exception escaping the standard `subprocess.run` constructor/launch boundary is `CLEANUP` with `child_status=null` and `child_wait_completed=false`. This includes `OSError`, failures before `CreateProcess`, `CreateProcess` failure, and `CreateProcess` success followed by pipe-handle cleanup failure. The public caller cannot distinguish them safely.
- `LAUNCH` is reserved for positively established pre-child failures outside that ambiguous boundary. In the current flow, the useful case is the diagnostic child returning the exact sanitized stage triple `(2, "", "LAUNCH\n")`; the whole-command child existed and was waited, so the parent record is `LAUNCH`, `child_status=2`, `child_wait_completed=true`. A future parent-side `LAUNCH/null/false` is allowed only if a local check fails before `subprocess.run` is invoked and the code path structurally cannot create a process. No current exception from `subprocess.run` qualifies.
- Every exception escaping `subprocess.run`, including `TimeoutExpired`, is `CLEANUP/null/false`. The outer catch cannot prove which operation raised an exception of that type or that context-manager exit and wait completed. `TIMEOUT` is therefore removed from the public failure taxonomy rather than inferred from exception class.
- A `CompletedProcess` returned by the real production `subprocess.run` continues to prove the whole-command child was waited. Existing exact stage/scalar/stderr classification and `child_status=<exact int>, child_wait_completed=true` remain unchanged. Injected runners are trusted test seams that simulate these public outcomes; their fabricated objects are not runtime OS evidence.

This is the smallest fail-closed repair. It may overclassify a true pre-process failure as `CLEANUP`, but it never underclassifies a possibly created, unproved child as `LAUNCH`.

## Exact CPython 3.13 Windows evidence

Inspected the installed CPython 3.13.5 Windows `Lib/subprocess.py` used by `uv run --frozen`: 89,486 bytes, SHA-256 `21ecc4c8f4fcf641974fc0d9cc28e97b07670ed1cef7e9d96769b31bb8345586`.

- `run` constructs `Popen` in the `with` expression (`subprocess.py:554`). No `process` local is bound if `Popen.__init__` raises.
- Windows `_execute_child` calls `_winapi.CreateProcess` at lines 1553-1561, then calls `_close_pipe_fds` in the `finally` at lines 1562-1571.
- `_close_pipe_fds` uses `ExitStack` callbacks whose close errors can propagate (`1298-1325`).
- Only after that `finally` completes does `_execute_child` set `_child_created = True`, retain the process handle, and assign the PID (`1573-1577`). Therefore `CreateProcess` may have succeeded while `_child_created` remains false and the caller receives a constructor exception.
- `Popen.__init__` catches the exception and performs best-effort stream/handle cleanup (`1048-1075`), but it has no retained `self._handle` at this point. `_child_created` is a private destructor bookkeeping flag (`815`, `1133-1146`), not externally valid evidence that no child was created.
- On a normal timeout, Windows `run` kills, calls `communicate` again, re-raises, and then `Popen.__exit__` waits (`557-570`, `1102-1131`). But the caller sees only the escaping exception after the `with` statement. The same `TimeoutExpired` type can be raised by a hostile/failing kill, second communication, stream close, context exit, or wait, so exception type cannot prove that sequence completed.

Consequently neither `OSError` nor `_child_created is False` proves pre-child failure. The current traceback/private-state helper is unsound even for the exact CPython version it attempts to inspect.

## Donor and caller preservation

The selected donor remains BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (tree `0553306bfd30a32e31e427a0b77ff22c41f55901`, MIT):

- `app/services/window.py:68-85,295-350` supplies the memory-returning `WindowService.screenshot()` seam used throughout `app/core/bot.py`.
- `app/services/vision.py:130-164,184-226` supplies geometry/template matching.
- `app/core/bot.py:223-225,242-248,1570-1584` supplies geometry update, Builder-first recognition, and the Home/Builder/Unknown decision; `_ensure_correct_village` at `1603-1619` is the donor production caller.
- Local adapters remain `basepilot_window.py`, `basepilot_vision.py`, `no_input_home_diagnostic.py`, and `NoInputDiagnosticCycle`. Their capture, recognition, stop, and mutex-release behavior is unaffected.

Only the public parent evidence adapter in `cli.py` and its focused tests need revision. No donor-facing interface, lifecycle ownership, child command, capture path, scalar grammar, or transport changes.

## Exact implementation map

Production (`src/clash_rush_rebuild/cli.py`) only:

1. Delete `_standard_run_reached_process_context` completely.
2. Remove the `TimeoutExpired` classification branch and remove `TIMEOUT` from `_DIAGNOSTIC_FAILURE_CLASSES` and `_diagnostic_failure_semantics_are_valid`.
3. Map every exception escaping the runner call, including `TimeoutExpired` and every `OSError`, to `_emit_diagnostic_failure("CLEANUP", None, False)`.
4. Do not inspect traceback locals, `_child_created`, `process`, exception subclasses/messages, or any `Popen` attribute.
5. Keep the existing returned-`CompletedProcess` precedence, exact compact JSON, empty public stdout on classified failure, and raw-detail suppression.
6. Keep `LAUNCH` in the closed schema because the exact child stage triple remains valid. Do not emit parent `LAUNCH/null/false` from a `subprocess.run` exception.

Tests (`tests/test_cli.py`) only:

1. Replace the two `_child_created` constructor tests with a finite boundary matrix that expects `CLEANUP/null/false` for both pre-`CreateProcess` failure and simulated `CreateProcess` success followed by pipe cleanup failure.
2. Keep post-spawn kill, second-communicate, and context-exit failures as `CLEANUP/null/false`.
3. Change arbitrary injected runner exceptions, including `OSError` and `TimeoutExpired`, to `CLEANUP/null/false`; an injected exception carries no child-absence or completed-wait proof.
4. Exercise the standard `subprocess.run` algorithm with inert `Popen` and make every escaping timeout-path exception `CLEANUP/null/false`: initial `communicate` timeout after otherwise successful kill/second-communicate/context-wait, plus same-type `TimeoutExpired` from kill, second communicate, stream close, context exit, and wait.
5. Retain exact child `LAUNCH` stage forwarding as `LAUNCH/2/true`, scalar cases, malformed-record rejection, unexpected-stderr precedence, and privacy assertions. Treat injected `CompletedProcess` values as simulations of the real standard runner's public return, never as independent OS proof.

No changes are required in `cycle.py`, `no_input_home_diagnostic.py`, BasePilot-derived adapters/assets, lifecycle code, or their tests.

## Finite hostile matrix

| Boundary/event | Public classification | Child status | Wait completed | Required proof |
|---|---:|---:|---:|---|
| Local validation fails on a structurally pre-run path | `LAUNCH` only if represented as classified evidence | `null` | `false` | `subprocess.run` was not called and the path cannot create a process |
| Pipe creation, handle duplication, stream wrapping, audit hook, argument/cwd conversion, or `CreateProcess` raises inside `Popen` | `CLEANUP` | `null` | `false` | No attempt to distinguish within private internals |
| `CreateProcess` succeeds; `_close_pipe_fds` raises before `_child_created=True` | `CLEANUP` | `null` | `false` | Mandatory regression case; no absence/wait claim |
| Constructor fails after any apparent/private child-created marker | `CLEANUP` | `null` | `false` | Private marker ignored |
| `communicate`, kill, second `communicate`, pipe close, `__exit__`, or wait fails | `CLEANUP` | `null` | `false` | No completed wait |
| Initial communication timeout, even when kill + second communicate + context wait appear to complete | `CLEANUP` | `null` | `false` | The outer exception alone cannot prove provenance or completed wait |
| `TimeoutExpired` raised by kill, second communicate, stream close, `__exit__`, or wait | `CLEANUP` | `null` | `false` | Same-type hostile cases prevent exception-class inference |
| Exact child stage `(2, "", "LAUNCH\n")` | `LAUNCH` | `2` | `true` | Returned `CompletedProcess` and exact triple |
| Exact child stage `CAPTURE`, `RECOGNITION`, or `CLEANUP` | matching stage | `2` | `true` | Returned `CompletedProcess` and exact triple |
| Valid scalar with empty stderr | existing scalar outcome | exact `0/1` | `true` | Exact existing pair |
| Nonempty or non-string stderr not in an exact stage triple | `UNEXPECTED_STDERR` | exact int or `null` | `true` | Returned `CompletedProcess` |
| Empty stderr with malformed scalar/status | `SCALAR_PARSE` | exact int or `null` | `true` | Returned `CompletedProcess` |
| Arbitrary injected runner exception | `CLEANUP` | `null` | `false` | Test seam cannot manufacture absence/wait evidence |
| Injected runner returns a `CompletedProcess` | simulated returned-process row only | simulated exact value | simulated `true` | Trusted unit-test model; not independent runtime OS evidence |

Every row must assert empty failure stdout, canonical one-line JSON only, no exception text/command/path/identifier leakage, and exact boolean/integer/null types.

## Cleanup and privacy assessment

PASS for the bounded evidence contract, with one explicit limit. The reduced taxonomy fully meets the current reporting/privacy need without custom IPC, a second Job design, monkeypatching, or private stdlib state: it emits only closed scalars, discards exception content, and refuses to claim absence or completed wait when CPython cannot prove either to the caller.

It does not positively retire a helper child in the ambiguous `CreateProcess`-then-cleanup-failure case. Instead it reports `CLEANUP/null/false`, which must fail any gate requiring helper retirement and authorize no retry or promotion. If a future requirement demands active recovery or positive retirement proof for that rare constructor seam, standard `subprocess.run` is insufficient and a separately reviewed ownership architecture would be required; that is not needed to make this evidence contract truthful.

Confidence: high for the CPython control-flow finding and revised classification; medium-high that this is sufficient for the current diagnostic evidence gate because its adequacy depends on preserving `CLEANUP/null/false` as blocking rather than treating it as cleanup success.

## Promotion boundary

Implementation remains a separate static card. It must run focused tests, canonical `pytest tests/`, exact-export tests, privacy/prohibited-surface scans, and independent exact-tree review. Static PASS authorizes no BlueStacks/process launch, capture, lifecycle mutation, approval issuance, or live retry.