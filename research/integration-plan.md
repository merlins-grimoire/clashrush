# Integration plan

## Target outcome

A Windows/BlueStacks runner that keeps five named emulator instances available, services all five fairly, and runs Home Village and Builder Base using copied/adapted proven donor mechanics behind local safety controls.

The new runner is not a wholesale fork. It is a small coordinator with explicit adapters and copied, attributed mechanics from the three MIT donors. Published Clash Rush is historical first-party evidence and a source of narrow primitives/pure policy only after re-verification; it is not presumed to contain known-good end-to-end tactics.

## Reproducible source seals

| Source | Canonical URL | Commit | Tree |
|---|---|---|---|
| BasePilot | `https://github.com/efebolukbasi/BasePilot` | `e17c23e88cff58047123d66747c937d3bbf8f815` | `d038e2b99cb440002199aa5a1b9cd64474dda95f` |
| CoC_Bot | `https://github.com/m24842/CoC_Bot` | `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | `d79368fbe550036f1542883f18434e318016b279` |
| ClashAutomation | `https://github.com/calebmwelsh/ClashAutomation` | `c41fe12a6df051e241c695b71b6859286e24c612` | `b549901e7ca8b871a17265517d730f0b3c84a618` |
| Published Clash Rush policy/Home engine | `https://github.com/merlins-grimoire/autoclasher` | `949497bf0a543a43ec6ef39a8c897e366bc10362` | `12ded3ccdb9f27610d9f07fdc7a6ad5b9fd74cc0` |

The dirty legacy checkout is not a reproducible donor. Only files from the published Clash Rush commit above may be copied directly. That seal includes `builder_base_policy.py`; later uncommitted Builder navigation/reconciliation work is excluded.

## Runtime model

### One live instance, five durable slots

Only one BlueStacks instance runs at a time. One coordinator launches the next configured slot, verifies its process/window/account binding, services Home and Builder Base, returns it to a positively identified safe screen, stops it, and advances the persisted cursor. The other four instances remain stopped.

This avoids host memory/CPU pressure and removes cross-instance cursor/capture races. Every transaction still obtains fresh evidence after acquiring the instance lease and immediately before input.

### Fair service schedule

Use two work queues per account:

`Home[0], Builder[0], Home[1], Builder[1], …, Home[4], Builder[4]`

Each account visit has a hard **10-minute** wall-clock budget. Within that visit, drain all immediately actionable Home and Builder work that can be completed safely. Reserve the first five minutes for Home and the remaining five minutes for Builder so one village cannot consume the other village's opportunity; unfinished work resumes on the next cycle. A transaction-specific continuation may exceed its lane boundary only to reach a safe stopping screen, never the ten-minute account deadline except for fail-closed cleanup. Persist:

- next slot and village lane as one atomic `(slot, lane)` cursor;
- per-slot readiness/defer time;
- active battle type and deadline;
- consecutive failure/quarantine state;
- current account/tag-digest binding;
- Home wall-batch progress;
- last successful Home and Builder observations.

A battle must finish or reconcile before rotation. If no safe game screen can be proved, emit no more game input, release every held key/button, terminate only the exact bound emulator process tree, and verify the PID tree and HWNDs disappeared. If shutdown cannot be proved, stop the complete fleet and do not start the next slot.

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
Operator Setup / Run / Stop / Status entry points
  -> five-slot config + internal safety gates + bounds
  -> Fleet coordinator (durable scheduler)
       -> BlueStacks registry (exact process/HWND binding)
       -> Lifecycle supervisor (start/stop/process-tree proof)
       -> Capture adapter (memory-only full frame)
       -> Screen classifier
       -> Home observer -----------+
       -> Builder observer --------+--> Clash Rush planners
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

Adapt ClashAutomation’s controller abstraction, normalized coordinates, and `PrintWindow` capture, but not its unproven CROSVM `PostMessage` transport. Pin the physical foreground-bound BlueStacks gesture implementation to published Clash Rush commit `949497bf0a543a43ec6ef39a8c897e366bc10362`. Required properties:

- no ADB/minitouch imports or subprocess calls;
- a single coordinator-owned native-input path;
- verified PID/HWND immediately before every gesture;
- explicit down/up completion and unconditional key release;
- source→destination state proof;
- no random jitter or blind retry.

Before any gameplay slice, run a transport spike proving exact BlueStacks capture, focus/binding, click, drag, scroll, hold, and unconditional release on a reversible screen. If the spike fails, stop; do not transplant higher-level donor flows.

### 3. `RecoveryStateMachine`

Adapt BasePilot’s popup, disconnect, battle-result, and delayed-return flows. Every recovery gesture requires a positive source-state detector. Unknown or persistent near-black states are deferred/quarantined rather than clicked through.

### 4. Observers

#### Home observer

Reuse only published-seal Clash Rush world-export reconciliation and proven Home detectors where possible:

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
- the published `BuilderBasePlanner`, plus new locally reviewed observation/reconciliation types.

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

### 5. Clash Rush planners

Copy only pure policy/model modules from the published Clash Rush seal first; do not transplant live UI orchestration:

- `models.py`
- `policy.py` and `policy_data.py`
- `mapping.py` and any mechanically required immutable data loaded by `policy.py`
- `upgrade_planner.py`
- `suggested_upgrades.py`
- `cycle.py`
- `builder_base_policy.py`
- mechanically required reconciliation/model helpers

The published `builder_base_policy.py` is a reproducible pure-policy donor. Builder Base navigation and observation reconciliation are not taken from dirty legacy files; implement and review those boundaries in this repository.

Add one closed adapter type before any executor exists:

`ActionIntent(slot, village, kind, target_id, resource, max_cost, reason_code)`

Adapters translate `RushPlanner`, `cycle.py`, and the published `BuilderBasePlanner` outputs into this exact vocabulary. Unknown recommendation/result types fail closed; planners never call executors directly.

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

The typed resource allowlist distinguishes `HOME_GOLD`, `HOME_ELIXIR`, `BUILDER_GOLD`, and `BUILDER_ELIXIR`. Each normal-resource type requires explicit owner enablement. `HOME_DARK_ELIXIR`, `GEMS`, real-money purchases, purchase fallbacks, reward/chest/cart claims, social/clan/war/donation, Supercell account switching, evasion, and CAPTCHA handling have no executor implementation.

### Crash-safe action boundary

Persist a write-ahead transaction record atomically before input: `PLANNED → INTENT_RECORDED → INPUT_STARTED → INPUT_COMPLETED → CONFIRMED|FAILED|UNCERTAIN`. Cursor/lane advancement occurs in the same SQLite transaction as the terminal outcome. On restart:

- reconcile `INPUT_STARTED`/`INPUT_COMPLETED` from read-only game state;
- never replay a spend, deployment, or claim whose outcome is uncertain;
- quarantine unresolved spend/deployment intents for manual review;
- resume only after binding and source-state proof.

## Donor map

| Capability | Primary donor | Treatment |
|---|---|---|
| BlueStacks instance discovery/launch | CoC_Bot | Copy structure; remove all ADB fields and commands |
| Five-instance process inventory | CoC_Bot | Replace independent loops with one coordinator that launches one named instance at a time |
| Native input transport | Published Clash Rush `949497b…` | Copy the proven foreground-bound BlueStacks path and re-prove it in Slice 1 |
| Capture/interface/geometry | ClashAutomation | Adapt interface/geometry only; do not assume CROSVM `PostMessage` works on BlueStacks |
| Home builder menu and walls | BasePilot + published Clash Rush seal | Reuse positive detectors and guarded mechanics |
| Home attacks | Published Clash Rush seal + ClashAutomation | Keep verified slot geometry and positive scout transitions |
| Popup/reconnect/result recovery | BasePilot | Convert each branch to positive state transitions; omit every reward/claim branch |
| Builder boat/HUD/build/lab | CoC_Bot | Adapt mechanics to native input and typed observations |
| Builder battle | BasePilot + local implementation | Reuse guarded deployment/return ideas; build positive two-stage detection locally; exclude cart/rewards |
| Upgrade decisions | Published Clash Rush seal | Pure reproducible Home and Builder planners only |
| Scheduler, safety, audit | Published Clash Rush seal | Rebuild cleanly around five slots and two village lanes |

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

### Slice 2 — dual-lane dry scheduler

- Emit `H0,B0,H1,B1,…,H4,B4` twice across a restart.
- Assert lifecycle order `START0,H0,B0,SAFE_HOME,STOP0,START1` and that no two configured emulator processes overlap.
- No native input, ADB, screenshots, or spending.
- Deferred slot remains explicit and cannot silently disappear.
- Global action/failure/wall-clock limits span all slots.
- Crash after intent, input start, input completion, and outcome persistence never replays a transaction.
- Home failure, Builder failure, quarantine, and a limit between lanes preserve a bounded non-starving cursor.

### Slice 3 — Home observer + one safe transaction

- Import pure Clash Rush Home policy.
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

- Ship and exercise owner-facing Setup, Run, Stop, and Status commands plus Windows shortcuts/wrappers.
- Require no source or JSON edits for ordinary start/stop control after one-time setup.
- Verify Stop blocks new transactions, releases held input, safely closes the owned instance, and preserves a resumable cursor.

- All five slots and both lanes serviced.
- Per slot: lab checked, builder count checked, planner decision recorded, outcome recorded.
- Each account stops within ten minutes, with no Home or Builder lane starvation.
- All immediately actionable work attempted within its lane budget; unfinished work remains durable for the next visit.
- Every battle has a bounded finish/recovery path before rotation.
- No full frames retained; no private identifiers in public artifacts.
- Kill switch returns off after every exit.

## What we will not copy

- CoC_Bot ADB/minitouch, external Groq OCR, unauthenticated web server, random upgrades, blind collection grid, forced app restart completion, or independent endless workers.
- ClashAutomation first-window binding, coordinate account switching, fixed-offset confirmation fallbacks, recursive long passes, or unwired Builder lab behavior.
- BasePilot Supercell-ID switching, random input jitter, blind chest taps, full-frame debug persistence, or recovered/decompiled scheduler code.

## Immediate build recommendation

Begin with **Slice 1**, adapting only CoC_Bot’s BlueStacks registry into a new typed, read-only module. Do not copy attacks or upgrades yet. The first executable artifact should list five sanitized slot indexes with verified process/window bindings and emit no game input.
