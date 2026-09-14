# Clash Rush Rebuild

Clean-room comparison workspace for selecting a proven five-account Home Village and Builder Base runner, then integrating the existing Clash Rush strategic-upgrade engine. The host runs one BlueStacks instance at a time and cycles deterministically through five configured slots.

## Non-negotiable constraints

- Native Windows/BlueStacks input only; no ADB.
- Exactly five configured accounts with deterministic, restart-safe rotation.
- Home Village and Builder Base must both be first-class modes.
- Never spend gems, make purchases, claim irreversible rewards, use chat/clan/war/donations, switch Supercell accounts, evade detection, or bypass CAPTCHA.
- Private account names, player tags, tag hashes, screenshots, calibration data, traces, and machine paths stay local and ignored.
- Home Gold, Home Elixir, Builder Gold, and Builder Elixir each require explicit owner enablement, kill switches, bounded runs, and transaction verification. Dark Elixir and gems remain disabled.
- Private files may be written only beneath ignored `private/` or `var/` roots; runtime path validation will reject every other destination.
- Each account receives one bounded ten-minute visit. The runner completes as much immediately available Home and Builder work as possible, reserves time for both villages, then rotates.
- The first Builder Base release includes Baby Dragon attacks, Star Laboratory research, and named building upgrades; new-building placement and reward claims remain excluded.
- Unattended runs require Windows to remain logged in, unlocked, awake, and on a stable display configuration.

## Layout

- `references/` — local pinned comparison checkouts; excluded from this workspace's Git history.
- `research/runner-comparison.md` — evidence-based comparison and recommendation.
- `research/integration-plan.md` — architecture for adding the Clash Rush policy engine and Builder Base.
- `research/delivery-guardrails.md` — binding anti-regression, verification, privacy, and live-test gates.
- `research/slice-1-contract.md` — frozen inert lifecycle scope and exit gate.
- `src/clash_rush_rebuild/` — exact-five registry, crash-durable `READY`/`ACTIVE` lifecycle state, protected host-wide mutex, Job-owned lifecycle supervisor, memory-only capture health, and native Windows adapters.

## Current status

The comparison and architecture gate passed. Slice 1 is under static verification. It contains no game-input API and cannot upgrade, attack, navigate, spend, or claim anything. Live lifecycle/capture execution remains disabled until the exact staged tree passes clean-export tests, privacy scanning, independent review, and a separate owner approval.

## Local setup for Slice 1

1. Run `uv sync --extra test` from the repository root.
2. Create `private/slots.json` by copying `config/slots.example.json`, then replace the five generic display names with the exact five local BlueStacks display names in rotation order. `private/` is ignored; never add player tags, account IDs, credentials, or Supercell IDs.
3. Confirm BlueStacks stores its host configuration at `C:\ProgramData\BlueStacks_nxt\bluestacks.conf`. Slice 1 rejects missing, duplicate, ambiguous, or noncanonical instance records.
4. With every BlueStacks player already closed, initialize once with `uv run clash-rush-rebuild initialize --project-root . --slots private/slots.json`. Initialization acquires the protected global mutex, validates all five slots, proves no player is running, and creates only `READY(0)`.
5. Do not run `visit-one` until the staged tree has an independent PASS and the owner gives fresh approval. The command additionally requires `--owner-approved`; it performs one inert launch/bind/capture/forced-stop visit and has no gameplay-input surface.

`CCBackBeat.ttf` is a commercially licensed Comicraft font and is not redistributable from an unverified download. If a later UI-recognition slice requires it, place an operator-owned licensed copy at `private/assets/CCBackBeat.ttf`; that private asset will remain ignored and publication checks must fail closed if the required local copy is absent.
