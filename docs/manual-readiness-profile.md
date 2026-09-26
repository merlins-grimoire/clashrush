# Manual private readiness profile

## Purpose

`build-readiness-profile` replaces automated calibration navigation with an
offline import step. It reads exactly nine owner-reviewed **narrow PNG crops**,
validates a strict review manifest, canonicalizes and seals the images, and
publishes the existing runtime `private/readiness/profile.json` last with an
atomic create-if-absent hard link. It never overwrites a profile, including one
created concurrently. It never
launches BlueStacks, captures a screen, reads the clipboard, or sends input.

Do not provide full screenshots. Use the Windows Snipping Tool or another local
crop tool to save only the reviewed control/state region. Account names, tags,
resources, buildings, and other game content may remain in these ignored private
crops, but the crops and manifest must never be committed, packaged, uploaded, or
included in a public support bundle.

## Donor and documentation basis

The read → validate → extract narrow template → save-template flow is adapted
from ClashAutomation's MIT-licensed
`dev_tools/harness.py::cmd_extract_template` at pinned commit
`c41fe12a6df051e241c695b71b6859286e24c612`. Its broad debug persistence and
live automation are not copied. Local adaptations add the exact nine-name
vocabulary, strict private path containment, narrow-image limits, digest sealing,
DACL protection, fail-closed validation, and manifest-last publication.

OpenCV 4.x Image file reading and writing documents that `imdecode` reads from an
in-memory encoded buffer and `imencode` compresses an image into an in-memory
buffer. The builder uses those operations so it can validate input before
publishing canonical PNG bytes:
https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html

DOC GAP: neither OpenCV nor any pinned donor defines safe crop boundaries,
runtime ROIs, or thresholds for this installation's current Clash of Clans UI.
Those values require owner review and a separate bounded runtime readiness proof.

## Review packet

Create an ignored directory such as `private/manual-readiness/`. Put these nine
narrow PNG files in it:

| Name | Reviewed content |
|---|---|
| `home` | Stable Home-only state cue |
| `settings_button` | Settings control on Home |
| `settings` | Stable Settings-only state cue |
| `more_button` | More Settings control on Settings |
| `more` | Stable More Settings-only state cue |
| `export` | Export control after the documented three scrolls |
| `more_close` | Close control on More Settings |
| `settings_close` | Close control on Settings |
| `ordinary_card` | Configured ordinary-account identity card cue |

Each crop must be at least 8 pixels on each side and no more than 320 pixels on
its largest side. Every input must be an actual PNG with a lowercase `.png`
filename. Tight crops are preferred. A full frame is rejected.

Beside them, create `review.json` with this exact schema:

```json
{
  "schema": 1,
  "profile_id": "owner-reviewed-v1",
  "templates": {
    "home": {
      "file": "home.png",
      "threshold_ppm": 900000,
      "roi_ppm": [100000, 100000, 300000, 300000]
    }
  }
}
```

The displayed `home` entry is structural only; `templates` must contain all nine
names exactly. Each entry has only `file`, `threshold_ppm`, and `roi_ppm`.
`threshold_ppm` is an integer from 500000 through 1000000. `roi_ppm` is the
reviewed runtime search region `[x0,y0,x1,y1]`, normalized to one million. Do not
copy the illustrative ROI without checking the control's actual location.

## Build

Run from the project directory while the bot is stopped:

```text
uv run clash-rush-rebuild build-readiness-profile \
  --project-root "C:/path/to/clash-rush-rebuild" \
  --review-manifest "C:/path/to/clash-rush-rebuild/private/manual-readiness/review.json"
```

Success prints only `private readiness profile built`. Failure prints only
`private readiness profile build failed`; private filenames and identifiers are
not emitted. The builder refuses to replace an existing profile. Remove or move
an existing profile manually only after reviewing it.

Building the profile is offline preparation, not readiness proof and not gameplay
authorization. The separately authorized `mvp-account-ready-one` run must still
prove every state transition, the exported account identity, the ordinary-card
cue, Home return, and owned-player cleanup.
