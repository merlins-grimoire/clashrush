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
