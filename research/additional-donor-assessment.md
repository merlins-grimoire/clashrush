# Additional donor assessment

Research checked 2026-09-14. The repositories are stored as ignored local references and sealed below. This assessment does not authorize live execution or change the current `ACTIVE/WINDOW_BINDING` lifecycle state.

## Frozen reference inventory

| Source | Commit | Tree | License decision |
|---|---|---|---|
| `keshav-x/coc-bot` | `69392ab8ca58cd9b7725bf4106e650dbcd5e73bd` | `542d2a9908bbb7f49559c5f29175a020b08cccb9` | No root source-code grant; study only |
| `alisakkaf/Clash-of-Clans-Bot-Auto-Farmer` | `0120019918758e45feddf84fd5522e31cc6fd578` | `acb9709289cc1030787dc87d554ff1c16562bde4` | MIT; attribution required |
| `N1xUser/NX-ClashClient` | `e4c79fd671912714d48fc04c2ff9ffb60e09ef50` | `dcfc43125e3e74dbca52ee00bb0b743a4e3c7fbd` | No license grant; clean-room concepts only |
| `efebolukbasi/BasePilot` | `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | `0553306bfd30a32e31e427a0b77ff22c41f55901` | MIT; attribution required |

`keshav-x/coc-bot` contains files named `license.py`, but these implement commercial activation rather than granting rights to copy the source. Its purchase code also says “All rights reserved,” so no implementation is eligible for reuse without permission.[1]

## keshav-x/coc-bot

### Smart loot filtering

The useful conceptual seam is its separation of `LootFilterConfig`, an evaluated decision, and session statistics. It supports minimum Gold/Elixir/Dark-Elixir/total thresholds and AND/OR-style policy selection.[2]

Do not copy or reproduce its behavior directly. Its max-skip path can force an attack before unreadable loot is rejected, and “dead base” is treated as a loot-threshold proxy rather than proven collector state. Our clean-room version should:

1. return a typed `ACCEPT | REJECT | UNKNOWN` decision;
2. require all policy-required loot fields to be readable and agreed across fresh frames;
3. treat scan-budget exhaustion as `DEFER/STOP`, never “attack anyway”;
4. treat Dark Elixir as first-release scope for hero upgrades and laboratory research, requiring its own explicit policy field and separately promoted executors before use; pet upgrades remain deferred;
5. bind the accepted loot observation to the same scout generation used by deployment.

### Sneaky Goblin farming

The donor recognizes a Sneaky Goblin card but then uses configured diamond geometry, randomized directions/jitter, and a blind fallback slot when recognition fails.[3] That executor is rejected. The useful requirement is narrower: a dedicated Sneaky Goblin strategy should use positively identified cards, observed deployable edges/targets, deterministic batches, measured remaining-card state, and a verified stop/return condition.

### “Anti-ban” architecture

The anti-ban module defines random breaks, Bézier paths, Gaussian points, and action-rate concepts.[4] Source inspection found only partial production wiring and no evidence that this prevents or reduces bans. We will not describe randomization as anti-ban, implement evasion, or weaken deterministic evidence gates. Safe ideas that remain valid without the framing are:

- enforce explicit action-rate and continuous-runtime safety ceilings;
- insert interruptible rest periods only as operator-configured workload limits;
- maintain monotonic action counters and visible pause state;
- never use randomness to choose a control, target, deployment zone, or recovery action.

### Discord and operational ideas

The webhook module provides small event-specific payload builders, but stores the webhook credential in plaintext, accepts arbitrary URLs, ignores response status, and sends through untracked daemon threads.[5] We will reimplement only the event vocabulary behind the planned durable exact-channel Discord outbox: run start/stop, accepted/skipped scout summary, confirmed raid result, blocker, and bounded health notification. The production bot will use protected credentials, destination allowlists, idempotent delivery state, restricted mentions, redaction, and acknowledgement handling.

### Other useful concepts

Study, then independently implement:

- local OCR batching and multi-frame agreement;
- unknown-builder/HUD states blocking upgrade selection;
- cross-scan cost/resource corroboration and final row re-find;
- immutable run-plan data plus cooperative cancellation;
- rotating logs with retention.

Reject:

- `SendMessage`/`PostMessage` input;
- fuzzy window fallback;
- random or blind deployment/recovery;
- forced attack after scan exhaustion;
- wall success recorded after a payment click without state/resource proof;
- unbounded screenshot persistence;
- commercial activation/hardware fingerprinting and plaintext secrets.

## alisakkaf Auto Farmer

The source is MIT licensed, but its runtime is ADB-centered and bundled ADB binaries are not candidates for execution or redistribution.[6]

### Performance profiles

`core/settings.py` defines `ultra`, `high`, `medium`, `low`, and `smart_default` presets spanning scheduler tick rate, input delays, swipe duration, template scales, OCR cadence/workers, and recognition thresholds.[7] The presentation is useful; the coupling is not. Lower profiles reduce recognition thresholds and can increase false positives, `ocr_workers` is not actually consumed, and “smart” selection is based mainly on CPU count and cached geometry rather than measured detector latency or GPU capability.

Adopt the UX as two independent versioned controls:

1. **Performance profile** — capture/OCR cadence, cache lifetime, worker budget, and preview refresh rate.
2. **Detection policy** — sealed detector versions, required confidence/agreement, supported geometry, and fail-closed thresholds.

A performance profile may reduce polling frequency or concurrency but must never lower evidence thresholds, skip loot/timer OCR, disable postconditions, shorten required settling time, or enable a fallback. Setup should offer `Conservative`, `Balanced` (default), and `Responsive`, run a local benchmark, show estimated CPU/GPU cost, and stage profile changes only at a stopped clean boundary.

Useful clean-room/MIT candidates include manifest-backed local assets, resolution-relative ROIs, multi-scale matching, cache invalidation, explicit attack context, and separated perception/planning skills.[8] Before copying any source, isolate a complete dependency seam, preserve the MIT notice, replace ADB with the one physical foreground-input adapter, remove fabricated geometry and random fallbacks, and add synthetic safety tests.

Reject:

- all ADB transport and bundled opaque binaries;
- warning-only state transitions;
- missing-template continuation;
- fabricated centered base boxes/deployment lines;
- random jitter and fixed-coordinate macro replay;
- automatic EasyOCR model downloading;
- profiles that trade correctness for speed.

## BasePilot update

The reference advanced one commit from `e17c23e…` to `4ede1efd…`; the new revision remains MIT licensed.[9]

Adopt after local RED tests:

- cross-scan currency disagreement rejection;
- final upgrade-row re-find requiring affordability, cost/resource agreement, label agreement, and stable position;
- saturated-blue magic-item button filtering;
- improved bottom-bar price sampling and red-cost rejection.[10]

Study the timed-reward behavior only. Its valuable pattern is to release held input, avoid selecting a reward, wait interruptibly for the game-controlled overlay to close, reacquire a fresh frame, and relocate displaced controls.[11] It is not safe to transplant as-is because overlay absence is not positive destination proof, long drag segments can continue through an overlay, and the new seasonal crafting path expands spending without our separate authorization boundary.

The BasePilot donor map remains unchanged: it is still the primary guarded Home/recovery reference, now pinned at `4ede1efd220ffc79a5b490cfd3788b44d2584da4`. Rewards, crafting, random input, and full-frame debug persistence remain excluded.

## NX-ClashClient reference

The previously audited source is now also saved under ignored `references/NX-ClashClient` at the exact unlicensed commit/tree above.[12] It remains a clean-room concept source for OCR live preview, normalized ROI calibration, dashboard information architecture, deterministic deployment planning, and per-hero ability deadlines. Its source, `PostMessage`, random/blind paths, cloud frames, wall/donation executors, tracked secrets, and full-frame debug storage remain rejected.

## Resulting donor policy

1. Keep BasePilot, CoC_Bot, and ClashAutomation as the required pre-slice comparison set.
2. Add the updated BasePilot guards to the next Home-upgrade comparison.
3. Treat `keshav-x/coc-bot` and NX-ClashClient as unlicensed study-only references.
4. Permit Auto Farmer MIT seams only after complete-dependency review, attribution, replacement of ADB, and local fail-closed tests.
5. Add performance profiles before gameplay production, but never let a performance choice weaken recognition, transaction, privacy, or lifecycle gates.
6. Build Sneaky Goblin farming as a separate later attack strategy after generic scout/deployment safety is proven; no random/blind fallback.
7. Reject “anti-ban” claims and evasion behavior. Runtime limits and pauses are safety/workload controls, not ban prevention.

## Sources

[1] https://github.com/keshav-x/coc-bot/tree/69392ab8ca58cd9b7725bf4106e650dbcd5e73bd — keshav-x/coc-bot frozen source
[2] https://github.com/keshav-x/coc-bot/blob/69392ab8ca58cd9b7725bf4106e650dbcd5e73bd/app/core/loot_filter.py — coc-bot loot filter
[3] https://github.com/keshav-x/coc-bot/blob/69392ab8ca58cd9b7725bf4106e650dbcd5e73bd/app/core/strategies.py — coc-bot strategies
[4] https://github.com/keshav-x/coc-bot/blob/69392ab8ca58cd9b7725bf4106e650dbcd5e73bd/app/services/antiban.py — coc-bot anti-ban module
[5] https://github.com/keshav-x/coc-bot/blob/69392ab8ca58cd9b7725bf4106e650dbcd5e73bd/app/services/webhook.py — coc-bot webhook module
[6] https://github.com/alisakkaf/Clash-of-Clans-Bot-Auto-Farmer/tree/0120019918758e45feddf84fd5522e31cc6fd578 — Auto Farmer frozen source
[7] https://github.com/alisakkaf/Clash-of-Clans-Bot-Auto-Farmer/blob/0120019918758e45feddf84fd5522e31cc6fd578/core/settings.py — Auto Farmer performance profiles
[8] https://github.com/alisakkaf/Clash-of-Clans-Bot-Auto-Farmer/blob/0120019918758e45feddf84fd5522e31cc6fd578/vision/screen_reader.py — Auto Farmer screen reader
[9] https://github.com/efebolukbasi/BasePilot/tree/4ede1efd220ffc79a5b490cfd3788b44d2584da4 — BasePilot v1.0.1 source
[10] https://github.com/efebolukbasi/BasePilot/blob/4ede1efd220ffc79a5b490cfd3788b44d2584da4/app/core/upgrader.py — BasePilot upgraded selection guards
[11] https://github.com/efebolukbasi/BasePilot/blob/4ede1efd220ffc79a5b490cfd3788b44d2584da4/app/core/battle_rewards.py — BasePilot timed reward handler
[12] https://github.com/N1xUser/NX-ClashClient/tree/e4c79fd671912714d48fc04c2ff9ffb60e09ef50 — NX-ClashClient frozen source
