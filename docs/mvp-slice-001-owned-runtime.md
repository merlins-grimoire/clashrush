# MVP-SLICE-001 owned bounded runtime

This is a static, inert repair contract. It grants no BlueStacks launch or gameplay-input authority. The implementation card must not launch BlueStacks.

## Frozen ownership state diagram

```text
IDLE
  | acquire protected host mutex (normal acquisition only)
  v
HOST_LEASED
  | verify exact-five v1 registry + READY bytes + no player
  v
ACTIVE_DURABLE                 (ACTIVE is committed before process creation)
  | CreateProcess suspended
  v
RAW_PROCESS_OWNED              (raw process + thread handles have one owner each)
  | duplicate retained process identity
  v
RETAINED_IDENTITY_OWNED
  | assign to private non-breakaway Job; prove membership
  v
JOB_OWNED
  | resume exactly once; close raw thread exactly once
  v
RUNNING_OWNED
  | exact retained identity + root/render HWND bind
  v
BOUND
  | transient capture only; no persisted frame and no input
  v
RETIREMENT
  | independently attempt Job termination, retained-process wait,
  | Job-empty proof, HWND invalidation, complete host absence, and every close
  v
READY_DURABLE                  (only after every authoritative proof and close)
  | release protected host mutex
  v
IDLE
```

Any uncertain transition stays `ACTIVE`, preserves authoritative handles/evidence until each bounded retirement attempt has run, and forbids another admission. A blocked native worker is bounded by an owner outside that worker. Forced retirement may release only a matching already-held control; it never emits a new press, move, or click. An abandoned host mutex is a sticky consistency fault: releasing the abandoned lease and later receiving an ordinary kernel acquisition does not make the process usable. Only an explicit reconciliation boundary may clear it.

## Public command ownership

The operator-facing `visit-one` command never composes or executes `InertCycle`
in its own process. It starts exactly one hidden `visit-one-child` through the
Hermes-derived atomic Job owner, applies a fixed 120-second parent deadline,
and accepts only the exact `0 / STOPPED / empty-stderr` scalar after child wait,
Job-empty cleanup, and stream cleanup all succeed. Timeout, malformed output,
child failure, or uncertain retirement produces one sanitized failure and no
automatic retry. The hidden child alone owns the host mutex and the complete
`InertCycle`; if it is forcibly retired, durable `ACTIVE` remains the
fail-closed reconciliation barrier.

This was selected as an architecture reassessment rather than an in-process
timeout patch: Python cannot safely interrupt a blocked native `PrintWindow`
call in the lifecycle thread, while the already-pinned donor owner provides an
external process boundary and authoritative descendant retirement.

## Immutable donor/import manifest

Primary ownership donor:

- NousResearch/hermes-agent commit `c10c89f74637cc945e9840705e0209df29aa08c8`, MIT, atomic Windows Job-owned subprocess seam.
- Existing local adaptations retained: `src/clash_rush_rebuild/diagnostic_child_job.py`, `src/clash_rush_rebuild/win32_runtime.py`, `src/clash_rush_rebuild/lifecycle.py`, `src/clash_rush_rebuild/win32_lifecycle_host.py`, and their production CLI/cycle callers.
- Retained behavior: suspended create, private kill-on-close Job, retained process identity, finite parent deadline, authoritative Job/process retirement, strict scalar result protocol, and fail-closed cleanup.

Comparison donors (behavior only; neither replaces lifecycle ownership):

- CoC_Bot commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, MIT: exact named-instance startup and capture behavior. Android/ADB input and process stopping remain rejected.
- ClashAutomation commit `c41fe12a6df051e241c695b71b6859286e24c612`, MIT: capture geometry and missing-image guards. Its lifecycle and input transport remain rejected.

Four boundary adaptations remain fixed:

1. Exactly one selected BlueStacks instance under the protected host mutex and private Job.
2. Explicit one-command operator approval for the inert visit; this slice does
   not claim durable tree/state-bound approval, and performs no gameplay input.
3. Private configuration and memory-only full-frame capture; source/export verification excludes private canaries and machine-local helper scripts.
4. Zero gameplay input and no spending/reward/account-switch executor. Forced cleanup can only release a matching control proven held by the retired worker.

## Source-bound proof contract

Ordinary tests import only tracked package/test sources. They do not load scripts from a personal Hermes profile and do not copy an ambient working tree containing ignored/private files. Broader verification materializes a clean Git tree export, binds `PYTHONPATH` to that export, runs the synthetic/inert suite there, builds and installs the tracked distribution, and scans only the exported tracked snapshot for private canaries.

## Documentation checked

- Python `subprocess.Popen` Windows implementation and `communicate()` timeout contract: https://docs.python.org/3/library/subprocess.html
- Microsoft Job Objects process-tree ownership: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- `GetDIBits` requirement that the bitmap not be selected into a DC: https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdibits
- `EnumChildWindows` completion/empty semantics: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumchildwindows

DOC GAP: these APIs do not define game-state meaning or owner permission. This slice therefore remains inert and does not authorize a selected-instance live proof.
