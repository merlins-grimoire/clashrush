# Complete donor bot spine

## Selection

The transplanted spine is CoC_Bot, https://github.com/m24842/CoC_Bot, pinned at commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24` and tree `d79368fbe550036f1542883f18434e318016b279`. The source is MIT licensed; its license is copied at `donors/coc_bot/LICENSE` and already reproduced in `THIRD_PARTY_NOTICES.md`.

CoC_Bot was selected because it is the only reviewed licensed donor whose production loop combines exact BlueStacks display-name discovery, launch/stop, capture, local recognition, Home and Builder decisions/actions, recovery, and return-to-Home orchestration. BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` has stronger Home recovery but is a one-window Google Play Games bot with unfinished Builder upgrades. ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` has the best native capture/input patterns and tests but targets the first Google Play Games window and switches accounts in-game. Neither is a more complete BlueStacks + dual-village operational spine.

## Exact copy

`donors/coc_bot/` preserves 37 eligible donor runtime files byte-for-byte: the complete `src/` tree, every runtime image, and the license. `donor-files.sha256` records each byte identity. The two upstream font binaries (`CCBackBeat.ttf` and `SupercellMagic.ttf`) are excluded because independent redistribution rights were not established; a later private/local adapter must supply approved fonts or replace that recognizer. The copied production chain is:

1. `src/main.py` calls `launch.launch`.
2. `src/launch.py` parses one instance, initializes it, enables logging, constructs `CoC_Bot`, and calls `run`.
3. `src/coc_bot.py` performs online/run gating, starts Clash of Clans, recognizes exclusions, services Home upgrades/attacks, services Builder upgrades/attacks, returns Home, stops the app, and recovers by stopping on exceptions.
4. `src/utils.py` contains BlueStacks discovery/lifecycle, capture, OCR/template recognition, input, navigation, status, and recovery utilities.
5. `src/attacker.py` and `src/upgrader.py` retain the production action seams and their Home/Builder callers.
6. `src/gui.py` and `src/gui_server/` retain the alternative production control caller.

CoC_Bot has no automated test suite at this revision. Its `src/test.py` file is a commented manual harness and is copied, but it is not represented as passing donor tests.

## Current boundary and adapter inventory

The copy is intentionally inert: it is outside the installable package, absent from `pyproject.toml` package data and console scripts, has no generated `configs.py`, and its dependency list is not installed. The existing `clash-rush-rebuild` application does not import it. No BlueStacks process, capture, network request, or input was used while creating or testing this snapshot.

The following donor boundaries are incompatible and must be replaced by thin adapters before activation:

| Boundary | Donor behavior retained for study | Required local adapter |
|---|---|---|
| One instance | Exact display-name parsing and per-instance initialization | Existing host-wide mutex, exact selected instance, retained process identity, private Job, and verified stop; reject parallel GUI workers |
| Run authorization | `running()` plus mutable GUI/web state | Fresh explicit local Run authorization and default-deny admission |
| Private configuration/logging | Generated Python config, debug images, web/Telegram/Groq values | Ignored validated local config, no credentials in donor files, protected private sinks, sanitized console/status |
| Capture/input | ADB/minitouch and cached frames | Existing exact HWND/identity capture plus one reviewed physical native-input adapter; no ADB |
| Actions/spending | Upgrades, research, assistants, rewards, attacks, random/blind fallbacks enabled by defaults | Default-deny executor allowlist; all spending, rewards, account switching, unknown-screen input, network, and unapproved actions disabled |
| Recovery/stop | Broad exception handling and app stop | Bounded fail-closed recovery, positive safe-state evidence, authoritative owned-process cleanup |

This card freezes the complete source before boundary edits. Follow-on work may adapt only the tabled boundaries and must retain a mapping back to these sealed files.

## Local lifecycle wiring

`src/clash_rush_rebuild/donor_spine_lifecycle_adapter.py` is the thin adapter for the donor's `src/launch.py` lifecycle caller. The donor source remains byte-sealed and inert. The `donor-visit-one` CLI path replaces generated Python configuration and donor process helpers with these existing reviewed local seams:

- `LocalControlStore` supplies the ignored `var/mvp-local-control.json` selection and requires explicit `RUNNING` mode.
- `ExplicitRunAuthorization` is created only after the CLI receives `--owner-approved`, is bound to one canonical `slot-N`, and is consumed before configuration or lifecycle work.
- `InertCycle` and `LifecycleSupervisor` retain the host-wide protected mutex, exact private slot registry, one selected BlueStacks process family, private Job ownership, and authoritative no-input stop proof.
- `ProtectedLifecycleLog` writes only `schema`, sanitized slot index, and the closed `NO_INPUT_STOPPED` event beneath ignored `var/private-logs/`; the directory and file are restricted to SYSTEM and the current operator and verified before use.

The adapter deliberately does not import donor capture, input, network, GUI, upgrade, reward, account-switching, or attack modules. `tests/test_donor_spine_lifecycle_adapter.py` proves missing/reused authorization, non-running control, selected-slot mismatch, verified-stop mismatch, and protected sanitized logging without launching BlueStacks or sending input. The existing lifecycle suites continue to prove mutex ownership, atomic suspended Job assignment, exact binding, rollback, and stop cleanup.
