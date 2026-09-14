# Delivery guardrails

These gates exist to prevent the regression loops, hidden dependencies, unsafe live retries, and incomplete five-account behavior encountered in the previous build.

## 1. Do not build on the mixed legacy working tree

- The rebuild workspace is the only implementation target.
- Import source only from sealed donor commits recorded in `research/integration-plan.md`.
- Never copy an uncommitted legacy file.
- Copy the largest compatible complete behavioral seam that fits the frozen slice, including its caller assumptions and tests—not isolated helpers or unrelated donor subsystems.

## 2. Freeze each vertical slice before coding

Every slice begins with a written contract containing:

- exact behavior;
- permitted input and spending;
- prohibited behavior;
- production callers and dependencies;
- synthetic tests;
- dry-run proof;
- live exit gate;
- files allowed in the commit.

Reviewers may block contract violations but may not expand the slice. Unrelated findings go to the backlog unless they affect safety, privacy, account identity, audit truth, or irreversible actions.

After two failed repair/review attempts, stop patching and reassess the architecture.

## 3. Prove transport before gameplay

The operator contract requires Windows to remain logged in, unlocked, awake, and on a stable display configuration. Lock, sleep, display reconfiguration, near-black capture, or lost foreground binding fails closed.

The first implementation spike must prove, on one reversible screen:

- exact BlueStacks instance → PID → render HWND binding;
- memory-only capture;
- foreground-bound click;
- drag and scroll;
- key/button hold and unconditional release;
- positive source→destination transition;
- no ADB process, module, port, or command.

No attack, upgrade, or Builder Base flow is copied until this spike passes.

## 4. Use explicit state machines, not click scripts

Every interaction must be:

`PROVE_SOURCE → AUTHORIZE → RECORD_INTENT → INPUT → PROVE_DESTINATION → RECORD_OUTCOME → RECOVER_SAFE_SCREEN`

Unknown screens emit no game input. Timed sleeps may delay a check but never prove a state. There are no blind fallback clicks.

Required screen states include Home, Builder Home, My Army, Scout, Home Battle, Builder Stage 1, Builder Stage 2, Result, popup, loading, disconnected, and unknown.

## 5. Separate observation, policy, and execution

- Observers return immutable typed state.
- The selected typed planners return recommendations only; historical Clash Rush planners receive no privileged execution path.
- A closed adapter converts recommendations to `ActionIntent`.
- Executors accept only known intent/resource/action enums.
- Planners cannot capture, click, sleep, launch, or read private configuration.
- Executors cannot choose targets or silently substitute a cheaper/random target.

This prevents a detector or policy change from accidentally changing click behavior elsewhere.

## 6. Make transactions crash-safe and non-replayable

Persist before input:

`PLANNED → INTENT_RECORDED → INPUT_STARTED → INPUT_COMPLETED → CONFIRMED|FAILED|UNCERTAIN`

The terminal action outcome and pending-lane state commit atomically in SQLite. The lifecycle tie cursor advances separately only through `ACTIVE → READY` after verified stop; reconcile the write-ahead visit generation across both stores before admitting work. Never replay an uncertain spend, deployment, reward, or confirmation.

## 7. Test the real five-account lifecycle

The scheduler gate is not just a list of indexes. With five equal due times, tests must prove the tie cursor emits:

`START0 → HOME0 → BUILDER0 → SAFE_HOME0 → STOP0 → START1`

for all five slots, twice, across a process restart.

Also prove:

- only one emulator process tree exists at a time;
- previous PID tree and HWNDs disappear before the next start;
- a stop failure halts the fleet;
- Home failure does not silently delete Builder work;
- Builder failure does not skip the slot forever;
- a global limit between lanes resumes at the correct lane;
- iteration/failure/time counters never reset during rotation;
- no runnable lane can be starved indefinitely.
- after schema-v2 migration, non-equal due times run strictly oldest-due first regardless of account order, and `READY(tie_after_account_key, configuration_generation)` acts only as the equal-deadline tie cursor. The sealed v1 `READY(next_slot)` contract remains unchanged and is not used to admit due-selected work.

## 8. Require a complete per-account visit report

A release-candidate run is incomplete unless every sanitized slot records:

- Home laboratory checked;
- Home free/total builders checked;
- Home planner decisions and outcomes;
- Builder Base reached and positively identified;
- Star Laboratory checked;
- Builder free/total builders checked;
- Builder planner decisions and outcomes;
- safe return and verified process shutdown;
- elapsed time within the ten-minute account budget.

“Launcher exited successfully” is not a pass.

## 9. Protect Home and Builder time

Each account receives ten minutes:

- first five minutes: drain immediately actionable Home work, laboratory first;
- next five minutes: drain immediately actionable Builder work, Star Laboratory first;
- unused Home time may be donated to Builder, but Builder always receives an attempt;
- cleanup may exceed a lane boundary only to reach a safe stopping state;
- unfinished work persists to the next visit.
- a positively confirmed free builder at the final observation creates one immediate successor due at visit finish with no random offset; older eligible jobs still run first;
- only a future builder completion receives one persisted 0–60 minute random offset; unknown final state defers.

## 10. Build Builder Base in separate proven slices

Promote independently:

1. reversible boat navigation and return;
2. read-only Builder/Star Laboratory/candidate observation;
3. one named Star Laboratory research transaction;
4. one named building transaction;
5. Baby Dragon Stage 1 deployment;
6. positive Stage 2 detection and deployment;
7. result and safe Home return.

Never copy ClashAutomation’s blind timed second deployment. New-building placement and reward/cart/chest claims remain excluded.

## 11. Use self-contained tests

- Safety-critical tests use committed synthetic frames and generic templates.
- Private screenshots may supplement tests but cannot be the only positive proof.
- Test a clean Git archive, not the developer working tree.
- Explicitly bind `PYTHONPATH` to the archive.
- No skipped safety-critical classifier paths.
- Run focused tests, full tests, exact-index privacy scan, then independent review of the exact tree hash.

## 12. Keep live debugging bounded

- No live action before static PASS, independent PASS, and owner approval.
- Authorize only the bounded run through the Run command; on every exit, automatically revoke further transaction admission without requiring a file edit.
- First live failure ends the run and creates one narrow reliability slice.
- Do not patch and immediately retry live.
- Compare all three donors before revising mechanics.

## 13. Keep operation owner-simple and donor-first

- Never require the owner to edit source code or a JSON kill-switch to start or stop normal operation.
- Treat the owner's Run action as bounded runtime authorization; expose Stop and Status as dedicated commands/shortcuts while enforcing internal fail-closed gates automatically.
- Before implementing a gameplay mechanic, inspect BasePilot, CoC_Bot, and ClashAutomation and copy/adapt the largest compatible, licensed behavioral seam rather than reinventing it.
- Treat published Clash Rush as unreliable historical first-party evidence, not the default tactical authority. Reuse only narrow code whose assumptions and behavior have been independently re-proved.
- Use two-to-five-minute diagnostic runs before another full release run.
- Every diagnostic input gets a red crosshair and narrow before/after reviewed crops. Full frames stay memory-only except for the separately reviewed blocker-only Discord upload to an exact private account channel.
- Diagnostic mode is explicitly bounded to two-to-five minutes, auto-disables on timeout/Pause/Stop/disconnect/blocker/exit, and may show a non-recording full-frame local preview plus approved OCR crop/value/confidence panels. A local Debug control is primary. Remote `/debug start|status|stop team:<name>` is disabled by default, requires Setup opt-in plus fresh Captain/Fleet Captain authorization, applies only to an already-running team, and cannot start/resume automation or widen any safety/image permission. Only reviewed narrow crops may persist under protected ignored `var/private-debug/` with bounded retention.
- “Approved crop” means an entry in a sealed manifest binding crop ID, source screen, normalized ROI, maximum dimensions, preprocessing, and permitted closed/numeric output grammar. Arbitrary ROIs and raw OCR text never enter logs or Discord panels.

## 14. Enforce privacy structurally

- Runtime writes only beneath ignored `private/` or `var/` roots.
- Reject other output paths in code.
- Public fixtures contain no real names, instance IDs, tags, hashes, screenshots, machine paths, or calibration values.
- Scan the exact staged archive before every commit.
- References and commercial fonts remain ignored and are never packaged.
- Actual Discord guild/category/channel/role/user IDs, team assignments, runner credentials, account mappings, and bot credentials remain under ignored private roots or the operating-system credential store. Public schemas use placeholders only.
- A blocker screenshot may leave memory only through the explicit Discord exception: exact mapped private account channel, immediate destination revalidation, restricted captain mentions, no local persistence, and no fallback destination.
- An optional OCR diagnostic panel may accompany that blocker only when its narrow-crop manifest was prepared before upload; neither attachment may be retried or recaptured after an ambiguous send.
- Guided Setup writes real topology only to ignored private configuration, stores token values in the OS credential store, and activates versioned configurations only at a stopped clean boundary. Public examples contain synthetic placeholders and secret references only.
- The protected local operator log/debug view may include configured account names, player tags, and BlueStacks display names. Its sink must verify current-operator-plus-`SYSTEM` DACL restriction or operator-bound encryption before writing; otherwise only `account_ref` is logged. Private identifiers never enter filenames, lifecycle state, Status, console output, Discord summaries, telemetry, crash reports, support/public exports, tests, or Git.

## 15. Serialize physical input across the host

- Multiple logical runners on one computer share one fair host-wide visit arbiter.
- The lease spans launch through verified stop and final durable commit; another runner cannot foreground/click or keep another player alive during it.
- Abandoned lease, unexpected player, focus loss, locked desktop, ownership mismatch, or stop-proof failure blocks every local runner.
- True concurrent automation requires separate hosts or independently validated interactive sessions; multiple windows do not create independent physical cursors.

## 16. Quarantine and blocker responses are state machines

- `/quarantine` targets one immutable account key, preserves due/pending state, and prevents every new `INPUT_STARTED`; an admitted action finishes only reconciliation/release/safe stop before entering quarantine.
- `/unquarantine` restores preserved eligibility but never starts Run or authorizes spending.
- Each blocker generation exposes only a compiled class-specific action set. First authorized terminal response wins; unsupported, duplicate-conflicting, free-text, stale-generation, or unsafe retry requests fail closed.
- Lifecycle, identity, CAPTCHA, stop-proof, and uncertain spend/deployment blockers cannot be resumed or retried through Discord.

## 17. Preserve safety policy in code structure

Only explicitly enabled `HOME_GOLD`, `HOME_ELIXIR`, `BUILDER_GOLD`, and `BUILDER_ELIXIR` have executors. Dark Elixir, gems, purchases, fallbacks, rewards, social/clan/war/donation, Supercell account switching, evasion, and CAPTCHA behavior have no implementation—not merely disabled flags.

## Definition of done

The rebuild is done only when:

- a clean install exposes and exercises Setup, Run, Pause, Resume, Stop, and Status without source/config kill-switch edits;
- the frozen exact-five foundation remains reproducible and its separately promoted schema-v2 migration schedules every configured account oldest-due-first, using restart-safe round-robin only for equal deadlines, with one emulator per host;
- Home and Builder Base are serviced for every slot;
- labs and all available builders are checked and actionable work is attempted within budget;
- attacks deploy every configured card and Builder Stage 2 is positively detected;
- crash/restart tests prove no duplicate spend or deployment;
- bounded live evidence passes for every account in the promoted configuration (including the current exact-five v1 proof);
- local builder-due queues survive restarts and multi-runner Discord routing cannot cross team or account channels;
- the exact published archive passes tests, privacy scan, and independent release review.
