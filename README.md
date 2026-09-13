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

## Layout

- `references/` — local pinned comparison checkouts; excluded from this workspace's Git history.
- `research/runner-comparison.md` — evidence-based comparison and recommendation.
- `research/integration-plan.md` — architecture for adding the Clash Rush policy engine and Builder Base.
- `research/delivery-guardrails.md` — binding anti-regression, verification, privacy, and live-test gates.

No automation will be run from this workspace until the comparison and architecture gate are approved.
