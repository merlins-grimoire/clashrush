# Clash Rush Rebuild

Clean-room comparison workspace for selecting proven Home Village and Builder Base mechanics, then integrating them behind local safety and transaction controls. The promoted Slice 1 is intentionally frozen to exactly five slots; a later reviewed schema-v2 migration makes team cardinality configurable while preserving that proof. Each runner selects deterministically from a persisted due queue and the entire Windows host permits only one active BlueStacks visit at a time.

## Non-negotiable constraints

- Native Windows/BlueStacks input only; no ADB.
- The current Slice 1 requires exactly five configured accounts. A later explicit schema-v2 migration supports 1–10 accounts per team (Setup defaults to five), never a silent reinterpretation of reviewed state.
- Home Village and Builder Base must both be first-class modes.
- Never spend gems, make purchases, claim irreversible rewards, use chat/clan/war/donations, switch Supercell accounts, evade detection, or bypass CAPTCHA in the initial production release. Cart and donation automation remain separately governed backlog slices.
- Private account names, player tags, BlueStacks instance names, tag hashes, calibration data, traces, and machine paths stay local and ignored. Names/tags/instance names may appear in the protected local operator log and bounded local Debug view, but never in Status, Discord summaries, support/public exports, crash reports, tests, or Git. Full frames remain memory-only except for the separately reviewed blocker-only Discord upload authorized in `research/discord-fleet-control-plan.md`.
- Home Gold, Home Elixir, Home Dark Elixir, Builder Gold, and Builder Elixir each require independent owner enablement, kill switches, bounded runs, and transaction verification. Dark Elixir is first-release scope for hero upgrades and laboratory research; pet upgrades remain deferred because the Rush Bible makes their order meta-dependent. No Dark Elixir executor exists in the current slice; gems remain disabled.
- Private files may be written only beneath ignored `private/` or `var/` roots; runtime path validation will reject every other destination.
- Each account receives one bounded ten-minute visit. The runner completes as much immediately available Home and Builder work as possible, reserves time for both villages, then rotates.
- The first Builder Base release includes Baby Dragon attacks, Star Laboratory research, and named building upgrades; new-building placement and reward claims remain excluded.
- Unattended runs require Windows to remain logged in, unlocked, awake, and on a stable display configuration.
- Normal operation must not require editing Python, JSON, or a kill-switch file. Setup is one-time; starting the installed runner is the owner's explicit authorization for that run, and stopping it uses a dedicated command or shortcut.

## Operator experience contract

The finished runner will expose seven obvious operations: **Setup**, **Run**, **Pause**, **Resume**, **Stop**, **Status**, and **Debug**. Guided Setup collects topology once, writes `private/installation.toml`, stores token values in Windows Credential Manager, and validates the configured account/channel/BlueStacks bindings without gameplay. Run starts the bounded unattended scheduler. Pause finishes the admitted transaction safely, closes the owned emulator, and idles until Resume. Stop performs the same safe boundary and exits until Run. Status is read-only and reports sanitized lifecycle, queue, instance, timer, blocker, and last-outcome data without private names, IDs, paths, or secrets. Debug is primarily a local button with a two-to-five-minute duration; Setup may optionally permit `/debug start|status|stop team:<name>` for authorized Captains, but that command never starts or resumes automation. Emergency termination remains fail-closed and must never guess at process ownership.

Internal resource and transaction gates remain mandatory, but the owner will not toggle them by opening a source or configuration file. Initial resource authorization belongs in Setup and can be changed through a command/UI; command-scoped approval flags used during development spikes are not the final everyday interface.

## Donor policy

Behavioral mechanics are donor-first: inspect the pinned BasePilot, CoC_Bot, and ClashAutomation implementations before writing each gameplay slice, copy the complete compatible seam where licensing permits, preserve attribution, and test the adapted behavior. Additional pinned references supplement that comparison: Auto Farmer contributes MIT-licensed performance/profile concepts after ADB removal, while `keshav-x/coc-bot` and NX-ClashClient are unlicensed study-only sources whose code cannot be copied. Published Clash Rush is historical first-party evidence—not a presumed-correct tactical donor—because its prior live runner was unreliable. It may supply a narrowly verified local policy or native-input primitive, but every such reuse must be compared with the other pinned references and re-proved in this runner. Unsafe donor transport, account switching, rewards, privacy behavior, “anti-ban” claims, or spending shortcuts remain excluded.

## Layout

- `references/` — tracked public policy-source index plus local pinned comparison checkouts; donor checkout contents remain excluded from Git.
- `research/runner-comparison.md` — evidence-based comparison and recommendation.
- `research/integration-plan.md` — architecture for donor-backed Home Village and Builder Base behavior.
- `research/discord-fleet-control-plan.md` — multi-runner Discord commands, role scoping, private channel routing, and builder-due scheduling.
- `research/discord-control-protocol.md` — frozen central-service/runner command, acknowledgement, expiry, epoch, generation, and idempotency contract.
- `research/runner-enrollment.md` — one-time private enrollment, revocable Team-bound runner credentials, and authenticated outbound channel contract.
- `research/ecosystem-assessment.md` — cited official-API, Discord, asset, GitHub-topic, and NX-ClashClient decisions.
- `research/additional-donor-assessment.md` — pinned coc-bot, Auto Farmer, updated BasePilot, and saved NX source-level findings.
- `research/production-backlog.md` — sequenced observability, CWL/stats, deployment, donation, and cart work.
- `config/installation.example.toml` — synthetic public example for the future guided Setup schema; it contains no working IDs or credentials.
- `research/delivery-guardrails.md` — binding anti-regression, verification, privacy, and live-test gates.
- `research/slice-1-contract.md` — frozen inert lifecycle scope and exit gate.
- `src/clash_rush_rebuild/` — exact-five registry, crash-durable `READY`/`ACTIVE` lifecycle state, protected host-wide mutex, Job-owned lifecycle supervisor, memory-only capture health, and native Windows adapters.

## Current status

The comparison and architecture gate passed, and the inert Slice 1 implementation passed static verification and independent review. Its first bounded live spike launched and stopped the owned player safely but failed closed at exact `WINDOW_BINDING`; durable state remains blocked for explicit reconciliation. It contains no game-input API and cannot upgrade, attack, navigate, spend, or claim anything. The next live diagnostic requires a new narrow reviewed slice and separate owner approval.

## Local setup for Slice 1

1. Run `uv sync --extra test` from the repository root.
2. Create `private/slots.json` by copying `config/slots.example.json`, then replace the five generic display names with the exact five local BlueStacks display names in rotation order. `private/` is ignored; never add player tags, account IDs, credentials, or Supercell IDs.
3. Confirm BlueStacks stores its host configuration at `C:\ProgramData\BlueStacks_nxt\bluestacks.conf`. Slice 1 rejects missing, duplicate, ambiguous, or noncanonical instance records.
4. With every BlueStacks player already closed, initialize once with `uv run clash-rush-rebuild initialize --project-root . --slots private/slots.json`. Initialization acquires the protected global mutex, validates all five slots, proves no player is running, and creates only `READY(0)`.
5. Do not run `visit-one` until the staged tree has an independent PASS and the owner gives fresh approval. The command additionally requires `--owner-approved`; it performs one inert launch/bind/capture/forced-stop visit and has no gameplay-input surface.

An operator-provided local `CCBackBeat.ttf` copy is available at `private/assets/CCBackBeat.ttf`. It remains ignored and may be used for local recognition/template rendering, but it must never be packaged or committed without independently verified redistribution rights.
