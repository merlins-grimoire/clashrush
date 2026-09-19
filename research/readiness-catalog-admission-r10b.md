# R10 public-synthetic catalog admission

Status: admitted for static synthetic verification only. This record does not
admit game artwork, a real calibration, native capture, helper launch, gameplay,
or a production readiness caller.

## Admitted seam

`src/clash_rush_rebuild/readiness_catalog.py` owns one fixed 640x360 procedural
catalog generation, `SYNTHETIC_PUBLIC_V1`. It contains all 15 combinations of
the closed HOME, LOADING, and BUILDER screen vocabulary with NOMINAL,
H_INSET_5, H_INSET_10, V_INSET_5, and V_INSET_10. Every constellation contains
three nonoverlapping, spatially separated 8..96 pixel BGR motifs. The 45 motifs
are generated from public arithmetic and contain no captured, copied, or
game-derived pixels.

Admission validates the complete cardinality, exact built-in scalar types,
geometry, floor-mapped inset transforms, bounds, nonoverlap, separation,
meaningful displacement, byte lengths, per-channel variance, procedural byte
identity, generation, and provenance. Missing, duplicate, constant, forged,
out-of-frame, malformed, or open-vocabulary values fail with only
`CATALOG_INVALID`. The module performs no file I/O, capture, logging, Status
emission, network action, or hashing.

This is deliberately an inert catalog seam. Nothing imports it as a production
caller. The accepted R10A helper remains unavailable until its separate bounded
native composition is implemented and reviewed. A future real catalog still
requires its own asset-rights, privacy, calibration, negative-corpus, and exact
helper-bundle review; this synthetic admission cannot satisfy that gate.

## Exact donor and license evidence

All source references are pinned MIT repositories. No donor assets are copied.

- BasePilot commit `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, tree
  `0553306bfd30a32e31e427a0b77ff22c41f55901`,
  `app/services/vision.py:185-267`, Copyright (c) 2026 Efe Bolukbasi. Used for
  the bounded-region and normalized-correlation contract; file loading,
  resizing, score maps, first-hit authority, logs, recovery, input, and artwork
  remain excluded.
- CoC_Bot commit `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, tree
  `d79368fbe550036f1542883f18434e318016b279`, `src/utils.py:496-520` and
  `src/utils.py:1572-1665`, Copyright (c) 2026 m24842. Used only for the lesson
  that Home and Builder need distinct bounded geometry; ADB, caches, OCR,
  screenshots, sleeps, and input remain excluded.
- ClashAutomation commit `c41fe12a6df051e241c695b71b6859286e24c612`, tree
  `b549901e7ca8b871a17265517d730f0b3c84a618`,
  `utils/game_window_controller.py:225-237` and
  `utils/object_detection.py:43-57`, Copyright (c) 2026 Caleb Welsh. Used only
  for exact render-target, BGR-order, and bounds evidence; screenshot files,
  annotation, foreground restoration, retries, and one-pixel classification
  remain excluded.

The complete applicable MIT texts are retained in `THIRD_PARTY_NOTICES.md`.

## Closed scalar grammar

The catalog records the accepted schema-3 vocabulary without adding an output
path: schema 3; method `ANCHOR_CONSTELLATION_V1`; classifications
HOME_SIGNATURE, NON_HOME_SIGNATURE, VIEWPORT_MISMATCH_SIGNATURE, UNKNOWN;
sources HOME, LOADING, BUILDER, UNVERIFIED; layouts NOMINAL, OFFSET,
UNVERIFIED; observations 0 or 1; and reasons POSITIVE, CATALOG_UNAVAILABLE,
CATALOG_INVALID, BOUND_UNAVAILABLE, BINDING_INVALID, BINDING_UNVERIFIED,
CLOCK_INVALID, DEADLINE, CAPTURE_FAILED, FRAME_INVALID, HEALTH_UNVERIFIED,
UNSCORABLE, NO_MATCH, AMBIGUOUS, REDUCTION_FAILED, CLEANUP_UNPROVEN.

No free text, score, coordinate, donor metadata, revision, path, filename,
identifier, pixel, frame hash, digest, or calibration value is added to runtime
output.

## Boundary coverage

`tests/test_readiness_catalog.py` covers the complete 15/45 shape; exact donor
pins, trees, paths, MIT attribution, and immutable provenance; exact scalar
grammar; procedural BGR length and per-channel variance; exact inset mapping;
duplicate entries; constant templates; forged provenance including hostile
equality; admission copy isolation; boolean and undersized dimensions; missing,
hostile, subclassed, boolean, negative, inverted, oversized, and out-of-frame
coordinates rejected before coordinate arithmetic or motif generation; invalid
byte lengths rejected before motif generation; transform-boundary ordering;
generation mismatch; and absence of runtime output, filename, identifier, and
frame-hash surfaces.
