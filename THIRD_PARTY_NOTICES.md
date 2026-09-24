# Third-party notices

The exact-name registry behavior in `src/clash_rush_rebuild/registry.py` and named-instance launch concepts in `src/clash_rush_rebuild/win32_lifecycle_host.py` were adapted from the pinned CoC_Bot source identified below. The local implementation removes its Android-debug transport, caches, globals, broad executable search, and first-match behavior.

- BasePilot — https://github.com/efebolukbasi/BasePilot — pinned `4ede1efd220ffc79a5b490cfd3788b44d2584da4`
- CoC_Bot — https://github.com/m24842/CoC_Bot — pinned `a5c943afed0ed3b9abedbbc228b0889145ecaf24`
- ClashAutomation — https://github.com/calebmwelsh/ClashAutomation — pinned `c41fe12a6df051e241c695b71b6859286e24c612` (prefix `c41fe12a6df0`)

Additional ignored research checkouts are recorded in `research/additional-donor-assessment.md`. Auto Farmer is MIT licensed but has not been copied. `keshav-x/coc-bot` and NX-ClashClient have no verified source-code license grant and are study-only; none of their implementation is included.

The complete licenses remain available in ignored local checkouts under `references/<repository>/LICENSE`. The applicable CoC_Bot notice is reproduced here:

The complete inert CoC_Bot runtime snapshot under `donors/coc_bot/` is copied
unchanged from commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24`
(tree `d79368fbe550036f1542883f18434e318016b279`). It includes the donor's
production source, runtime images, and its `LICENSE`; the snapshot is not part
of the installable `clash_rush_rebuild` package. The donor's two font binaries
are excluded because independent redistribution rights were not established.
Exact file digests and the local adapter boundary are documented in
`donors/coc_bot/README.md` and `docs/complete-donor-spine.md`.

> MIT License
>
> Copyright (c) 2026 m24842
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## BasePilot transplant

The startup diagnostic failure envelope in `startup_failure.py`, `cycle.py`,
`startup_debug.py`, `startup_geometry.py` and `cli.py` additionally adapts the
same pinned BasePilot `app/core/bot.py:74-130` try/re-raise/finally production
seam and `app/ui/qt/bot_controller.py:32-58` outer worker presentation boundary.
The complete existing Hermes-derived owned-child runner/parser and their tests
are reused for startup. Raw logging/tracebacks are replaced by closed enums;
see `docs/startup-failure-spine.md` for callers, tests and boundary adaptations.

`src/clash_rush_rebuild/basepilot_window.py`, `src/clash_rush_rebuild/basepilot_vision.py`, and `src/clash_rush_rebuild/no_input_home_diagnostic.py` copy and narrowly adapt BasePilot's memory-returning `WindowService.screenshot`, aspect/template geometry, `VisionService.find_template`, and Home-vs-Builder controller seam. The six packaged templates under `src/clash_rush_rebuild/assets/basepilot_templates/` are copied unchanged from BasePilot commit `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (tree `0553306bfd30a32e31e427a0b77ff22c41f55901`).

`src/clash_rush_rebuild/readiness_catalog.py` records the same BasePilot revision and `app/services/vision.py:185-267` as MIT evidence for bounded region validation and the normalized-correlation contract. Its admitted templates are public procedural motifs; no donor or game artwork is included. The exact CoC_Bot and ClashAutomation pins already listed above are recorded as geometry/BGR bounds evidence only, with no implementation or assets copied into this catalog seam.

> MIT License
>
> Copyright (c) 2026 Efe Bolukbasi
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## ClashAutomation fixture and test adaptation

`tests/fixtures/donor/home_screen.png` is a privacy-redacted derivative of ClashAutomation's `tests/fixtures/home_screen.png` at commit `c41fe12a6df051e241c695b71b6859286e24c612` (tree `b549901e7ca8b871a17265517d730f0b3c84a618`), and the tracked-fixture/fail-closed assertion pattern in `tests/test_no_input_home_diagnostic.py` is adapted from the same revision. The source fixture is identified by SHA-256 `279acc36eaf56c298c668ecb110d5001628c2ec5607f0f57604d8460bdb42611`; it is not tracked because it exposes identifying account HUD fields. The derivative preserves the 1728x1080 geometry and Home-builder detector region while replacing every peripheral identity-bearing HUD region and the in-scene account-associated identity label and badge with opaque pixels. Its SHA-256 is `d5a9cdded32cf353da3473b23bb9015b7d0b3332ee8c28d6644fae3a9dc9aa84`; exact transformation provenance and regression-enforced regions are recorded in `tests/fixtures/donor/home_screen.redaction.json`.

> MIT License
>
> Copyright (c) 2026 Caleb Welsh
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## First-party source reference

The diagnostic-only `startup_debug.py`, `startup_debug_native.py`, and
`startup_geometry.py` additionally
adapt this same first-party revision's `bluestacks_launcher.py` generic icon
guard/stability and bounded root geometry normalization (including its production
ordering and donor width-correction test), `instance_switch.py` ordered startup popup loop, and `observe.py`
paired crosshair evidence seam. Synthetic guard tests are adapted from
`tests/test_bluestacks_launcher.py`. Exact callers, boundary replacements and
non-reused behavior are recorded in `docs/startup-debug-transplant.md`; no new
third-party artwork or font bytes are included.

The published Clash Rush Home engine is pinned separately at https://github.com/merlins-grimoire/autoclasher commit `949497bf0a543a43ec6ef39a8c897e366bc10362`. It is first-party project source, not listed as an MIT third-party donor. Its ignored frozen checkout is `references/ClashRush`. Slice 1 adapts its strict registry grammar, exact PID/title/HWND binding, native process discovery, PrintWindow capture, and near-black rejection. Launcher-icon input, Android-debug state, broad process stopping, and frame persistence were not imported.

## Hermes Agent atomic Job ownership transplant

`src/clash_rush_rebuild/diagnostic_child_job.py` and
`tests/test_diagnostic_child_job.py` copy and narrowly adapt the permanent
thread-gated `_winapi.CreateProcess` wrapper, install/reload marker pattern,
`CREATE_SUSPENDED -> AssignProcessToJobObject -> ResumeThread` ordering, and
behavioral tests from Nous Research's Hermes Agent PR #69076 at commit
`c10c89f74637cc945e9840705e0209df29aa08c8` (tree
`e10e7738c99c31e16452be7fd116517d9b04da17`). The source paths are
`hermes_cli/_subprocess_compat.py` and
`tests/test_windows_terminal_kill_on_exit_job.py`. Local adaptations replace
the donor's process-wide fail-open Job with one private fail-closed diagnostic
Job, retained-handle retirement proof, and bounded pipe draining.

> MIT License
>
> Copyright (c) 2025 Nous Research
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
