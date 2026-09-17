# Slice 1 contract: inert five-slot lifecycle

## Scope

Build the smallest production foundation that discovers exactly five operator-configured BlueStacks instances, launches and binds only one at a time, captures a transient frame without persistence, stops the exact process tree, proves every bound window invalid, and advances a durable slot cursor only after stop proof.

## Permitted behavior

- Read `C:\ProgramData\BlueStacks_nxt\bluestacks.conf`.
- Read private slot configuration beneath `private/`.
- Read/write scheduler state beneath `var/`.
- Start one `HD-Player.exe --instance <exact-internal-name>` process.
- Inspect Windows processes and windows.
- Restore/focus the exact bound window for capture readiness.
- Capture a full frame in memory with `PrintWindow` and immediately reduce it to non-identifying health metadata.
- Request a graceful window close only when the caller supplies positive safe-screen proof.
- Terminate the exact bound process tree when graceful close is unavailable or unsuccessful.

## Prohibited behavior

- ADB binaries, modules, ports, shells, or commands.
- Any mouse, keyboard, touch, game, launcher-icon, upgrade, attack, navigation, spending, reward, social, or account-switching input.
- Persisting full frames or account-identifying data.
- Fuzzy display-name matching, first-window fallback, or launch while any `HD-Player` process exists.
- Starting slot N+1 until slot N's complete process tree is absent and all captured HWNDs are invalid.
- Cursor advancement after uncertain or failed stop proof.

## Public API and dependencies

- `registry.load_exact_five(conf_text, configured_display_names)` returns slots 0..4 in operator order.
- `LifecycleSupervisor.start(slot)` creates the player suspended, assigns it to a non-breakaway Windows Job Object, and returns an exact immutable creation-time/process/window binding.
- `LifecycleSupervisor.stop(binding, safe_screen_proof)` accepts only `None` or an exact same-binding proof; Slice 1 always supplies `None` and returns a verified stop record or raises fail-closed.
- `DurableLifecycleState` stores exact `READY(next_slot)` or `ACTIVE(slot, run_nonce, blocked_reason)` state beneath project `var/`; `ACTIVE` is flushed, write-through replaced, and read back before process creation. Failures preserve its slot and nonce. Only complete stop proof may durably produce `READY(next_slot)`.
- The protected host-wide mutex `Global\\ClashRushRebuildLifecycle-v1` is held from before discovery/state access through stop proof and final state commit. `WAIT_ABANDONED` forbids process creation and requires reconciliation.
- Native OS calls sit behind injected ports so synthetic tests never start or stop BlueStacks.

## Synthetic gates

- Exactly five distinct configured names bind to five distinct internal names.
- Missing, duplicate, malformed, ambiguous, or extra configured slots fail closed.
- Start refuses any pre-existing player and any ambiguous/mismatched PID/HWND binding.
- Slice 1 always skips graceful close and performs bounded Job-object termination; a later same-binding proof may authorize immediate identity-revalidated `WM_CLOSE`. Numeric-PID tree termination is forbidden.
- Stop fails if any descendant survives or any bound HWND remains valid.
- Lifecycle state advances only after verified stop and successful retained-handle closure; restart blocks on `ACTIVE` and resumes only from `READY`.
- A mutex-release failure after `READY` permanently disables the current process and requires abandoned-mutex reconciliation; it cannot roll the cursor backward.
- Complete dry trace is `START0,STOP0,...,START4,STOP4` with no overlap.
- Static source scan rejects ADB tokens and frame-write APIs in production modules.

## Live exit gate

Live execution is not authorized by this contract. After static tests, clean-export verification, privacy scan, and independent PASS, request explicit owner approval for one bounded reversible lifecycle/capture test. Native gesture tests are a later separately authorized transport spike.

## `WINDOW_BINDING` diagnostic sub-slice

The first inert compatibility trial ended in durable `ACTIVE(slot 0, blocked_reason=WINDOW_BINDING)`. Static diagnostic development is authorized; another BlueStacks launch and any reconciliation of that exact `ACTIVE` generation remain two separate owner approvals. This sub-slice does not weaken or bypass normal startup's rule that unresolved `ACTIVE` blocks ordinary launch.

### Static scope and invariants

- Keep `LifecycleSupervisor.start`, schema-v1 state encoding, retained-handle ownership, private non-breakaway Job containment, complete process enumeration, exact Job-membership proof, and stop proof unchanged.
- A successful result still requires one exact root, one exact render, private-Job membership, exact creation-time identities for both window owners, root ancestry, positive geometry, visibility, and a unique result. The render owner may be a different process from the launched root only when its exact identity is in the stable private-Job snapshot and is pinned separately in `PlayerBinding` for immediate capture revalidation.
- Diagnose before changing a predicate. Static review may identify hypotheses but cannot justify loosening title, process-identity, ancestry, uniqueness, visibility, geometry, or Job-membership requirements.
- Preserve the existing blocked slot and nonce. Static commands may not write lifecycle state, create a process, acquire input authority, focus a window, capture pixels, or reconcile `ACTIVE`.
- No mouse, keyboard, `PostMessage`, ADB, game navigation, gameplay, spending, reward, social, or account-switching surface may be imported or called.

### Closed diagnostic output

Donor inventory for this boundary: BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (`app/services/window.py`, MIT) has the largest inventory/descendant seam, but its diagnostic objects and labels expose raw titles, HWNDs, classes, dimensions, and ancestry depth. ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` (`utils/settings.py` and `utils/game_window_controller.py`, MIT) exposes raw titles/HWNDs and uses partial-title, first-match selection. CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` (MIT) has no native window-inventory seam; its `src/utils.py` instance path is ADB/web-service based. None is compatible with this closed privacy and exact-identity contract. The local diagnostic is therefore a clean-room reduction over the already reviewed native window primitives; no donor implementation is copied, and `THIRD_PARTY_NOTICES.md` remains unchanged.

The pure evaluator accepts already validated synthetic/native facts and returns one immutable record. It processes at most 4096 top-level windows and at most 4096 children of the selected root; exceeding either bound, an incomplete enumeration, or an invalid native value returns `NATIVE_FACT_INVALID`. It reduces counts to `ZERO | ONE | MULTIPLE` and dimensions to a closed geometry class, so no numeric PID, HWND, count, or dimension enters output.

Root evaluation starts from the complete top-level enumeration. An empty initial universe returns `ROOT_NONE`. Otherwise it applies these filters in this exact order: visible; exact expected title; `GA_ROOT == self`; exact launched-process identity. The record contains the reduced cardinality after each filter. A filter reason is returned only when that filter changes a previously nonempty survivor set to empty: respectively `ROOT_NOT_VISIBLE`, `ROOT_TITLE_MISMATCH`, `ROOT_ANCESTRY_MISMATCH`, or `ROOT_IDENTITY_MISMATCH`. Invalid or incomplete native facts take precedence as `NATIVE_FACT_INVALID`. More than one final survivor returns `ROOT_MULTIPLE`; exactly one proceeds to render evaluation. Rejections of unrelated windows do not fail an otherwise successful stage.

Render evaluation starts from the complete child enumeration of that unique root. An empty initial universe returns `RENDER_NONE`. Otherwise it applies these survivor filters in this exact order: visible; `GA_ROOT == selected root`; exact process identity belongs to the stable private-Job snapshot; valid client geometry. The record also reports how many Job-owned survivors share the launched root identity, but that structural count is not a survivor filter: BlueStacks may assign the render HWND to a child process in the same owned Job. A filter reason is returned only when a survivor filter changes a previously nonempty set to empty: respectively `RENDER_NOT_VISIBLE`, `RENDER_ANCESTRY_MISMATCH`, `RENDER_OUTSIDE_JOB`, or a geometry reason. Rejections of unrelated children do not fail an otherwise successful stage. The selected render owner's exact creation-time identity is pinned separately from the launched root identity and revalidated before capture. Invalid or incomplete native facts take precedence as `NATIVE_FACT_INVALID`.

Geometry validation applies only to candidates surviving the visibility, ancestry, and exact private-Job-membership filters. Valid geometry means exact integer width at least 640 and height at least 360, matching `PlayerBinding`, with `width * height * 4 <= 64 * 1024 * 1024` bytes, matching the native capture bound. If every surviving geometry fact is well formed and within the 64 MiB bound but every candidate is smaller than 640 by 360, return `RENDER_GEOMETRY_TOO_SMALL`; any zero, negative, non-integer, malformed, or over-64-MiB fact returns `RENDER_GEOMETRY_INVALID` for the entire surviving set rather than filtering that candidate. One or more valid candidates proceed to largest-area selection. Multiple candidates are allowed when one has a strict largest area; only a tie for largest returns `RENDER_EQUAL_LARGEST`. A unique strict largest returns `BINDING_READY`. There is no separate `RENDER_MULTIPLE` reason.

The command envelope contains only `schema_version`, `command_outcome`, and an optional pure-evaluator record. Allowed command outcomes are `PRECONDITION_BLOCKED`, `BINDING_OBSERVED`, `DIAGNOSTIC_FAILED`, `STOP_UNPROVED`, and `STATE_CHANGED`. `BINDING_OBSERVED` means evaluation completed, not that the current rule succeeded; only an evaluator reason of `BINDING_READY` may yield `PlayerBinding` in later production code. Unknown, conflicting, overflowing, incomplete, or exception-producing facts fail closed and never authorize binding.

No real/private runtime value may enter the envelope, console, ordinary logs, Status, Discord, crash reports, filenames, or tracked artifacts: window titles, account names, player tags, BlueStacks instance/display/internal names, PIDs, process names, creation times, process handles, Job handles, HWNDs, dimensions, paths, command lines, exception text, pixels, frames, crops, screenshots, or derived digests are prohibited. Public synthetic tests may use conspicuously fake canary values for those fields solely to prove reduction and non-leakage; they may not use values copied from private configuration or a live run.

The diagnostic boundary catches `BaseException` around complete fact collection/evaluation, immediately discards the exception and any traceback reference, clears raw fact containers, and constructs `NATIVE_FACT_INVALID` or `DIAGNOSTIC_FAILED` from constants in a separate no-argument helper. No raw native exception may cross into the CLI boundary. Tests inspect serialized output and the final raised/returned object; they do not require arbitrary lower-level traceback frames to be free of synthetic local variables.

### Synthetic and review gates

- RED-test zero and multiple roots; each root-filter failure and precedence combination; launched-root versus Job-child ownership; zero-area, too-small, and malformed children; each render-filter failure and precedence combination; no render; multiple renders; equal-largest renders; 4096/4097 bounds; invalid native values; and one exact positive binding.
- Prove every rejected case returns one closed reason and no `PlayerBinding`; prove permutations within each complete enumeration cannot change the record.
- Prove exception collapse, serialization, console output, and final failure representations contain none of the prohibited real/private canaries.
- For reconciliation, RED-test missing, malformed, expired, wrong-action, wrong-tree, wrong-state, and reused approvals; wrong slot, nonce, block reason, or state bytes; non-canonical state; transition guard; abandoned/contended mutex; any pre-existing player/window; and every approval-consumption, absence-proof, audit, state-write, read-back, and handle-close failure.
- For the later normal diagnostic visit, retain the existing start/rollback/stop matrix and RED-test every evaluator reason plus output reduction. Prove no native launch occurs before protected mutex acquisition, one-shot launch-approval consumption, canonical `READY`, transition-guard absence, durable `ACTIVE`, and complete player-absence proof.
- Run focused tests and the canonical suite, export the exact staged tree, scan it for private identifiers and forbidden transports, and obtain independent review of the exact tree hash before requesting any live action.

### Later live gates

Do not create an alternate launcher from `ACTIVE`. The reviewed `LifecycleSupervisor.start` remains the only inert launch boundary. Therefore the current blocked generation must be reconciled before—not after—the diagnostic visit.

Both reconciliation and the later diagnostic launch use separate one-shot local approval artifacts. The local approval service creates a random 128-bit approval ID, binds it to exactly one action kind (`RECONCILE_WINDOW_BINDING` or `LAUNCH_WINDOW_BINDING_DIAGNOSTIC`), the reviewed candidate tree hash, an expiry no more than ten minutes away, and the SHA-256 digest of the exact canonical lifecycle-state bytes observed when approval is granted. The complete artifact is protected for the current operator plus `SYSTEM` beneath ignored `var/approvals/`; none of its ID, state digest, nonce, path, or bytes enters CLI arguments, output, ordinary logs, Discord, or tracked files. Under the protected host-wide mutex, the command exclusively consumes the artifact before any lifecycle write or process creation. Consumption is durable and non-replayable even when the subsequent operation fails. Missing, malformed, expired, wrong-action, wrong-tree, wrong-state, or previously consumed approval blocks without side effects. Approval for either action cannot satisfy the other.

First, implement and independently review an owner-controlled reconciliation command. After separate approval, it must acquire the protected schema-v1 mutex; load and validate the approval; read canonical `ACTIVE(slot 0, exact run_nonce, WINDOW_BINDING)` with no transition guard; match the approval's protected state digest; completely enumerate processes and windows; prove no `HD-Player.exe` identity and no exact configured slot-title root window survives; durably consume approval; append a sanitized reconciliation-intent audit event; write/read back one canonical `READY(slot 0)` transition; and release the mutex. Any uncertainty before successful `READY` read-back preserves `ACTIVE` or its transition guard, except that a consumed approval stays consumed. A cleanup failure after successful `READY` follows the existing committed-state cleanup rule and never rewrites the cursor. Reconciliation emits no input, creates no process, and cannot authorize a launch.

Second, after reconciliation is proved and the evaluator implementation has static PASS, request a fresh `LAUNCH_WINDOW_BINDING_DIAGNOSTIC` approval. Under the same protected mutex, the normal inert visit must first read canonical `READY(slot 0)` with no transition guard, validate the approval and match its protected state digest, then durably consume the approval. Only afterward may it call unchanged `LifecycleSupervisor.start`, which repeats canonical `READY` validation, proves complete player absence, durably writes `ACTIVE`, and then creates the suspended process. A state/guard mismatch discovered before consumption leaves the approval unused; any failure after consumption leaves it consumed. Start uses the existing stage-specific rollback matrix and records the closed evaluator result. The visit performs no capture when binding is not `BINDING_READY`, no gameplay input in every case, and always uses existing authoritative stop/absence proof. Failure remains blocked `ACTIVE`; success completes the existing inert bind/capture-health/stop path and advances exactly once to `READY(slot 1)`. Neither result authorizes retry or another launch.

The one-shot approval service, reconciliation command, evaluator integration, and diagnostic visit each require RED tests, canonical tests, exact-tree privacy review, and independent PASS before their respective owner request. Static implementation never constitutes either approval.

### Allowed files for this sub-slice

- `research/slice-1-contract.md`
- `src/clash_rush_rebuild/win32_lifecycle_host.py`
- `src/clash_rush_rebuild/lifecycle.py` solely for the separately pinned render-owner identity in `PlayerBinding`
- one narrowly scoped diagnostic module under `src/clash_rush_rebuild/`
- one narrowly scoped approval/reconciliation module under `src/clash_rush_rebuild/`
- `src/clash_rush_rebuild/lifecycle_state.py` solely to expose the exact canonical bytes already held during a state load
- the composition-root command in `src/clash_rush_rebuild/cli.py`
- focused synthetic tests under `tests/`
- static privacy/forbidden-transport tests under `tests/`

No other production file is in scope without revising this contract before RED.

## Allowed commit files

- `.gitignore`
- `README.md`
- `pyproject.toml`
- `uv.lock`
- `src/clash_rush_rebuild/**`
- `tests/**`
- `config/slots.example.json`
- `research/slice-1-contract.md`
- attribution updates required by copied or adapted source
