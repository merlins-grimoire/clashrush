# Runner comparison: five BlueStacks accounts + Builder Base

**Pinned review date:** 2026-09-12
**Decision:** Use **CoC_Bot as the primary behavioral donor**, but do **not** fork any repository wholesale. Build a small coordinator around native Windows input, borrowing focused mechanics from all three.

## Executive conclusion

None of the three repositories is a safe drop-in foundation.

- **CoC_Bot is the closest functional match** because it already discovers named BlueStacks instances, can launch one process per instance, and has the broadest Builder Base implementation. Its ADB/minitouch transport, independent endless workers, shared-state risks, and unsafe fallbacks must be replaced.
- **ClashAutomation supplies the best native-Windows substrate**: local Win32 input/capture, normalized coordinates, local vision, and the only meaningful test suite among the donors. Its Google Play Games window discovery, account switching, and scheduling are not suitable.
- **BasePilot is the best recovery and Home Village mechanics donor**: popup/reconnect/post-battle recovery, builder-menu parsing, guarded upgrades, wall batches, and useful Builder Base attack/return flows. It is not a five-instance runner and its source contains substantial recovered/decompiled control flow.

The resulting runner should therefore be a **hybrid clean architecture**, not a renamed copy. CoC_Bot determines the feature inventory; ClashAutomation contributes Windows adapter patterns; BasePilot contributes recovery and mature interaction sequences; and the required Rush Bible behavior is extracted as a contract. Published Clash Rush is unreliable historical evidence whose narrow policy or input candidates require donor comparison and differential proof.

## Decision matrix

Scores are 0–5. “Native input” measures compatibility with the required no-ADB BlueStacks design.

| Repository | Five accounts | Builder Base | Maintainability | Native input | Policy integration | Simple average |
|---|---:|---:|---:|---:|---:|---:|
| BasePilot | 1 | 2 | 1 | 1 | 3 | 1.6 |
| CoC_Bot | 2 | 3 | 1.5 | 1 | 2 | **1.9** |
| ClashAutomation | 2 | 2 | 2 | 3 | 2 | **2.2** |

The numerical winner is ClashAutomation because of transport quality, but **CoC_Bot wins the functional-foundation decision**: it is the only donor with both named BlueStacks instances and substantial Builder Base building/laboratory behavior. The planned adapter replacement makes its low transport score acceptable. We should not inherit its process model unchanged.

## Repository findings

### 1. CoC_Bot — primary behavioral donor

**Pinned revision:** `a5c943afed0ed3b9abedbbc228b0889145ecaf24`
**License:** MIT (`references/CoC_Bot/LICENSE`)

**Why it is closest**

- Resolves exact BlueStacks display names from `bluestacks.conf` and launches `HD-Player.exe --instance ...` (`src/utils.py:847-976`).
- GUI can start one operating-system process per configured instance (`src/launch.py:27-67`).
- Implements separate Home and Builder builder/lab regions (`src/upgrader.py:24-28,105-128`).
- Implements Builder Base boat navigation, collection, buildings, Star Laboratory research, and attacks (`src/utils.py:611-670`; `src/upgrader.py:142-178,779-1124,1174-1212`).
- Has named Home and Builder upgrade methods that can be adapted into target-specific executors (`src/upgrader.py:424-527,644-737,871-959,1036-1124`).

**Why it cannot be used directly**

- Input, screenshots, app lifecycle, and recovery are ADB/minitouch based (`src/utils.py:522-588,1371-1570,1599-1609`). ADB remains prohibited.
- Workers run independent endless loops rather than a durable fair coordinator (`src/coc_bot.py:14-75`).
- No exact-five contract, restart cursor, fair transaction budget, or safe shared-state model.
- Shared cache snapshots can overwrite each other across processes (`src/utils.py:732-762`).
- Random upgrades, blind corner taps, blind collection grids, forced app restarts after attacks, and swallowed exceptions violate the target safety model.
- No automated test suite.

**Copy/adapt only**

1. BlueStacks configuration parsing and named-instance launch concepts.
2. Separate Home/Builder HUD regions.
3. Boat-navigation and Builder Base recognition sequence.
4. Builder building and Star Laboratory vocabulary/flow.
5. Occupied troop-slot discovery and named executor structure.

### 2. ClashAutomation — native adapter and test-pattern donor

**Pinned revision:** `c41fe12a6df051e241c695b71b6859286e24c612` (prefix `c41fe12a6df0`)
**License:** MIT (`references/ClashAutomation/LICENSE`)

**Strong parts**

- Native Win32 `PostMessage` input and `PrintWindow(..., 2)` capture (`utils/game_window_controller.py:107-126,182-239,274-299`).
- Percentage-coordinate scaling and injected controller/action construction (`utils/settings.py:148-175,447-478`; `utils/clash_base.py:15-91`).
- Local OpenCV/Tesseract; no cloud OCR in the dependency set.
- Home attack-screen and missing-anchor guards, bounded enemy search, popup/reload concepts.
- Real Home and Builder attack/build/research routines (`home_base_actions.py`; `builder_base_actions.py`).
- A small donor test tree exists, but its clean-checkout collection currently fails while initializing `config.toml`; no passing count is used as evidence for this decision.

**Weak parts**

- Targets Google Play Games/CROSVM and picks the first matching Clash window, not a verified BlueStacks instance (`config.template.toml:4-8`; `utils/game_window_controller.py:41-73`).
- Sequential coordinate-based account switching; no durable fairness cursor (`main.py:96-142,243-275`).
- One account can perform dozens of attacks before rotation.
- Builder laboratory execution exists but is not called by production orchestration (`builder_base_actions.py:352-375`; `main.py:231-236`).
- Fixed-offset suggestion selection, confirmation fallbacks, recursive flows, and broad exception swallowing are unsuitable for the transaction boundary.
- Privacy ignores only the first two account configuration filenames; additional account files could be committed.

**Copy/adapt only**

1. Window-controller interface and normalized geometry.
2. `PrintWindow` capture and local vision patterns.
3. Missing-image and positive-screen guards.
4. Bounded Builder loop tests and repeated-deployment geometry only. Its 60-second second deployment is blind and is **not** a second-stage detector.
5. Per-account configuration merge concept, with a strict private schema replacing its files.

### 3. BasePilot — recovery and mature Home mechanics donor

**Pinned revision:** `e17c23e88cff58047123d66747c937d3bbf8f815`
**License:** MIT (`references/BasePilot/LICENSE`)

**Strong parts**

- Native Win32 message input and `PrintWindow` capture concepts (`app/services/input.py`; `app/services/window.py`).
- Strong popup, reconnect, result-screen, and delayed-return recovery (`app/core/bot.py:1423-1475`).
- Builder-menu OCR, resource classification, target-name gating, cooldowns, and builder-count postconditions (`app/core/upgrade_menu.py`; `app/core/upgrader.py`).
- Mature wall batching (`app/core/bot.py:624-765`).
- Useful Builder Base Baby Dragon attack, hero deployment, and return flows; cart/reward behavior is present but excluded from reuse (`app/core/bot.py:894-1187,1658-1697`).

**Weak parts**

- Explicitly built for one Google Play Games/CROSVM window, not BlueStacks (`README.md`; `app/services/window.py`).
- Multi-account means sequential Supercell-ID switching inside one game session, which is excluded from the target design (`app/core/bot.py:1478-1509`).
- No durable fair cursor or instance supervisor.
- Builder Base upgrades and multi-run are explicitly unfinished; lab research remains manual.
- Full debug frames and usernames can be logged.
- No tracked tests; large 1,895-line orchestrator and numerous recovered/decompiler markers reduce confidence.

**Copy/adapt only**

1. Popup/reconnect/post-battle recovery state machine.
2. Home builder-menu parsing and positive transaction guards.
3. Wall batch mechanics and cooldown/quarantine concepts.
4. Builder Base attack/return flows, excluding cart and reward claims.
5. Idle/resume behavior.

## Required architecture decision

Run **exactly one BlueStacks instance at a time**. The coordinator selects the oldest due account, positively binds its PID/HWND/account, services its Home and Builder Base lanes, returns to a safe screen, stops that instance, and commits the queue outcome. A restart-safe round-robin cursor breaks equal-deadline ties only. This matches the host's capacity and eliminates cross-instance cursor, capture, and memory contention.

A safe equal-deadline proof order is:

`H0 → B0 → H1 → B1 → H2 → B2 → H3 → B3 → H4 → B4 → repeat`

where `H` is one bounded Home lane and `B` is one bounded Builder Base lane during the same instance session. Non-equal deadlines run oldest-due first instead. A battle is completed or reconciled before queue advancement. If no safe game screen can be proved, the coordinator emits no further game input, terminates only the exact bound emulator process tree, verifies that the PID tree and HWNDs disappeared, and refuses to start another slot if shutdown cannot be proved. Global action, failure, and wall-clock bounds still apply across the complete troop run.

## Final recommendation

**Primary donor:** CoC_Bot, for BlueStacks and Builder Base behavior.
**Transport donor:** ClashAutomation, rewritten for exact BlueStacks PID/HWND binding.
**Recovery/Home donor:** BasePilot.
**Policy selection:** extract the Rush Bible contract, compare the pinned donors, and admit historical Clash Rush planner code only after differential proof.

Do not copy any donor’s scheduler, unsafe fallback clicks, account-switching UI, random upgrade selection, ADB transport, external OCR, or persistent full-frame diagnostics.
