# Bounded startup diagnostic transplant

## Scope and immutable donor inventory

This is a static implementation, not evidence of a successful live startup.
No emulator launch or physical input is authorized by installing or importing it.

The largest compatible startup seam is first-party Clash Rush at
https://github.com/merlins-grimoire/autoclasher, commit
`949497bf0a543a43ec6ef39a8c897e366bc10362`, tree
`12ded3ccdb9f27610d9f07fdc7a6ad5b9fd74cc0`. It is first-party source, not a
new third-party MIT license claim.

Copied/adapted production functions and callers:

- `src/clash_rush/bluestacks_launcher.py:90-98,129-167,170-252`:
  reviewed generic icon point/signature, `_block_signature`, `verify_target`,
  stable-frame polling and one positively guarded icon click. The new BGR-array
  implementation preserves 4x4 blocks, quantization and the fixed distance limit.
- `src/clash_rush/instance_switch.py:32-117,160-176`:
  `switch_instance` launch/guard/click/25-second settle caller and `dismiss_popups`
  red-close before green-Okay loop. Existing local Job ownership replaces all
  donor process stopping and discovery. No account switching is transplanted.
- `src/clash_rush/observe.py:58-139`:
  opt-in capture, `_mark` red ring/crosshair, `click_before`/`click_after` pairing,
  and post-input rendering wait. Global counters and swallowed errors are
  replaced with per-run/per-action random nonces and protected exclusive writes.
- `tests/test_bluestacks_launcher.py:65-90`:
  synthetic geometry-relative positive/changed-negative guard contract adapted
  into `tests/test_startup_debug.py`. No private screenshot fixture is used.
  New synthetic tests cover the stronger control, authorization, evidence and
  native seams; there is no claim that the donor supplied those extra tests.
- `src/clash_rush/bluestacks_launcher.py:35-55,189-217,276-326`:
  `_restore_window`, `_corrected_root_width`, `_normalize_window_geometry` and
  their pre-detection production ordering, now in `startup_geometry.py`.
  `tests/test_bluestacks_launcher.py:37-54` supplies the pinned scaled-render
  policy and host-chrome correction regression. The initial transplant omitted
  this required production dependency: copying the icon guard without normalizing
  the render aspect can reject an otherwise valid launcher. This repair restores
  the donor algorithm, not a new target calibration or relaxed icon threshold.

## Geometry normalization repair

Only the diagnostic composition uses `StartupGeometrySupervisor`. After the
existing consumed `STARTUP_DEBUG` approval and Job-owned launch, it restores the
exact root (`SW_RESTORE`), waits 150 ms, and rebinds the exact root/render identities.
It retains the donor width correction `root_width + round(render_height * 16/9)
- render_width`, at most four resize attempts, 200 ms settle per attempt, minimum
root bounds 700x430, minimum render bounds 640x360, and relative aspect tolerance
0.015. Already-conforming geometry is restored/rebound but never resized.
Normalization has an additional ten-second admission deadline.

Every rebind uses a fresh retained-handle Job-member snapshot, exact title/root
selection, the existing render-lineage selection and sibling-tie rejection, and
current Job/identity/ancestry/size/unlocked/non-iconic checks. Handles close even
when a rebind fails. Changed root or render HWND/process identity, missing or
ambiguous render, locked/minimized state, failed bounds/resize/restore postcondition,
or nonconvergence stops preparation. There is no replacement-window fallback.
The initial lifecycle capture-health probe remains transient and unchanged;
stable diagnostic capture, protected evidence, icon detection and possible input
are constructed only after successful normalization. No pre-resize frame is reused.
The normalized binding becomes the supervisor's capture/stop binding. On failure,
the original binding still authorizes only Job-owned stop; no diagnostic/input
port is constructed. A stop-proof failure preserves blocked ACTIVE as before.

Microsoft Learn references checked for this repair:

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow
  — Parameters (`SW_RESTORE`) and Return value. The BOOL reports prior visibility,
  not success; a zero return cannot be treated as a failed restore. Rebinding and
  visible/non-iconic postconditions prove the restoration instead.
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos
  — Parameters (`SWP_NOZORDER`, `SWP_NOACTIVATE`) and Return value. Preserve the
  donor flags and reject a zero result. Pointer-width HWND signatures are explicit.

All repair tests use synthetic native ports; no live normalization or new approval
has been performed. Live compatibility remains separately owner-gated.

Compared complete competing seams:

- CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` (MIT),
  `src/coc_bot.py` startup caller and `src/utils.py:522-580`: retained existing
  Continue/update matcher and positive Home/Builder result contract; rejected
  Android activity/input transport. The donor has no automated startup suite.
- BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (MIT),
  `app/core/bot.py` Home recovery/wake and village caller: retained the already
  transplanted local Home/Builder classifier and its packaged templates; its
  single-window lifecycle is not adopted.
- ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` (MIT): broad
  OCR popup actions are incompatible with the no Claim/Collect/Skip/Yes boundary
  and depend on unavailable Tesseract. No new code/assets copied from it.

## Four local boundary adaptations

1. One-instance ownership: existing protected host mutex, exact selected slot,
   retained identity/HWND capture, private Windows Job and authoritative stop.
   `StartupDebugCycle` reuses the existing lifecycle cleanup path with a distinct
   approval action; the no-input and Continue-only commands keep their purposes.
2. Explicit authorization: exact-tree/READY-byte bound, expiring, durably consumed
   `STARTUP_DEBUG` approval. Native input additionally has a monotonic run deadline,
   per-control caps and immediate authorization/foreground/cursor/geometry checks.
   Caps are launch=1, promo-close=3, Okay=1, Continue=1. No other input action can
   pass this purpose; adding these enum labels does not expand attack authority.
3. Private configuration/evidence: the separately opted-in command alone writes
   full frames under ignored `var/private-debug/<random-run-nonce>/`. Parent/run
   directories and empty exclusive files receive exact operator+SYSTEM DACLs and
   read-back verification before pixels are written. Writes are flushed and
   byte-read-back verified. Normal runtime remains memory-only. No account names,
   tags, instance names, paths, pixels or hashes enter the manifests/console.
4. Disabled spending/unapproved actions: retain positive launcher and ordered
   red-X/Okay/Continue detection only. Red color alone is rejected without a
   white X glyph in the reviewed close band. Okay requires the label inside the
   selected green component plus the Welcome Back title context. Multiple large
   components veto before geometry narrowing. No reward/purchase/attack/reload/
   return-home/unknown-screen input, donor PID killing or network is imported.

The X and Okay semantic gates are local safety adaptations to donor color-only
behavior, not claimed live calibrations. Failure to match remains UNKNOWN/failure,
not permission to loosen thresholds. Existing Continue thresholds are unchanged.

## Diagnostic operator contract

After independent static promotion and separate owner live authorization:

1. Issue `issue-startup-debug-approval --project-root <root>
   --allow-private-full-frames` (optional `--lifetime-seconds`, existing approval
   service maximum 600 seconds).
2. Run `startup-debug-one --project-root <root> --slots <private-slots-file>
   --allow-private-full-frames` exactly once. Neither command is enabled by Setup.
3. Inspect evidence privately. Initial evidence follows three stable observations;
   final evidence is attempted on success, timeout and failure. If capture itself
   fails, a frame cannot be fabricated: the command fails and lifecycle still
   attempts authoritative stop. Before pixels are the final native revalidation
   frame, with a red crosshair at the exact native pixel. After pixels are unchanged.
   Intent/outcome files bind each pair to a random action nonce and closed action.
   A completed input fact survives failure of the subsequent capture; missing after
   evidence is failure, never a reason to replay the action.

Timeout is 60 seconds for the composition (hard admission maximum 120). A capture
must still be within two seconds at mouse-down, including detection and protected
before-write time. Native input checks the cursor and re-mapped client target
again after those operations. Loss of freshness or movement vetoes the click.
A successful click permits only fresh re-observation and bounded settle/polling.

Retention is bounded to three run directories and fourteen images per run.
Admission fails at that cap rather than overwriting/deleting earlier evidence.
After private inspection, the operator may delete a completed run directory;
`DebugEvidence.delete()` implements tested exact-directory regular-file deletion
for an owned run. Never attach these images to a board/public report. No automatic
live retry or automatic deletion of uninspected evidence is provided.

## Documentation checked

Microsoft Learn, `SetNamedSecurityInfoW`, Parameters (`SecurityInfo`, `pDacl`) and
Return value, and `GetNamedSecurityInfoW`, Parameters (`ppDacl`,
`ppSecurityDescriptor`) and Return value:

- https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow
- https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getnamedsecurityinfow

The existing `_seal_private_path` / Windows DACL read-back implementation is
reused, including a real local DACL test with generated generic pixels.
DOC GAP: these APIs establish access control, not a game popup's meaning.
Popup authority comes only from the pinned donor plus the closed local semantic
checks and synthetic regressions, pending separately authorized live validation.

## Verification record

RED: missing startup module; missing CLI/distinct approval composition; plain red
rectangle wrongly accepted; post-input capture failure lost delivery outcome.
Focused tests exercise positive/negative launcher guard, stale revalidation,
component ambiguity, X/Okay context, action vocabulary/caps, protected write/DACL
failures, initial/final/pairs/crosshair/non-overwrite, actual DACL read-back,
retention/deletion, stale/cursor/window native vetoes and inherited stop cleanup.
Canonical and exact-export counts/tree are recorded in the task handoff rather
than embedding a self-referential tree hash in this file.
