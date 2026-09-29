# H0 scalable-attack choice record

Status: offline architecture gate only. This record does not modify production behavior, admit a profile, authorize BlueStacks, or satisfy S004.

## Frozen baseline

- Repository base: `195dddadcfe5a184e839b33f4d7d82792cf5bfe0`
- Base tree: `f5eea1d195d6e016569ff10532a1c17ce8ff5ba4`
- Parked candidate: `3c8ebb4f545a28d431be7c7b948a9cb80f77b3cc`
- Parked tree: `8b8274cf6551d608acf075e48ea5d48a53494fc2`
- The parked candidate remains failed, unmerged, unpushed, and unusable as runtime authority.

## Decisions

### Recognition occupancy: reject badge and empty-slot authority

**Decision: NO-GO** for NX-style level badges as complete-card occupancy proof.

The pinned study-only source `N1xUser/NX-ClashClient` at `e4c79fd671912714d48fc04c2ff9ffb60e09ef50`, `src/core/attack.py:646-727`, uses broad dark-gray/gold masks and permissive contour geometry. It does not prove one badge per complete card, identity, current card state, or viewport endpoints. Its static-position fallback at `729-748` is prohibited.

Offline reproduction against the two existing ignored diagnostic bars found:

- diagnostic A: 387 contours, 143 accepted by the donor rule; 26 inside occupied cards and 117 outside;
- diagnostic B: 310 contours, 67 accepted; 25 inside occupied cards and 42 outside;
- individual occupied cards contained between 1 and 11 accepted components;
- the donor cue fraction changed from 17.03% to 9.41% across the two backgrounds.

A generated collision kept badge and empty cues identical while changing actual occupancy from four complete cards to five cards or four plus a partial card. Badge absence also cannot distinguish selection, partial consumption, depletion, or hero deployment.

**Decision: NO-GO** for a local dashed-empty-slot cue as occupancy or endpoint proof.

The PixelChief caller `_filled_slots` treats a configured-position dashed placeholder as a stop cue. A local empty position does not prove that no occupied card follows it, that no card is clipped, or that the viewport ends there. Existing diagnostic windows also overlap occupied-card edge density and saturation. This cue may corroborate a declared location but cannot establish completeness.

### Selected recognition method

Use one bounded **positive complete-frame + identity + state reconciliation** method for a profile-driven N-card single-page capability:

1. enumerate every bounded complete-card structural hypothesis across the declared viewport; never truncate to a longest or top-N run;
2. require each admitted box to have the calibrated complete-frame signature and one unique identity in its relative identity ROI;
3. require every structural, identity, state, quantity, clipped-edge, and endpoint witness to have exactly one compatible owner;
4. reject missing, unknown, duplicate, additional, partial, ambiguous, conflicting, or unexplained evidence;
5. use positive mutually exclusive `END` and `CONTINUES` endpoint states; crop boundaries and absence are not endpoints;
6. preserve cards through selected, partially consumed, depleted, and hero-deployed states using positive state evidence; missing badge/count never erases a card.

The current longest-run function is only a hypothesis source and cannot certify the inventory. The H0 RED tests pin internal-edge truncation and subthreshold ambiguity against the real current module.

**Production disposition: BLOCKED.** Existing private evidence does not yet prove available/selected/partially-consumed/depleted/hero-deployed appearances or semantic bar endpoints. No runtime profile may be compiled or admitted from H0.

### Ground and view strategy

**Decision: KEEP LOCAL**, confidence 0.94.

Retain the current ClashAutomation-derived `base_bbox` / `candidate_ring` proposal plus local `validated_ground` and profile-bound view anchors. Do not adapt PixelChief now.

PixelChief pin and provenance:

- repository: `Leixien/pixelchief`;
- commit: `be36984b379bb8ded8707e869eeccd61f37ad4ee`;
- tree: `356f67f9ebc02cbc1ac95d1736347b9aa51689e0`;
- root MIT notice: Copyright (c) 2026 Efe Bolukbasi;
- README identifies a BasePilot-related reconstruction, so it is not independent validation.

Reviewed PixelChief seams:

- `app/services/vision.py:126-247`, `_deployment_border`;
- `app/services/vision.py:250-291`, `_deployment_camera_stable`;
- `app/core/strategies.py:103-200`, caller/cache/probe path.

Reasons not to transplant:

- synthetic fragmented boundaries remained accepted;
- a small disconnected forbidden region could be ignored;
- the grass rule is a hardcoded HSV assumption, not current-view calibration;
- the camera grid missed large top/bottom scene changes;
- the caller reuses cached points without a fresh camera proof;
- its failure path includes a blind center probe;
- its production closure is coupled to ADB input, diagnostic writes, random/fixed targets, configured slots, and excess fixed-count taps;
- no donor tests directly cover the two helpers or center-probe behavior.

Current local control behavior retains all small forbidden-mask components and rejects the generated fragmented-boundary case. Later H2 tests must add explicit fragmented-boundary, calibrated-terrain, changed-view, stale-anchor, and no-center-probe regressions before target promotion.

## Donor/source dependency manifest

| Source | Pin | License/reuse | H0 disposition |
|---|---|---|---|
| CoC_Bot `src/attacker.py:97-295` | `a5c943afed0ed3b9abedbbc228b0889145ecaf24` | MIT; existing notice retained | Keep existing local typed deployment topology; reject gap typing, defaults, fixed/random targets, ADB/minitouch, scroll completion, restart tail |
| ClashAutomation `utils/object_detection.py` | `c41fe12a6df051e241c695b71b6859286e24c612` | MIT; existing notice retained | Keep current geometry proposal, wrapped by local validation |
| BasePilot result/Return functions | `4ede1efd220ffc79a5b490cfd3788b44d2584da4` | MIT; existing notice retained | Keep narrow Result / battle confirmation / Return sequence; reject automatic End, generic Okay, rewards, restart/recovery |
| PixelChief vision/strategy seams | `be36984b379bb8ded8707e869eeccd61f37ad4ee` | MIT; no code copied in H0 | Comparison only; no adaptation selected |
| NX-ClashClient card-square cue | `e4c79fd671912714d48fc04c2ff9ffb60e09ef50` | no applicable reuse grant established | Study-only; no code/assets copied |

H0 copies no donor code or artwork and adds no dependency.

## Frozen implementation sequence

- **H1:** generic finite N-card single-page recognition and immutable compiled plan; separately sealed first profile remains four ordered cards with Lightning exactly two. Delete active scrolling/partial authority for this capability.
- **H2:** existing CoC-derived typed transaction plus current local target proposal/validation, with current-view target reacquisition and no fallback.
- **H3:** battle monitor inside the existing one-shot engine/executor and absolute deadline; natural result, approved policy End, timeout abort, assisted, and unproved remain distinct. Use payload schema v2 in the existing opaque JSON receipt unless evidence proves DDL is necessary; schema-1 remains readable legacy evidence and cannot satisfy the new acceptance predicate.
- **H4:** clean-tree canonical verification, privacy scan, and independent exact-tree review. Profile calibration/admission and live authorization remain separate gates.

The monitor must not create a second worker, process, input adapter, lifecycle owner, or fresh timeout. It must use the existing observer, intervention monitor, live gate, lease, and remaining absolute deadline.

## H0 test disposition

`tests/test_deployment_h0_counterexamples.py` contains generated-only counterexamples against the real current code. Its failing assertions are intentional RED evidence for H1:

- an internal artwork edge must not truncate a four-card page;
- an incompatible 0.92 runner-up must remain relevant to a 0.94 winner when the margin is 0.04;
- compiled profile data and decoded template authority must not be mutable after validation.

Do not weaken or delete these RED cases to obtain a green H0 branch. H1 must make them green through the real composition, then add the complete seven-defect and alternate-N matrix before production promotion.
