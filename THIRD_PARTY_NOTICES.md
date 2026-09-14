# Third-party notices

The exact-name registry behavior in `src/clash_rush_rebuild/registry.py` and named-instance launch concepts in `src/clash_rush_rebuild/win32_lifecycle_host.py` were adapted from the pinned CoC_Bot source identified below. The local implementation removes its Android-debug transport, caches, globals, broad executable search, and first-match behavior. The other repositories remain pinned research references; no BasePilot or ClashAutomation implementation has been copied into this slice.

- BasePilot — https://github.com/efebolukbasi/BasePilot — pinned `e17c23e88cff58047123d66747c937d3bbf8f815`
- CoC_Bot — https://github.com/m24842/CoC_Bot — pinned `a5c943afed0ed3b9abedbbc228b0889145ecaf24`
- ClashAutomation — https://github.com/calebmwelsh/ClashAutomation — pinned `c41fe12a6df051e241c695b71b6859286e24c612` (prefix `c41fe12a6df0`)

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

## First-party source reference

The published Clash Rush Home engine is pinned separately at https://github.com/merlins-grimoire/autoclasher commit `949497bf0a543a43ec6ef39a8c897e366bc10362`. It is first-party project source, not listed as an MIT third-party donor. Its ignored frozen checkout is `references/ClashRush`. Slice 1 adapts its strict registry grammar, exact PID/title/HWND binding, native process discovery, PrintWindow capture, and near-black rejection. Launcher-icon input, Android-debug state, broad process stopping, and frame persistence were not imported.
