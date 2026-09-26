# MVP slice 003: startup to identity-proved AccountReady

## Scope and evidence state

This slice is static implementation and synthetic proof only. It does not authorize or claim a live BlueStacks startup, export, or gesture. The first reversible live startup/export/return proof remains separately owner-gated. `mvp-account-ready-one` retires the owned player immediately after `AccountReady`; it never constructs monitored-attack authority and never requests attack navigation or troop deployment.

The complete bounded sequence is authorized with `mvp-run-readiness` and executed with `mvp-account-ready-one`; neither command admits `MVP_SINGLE_ACCOUNT_ATTACK`. The sequence is:

1. acquire the existing host mutex and exact lifecycle-selected slot;
2. launch and bind the player through the existing private Job-owned lifecycle;
3. repeatedly capture fresh memory-only frames under one absolute deadline;
4. require both the private Home template and the BasePilot-derived Home classifier, or locate and click CoC_Bot's private-font `Continue` target once;
5. on a fresh Home frame, locate Settings from the private manifest and click its detected center;
6. require a fresh Settings destination, locate More Settings, and click its detected center;
7. before each of three bounded scroll drags, require fresh More Settings evidence;
8. locate the moved Export target on a fresh More Settings frame and click its detected center;
9. parse a newly replaced clipboard value, require the expected tag hash and a timestamp no more than 120 seconds old or 30 seconds in the future, and clear the clipboard;
10. positively locate both close controls on their source screens, return to fresh Home, and retain the same identity proof already bound by the fresh export;
11. emit `AccountReady(run_nonce, profile_id)`, stop the owned player, prove cleanup, and release the mutex.

Every capture, matcher, live gate, and native-input seam rechecks the same absolute monotonic deadline. A missing destination or target denies the next gesture. Continue is capped at one click; repeated Continue evidence fails instead of retrying. Captured arrays are zeroed after each observation. The implementation contains no frame writer or network client.

## Donor seam and local boundaries

The startup order retains CoC_Bot `start_coc` from `m24842/CoC_Bot` commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, `src/utils.py:522-580`: finite capture/classify, Home/Builder evidence before Continue, a detected Continue target, and re-observation. Android-debug launch/input, update, reward, reload, and generic popup behavior remain excluded.

Home evidence retains the existing BasePilot-derived `NoInputHomeDiagnosticController.detect_frame` from BasePilot commit `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, plus the exact private profile template. The world-export parser remains the strict local parser in `mvp_local_world_export.py`. ClashAutomation commit `c41fe12a6df051e241c695b71b6859286e24c612` informed the missing-image and bounded-loop negatives; no new ClashAutomation code or asset is copied here.

The four local boundaries remain:

- one instance: existing mutex, exact slot, retained process/HWND identity, private Job, and verified stop;
- explicit authorization: separate `STARTUP_CONTINUE_ONLY` and `ACCOUNT_READINESS` capabilities; readiness can request only `ACCOUNT_EXPORT_NAVIGATION` and cannot request attack, deployment, Return Home, cleanup-key, reward, spending, or account-switching actions;
- private configuration: every readiness template and its manifest remain below ignored `private/`; no font or profile asset is bundled;
- disabled actions: this entry point stops at `AccountReady`; the existing attack command is separate and is not invoked.

## Private manifest contract

The required path is `private/readiness/profile.json`. The manifest and every named asset must resolve beneath the project `private/` root. Files are read only; symlink/junction escape, duplicate JSON keys, extra fields, malformed exact types, oversized files, digest mismatch, decode failure, or incomplete template vocabulary fails closed.

Schema 1 has exact top-level keys `schema`, `profile_id`, and `templates`. `templates` must contain exactly:

- `home`
- `settings_button`
- `settings`
- `more_button`
- `more`
- `export`
- `more_close`
- `settings_close`


Each entry has exact keys `file`, `file_sha256`, `pixel_sha256`, `threshold_ppm`, and `roi_ppm`. `file` is one leaf filename beside the manifest. SHA-256 values bind encoded bytes and decoded BGR pixels independently. `threshold_ppm` is an integer from 1 through 1,000,000. `roi_ppm` is four integer normalized coordinates `[x0, y0, x1, y1]` on a one-million scale. The loader converts these to an immutable in-memory template; no raw path, digest, profile image, account label, or tag is emitted to console.

The operator-supplied startup font remains `private/assets/CCBackBeat.ttf`. Redistribution rights are not established, so it is never packaged or committed.

## Tool documentation checked

- Microsoft Learn, `OpenClipboard`, sections “Return value” and “Remarks”: opening can fail while another window owns the clipboard, and every successful open must be paired with `CloseClipboard`.
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openclipboard
- Microsoft Learn, `GetClipboardData`, sections “Return value” and “Remarks”: the clipboard owns the returned handle; callers copy immediately and do not use it after close/empty.
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboarddata
- Microsoft Learn, `EmptyClipboard`, sections “Return value” and “Remarks”: the clipboard must already be open and a zero result is failure.
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-emptyclipboard
- OpenCV 4.x Object Detection, `matchTemplate` and `TemplateMatchModes`: `TM_CCOEFF_NORMED` compares the supplied template against overlapping image regions and yields the normalized correlation used by the sealed threshold.
  https://docs.opencv.org/4.x/df/dfb/group__imgproc__object.html
- OpenCV 4.x Image file reading and writing, `imdecode`: reads an image from an in-memory encoded buffer, which permits encoded-byte digest verification before decoding without passing an asset path to OpenCV.
  https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html

DOC GAP: neither platform documentation nor donor source establishes the meaning or safe ROI of this installation's current game controls/cards. Those values must come from separately reviewed private calibration and remain pending live evidence.

Automated calibration navigation is intentionally outside this narrowed MVP.
`docs/manual-readiness-profile.md` defines the offline donor-adapted import path
for exactly nine owner-reviewed narrow crops. Building that profile sends no
input and grants no runtime authority; the readiness transaction remains the
separate live proof.

## Verification contract

Synthetic tests cover the full real `cv2.matchTemplate` adapter trace, exact moved-target centers, wrong destination denial, missing card denial, private manifest/digest sealing, clipboard cleanup failure, one-click Continue cap exhaustion, deadline crossing during capture, separate startup/readiness capabilities, and the owned native readiness-only composition. Existing world-export tests cover wrong-account and clipboard replacement failures. Canonical test counts and exact tree identity belong in the task handoff, not this source-controlled document.
