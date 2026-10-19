# CoC_Bot donor spine

This directory is an exact, inert runtime snapshot of the licensed CoC_Bot donor at commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24` (tree `d79368fbe550036f1542883f18434e318016b279`). The canonical repository is https://github.com/m24842/CoC_Bot.

Copied without source edits:

- `src/`: CLI and GUI callers, launch orchestration, bot loop, capture/recognition/input utilities, Home and Builder attack logic, upgrade logic, logging, runtime requirements, and the donor's manual test harness.
- `assets/`: every runtime image loaded by the copied source.
- `LICENSE`: donor MIT grant and attribution.

The donor's `assets/fonts/CCBackBeat.ttf` and `assets/fonts/SupercellMagic.ttf`
are deliberately excluded because their independent redistribution rights were
not established. A future private/local adapter must supply approved fonts or
replace the font-rendering recognizer. `donor-files.sha256` seals every copied
byte. This snapshot is deliberately outside `src/clash_rush_rebuild`, is absent
from project package data and console entry points, and has no generated
`configs.py`; therefore the current application cannot import or execute it.
Do not run `donors/coc_bot/src/main.py`.

The donor is operationally complete but not locally safe: its runtime uses ADB/minitouch, independent workers, mutable public configuration, broad exception recovery, reward collection, spending/upgrades, and blind/random fallbacks. Follow-on adapters must replace those boundaries while preserving the donor's internal loop where compatible. No donor dependency has been added to the project environment.
