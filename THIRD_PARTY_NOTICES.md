# Third-party notices

The exact-name registry behavior in `src/clash_rush_rebuild/registry.py` and named-instance launch concepts in `src/clash_rush_rebuild/win32_lifecycle_host.py` were adapted from the pinned CoC_Bot source identified below. The local implementation removes its Android-debug transport, caches, globals, broad executable search, and first-match behavior.

- BasePilot — https://github.com/efebolukbasi/BasePilot — pinned `4ede1efd220ffc79a5b490cfd3788b44d2584da4`
- CoC_Bot — https://github.com/m24842/CoC_Bot — pinned `a5c943afed0ed3b9abedbbc228b0889145ecaf24`
- ClashAutomation — https://github.com/calebmwelsh/ClashAutomation — pinned `c41fe12a6df051e241c695b71b6859286e24c612` (prefix `c41fe12a6df0`)

Additional ignored research checkouts are recorded in `research/additional-donor-assessment.md`. Auto Farmer is MIT licensed but has not been copied. `keshav-x/coc-bot` and NX-ClashClient have no verified source-code license grant and are study-only; none of their implementation is included.

The complete licenses remain available in ignored local checkouts under `references/<repository>/LICENSE`. The applicable CoC_Bot notice is reproduced here:

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

`src/clash_rush_rebuild/basepilot_window.py`, `src/clash_rush_rebuild/basepilot_vision.py`, and `src/clash_rush_rebuild/no_input_home_diagnostic.py` copy and narrowly adapt BasePilot's memory-returning `WindowService.screenshot`, aspect/template geometry, `VisionService.find_template`, and Home-vs-Builder controller seam. The six packaged templates under `src/clash_rush_rebuild/assets/basepilot_templates/` are copied unchanged from BasePilot commit `4ede1efd220ffc79a5b490cfd3788b44d2584da4` (tree `0553306bfd30a32e31e427a0b77ff22c41f55901`).

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

`tests/fixtures/donor/home_screen.png` is a privacy-redacted derivative of ClashAutomation's `tests/fixtures/home_screen.png` at commit `c41fe12a6df051e241c695b71b6859286e24c612` (tree `b549901e7ca8b871a17265517d730f0b3c84a618`), and the tracked-fixture/fail-closed assertion pattern in `tests/test_no_input_home_diagnostic.py` is adapted from the same revision. The source fixture is identified by SHA-256 `279acc36eaf56c298c668ecb110d5001628c2ec5607f0f57604d8460bdb42611`; it is not tracked because it exposes identifying account HUD fields. The derivative preserves the 1728x1080 geometry and Home-builder detector region while replacing every peripheral identity-bearing HUD region and the in-scene Clan Castle identity label with opaque pixels. Its SHA-256 is `13b52280bee6432b64f1173962529c49d19199faecd3b9ae16aba1b47c1fd22e`; exact transformation provenance and regression-enforced regions are recorded in `tests/fixtures/donor/home_screen.redaction.json`.

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

The published Clash Rush Home engine is pinned separately at https://github.com/merlins-grimoire/autoclasher commit `949497bf0a543a43ec6ef39a8c897e366bc10362`. It is first-party project source, not listed as an MIT third-party donor. Its ignored frozen checkout is `references/ClashRush`. Slice 1 adapts its strict registry grammar, exact PID/title/HWND binding, native process discovery, PrintWindow capture, and near-black rejection. Launcher-icon input, Android-debug state, broad process stopping, and frame persistence were not imported.
