# S003 donor-first private readiness calibration

## Evidence state

This is a static implementation and synthetic verification only. No BlueStacks
process was launched and no desktop/game input was sent while implementing or
testing it. A future calibration is a separate, explicit, owner-approved live
transaction; it does not authorize `mvp-run-readiness`, an attack, spending,
account switching, rewards, or any other run.

## Donor inventory and retained seam

- First-party ClashRush, pinned commit
  `949497bf0a543a43ec6ef39a8c897e366bc10362`,
  `src/clash_rush/world_export_live.py`: retained normalized Settings
  `(0.9547, 0.7271)`, More Settings `(0.5110, 0.8340)`, Export
  `(0.7058, 0.6288)`, scroll drag `(0.50, 0.74, 0.50, 0.24)`, exactly three
  drags, and clipboard cleanup. `src/clash_rush/run_m11_cycle.py` supplies the
  Settings close point `(0.802, 0.119)`.
- BasePilot, MIT, pinned commit
  `4ede1efd220ffc79a5b490cfd3788b44d2584da4`: copied unchanged
  `templates/16_9|16_10/settings.png` and `changeuser.png`; retained its
  bounded aspect-aware `VisionService.find_template` matcher. `settings.png`
  is positive Home evidence and `changeuser.png` is positive Settings evidence.
  The transaction never clicks `changeuser`.
- The existing Hermes-derived `run_owned_diagnostic_child` parent/child Job
  ownership, finite timeout, wait/Job-empty proof, pipe cleanup, and sanitized
  scalar protocol are reused unchanged.
- The existing `StartupDebugController`/`StartupNativeInput` loop is composed
  before calibration under the same lifecycle visit and absolute 150-second
  deadline. Its launcher decision comes only from the digest-sealed private
  bootstrap template; popup/Okay/Continue handling remains delegated to the
  existing donor-derived `StartupDetector` non-launcher path.
- CoC_Bot provides no complete calibration seam beyond startup/Home, and no
  donor supplies the complete nine-template manifest. The transaction-specific
  stable-sample and atomic publication logic is a local fail-closed adapter.

The four boundary adaptations remain: one lifecycle-selected instance under the
host mutex/private Job; one exact-tree/READY, short-lived
`READINESS_CALIBRATION` approval; artifacts only beneath ignored
`private/readiness`; and a closed input vocabulary containing only
`ACCOUNT_EXPORT_NAVIGATION`.

## Operator contract

Issuance is static and does not launch BlueStacks:

```text
clash-rush-rebuild issue-readiness-calibration-approval \
  --project-root <root> --lifetime-seconds 300 \
  --allow-private-full-frames
```

The separately approved transaction is:

```text
clash-rush-rebuild calibrate-readiness \
  --project-root <root> --slots <private-slots.toml> \
  --allow-private-full-frames --timeout-seconds 240
```

The public parent owns exactly one hidden child in a kill-on-close Job. Success
is exactly `CALIBRATED\n`; malformed output, stderr, timeout, unproved wait, or
unproved cleanup fails. The child consumes the distinct approval before launch,
starts the lifecycle-selected slot, runs under one 150-second internal monotonic
deadline, and always reaches the inherited stop/release cleanup boundary.

## Transaction and artifact contract

1. Run the existing bounded startup controller first. Locate the launcher only
   with `private/readiness/bootstrap/launcher.png` and its strict schema-1
   manifest entry and bounded ROI; then delegate popup/Okay/Continue states to
   the existing detector. The reviewed current launcher geometry supersedes the
   incompatible historical donor coordinate without adding a coordinate
   fallback. Delete startup full-frame evidence after proved Home, or retain it
   privately on failure.
2. Positively prove Home with BasePilot `settings.png`; capture two stable
   samples each for `home` and `settings_button`; independently locate Settings
   with BasePilot `settings.png`, require the detected center near the pinned
   donor coordinate, and click that detected center.
3. Positively prove Settings with BasePilot `changeuser.png`; capture two stable
   samples for `settings` and `more_button`. A reviewed private bootstrap
   `more_button` template/ROI must locate near the donor point before the click.
4. Before every scroll, require the reviewed private `more_close` template/ROI
   near the donor close coordinate in a positively proved More frame. Export and
   both close controls likewise require fresh private-template matches near their
   donor coordinates. A live crop never authorizes its own click.
5. Capture two stable samples for `more`, `export`, and `more_close`; clear the clipboard before the
   transaction, require Export to produce a nonempty replacement, then clear it
   immediately and again on every exit. Clipboard text is held only long enough
   to prove replacement; it is never logged or persisted.
6. Re-prove Settings before capturing/clicking `settings_close`, then re-prove
   Home. `ordinary_card` requires a separately owner-reviewed private bootstrap
   template/ROI and two stable live samples; an arbitrary Home crop cannot pass.
7. Encode and decode-verify exactly nine narrow PNGs, compute encoded-byte and
   decoded-BGR SHA-256 values, and publish the nine assets before publishing the
   canonical schema-1 `profile.json` last.

The exact public vocabulary is `home`, `settings_button`, `settings`,
`more_button`, `more`, `export`, `more_close`, `settings_close`, and
`ordinary_card`. Every published template is backed by at least two stable live
samples, and every crop is at most 320 pixels on its largest side. The
result loads through the existing `ReadinessVisualProfile` contract.

Full checkpoint frames are written only beneath
`private/readiness/calibration/<random-nonce>/`, protected through the existing
private-path DACL adapter. They are deleted after successful crop verification
and before publication. On failure they remain there for private owner review; no
`profile.json` is published. Clipboard cleanup, checkpoint deletion, crop
encoding/verification, asset movement, and staging cleanup all occur before the
final atomic `profile.json` replace. That replace is the last fallible operation,
so no post-publication rollback is needed or attempted.

## Known live calibration risks

- The donor assets and coordinates are pinned evidence, not proof that the
  current installed game build still renders the same controls. Any mismatch
  fails before the next gesture.
- The current ignored bootstrap contains only the owner-reviewed `launcher`
  asset. It deliberately contains no fabricated More/export/close/card assets.
  Therefore the next bounded live calibration can use the reviewed launcher,
  clear startup popup/Continue states, prove Home, click the BasePilot-located
  Settings control, and then fail before clicking More because
  `more_button` bootstrap evidence is absent. It retains the calibration
  checkpoint privately for owner review while publishing no profile.
- Later staged runs require the owner to add reviewed `more_button`, `more_close`,
  `export`, `settings_close`, and `ordinary_card` entries one boundary at a time.
- State-evidence crop boxes may include animation or account-specific pixels and
  may be too unstable for the runtime threshold. Control/card crops use the
  positively located reviewed-template bounds. The generated profile must be
  tested in the separately authorized readiness run; calibration success alone
  is not readiness evidence.
- Donor coordinates remain proximity fences, not location evidence. Every close
  click still requires a fresh reviewed-template match; mismatch fails before
  input and retains private checkpoints.
