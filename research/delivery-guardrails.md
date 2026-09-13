# Delivery guardrails

These gates exist to prevent the regression loops, hidden dependencies, unsafe live retries, and incomplete five-account behavior encountered in the previous build.

## 1. Do not build on the mixed legacy working tree

- The rebuild workspace is the only implementation target.
- Import source only from sealed donor commits recorded in `research/integration-plan.md`.
- Never copy an uncommitted legacy file.
- Copy the smallest complete behavioral seam, including its caller assumptions and tests—not isolated helpers.

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
- Clash Rush planners return recommendations only.
- A closed adapter converts recommendations to `ActionIntent`.
- Executors accept only known intent/resource/action enums.
- Planners cannot capture, click, sleep, launch, or read private configuration.
- Executors cannot choose targets or silently substitute a cheaper/random target.

This prevents a detector or policy change from accidentally changing click behavior elsewhere.

## 6. Make transactions crash-safe and non-replayable

Persist before input:

`PLANNED → INTENT_RECORDED → INPUT_STARTED → INPUT_COMPLETED → CONFIRMED|FAILED|UNCERTAIN`

Cursor advancement and terminal transaction state commit atomically. On restart, reconcile unfinished records from read-only evidence. Never replay an uncertain spend, deployment, reward, or confirmation.

## 7. Test the real five-account lifecycle

The scheduler gate is not just a list of indexes. Tests must prove:

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
- Enable the kill switch only for the bounded run; return it to off on every exit.
- First live failure ends the run and creates one narrow reliability slice.
- Do not patch and immediately retry live.
- Compare all three donors before revising mechanics.
- Use two-to-five-minute diagnostic runs before another full release run.
- Every diagnostic input gets a red crosshair and narrow before/after reviewed crops; full frames stay memory-only.

## 13. Enforce privacy structurally

- Runtime writes only beneath ignored `private/` or `var/` roots.
- Reject other output paths in code.
- Public fixtures contain no real names, instance IDs, tags, hashes, screenshots, machine paths, or calibration values.
- Scan the exact staged archive before every commit.
- References and commercial fonts remain ignored and are never packaged.

## 14. Preserve safety policy in code structure

Only explicitly enabled `HOME_GOLD`, `HOME_ELIXIR`, `BUILDER_GOLD`, and `BUILDER_ELIXIR` have executors. Dark Elixir, gems, purchases, fallbacks, rewards, social/clan/war/donation, Supercell account switching, evasion, and CAPTCHA behavior have no implementation—not merely disabled flags.

## Definition of done

The rebuild is done only when:

- a clean install launches through the operator command;
- exactly five private slots cycle sequentially with one emulator at a time;
- Home and Builder Base are serviced for every slot;
- labs and all available builders are checked and actionable work is attempted within budget;
- attacks deploy every configured card and Builder Stage 2 is positively detected;
- crash/restart tests prove no duplicate spend or deployment;
- bounded live evidence passes for all five slots;
- the exact published archive passes tests, privacy scan, and independent release review.
