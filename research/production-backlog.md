# Production backlog

P1 observability/operator Setup is required before the first Home/Builder production release. P2 and later features follow that release unless a read-only API slice is promoted independently. Backlog status does not authorize live input or asset redistribution.

## P1 — observability and operator UI

- Guided Setup that writes one ignored installation profile and puts tokens/runner credentials in Windows Credential Manager.
- Normal per-team and per-account structured audit views.
- Opt-in two-to-five-minute diagnostic mode with transient full-frame local preview, red crosshair/target box, OCR live-preview panels, state/instance/timer displays, and optionally retained reviewed narrow crops only.
- Optional in-memory OCR diagnostic panel attached to a blocker message under the same exact-channel/no-retry privacy protocol.
- Clean-room dashboard inspired by useful UX patterns in NX-ClashClient; no source copying.
- `Conservative`, `Balanced`, and `Responsive` performance profiles inspired by the MIT Auto Farmer UX. Profiles may change cadence, worker budget, cache lifetime, and preview rate, but never detector thresholds, required evidence, postconditions, settling bounds, or fallback policy.

## P2 — official API and Discord information

- Per-clan configurable CWL/war notifications, disabled by default. End users select exact channel, mention mode (`none`, role, or `everyone`), and reminder thresholds.
- CWL remaining attacks derived from `attacksPerMember` and completed member attacks.
- Player/clan/ranking/stat commands from the official API, with optional attributed ClashKing historical data.
- Donation-counter tracking and leaderboards from durable API snapshots.
- These handlers remain read-only and have no runner-control, spending, or quarantine-write capability.

## P3 — Home attack expansion

- Deterministic deployment seam selected after comparing BasePilot, CoC_Bot, and ClashAutomation; use NX only as an unlicensed concept reference.
- Typed troop, spell, siege, and hero deployment plan with no random/blind fallback.
- Hero abilities scheduled from each positively confirmed hero drop using monotonic per-hero deadlines; configurable delay does not start after unrelated spell/troop work.
- Positive card identity, deployed/depleted state, valid-zone proof, postcondition, and unconditional release for every input.
- Fail-closed smart loot filtering with typed `ACCEPT|REJECT|UNKNOWN`, fresh-frame agreement, scan budgets that defer/stop rather than force an attack, and no Dark Elixir requirement unless separately authorized.
- A dedicated Sneaky Goblin strategy only after generic scout/deployment safety passes: positively identified card, observed targets/deployable zones, deterministic batches, remaining-card evidence, and verified battle exit. No blind slot, jitter, or “anti-ban” claim.

## P4 — social donations

- Request donations in game only from a positively proved clan-chat/request state.
- Send donations only after exact requested-unit parsing, configured unit allowlist, army/training reserve, donation budget, and positive postcondition.
- No auto-reinforcement medal spending, gems, training fallback, or “first colorful card” behavior.
- Official API donation counters validate trends but cannot prove one specific UI donation transaction.

## P5 — daily resource cart

- Detect the Home Village loot cart as a world object, not a fixed button.
- Candidate source art such as ClashKingAssets `loot_cart.webp` may bootstrap detector research but cannot authorize runtime interaction or be redistributed automatically.
- Require a live-version/geometry-calibrated detector, hard negatives against similar carts/buildings/decorations, resource-capacity handling, claim authorization, and post-claim resource/cart disappearance proof.
- Cart claiming stays without an executor until separately approved; it is a reward/resource action and cannot be smuggled into popup recovery.

## P6 — optional read-only diagnostic integration

- Consider a separate local MCP server only for sanitized Status, recent reason codes, and log queries.
- It must hold no Discord token, issue no runner commands, expose no images/private identifiers, and remain outside the production Discord routing path.
