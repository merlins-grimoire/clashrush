# MVP1-LOCAL donor manifest

This checkpoint is a static-only adaptation. It does not authorize or perform a BlueStacks launch, capture, native/ADB input, credential access, network call, resource spend, screenshot write, or public push.

## First-party gameplay donor actually adapted

| Repository | Immutable revision | Tree | License/provenance | Inspected seam | Decision |
|---|---|---|---|---|---|
| Local first-party `clash-rush-automation` checkout (`merlins-grimoire/autoclasher`) | `949497bf0a543a43ec6ef39a8c897e366bc10362` | `12ded3ccdb9f27610d9f07fdc7a6ad5b9fd74cc0` | First-party project; no root source-code license file was present, so no third-party license claim is made | `operator_launcher.py`, `run_m11_cycle.py`, `bluestacks_launcher.py`, `transaction.py`, `m9_attack_transaction.py`, their production callers, and matching tests | Adapted the complete device-free M6/M9 attack transaction order and narrowed it to one local Team/account/instance with attack/farm as the sole executor |

Exact donor blob IDs used for the adapted seam:

- `src/clash_rush/transaction.py`: `36457866adcf072eee85700f5634760d0826cb75`
- `src/clash_rush/m9_attack_transaction.py`: `78d75b3a6b02459888a004bca1b5148af2d2e377`
- `tests/test_m9_attack_transaction.py`: `99dc799eae061cab5b132e674a1939bdb2962224`

The donor checkout had staged and unstaged work. None of that mutable work was copied. Inspection and provenance were bound to the immutable revision above.

## Pinned comparison references inspected

The pre-existing licensed-donor assessment remains authoritative for exact revisions:

| Source | Revision | License decision | MVP1-LOCAL use |
|---|---|---|---|
| BasePilot | `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | MIT; attribution required if copied | Compared for guarded Home/recovery behavior; no source copied in this checkpoint |
| Auto Farmer | `0120019918758e45feddf84fd5522e31cc6fd578` | MIT; attribution required if copied | Compared for attack/capture architecture; ADB and bundled binaries rejected; no source copied |
| keshav-x/coc-bot | `69392ab8ca58cd9b7725bf4106e650dbcd5e73bd` | No verified source-code grant; study only | No source copied |
| NX-ClashClient | `e4c79fd671912714d48fc04c2ff9ffb60e09ef50` | No verified source-code grant; study only | No source copied |

No third-party implementation was copied, so this checkpoint adds no third-party notice obligation.

## Preserved seam and local restrictions

`mvp_local_gameplay.py` preserves the donor transaction sequence:

1. read-only observation;
2. strict HOME/window/account/army validation;
3. intent audit before mutation;
4. immediate kill-switch recheck;
5. one bounded injected attack/farm executor;
6. unconditional cleanup;
7. read-only post-observation;
8. exact returned-HOME/account confirmation and outcome audit.

The adapter exposes only `setup`, `run`, `pause`, `stop`, `status`, and `visit_once`. Setup freezes exactly one local Team, account, and BlueStacks instance reference. Status is redacted. Unknown or mismatched window, screen, account, army readiness, callback result, or kill-switch state emits no attack input.

Upgrades, research, walls, collectors, donations, carts, placement, gems, purchases, Discord, fleet/multi-host routing, credentials, screenshots, and network behavior have no executor in this seam.

## Deliberate non-reuse and next boundary

The donor `run_m11_cycle.py` cannot be transplanted wholesale into this bounded checkpoint: it couples mutable dirty orchestration to upgrades, research, walls, collectors, world export, diagnostics, multi-instance switching, OpenCV/Pillow/Numpy, and live Win32 input. The donor `bluestacks_launcher.py` also uses process/PID and stop behavior that is weaker than the rebuild's retained-handle, Job Object, exact-HWND lifecycle foundation.

The single remaining integration blocker is a separately reviewed native composition adapter that binds the new attack-only transaction ports to the rebuild's existing exact lifecycle/window/capture seam and to a reversible attack executor. That adapter requires a fresh live owner approval gate before any BlueStacks or input exercise.
