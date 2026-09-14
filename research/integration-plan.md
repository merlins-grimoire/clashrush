# Integration plan

## Target outcome

A Windows/BlueStacks runner that services a configured troop fairly and runs Home Village and Builder Base using copied/adapted proven donor mechanics behind local safety controls. The already-reviewed inert foundation remains exact-five; variable cardinality enters only through a separately tested schema-v2 migration.

The new runner is not a wholesale fork. It is a small coordinator with explicit adapters and copied, attributed mechanics from the three MIT donors. Published Clash Rush is historical first-party evidence and a source of narrow primitives/pure policy only after re-verification; it is not presumed to contain known-good end-to-end tactics.

## Reproducible source seals

| Source | Canonical URL | Commit | Tree |
|---|---|---|---|
| BasePilot | `https://github.com/efebolukbasi/BasePilot` | `e17c23e88cff58047123d66747c937d3bbf8f815` | `d038e2b99cb440002199aa5a1b9cd64474dda95f` |
| CoC_Bot | `https://github.com/m24842/CoC_Bot` | `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | `d79368fbe550036f1542883f18434e318016b279` |
| ClashAutomation | `https://github.com/calebmwelsh/ClashAutomation` | `c41fe12a6df051e241c695b71b6859286e24c612` | `b549901e7ca8b871a17265517d730f0b3c84a618` |
| Published Clash Rush historical engine | `https://github.com/merlins-grimoire/autoclasher` | `949497bf0a543a43ec6ef39a8c897e366bc10362` | `12ded3ccdb9f27610d9f07fdc7a6ad5b9fd74cc0` |

The dirty legacy checkout is not reproducible evidence. Only files from the published Clash Rush commit above may even be considered for reuse, and each candidate must first survive donor comparison plus differential tests. That seal includes `builder_base_policy.py`; later uncommitted Builder navigation/reconciliation work is excluded entirely.

## Runtime model

### One live instance per host, configured durable accounts

Only one BlueStacks instance runs on a Windows host at a time. One host-wide arbiter grants a complete visit lease spanning discovery, launch, input, safe stop, absence proof, and final commit. Multiple logical troop runners on the same computer may schedule independently but cannot click or keep separate players alive concurrently; physical foreground input has one global cursor and target. Runners on different computers may operate concurrently.

This avoids host memory/CPU pressure and removes cross-instance cursor/capture races. Every transaction still obtains fresh evidence after acquiring the instance lease and immediately before input.

### Due-account service schedule

Each account has one durable job carrying its due time and pending village lane. A future builder completion creates one persisted due time at availability plus a uniform 0–60 minute delay. Select the oldest due account first; for equal deadlines, rotate deterministically from the restart-safe tie cursor. After a ten-minute visit, a final positive observation that any builder remains free creates an immediate successor due at visit finish with no random offset and a fresh ordering timestamp, so older waiting accounts still go first. Unknown final builder state is deferred, never guessed.

Each account visit has a hard **10-minute** wall-clock budget. Within that visit, drain all immediately actionable Home and Builder work that can be completed safely. Reserve the first five minutes for Home and the remaining five minutes for Builder so one village cannot consume the other village's opportunity; unfinished work resumes on the next cycle. A transaction-specific continuation may exceed its lane boundary only to reach a safe stopping screen, never the ten-minute account deadline except for fail-closed cleanup. Persist:

- per-account due time, availability event identity, and pending village lane;
- a round-robin tie cursor used only among equal oldest deadlines;
- active battle type and deadline;
- consecutive failure/quarantine state;
- current account/tag-digest binding;
- Home wall-batch progress;
- last successful Home and Builder observations.
- per-account audit event cursor, blocker/quarantine generation, and diagnostic-retention metadata.

A battle must finish or reconcile before rotation. If no safe game screen can be proved, emit no more game input, release every held key/button, terminate only the exact bound emulator process tree, and verify the PID tree and HWNDs disappeared. If shutdown cannot be proved, stop the complete fleet and do not start the next slot.

The due queue is the sole eligibility/ordering authority only after schema v2 is promoted. The reviewed exact-five v1 `LifecycleSupervisor.start` remains sealed with its `READY.next_slot == selected_slot` authorization and does not admit due-selected work. A stopped-only migration creates a new versioned `start_admitted(account_key, configuration_generation, visit_nonce)` boundary; it accepts an oldest-due account while holding the lifecycle mutex and writes matching schema-v2 `ACTIVE` before process creation. After verified stop, the v2 `READY` account-key cursor supplies only the next equal-deadline tie start. It never selects or makes a not-yet-due account eligible, and no second account/lane cursor exists.

Coordinate the SQLite queue/journal and separate lifecycle file with a write-ahead visit generation and nonce. Admission atomically marks one job `ADMITTED` and inserts its immutable nonce/generation before `ACTIVE`; actions use non-replayable journal states; a successor builder/reconciliation plan is persisted before normal shutdown; verified stop then commits `READY`; and one idempotent SQLite transaction consumes the admitted generation, creates exactly one successor, and finalizes the visit. Slice 2 adds an emergency owned-stop path that proves Job/process/window absence but deliberately leaves blocked `ACTIVE` when successor planning cannot be persisted. Startup under the lifecycle mutex reconciles every open visit against exact `READY/ACTIVE`, `ACTIVE.run_nonce == admitted.visit_nonce`, generation, action outcomes, successor plan, and complete process absence. `READY` before `ACTIVE` may release an unchanged admission only after absence proof; `READY` after a recorded successor plan finalizes it idempotently; matching `ACTIVE` blocks launch and requires lifecycle reconciliation; any mismatch blocks the troop.

Oldest-due applies only to currently eligible jobs. Lane-incomplete or uncertain jobs carry a durable `DEFERRED(reconcile_at)` deadline no later than 60 minutes and retain their original due event; explicit human/safety quarantines remain visible but cannot block other jobs and require captain clearance. This prevents one broken oldest job from freezing the troop without silently skipping or deleting it.

### Required lifecycle sequence

For every slot, the normal path is exactly:

`START(slot) → VERIFY_BINDING → HOME_DRAIN(slot, ≤5m) → BUILDER_DRAIN(slot, ≤5m) → SAFE_HOME → STOP(slot) → VERIFY_STOPPED → ADVANCE`

- A recoverable Home failure that returns to proved Home still proceeds to the Builder lane.
- Home drain checks the laboratory first, then uses every safely actionable free builder; Builder drain checks Star Laboratory first, then safely actionable named upgrades and bounded Baby Dragon attacks.
- “Complete all” is best-effort inside the ten-minute cap. No account may extend its visit merely because more farming or upgrades remain.
- An unknown/unsafe Home state prevents Builder navigation; persist `(slot, BUILDER)` as deferred, terminate the exact instance, and retry that lane on a later bounded run.
- A Builder navigation/action failure must recover to proved Home or use the fail-closed process-tree stop above.
- If a global limit expires between lanes, persist `(slot, BUILDER)`, stop the current instance, and resume there next run; do not replay Home.
- A quarantined slot remains explicit in the schedule and cannot silently disappear. Other slots may proceed only after the current process is proved stopped.

## Component boundaries

```text
Operator Setup / Run / Pause / Resume / Stop / Status entry points
  -> versioned 1–10-account config (v1 remains exact-five) + internal safety gates + bounds
  -> Fleet coordinator (durable scheduler)
       -> BlueStacks registry (exact process/HWND binding)
       -> Lifecycle supervisor (start/stop/process-tree proof)
       -> Capture adapter (memory-only full frame)
       -> Screen classifier
       -> Home observer -----------+
       -> Builder observer --------+--> selected typed planners
                                      -> typed ActionIntent
       -> Transaction executor <---+
       -> Audit/event store
```

### 1. `BlueStacksRegistry`

Adapt CoC_Bot’s `bluestacks.conf` parsing and named `HD-Player --instance` launch. Replace ADB port discovery with:

- exact configured display name → internal instance binding;
- launched PID verification;
- exact top-level and render HWND binding;
- geometry and render freshness checks;
- no first-window or fuzzy-name fallback.

### 1a. `LifecycleSupervisor`

- start only the scheduler-selected instance;
- prove no other configured `HD-Player` instance is active;
- attempt a bounded native graceful close only from a positively recognized safe screen;
- otherwise terminate the exact verified PID and descendants without ADB;
- wait for process-tree disappearance and invalidate every bound HWND;
- refuse cursor advancement and refuse the next start if stop proof fails.

### 2. `NativeWindowAdapter`

Compare BasePilot, ClashAutomation, and the historical published Clash Rush transport, adapting ClashAutomation’s controller abstraction, normalized coordinates, and `PrintWindow` capture but rejecting its unproven CROSVM `PostMessage`. Select a physical foreground-bound BlueStacks gesture seam only after the inert differential transport spike. Required properties:

- no ADB/minitouch imports or subprocess calls;
- a single coordinator-owned native-input path;
- verified PID/HWND immediately before every gesture;
- explicit down/up completion and unconditional key release;
- source→destination state proof;
- no random jitter or blind retry.

Before any gameplay slice, run a transport spike proving exact BlueStacks capture, focus/binding, click, drag, scroll, hold, and unconditional release on a reversible screen. If the spike fails, stop; do not transplant higher-level donor flows.

#### Target localization policy

Coordinates are the final transport representation, not the primary proof that a button exists. Locate dynamic buttons and screen states with small reviewed templates, color/shape evidence, or restricted OCR, then click the detected control center in normalized render coordinates. A fixed normalized coordinate is allowed only for an owner-calibrated stable HUD/keybind target after the exact screen and render geometry are positively proved; it is never a blind fallback. Verify the expected destination after every click.

Use the operator-provided `private/assets/CCBackBeat.ttf` only for local text rendering/matching where it improves restricted OCR or anchored label verification. The font remains ignored, is never packaged, and cannot replace a positive live control detector by itself.

### 3. `RecoveryStateMachine`

Adapt BasePilot’s popup, disconnect, battle-result, and delayed-return flows. Every recovery gesture requires a positive source-state detector. Unknown or persistent near-black states are deferred/quarantined rather than clicked through.

### 4. Observers

#### Home observer

Compare BasePilot, CoC_Bot, and ClashAutomation first and copy/adapt the largest compatible complete observation seam. Published-seal Clash Rush detectors are historical candidates only and must win differential tests before reuse:

- account/tag binding;
- builder count;
- laboratory state and affordability;
- resources;
- upgrade-menu rows and target identity;
- army readiness;
- Home/Scout/Battle/Result screen taxonomy.

#### Builder Base observer

Start with CoC_Bot’s separate Builder HUD regions and boat/lab/building vocabulary, then combine:

- CoC_Bot boat navigation and Star Laboratory/building recognition;
- BasePilot attack, hero, and return flows, excluding carts/rewards;
- ClashAutomation deployment geometry only; its timed second deployment is not a stage detector;
- the published `BuilderBasePlanner` only as a historical differential-test candidate, plus new locally reviewed observation/reconciliation types.

The two-stage Builder Base state machine has no proven donor. Build it locally with positive first-stage, transition, second-stage, result, and return detectors; never use a timed blind second deployment.

Required state:

- positively proved Builder Base destination;
- Builder Hall level;
- free/total builders;
- Star Laboratory busy/available state;
- Baby Dragon and required troop levels;
- B.O.B./sixth-builder prerequisite progress;
- named candidate buildings with level, cost, and resource;
- attack readiness and pending first/second stage.

### 5. Planner selection and adaptation

Extract the required strategic-rush behavior as a contract, compare it with the three pinned donors, and copy/adapt the largest compatible licensed policy seam. The following pure modules from the published Clash Rush seal are historical candidates only; do not transplant them or live UI orchestration without differential tests:

- `models.py`
- `policy.py` and `policy_data.py`
- `mapping.py` and any mechanically required immutable data loaded by `policy.py`
- `upgrade_planner.py`
- `suggested_upgrades.py`
- `cycle.py`
- `builder_base_policy.py`
- mechanically required reconciliation/model helpers

The published `builder_base_policy.py` is reproducible historical evidence, not presumed-correct policy. Builder Base navigation and observation reconciliation are not taken from dirty legacy files; implement and review those boundaries in this repository.

Add one closed adapter type before any executor exists:

`ActionIntent(account_key, configuration_generation, village, kind, target_id, resource, max_cost, reason_code)`

Adapters translate whichever planner wins the frozen donor/policy contract into this exact vocabulary. Historical `RushPlanner`, `cycle.py`, and `BuilderBasePlanner` outputs are supported only if differential tests justify their selection. Unknown recommendation/result types fail closed; planners never call executors directly.

The planner receives trusted typed observations and returns one typed `ActionIntent`. It never clicks, captures, launches, sleeps, or reads private configuration.

### 6. Transaction executor

Each intent executes through:

1. owner feature authorization;
2. global/account kill switches;
3. account and PID/HWND binding;
4. fresh positive source-state observation;
5. affordability/resource policy;
6. intent audit;
7. final lease/time/binding check;
8. one native input sequence;
9. positive postcondition;
10. outcome audit and safe-screen recovery.

The owner does not operate these gates by editing files. One-time Setup records explicit non-premium resource authorizations through a command/UI. Starting Run authorizes that bounded run; Stop revokes further transaction admission immediately and performs safe cleanup. Development-only `--owner-approved` flags do not define the release UX.

The typed resource allowlist distinguishes `HOME_GOLD`, `HOME_ELIXIR`, `BUILDER_GOLD`, and `BUILDER_ELIXIR`. Each normal-resource type requires explicit owner enablement. `HOME_DARK_ELIXIR`, `GEMS`, real-money purchases, purchase fallbacks, reward/chest/cart claims, social/clan/war/donation, Supercell account switching, evasion, and CAPTCHA handling have no executor in the initial release. Donations and the daily resource cart are separate post-production slices in `research/production-backlog.md`, never recovery side effects.

### Observability and diagnostic mode

Normal operation writes structured per-troop and per-account events beneath ignored `var/`: configuration generation, a random 128-bit `account_ref` generated once by Setup and never derived from a name/tag/instance, visit/action nonce, screen/detector/reason code, normalized intended target, state transition, timing, and confirmed/failed/uncertain outcome. The private configuration maps `account_ref` to the account key; Status and exported support bundles expose the opaque reference only. Logs never record tokens, private names/IDs, paths, PIDs/HWNDs, raw OCR, pixels, full-frame digests, or exception locals. Audit-intent failure prevents input; audit failure after input leaves the action uncertain and blocks replay.

An explicit two-to-five-minute diagnostic run enables a non-focus-stealing local preview with transient full frame, red crosshair/target bounds, account/lifecycle/instance timers, and NX-inspired OCR panels. A sealed diagnostic manifest allowlists each crop ID, screen state, normalized ROI, maximum dimensions, preprocessing pipeline, and permitted output grammar (closed reason/state vocabulary or bounded numeric value plus confidence); arbitrary user ROIs and raw OCR text cannot be persisted or uploaded. Full frames remain memory-only. Optional retained before/after evidence is limited to those reviewed narrow crops under ignored `var/debug/` with bounded retention. A blocker may include one optional in-memory OCR panel in its predeclared Discord attachment manifest; ambiguous delivery never retries or recaptures that generation.

### Crash-safe action boundary

Persist a write-ahead transaction record atomically before input: `PLANNED → INTENT_RECORDED → INPUT_STARTED → INPUT_COMPLETED → CONFIRMED|FAILED|UNCERTAIN`. The terminal action outcome and its pending-lane update commit together in SQLite. The equal-deadline tie cursor advances separately and only through the later lifecycle `ACTIVE → READY` transition after verified stop; the write-ahead visit reconciliation protocol bridges those stores. On restart:

- reconcile `INPUT_STARTED`/`INPUT_COMPLETED` from read-only game state;
- never replay a spend, deployment, or claim whose outcome is uncertain;
- quarantine unresolved spend/deployment intents for manual review;
- resume only after binding and source-state proof.

## Donor map

| Capability | Primary donor | Treatment |
|---|---|---|
| BlueStacks instance discovery/launch | CoC_Bot | Copy structure; remove all ADB fields and commands |
| Five-instance process inventory | CoC_Bot | Replace independent loops with one coordinator that launches one named instance at a time |
| Native input transport | BasePilot/ClashAutomation comparison + historical published Clash Rush `949497b…` | Select one foreground-bound physical Windows seam only after an inert differential transport spike; reject ADB and `PostMessage` |
| Capture/interface/geometry | ClashAutomation | Adapt interface/geometry only; do not assume CROSVM `PostMessage` works on BlueStacks |
| Home builder menu and walls | BasePilot first; compare CoC_Bot/ClashAutomation and historical Clash Rush | Copy the largest compatible guarded behavior and re-prove local transaction boundaries |
| Home attacks | BasePilot + ClashAutomation; historical Clash Rush only as evidence | Copy compatible deployment/transition mechanics but keep local physical input and positive state proof |
| Popup/reconnect/result recovery | BasePilot | Convert each branch to positive state transitions; omit every reward/claim branch |
| Builder boat/HUD/build/lab | CoC_Bot | Adapt mechanics to native input and typed observations |
| Builder battle | BasePilot + local implementation | Reuse guarded deployment/return ideas; build positive two-stage detection locally; exclude cart/rewards |
| Upgrade decisions | Donor comparison + Rush Bible contract | Select/rebuild deterministic Home and Builder policy; historical Clash Rush modules require differential proof |
| Scheduler, safety, audit | Local implementation | Rebuild around configured stable account keys, two village lanes, and the durable due-account queue; preserve exact-five v1 migration evidence |
| Operator preview/dashboard concepts | NX-ClashClient `e4c79fd…` | Clean-room only: no license grant; adopt information architecture and OCR-preview behavior, not code/input/storage |
| API/CWL/rankings/donation counters | Official API; ClashKing references | Read-only central adapter; never a builder/action authority |

## Delivery slices and exit gates

### Slice 0 — workspace and donor seals

- Pinned references recorded.
- MIT notices preserved in `THIRD_PARTY_NOTICES.md` before copied code is committed.
- `references/`, private config, frames, and runtime output ignored.
- Runtime code can write private material only through a path policy rooted under ignored `private/` or `var/`; reject every other destination.

### Slice 1 — inert five-instance registry

- Discover exactly five configured BlueStacks instances without launching all five.
- Launch and bind one slot at a time to a unique PID/HWND and tag digest without game input.
- Prove the previous player process is stopped before the next instance starts.
- Prove graceful-close, forced exact-process-tree termination, HWND invalidation, stop timeout, and refusal to start the next slot after failed stop proof.
- Reject missing, duplicate, stale, or ambiguous bindings.
- Clean restart preserves slot order.

### Slice 1b — configurable lifecycle migration

- Preserve the exact-five Slice 1 implementation, state, `start`, tests, and proof unchanged.
- While stopped with full player absence and no open visit/action/blocker delivery, migrate v1 indexes to stable account keys bound to one active configuration generation.
- Add a separate schema-v2 `start_admitted` boundary; do not reuse or weaken the sealed v1 `start` authorization.
- Accept 1–10 configured accounts, default five, deriving cardinality from the account array and rejecting zero or a separate count.
- Cover `N=1`, `N=2`, `N=5`, `N=10`, resize/reorder, restart, equal-deadline fairness, unsafe removal, and every migration crash seam.
- Adding/removing/reordering creates a new generation and fails while an affected account has admitted, uncertain, blocked, or unarchived state.
- Stage immutable configuration plus the operative schema-v2 mutable lifecycle file, bind the configuration hash and initial lifecycle-seed hash in a durable migration intent, and atomically switch one write-through active-generation pointer as the sole commit point. The pointer permanently binds the immutable configuration hash and lifecycle path/schema/generation—not mutable lifecycle bytes. Before normal admission, migration recovery verifies the seed once; afterward the selected lifecycle changes only through its guarded write-through protocol. Startup deterministically retains v1 before the pointer, selects v2 after it, and blocks every path/hash/generation/absence ambiguity.

### Slice 2 — due-account dual-lane dry scheduler

- Prove non-equal deadlines run strictly oldest-due first, regardless of slot number.
- Prove five equal due times emit `H0,B0,H1,B1,…,H4,B4` twice across a restart using only the lifecycle tie cursor.
- Prove schema-v2 `start_admitted` accepts the due-selected account while `READY.tie_after_account_key` names another account, and that the cursor affects equal-deadline ties only.
- Prove the admitted visit nonce becomes the exact schema-v2 `ACTIVE.run_nonce`, with no independently generated competing nonce.
- Assert each admitted account follows `START,H0,B0,SAFE_HOME,STOP` and that no two configured emulator processes overlap.
- No native input, ADB, screenshots, or spending.
- Every not-yet-due, deferred, or lane-incomplete account remains explicit and cannot silently disappear.
- Global action/failure/wall-clock limits span all slots.
- Crash after intent, input start, input completion, and outcome persistence never replays a transaction.
- Crash at every admission/`ACTIVE`/stop/`READY`/successor-finalization boundary deterministically releases, blocks, or finalizes one visit without duplicate work or a lost successor.
- A successor-plan persistence failure uses the emergency owned-stop proof, leaves exact blocked `ACTIVE`, and cannot expose another admission.
- A positively free builder at visit end produces one immediate fair-tail successor without a random offset; a future completion keeps one persisted 0–60 minute offset; unknown state defers.
- Home failure, Builder failure, quarantine, and a limit between lanes preserve a bounded non-starving cursor.


### Slice 3 — Home observer + one safe transaction

- Compare the three pinned donors, copy/adapt the largest compatible Home observation/policy seam, and use historical Clash Rush code only if differential tests prove it.
- Observe lab and all builders before planning.
- Run one owner-approved normal-resource transaction with postcondition and audit.
- One action cannot monopolize a round.

### Slice 4 — Builder Base read-only vertical slice

- Positively navigate to Builder Base and return Home.
- Read Builder Hall, builders, Star Laboratory, and one named candidate.
- Reconcile observations with `BuilderBasePlanner` without input beyond reversible navigation.

### Slice 5 — Builder Base action vertical slices

Promote separately:

1. Star Laboratory research;
2. named building upgrade;
3. first-stage attack deployment;
4. second-stage detection/deployment;
5. result/return recovery. Cart, chest, daily reward, and defense reward claims remain excluded.

The first Builder Base release is limited to Baby Dragon attacks, Star Laboratory research, and named upgrades. New-building placement is a later separately approved slice.

Each needs a synthetic test, exact-index privacy scan, independent review, and one bounded owner-approved live transaction.

### Slice 6 — five-account release candidate

- Ship and exercise owner-facing Setup, Run, Pause, Resume, Stop, and Status commands plus Windows shortcuts/wrappers.
- Require no source or JSON edits for ordinary start/stop control after one-time setup.
- Verify Stop blocks new transactions, releases held input, safely closes the owned instance, and preserves a resumable cursor.
- Verify Pause reaches the same safe closed-emulator boundary but keeps the scheduler alive and idle until Resume.

- Every configured account and both lanes serviced.
- Per slot: lab checked, builder count checked, planner decision recorded, outcome recorded.
- Each account stops within ten minutes, with no Home or Builder lane starvation; a remaining free builder requeues immediately behind already older eligible work.
- All immediately actionable work attempted within its lane budget; unfinished work remains durable for the next visit.
- Every battle has a bounded finish/recovery path before rotation.
- No full frames retained locally; the only external full-frame path is the separately reviewed blocker-only upload to the exact mapped private Discord account channel.
- Further transaction admission is automatically revoked after every exit; no owner file edit is required.

## Discord fleet control and due-account scheduling

`research/discord-fleet-control-plan.md` is the binding plan for one dedicated central Discord service, multiple uniquely named 1–10-account troop runners after schema-v2 promotion, private per-runner enrollment, explicit troop/account autocomplete, scoped Troop/Fleet Captain roles, account-channel routing, and the persistent builder-triggered due queue. Discord adapts the same local Setup/Run/Pause/Resume/Stop/Status service and never creates a second lifecycle or scheduler authority.

## What we will not copy

- CoC_Bot ADB/minitouch, external Groq OCR, unauthenticated web server, random upgrades, blind collection grid, forced app restart completion, or independent endless workers.
- ClashAutomation first-window binding, coordinate account switching, fixed-offset confirmation fallbacks, recursive long passes, or unwired Builder lab behavior.
- BasePilot Supercell-ID switching, random input jitter, blind chest taps, full-frame debug persistence, or recovered/decompiled scheduler code.
- NX-ClashClient source (unlicensed), PostMessage/ADB transport, random/blind deployment/recovery, cloud frames, unsafe wall/donation executors, tracked secrets, or full-frame debug persistence.

## Immediate build recommendation

Finish the separately reviewed sanitized `WINDOW_BINDING` diagnostic and owner-controlled reconciliation of the current blocked exact-five Slice 1 before implementing schema v2, the scheduler, Discord, or gameplay. No further BlueStacks launch is authorized by this plan change.
