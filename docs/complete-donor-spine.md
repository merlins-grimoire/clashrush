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

## Sealed source and active adapter inventory

The byte-sealed copy remains outside the installable package, absent from `pyproject.toml` package data and console scripts, and has no generated `configs.py`; this prevents its ADB, network, GUI, upgrade, and broad input dependencies from becoming reachable. The executable `diagnose-home` path now activates the donor production caller order through typed local adapters rather than importing those unsafe boundaries. No BlueStacks process, capture, network request, or input was used while implementing or testing this activation.

The following incompatible donor boundaries are replaced by thin local adapters in the active diagnostic splice:

| Boundary | Donor behavior retained for study | Required local adapter |
|---|---|---|
| One instance | Exact display-name parsing and per-instance initialization | Existing host-wide mutex, exact selected instance, retained process identity, private Job, and verified stop; reject parallel GUI workers |
| Run authorization | `running()` plus mutable GUI/web state | Fresh explicit local Run authorization and default-deny admission |
| Private configuration/logging | Generated Python config, debug images, web/Telegram/Groq values | Ignored validated local config, no credentials in donor files, protected private sinks, sanitized console/status |
| Capture/input | ADB/minitouch and cached frames | Existing exact HWND/identity capture plus one reviewed physical native-input adapter; no ADB |
| Actions/spending | Upgrades, research, assistants, rewards, attacks, random/blind fallbacks enabled by defaults | Default-deny executor allowlist; all spending, rewards, account switching, unknown-screen input, network, and unapproved actions disabled |
| Recovery/stop | Broad exception handling and app stop | Bounded fail-closed recovery, positive safe-state evidence, authoritative owned-process cleanup |

The sealed source remains byte-identical. Local adaptations are limited to the tabled boundaries and retain the mapping below.

## Activated startup and recognition caller splice

The executable path is `cli.build_no_input_diagnostic_cycle` -> `NoInputDiagnosticCycle.visit_once` -> `LifecycleSupervisor.start` -> `NoInputHomeDiagnosticController.wait_for_readiness` -> `WindowService.screenshot` -> `_detect_village_type` -> `LifecycleSupervisor.stop`. It activates these donor production callers and their order:

1. CoC_Bot `src/coc_bot.py:27` calls `start_coc(detailed=True)` before any village work. The local diagnostic enters the same startup phase only after explicit one-use authorization and Job-owned launch.
2. CoC_Bot `src/utils.py:536-570` owns one finite startup deadline, repeatedly captures, checks Home before Builder, tolerates unsettled/no-match frames, and stops on timeout. `wait_for_readiness` preserves that bounded polling and positive-evidence order over private in-memory frames; timeout/no-match returns `UNKNOWN`, and lifecycle cleanup always follows.
3. CoC_Bot `src/utils.py:546-560` refreshes the cached frame before invoking the Home and Builder recognizers. `WindowService.screenshot` replaces that capture boundary with exact lifecycle-owned BGRA capture and donor-compatible BGR arrays; every captured array is cleared after one observation.
4. BasePilot `app/core/bot.py:1570-1584` supplies the active Home/Builder portrait caller. Builder evidence remains first (`mbuilder.png`), then Home evidence (`builder.png`, `gbuilder.png`), and absence remains nonfatal.
5. BasePilot `app/config.py:168-184,218-232` supplies live-size adaptation. Supported 16:9/16:10 profiles are selected normally; an otherwise valid unsupported aspect keeps the donor default 16:10 profile and scales it to the actual capture dimensions. The implementation does not widen `_ASPECT_TOLERANCE`.
6. CoC_Bot `src/coc_bot.py:61-73` orders return/recovery before stop on normal and exceptional paths. The no-input variant cannot return Home or dismiss a popup because it owns no input capability; its recovery adaptation is authoritative Job stop and verified cleanup in the existing `finally`-equivalent lifecycle path.

Closed internal observation reasons are `UNSUPPORTED_GEOMETRY`, `TEMPLATE_UNAVAILABLE`, `TEMPLATE_MALFORMED`, `OPENCV_FAILURE`, `INVALID_EVIDENCE`, `NO_MATCH`, `CAPTURE_FAILURE`, and `TIMEOUT`. They contain no pixels, paths, exception text, instance labels, or account identifiers. The executable public scalar remains only `HOME`, `BUILDER`, or `UNKNOWN`; lifecycle failures remain the bounded `LAUNCH`, `CAPTURE`, `RECOGNITION`, or `CLEANUP` stages.

The donor Continue/update branch at `src/utils.py:562-567` is deliberately replaced, not activated: the no-input diagnostic has no click/input port, so popup, Continue, loading, and unsupported frames are observed again until positive village evidence or timeout. No popup is dismissed and no update/network path is reachable.

## Activated Continue-only startup recovery

The separate action-capable path is `issue-startup-continue-approval` -> `startup-continue-one` -> `StartupContinueCycle.visit_once` -> `LifecycleSupervisor.start` -> `StartupContinueController.run` -> `Win32StartupContinueInput.click_continue` -> re-observation -> `LifecycleSupervisor.stop`. It copies the complete compatible CoC_Bot `start_coc` slice at `src/utils.py:522-580`, including `render_text` at `src/utils.py:335-352` and the center-normalized `locate`/`batch_locate` contract at `src/utils.py:1631-1716`.

The local boundary replacements are deliberately narrow:

1. CoC_Bot launch, capture, and stop are replaced by the existing exact one-instance Job-owned Win32 lifecycle and memory-only capture.
2. `Input_Handler.click` is replaced by one exact native click at the detected normalized Continue center. The immutable binding, a fresh owned capture, the live gate, the closed `STARTUP_CONTINUE` action, and the wrapper's unused state are rechecked at the input boundary. A run can attempt that click only once.
3. `update_coc` is unreachable. Locally rendered `Update`/`UPDATE` evidence returns only `UPDATE_REQUIRED`, with no input or network operation.
4. Home and Builder evidence is checked before Continue on every newly captured frame. A successful click settles and then re-enters that repeated capture/classification order until positive village evidence or timeout.
5. Every other popup/action remains unrepresentable: Okay, Exit, Claim, Collect, Skip, Yes/No, rewards, chest flow, purchases, account switching, unknown-screen input, and blind/empty-area clicks have no executor.

`STARTUP_CONTINUE_ONLY` is a separate durable approval action bound to the exact candidate tree and canonical READY lifecycle bytes, expiring within ten minutes and durably consumed before launch. Missing, expired, replayed, mismatched, or malformed approval prevents process creation. This action path is not the no-input diagnostic and cannot be entered through `diagnose-home`.

The upstream `SupercellMagic.ttf` and `CCBackBeat.ttf` remain excluded because the embedded Active Images terms do not establish redistribution permission. `setup-startup-font` accepts an operator-provided compatible font and copies it only to ignored `private/assets/CCBackBeat.ttf`; neither the font, generated templates, its source path, nor a digest is packaged or logged. Templates exist only as transient arrays in memory.

## Local lifecycle wiring

`src/clash_rush_rebuild/donor_spine_lifecycle_adapter.py` is the thin adapter for the donor's `src/launch.py` lifecycle caller. The donor source remains byte-sealed and inert. The `donor-visit-one` CLI path replaces generated Python configuration and donor process helpers with these existing reviewed local seams:

- `LocalControlStore` supplies the ignored `var/mvp-local-control.json` selection and requires explicit `RUNNING` mode.
- `ExplicitRunAuthorization` is created only after the CLI receives `--owner-approved`, is bound to one canonical `slot-N`, and is consumed before configuration or lifecycle work.
- `InertCycle` and `LifecycleSupervisor` retain the host-wide protected mutex, exact private slot registry, one selected BlueStacks process family, private Job ownership, and authoritative no-input stop proof.
- `ProtectedLifecycleLog` writes only `schema`, sanitized slot index, and the closed `NO_INPUT_STOPPED` event beneath ignored `var/private-logs/`; the directory and file are restricted to SYSTEM and the current operator and verified before use.

The adapter deliberately does not import donor capture, input, network, GUI, upgrade, reward, account-switching, or attack modules. `tests/test_donor_spine_lifecycle_adapter.py` proves missing/reused authorization, non-running control, selected-slot mismatch, verified-stop mismatch, and protected sanitized logging without launching BlueStacks or sending input. The existing lifecycle suites continue to prove mutex ownership, atomic suspended Job assignment, exact binding, rollback, and stop cleanup.

## Default-deny action boundary

`src/clash_rush_rebuild/input_authorization.py` is the single physical-input authorization vocabulary. `NO_INPUT_DIAGNOSTIC` owns an explicit empty capability. `MONITORED_ATTACK` can authorize only account-export navigation, attack navigation, troop deployment, return Home, and held-key cleanup, and it rechecks the live local control gate immediately before every input. `Win32BoundInput` accepts only that exact authorization object and every click, drag, or key action carries one exact closed action label.

`PLACEMENT_ENABLED` is a literal `False`. Spending, purchases, gems, magic items, seasonal crafting, rewards, upgrades, research, donation, account switching, credential entry, unknown-screen input, random fallback input, placement, and all other donor executors have no action label and therefore cannot pass the native boundary. The sealed donor source remains outside the package and unimported. The diagnostic controller creates only `NO_INPUT_DIAGNOSTIC`; it has no physical-input port and every action request against its capability fails closed.

The monitored-attack vocabulary does not itself grant a run. The native composition creates it only after the existing one-use tree/lifecycle/control-bound live approval is consumed, and its dynamic gate remains the persisted `RUNNING` state. Cleanup may release the three known deployment keys after revocation but cannot click, drag, press a new key, or select another action.
