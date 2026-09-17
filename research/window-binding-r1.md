# WINDOW_BINDING R1 static diagnosis

## Evidence boundary

The approved inert visit on commit `95cbc739ad7c6a1b014e562afd5dcf96dfac3d83` recorded only the closed reason `WINDOW_BINDING`. Its sanitized post-observation proved zero remaining `HD-Player.exe` processes and zero configured-title roots. It did not expose private titles, HWNDs, PIDs, dimensions, frames, or a finer evaluator reason. Therefore static evidence can localize the failure to `Win32LifecycleHost.bind_exact`, but cannot honestly distinguish a live title, identity, membership, hierarchy, or geometry rejection from that audit alone.

The concrete reproducible defect is the hierarchy contract at the Win32 adapter boundary: native `EnumChildWindows(root, ...)` enumerates every descendant, while the injected `SyntheticNative.enum_child_windows` modeled only direct children. `bind_exact` flattened all returned descendants into independent render candidates. Equal-sized nested wrapper and render HWNDs therefore became an artificial largest-area tie and failed closed, although they represent one ownership/ancestry lineage. This discrepancy was absent from the synthetic suite and is consistent with the observed `WINDOW_BINDING` stage.

Confidence that this defect caused the live failure: 0.78. The closed live audit does not support a higher claim. A future separately approved diagnostic must retain a closed subreason if exact confirmation is required.

## Donor inventory

| Source | Pinned revision | License | Relevant seam | Decision |
|---|---|---|---|---|
| CoC_Bot | `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | MIT | `src/utils.py:847-963` resolves exact BlueStacks display name to internal instance and launches `HD-Player.exe --instance`; its runtime path is ADB-oriented and has no native root/render hierarchy proof. | Retain only configuration/launch compatibility evidence; do not copy ADB behavior. |
| ClashAutomation | `c41fe12a6df051e241c695b71b6859286e24c612` | MIT | `utils/game_window_controller.py:21-105` polls a title and `EnumChildWindows`; it uses partial title, first match, CROSVM class selection, and PostMessage input. | Confirms bounded polling and descendant surfaces, but selection/input are incompatible with exact BlueStacks ownership. No code copied. |
| BasePilot | `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | MIT | `app/services/window.py:88-218` has the largest complete window-inventory seam: explicit top-level versus descendant surfaces, recursive hierarchy walk, and depth recording. It targets Google Play Games/CROSVM and exposes raw diagnostic identifiers. | Reuse the hierarchy principle only; retain local exact identity, private Job, privacy, and BlueStacks rules. No donor implementation copied. |
| Previous Clash Rush automation | `949497bf0a543a43ec6ef39a8c897e366bc10362` | no source license found; private compatibility evidence only | `src/clash_rush/bluestacks_launcher.py:163-227,333-377` launches an exact configured instance, polls at 500 ms for up to 90 seconds, exact-matches the visible root title/PID, recursively enumerates descendants, then chooses maximum area. | Largest BlueStacks-compatible behavioral seam. Preserve exact title/PID and bounded polling, but do not copy its unsafe arbitrary resolution of equal-area ties or PID-only termination. |

The correction is clean-room local code over the existing reviewed Win32 seam. `THIRD_PARTY_NOTICES.md` does not change because no donor source was copied.

## Correction

The injected native model now reproduces recursive `EnumChildWindows` and true root ancestry. The host records each descendant's direct parent with `GetAncestor(..., GA_PARENT)`, requires the complete parent chain to remain within the selected root enumeration, and collapses only an equal-geometry viable ancestor wrapper when a deeper viable descendant exists in the same lineage. The existing strict largest-area selection then runs over terminal lineages.

This does not merge sibling candidates: equal-area siblings still fail closed. Exact configured title, launched root identity, separately pinned render identity, private-Job membership, visibility, root ancestry, geometry bounds, readiness attempts, capture revalidation, and memory-only pixels remain unchanged. No fuzzy title, PID fallback, class-name authority, ADB, PostMessage, input, or live enumeration is added.
