# Clash ecosystem assessment

Research checked 2026-09-14. This file records candidate sources and decisions; it does not authorize copying code or assets. Every adopted source must be pinned, licensed, attributed, adapted behind the local safety contracts, and independently tested.

## Official Clash of Clans API

Use the official API as a read-only telemetry source, not as gameplay authority.

Useful endpoints and data:

- CWL group discovery and round war tags: `GET /clans/{clanTag}/currentwar/leaguegroup`.
- CWL war state, roster, `attacksPerMember`, and completed attacks: `GET /clanwarleagues/wars/{warTag}`. Remaining attacks are derived from the configured clan member's completed attack count; they are not guessed.
- Normal current war: `GET /clans/{clanTag}/currentwar`.
- Clan roster: `GET /clans/{clanTag}/members`, including current donation and received-donation counters.
- Player profile: `GET /players/{playerTag}`, including public progression, trophy, war-star, troop, hero, spell, and equipment data.
- Home and Builder Base location rankings, plus historical Legend/ranked data.

The API does **not** expose free builders, builder timers, active building upgrades, resource balances, loot-cart state, claimable rewards, or a write API for gameplay. Those remain local UI observations. Donation counters support snapshots/deltas and leaderboards, not automatic in-game donating.

The central service may poll war/API state using a token held only in its secret store. It must persist retrieval time and event keys, tolerate `429`/`503`, use bounded backoff, and never promise a freshness SLA that Supercell does not publish. CWL `@everyone` is disabled by default and configurable per clan/channel/reminder threshold; normal messages use no mention parsing, while the explicitly enabled alert alone permits the everyone mention after a fresh permission check.

Sources:

- [Official Clash of Clans developer portal](https://developer.clashofclans.com)
- [Current API documentation transcription used to inspect schemas](https://github.com/MasiaAntoine/clash-of-clan-api-doc-official)
- [Discord allowed mentions](https://discord.com/developers/docs/resources/message#allowed-mentions-object)
- [Discord rate limits](https://discord.com/developers/docs/topics/rate-limits)

## GitHub topic candidates

The [`clashofclans` topic](https://github.com/topics/clashofclans) is useful primarily for API, Discord, analytics, and asset references. It did not reveal a safe drop-in gameplay or donation executor.

### Adopt or use behind an adapter

- [ClashKingInc/clashy.py](https://github.com/ClashKingInc/clashy.py) — maintained MIT Python client for the official API. Candidate for the Python central service; preserve its and upstream `coc.py` attribution.
- [clashperk/clashofclans.js](https://github.com/clashperk/clashofclans.js) — maintained MIT TypeScript client if the central service is implemented in Node instead.
- Prefer one client stack, not both. A small owned `ClashDataProvider` interface prevents upstream schemas from becoming Discord or runner contracts.

### Study, do not adopt wholesale

- [ClashKingInc/ClashKingBot](https://github.com/ClashKingInc/ClashKingBot) — MIT reference for slash-command UX, leaderboards, war tracking, reminders, embeds, and role handling. Its full MongoDB/Redis ecosystem is unnecessary here.
- [ClashKingInc/ClashKingAPI](https://github.com/ClashKingInc/ClashKingAPI) and [ClashKingInc/ClashKingTracking](https://github.com/ClashKingInc/ClashKingTracking) — useful API/event/reconciliation designs and optional hosted historical data. Their code is GPL-3.0; consume documented HTTP endpoints rather than copying implementation unless the whole licensing consequence is accepted. Credit ClashKing for its collected data.
- [mathsman5133/donationbot](https://github.com/mathsman5133/donationbot) — MIT reference for polling donation-counter deltas, season rollover, leaderboards, and metrics. It tracks donations; it does not perform them in game.
- [r-priyam/cocjs-sample-bot](https://github.com/r-priyam/cocjs-sample-bot) — compact MIT command/API example, not a production dependency.

### Reject as gameplay donors

- [oniisancr/COC_robot](https://github.com/oniisancr/COC_robot) — performs donations but depends on ADB and older game behavior.
- [Tanmoy-Mondal-07/Python-Game-Bot](https://github.com/Tanmoy-Mondal-07/Python-Game-Bot) — permissive license but blind PyAutoGUI/anti-detection approach conflicts with the project contract.
- [maxwell142857/COC-donation-script](https://github.com/maxwell142857/COC-donation-script) — unmaintained Auto.js/accessibility and hard-coded-coordinate flow.

No surveyed donation executor meets the required request matching, inventory reserve, exact UI evidence, transaction audit, and postcondition contract. Donation automation remains a clean-room production backlog slice.

## War announcer, rankings, and MCP

- [spAnser/discord-coc-war-announcer](https://github.com/spAnser/discord-coc-war-announcer) is MIT but unmaintained since 2020. Study its event vocabulary and missing-attack presentation only. Do not copy its legacy Discord.js, authorization, shared cursor, or unacknowledged send behavior.
- [ClashKingInc/ClashKingAPI](https://github.com/ClashKingInc/ClashKingAPI) can supplement the official API for historical leaderboards/statistics through an isolated read-only adapter. Runner control and quarantine must remain a separate capability.
- [v-3/discordmcp](https://github.com/v-3/discordmcp) is rejected for the production path. The inspected prototype has no verified repository license, tests, channel allowlist, caller role checks, mention constraints, idempotency, or audit boundary; its parsed server selector is not applied to channel resolution. A deterministic Discord service is safer than inserting an LLM/MCP confused-deputy boundary. A future separately authenticated read-only diagnostic MCP may expose sanitized Status only.

## NX-ClashClient

Pinned inspection source: [N1xUser/NX-ClashClient at `e4c79fd671912714d48fc04c2ff9ffb60e09ef50`](https://github.com/N1xUser/NX-ClashClient/tree/e4c79fd671912714d48fc04c2ff9ffb60e09ef50), tree `dcfc43125e3e74dbca52ee00bb0b743a4e3c7fbd`.

**Licensing:** no license grant was found. Its code cannot be copied, modified, or redistributed. The repository itself says the current attack is broken. Use only independently reimplemented ideas.

Clean-room ideas worth adopting:

- OCR live preview showing approved processed crops, interpreted text/value, confidence, and state label;
- guided normalized-ROI calibration with live preprocessing;
- dashboard information architecture: status badges, resource/builder cards, logs, preview, and explicit feature toggles;
- deterministic four-segment deployment planning and per-hero configurable ability deadlines as concepts, rebuilt from validated troop/hero/card evidence and actual monotonic hero-drop completion timestamps.

Ideas worth studying but not trusting:

- builder-menu paging/deduplication UX;
- donation popup geometry and rescan cadence;
- wall-list editing and target presentation.

Rejected implementation:

- root/child `PostMessage` input broadcasting and ADB/Waydroid;
- fuzzy single-window binding;
- random deployment, blind recovery, and “deploy every card” fallback;
- random spell placement;
- hero timing measured after later actions rather than from hero landing;
- wall cost tolerance, fixed commits, off-by-one quantity, virtual success accounting, and missing builder/gem gates;
- auto-donate without requested-unit matching, reserve policy, budget, or outcome proof;
- cloud full-frame vision, plaintext tracked keys, and saved full-frame debug screenshots;
- debug buttons that bypass the normal running authorization.

Exact source examples:

- [OCR live preview](https://github.com/N1xUser/NX-ClashClient/blob/e4c79fd671912714d48fc04c2ff9ffb60e09ef50/src/ui/controllers/vision_controller.py)
- [deployment and hero logic](https://github.com/N1xUser/NX-ClashClient/blob/e4c79fd671912714d48fc04c2ff9ffb60e09ef50/src/core/attack.py)
- [wall flow](https://github.com/N1xUser/NX-ClashClient/blob/e4c79fd671912714d48fc04c2ff9ffb60e09ef50/src/core/upgrade.py)
- [donation flow](https://github.com/N1xUser/NX-ClashClient/blob/e4c79fd671912714d48fc04c2ff9ffb60e09ef50/src/core/donation.py)
- [capture/input implementation](https://github.com/N1xUser/NX-ClashClient/blob/e4c79fd671912714d48fc04c2ff9ffb60e09ef50/src/vision/capture.py)

## Image assets

- [Enjoyop/coc-assets](https://github.com/Enjoyop/coc-assets) is mainly incomplete historical CSV/JSON game data, has no verified license, and is not a useful PNG template source.
- [Statscell/clash-assets](https://github.com/Statscell/clash-assets) has an MIT file but stale isolated TH/BH/troop PNGs, not current UI controls or live-screen templates.
- [ClashKingInc/ClashKingAssets](https://github.com/ClashKingInc/ClashKingAssets) is current and includes buildings, troops, decorations, obstacles, and a probable [`loot_cart.webp`](https://github.com/ClashKingInc/ClashKingAssets/blob/d84d7fa19e546047b6d1acf6f00c14519da8c00f/assets/obstacles/home-village/loot_cart.webp). It is useful for candidate discovery and Discord decoration, not as a fail-closed drop-in detector. Its repository is GPL-3.0 and the underlying art remains Supercell property.

Isolated PNG/WebP art omits live terrain, scaling, antialiasing, animation, shadows, overlays, enabled state, and client geometry. Any cart/building detector must be promoted against live positive and hard-negative captures for every supported geometry/version. Buttons still require reviewed live-state templates or equivalent positive evidence.

Do not bundle downloaded game art in a public automation repository without a separate policy/license decision. Supercell's [Fan Content Policy](https://supercell.com/en/fan-content-policy) and [Terms of Service](https://supercell.com/en/terms-of-service) create material publication and use risks: they restrict association of assets with bots/automation and prohibit automation/emulators. An API key or unofficial-content disclaimer does not authorize gameplay automation.

## Resulting decisions

1. Adopt the official API for read-only war/CWL, roster, donation-counter, player, ranking, and stats features.
2. Keep builders, upgrades, resources, cart, rewards, donations, and gameplay decisions under positive local UI evidence.
3. Build CWL notifications and statistics as a separate central read-only subsystem with no runner-command capability.
4. Clean-room the NX OCR-preview/dashboard concepts; do not copy NX code.
5. Do not use MCP in production control or Discord delivery.
6. Do not commit third-party game assets; use locally calibrated runtime templates only after review.
