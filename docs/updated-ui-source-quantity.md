# Updated-UI source quantity: offline implementation candidate

Status: **B CHANGES REQUIRED; C CORRECTED CANDIDATE; D REVIEW PENDING; NOT ADMITTED.** Completion and
review status belong to Kanban, not this document. The final owner-authorized
empty-support continuation below supersedes the preserved historical partial
dispositions. Four-still supported-precommit fit verdict:
**PROFILE_RECALIBRATION_SUFFICIENT**. This is not a deployable calibration pack,
native acceptance, architecture authority, independent PASS or permission to run.

Base commit `6c48ef1e63d93acc3d5e09e88373311119b656d9`, tree
`6bbc5731e5cf302778e6af1f32eb6f1580ddaa15`. Exact result identity and exported
verification evidence belong to Kanban card `t_e30129a0` on `clashrush-mvp`.

## Donor comparison before design

All three pinned read-only repositories were clean and matched their declared
commit/tree. Their MIT license grants were read; existing notices are retained.

- BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, tree
  `0553306bfd30a32e31e427a0b77ff22c41f55901`: inspect
  `app/services/vision.py:preprocess_bw_ui_text`,
  `filter_binary_ink_by_component_area`, `_preprocess_ocr_region`,
  `find_chars_ocr`, and `extract_grouped_numbers_in_region`. Production upgrade
  and HUD callers pass a strict brightness floor, then crop/preprocess before
  character recognition. `tests/test_upgrade_reads.py` covers exact cost and
  resource disagreement, not this source-count segmentation. Adapt the complete
  small foreground-transform seam; retain the local finite glyph reader instead
  of importing OCR, text correction, component upper-area deletion or debug writes.
- CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, tree
  `d79368fbe550036f1542883f18434e318016b279`: trace
  `src/attacker.py:detect_troop_positions -> deploy_troops -> complete_normal_attack`
  and `src/utils.py:OCR_Handler`. The x marker plus limited number templates,
  default one, unknown troop count, gap-based classes and external/local OCR
  fallback cannot satisfy exact source inventory. Retain the already-transplanted
  Battle geometry; do not import these quantity/default semantics.
- ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612`, tree
  `b549901e7ca8b871a17265517d730f0b3c84a618`: compare
  `utils/object_detection.py:detect_red_or_white`, `detect_word_in_region`,
  `detect_special_troops`, their Home callers, and
  `tests/test_vision_and_config.py`. Color masking is a useful comparison, but
  permissive OCR digit correction, fuzzy words, disk screenshots and cropped
  corner classification do not replace whole-field count evidence.

Authoritative numerical API references: [OpenCV structural analysis](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html),
`connectedComponentsWithStats` (8-connectivity and CC_STAT_AREA), and
[OpenCV color conversions](https://docs.opencv.org/4.x/de/d25/imgproc_color_conversions.html),
RGB/GRAY and `COLOR_BGR2GRAY`. [Kanban completion checkpoints](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban#completion-checkpoints-before-the-iteration-cap)
require contract verification, not merely a commit. No dependency was installed or changed.
DOC GAP: no donor supplies a complete exact-inventory source reader for these
updated stills with the local clipping/ownership/ambiguity contract.

## Bounded implementation

Only the profile and vision quantity seam changes. Schema-3 `source_layout` may
optionally contain `quantity` with exactly:

- `threshold`: integer 160 through 254; no lower foreground floor.
- `minimum_component_area`: integer 1 through 8; default behavior is no filtering.
- `glyphs`: exactly x and 0 through 9, each naming one through eight existing
  hash-bound private PNG assets. Same-label variants handle observed compression
  and shape differences without reducing the .92 score or .04 inter-label margin.

The profile loader validates and freezes these values, rejects cross-label
normalized duplicates, and binds canonical profile bytes and asset bytes into
existing source authority. All source glyph variants participate in global
witness ownership. This is not a second OCR path or an expected-count fallback.

The area transform rejects boundary foreground before deleting anything; a small
component can be removed only if it horizontally overlaps exactly one retained
component. A separate partial character cannot disappear between whole glyphs.
Large/merged components are never deleted. Every retained glyph column is still
consumed by the closed x[0-9]{1,3} grammar, including range and leading-zero checks.

No policy means the prior threshold 160, no filtering and the original glyph
bank. The Battle caller passes no new policy. Hero singleton logic, common source
slot/ROI schema, category/capacity reconciliation, navigation, deployment,
receipts and live gates are unchanged.

Lazier alternative: exposing only the existing threshold argument was tested
first. It did not provide complete glyph evidence across these fields: the spell
rim speck and varying compressed glyph masks remained. A global threshold or
score relaxation was not substituted.

## Actual RED and verification evidence

Before production edits, the exact baseline reader returned QUANTITY_UNKNOWN
for all 144 honest whole-field rectangles on each supplied Army frame. The two
frame hashes, every crop hash and every original-pixel rectangle remain private.

The new generated tests exercised connected bright-rim fields with values other
than the owner's count. The pre-edit run was 15 failed / 10 passed: positive
source-policy cases failed the old closed schema or missing helper capability.
Several proposed negative tests also failed because the keyword did not exist;
those are API REDs, not invented proof that prior clipping/ambiguity was broken.
Existing already-correct negatives were not presented as newly discovered bugs.

The focused final run passed 104 tests, including existing source, quantity and
fee/control-alias suites. The worktree canonical run passed 2,717 tests. New
coverage includes four clipped edges, boundary specks, unowned partial characters,
merged rim/glyphs, missing counts, range errors, leading zeros, competing variants,
invalid policy values, canonical authority changes and pixel counts differing
from the configured expectation. Exact-export results are recorded separately.

## Private field GREEN is not full-profile GREEN

The same exact decoded stills, not new captures, produced observed values
**3 / 169 / 4 on both frames** through the actual repaired `read_quantity`, using
threshold 235 and component floor 5. These values are assertions after recognition,
never inputs to recognition. Counts were not derived from aggregate capacity.
Each field also rejected a maximum one below its read value.

Calibration uses individual labeled glyph crops from both supplied stills and
retains their ROI/hash provenance privately. This is a fit to the supplied
samples, not held-out validation. Unseen labels 0/2/5/7/8 remain synthetic
competitors in this isolated probe and are explicitly **not calibrated** for a
real profile. No complete pack or native approval was manufactured.

The observed large hero is a singleton; level 20 is not a count. The unchanged
synthetic hero observer test passes. Complete real hero availability/lock-column
coverage is not established by that test or the portrait alone.

## Four-still reassessment and blocking gap

Using exact imported pure candidate methods on all four hash-verified stills:

- Home Battle plus two independent anchors passes occurrence ownership; Home
  Battle does not match menu/Army.
- Normal/ranked menu witnesses, Find a Match and the whole 550-Gold fee remain
  independently located. Whole-fee equality passes; one-pixel mutation and a
  nested fee/control occurrence reject.
- Army identity, separate context and final green Attack pass on both frames;
  the Attack template does not match Home/menu.
- Ordinary and spell panel crops match both Army frames. Individual quantities
  are GREEN as above.
- The full large animated hero crop matches its source frame but not the second
  at .93. This is a failed whole-panel template, not proof that a smaller honest
  hero identity/availability calibration is impossible.
- Empty support and locked siege crops each match both Army frames. They are
  distinct evidence roles, not interchangeable vacancy states. A same-sized
  support vacancy crop does not match the siege crop.
- The source schema still requires equal-sized slots with one relative count ROI.
  The successful private field crops have different widths. Testing the measured
  ordinary-field dimensions (69 by 27) at all 63 enumerated whole-count placements
  inside the measured spell-card envelope rejects on **each** Army frame because
  bright non-count artwork touches the field boundary. These finite failures do
  not prove every possible ROI/preprocessing/calibration choice impossible.

Therefore **MORE_EVIDENCE_NEEDED**: the candidate fixes supplied individual
quantity fields, but a complete honest shared source layout, real hero singleton
state, unseen glyph bank and global witness map remain unproved. Do not turn
individual-field results into a successful full-source observer or admission
claim. Different panel sizes alone are not a reason to redesign the slot schema.
The implementation card must not release its review descendants as completed
while this source-fit obligation remains unresolved; preserve the partial
candidate and request reassessment of the exact remaining fit boundary.

No postcommit Battle-bar, deployment/result, fee-debit timing, tutorial/dynamic
state or native/runtime compatibility is inferred. No native input/capture,
emulator action, ADB, installation, admission, main mutation, merge, push or
release occurred. Original private evidence and probe scripts remain ignored;
no game images, private fonts or identifying information belong in this diff.

## Owner-authorized continuation: complete source geometry

The owner resumed the same card from partial commit
`4b379551972cbbcf013dcf3674132c52aa3be77b`, tree
`945dfe35e3ef43ce03a0dbfa86ca4c003799d85b`. No baseline restart, alternate graph,
route change or Battle change was made. The preceding sections record the first
attempt, not the final fit verdict.

The exact predicate matrix classified the animated hero identity and category
status roles as calibration gaps, the individual-field-only probe as a harness
gap, and complete heterogeneous regions plus global numeric-alphabet ownership
as bounded grammar gaps. No evidence gap was proved for the supplied precommit
state. A separate source-plan policy obstruction was then reproduced below.
The original video was hash-verified and sampled offline; no new capture
was taken. The four original stills remain the positive fit inputs.

A genuine head/crown crop locates across the animated hero frames above .93;
equipment is not used as hero identity. Colored panel state and the separate
hero status provide the singleton evidence; the level is not a quantity.
All previously missing digit competitors were obtained from independently read
visible capacity/timer glyphs in the supplied stills. No configured quantity or
capacity arithmetic supplies a recognized count. This remains fitted evidence,
not a held-out calibration dataset or proof for unseen numeric presentations.

### Small bounded profile extension

Schema 3 retains its old behavior when `geometry` is absent. Explicit geometry
must be supplied for all five source categories together. Each category adds:

- `extent`: its complete normalized source-content rectangle;
- `fields`: one record per declared slot, using the existing `icon_roi`,
  `count_roi`, `state_roi`, `empty`, `available`, `selected`, `vacant` semantics;
- `background`: whole-field template locators covering every non-slot pixel.

These three fields live inside the category's `geometry` object. Slot rectangles
and whole-background rectangles must exactly partition the extent in decoded
pixel coordinates, with no hole, overlap, out-of-bounds region or zero area.
Category extents must not overlap. Background templates must equal their entire
field dimensions; substring matching cannot establish absence. Existing bounded
asset sizes, frozen data, canonical digest and source-plan authority apply.
EMPTY/LOCKED categories may use zero slots only with complete positive background
coverage; they are not skipped. State/header/end/complete checks still run.

The same source loop now selects each slot's validated field configuration.
Ordinary and spell count fields no longer have to share a width. No reader score,
margin, clipping rule or capacity check was relaxed. Global semantic occurrence
ownership remains in force, and the explicit mode also owns the Army anchors
and committing control globally.

Raw numeric glyphs are not globally unique semantic witnesses: actual digit-one
crops exceeded the existing match limit across both full stills, and other digits
legitimately occurred in capacities and timers. In explicit geometry mode, the
closed reader consumes each complete owned count field; complete background
checks reject new content outside slots. Icons/state/category/control witnesses
still undergo whole-frame ownership checks. Legacy global-alphabet behavior and
Battle behavior remain unchanged. This is a source-layout adaptation, not a
permissive OCR fallback or a global match-limit increase.

### Fit result and evidence boundary

The actual combined pure `_source` method returns ordinary counts 3 and 169,
hero singleton 1, spell count 4, and all five correct category states on BOTH
original Army stills. Whole support emptiness, siege lock region, locked hero
columns, unused troop/spell regions, category status, Army global roles and the
final green control are exercised together rather than in isolated count probes.
Home/menu/whole fee/Find independent checks remain passing on the other stills.

The private harness is explicitly not a runtime pack. All positive matches and
all quantity alphabet competitors use supplied pixels. Unobserved selected,
continuation and occupied-slot-vacancy alternatives are synthetic negative-only
sentinels in the fit harness, clearly marked in its provenance; no positive fit
or native claim is based on those sentinels. No actual alternative-state artwork,
selected-state behavior, complete deployable profile, installation binding,
postcommit compatibility or admission is established by this experiment. They
must not be exported as real private calibration. Generated public tests exercise
the closed negative-state branches independently.

Before production edits, the new geometry suite recorded 3 failing positive/API
checks and 20 already-rejecting negatives. The final focused suite passes 130
tests. Twenty-six derived negative checks on copies of the supplied frames reject
added support/card content, changed siege/hero locks, missing identity/count/
status/global witnesses, duplicate global roles and reduced numeric capacity.
Those mutations are synthetic negatives, not new native observations.

Lazier alternative: reuse the old source loop and its field/ownership helpers,
adding only the explicit complete-geometry data. A separate source recognizer,
general OCR engine, route rewrite and new deployment schema were not added.

### Decisive remaining source-plan obstruction — do not release B

The pure source reader's positive fit is **not complete-profile GREEN**. Tracing
the loader through `DeploymentProfile.validate` into `CompiledDeploymentPlan`
reproduced an independent incompatibility: the compiled source plan requires
`clan == POPULATED` and exactly one CLAN deployment-roster entry. The supplied
source state is honestly EMPTY support. Its exact observed inventory therefore
raises `SOURCE_CATEGORY_MISMATCH` before a complete profile can be constructed.

This is a **MINIMAL_GRAMMAR_GAP at the source-plan policy boundary**, not missing
footage or incomplete crop search. The old profile law is explicit in
`mvp_deployment.py:156–176`. Neither inventing reserve inventory nor marking the
empty area POPULATED is acceptable. No change to that deployment-plan requirement
has been made under a source-profile repair contract that also prohibits
deployment redesign. The owner must settle whether this lane may extend the
compiled source projection to represent EMPTY support with no CLAN deployment
entry, or whether populated support remains a required product precondition.
No repeat route, attack or additional screenshot is requested for this policy
decision.

Final fit classification is **SOFTWARE_GRAMMAR_CHANGE_STILL_NEEDED**, not
MORE_EVIDENCE_NEEDED and not PROFILE_RECALIBRATION_SUFFICIENT. The geometry work
and prior quantity repair are preserved as an unaccepted partial candidate.
Its 130 focused and 2,743 canonical worktree tests pass; those tests do not waive
the reproduced end-to-end profile obstruction or turn synthetic negative-only
sentinels into actual alternative-state calibration. A remains incomplete and
B–E must remain dependency-gated. Exact export and preservation identities are
recorded on the implementation card, without a completion/PASS claim.

## Final owner-authorized continuation: source-bound EMPTY support

The owner explicitly resolved the preceding policy obstruction on the same card,
preserving checkpoint `a04c119df1555301e0e75b48d19b7d5d8c31192c`, tree
`0161e3eec358c153a503247888f23559d4bcac81`. The historical blocker above no longer
describes the current candidate. Support is mandatory observed evidence, never
an optional category or a skip-on-error feature.

Schema-3 `source_layout.allow_empty_support` is an optional exact boolean,
default false. A true value opts into the owner's source-bound policy:

- positively proved EMPTY support has no source reserve contents and exactly
  zero CLAN plan entries;
- positively proved supported POPULATED support has reserve contents and exactly
  one semantic CLAN plan entry, irrespective of the number of reserve identities;
- missing, unknown, ambiguous, clipped or unsupported support still rejects.

The plan validates this policy only with source category/context authority,
includes true in its canonical digest, and preserves it through detached copies
and the executor's private core/public-plan reconstruction. False/absent retains
the legacy plan digest and populated-only semantics. Canonical profile bytes and
the existing asset/context seal bind the profile policy as well. The unchanged
source observer supplies the actual inventory, and the unchanged final pre-input
source comparison rejects content/state changes. The unchanged exact Battle
roster comparison rejects additional, missing or duplicate support projections
before deployment. No executor, navigation, receipt or finalization redesign.

Donors were rechecked at the pins above before editing. BasePilot's strict
preprocessing remains retained. CoC_Bot `src/attacker.py:148–231` supplies the
already-adapted discovered-card/singleton deployment seam, but neither its
default count nor optional exclusion is source-empty authority. ClashAutomation
`utils/object_detection.py:1495–1537` conflates missing/error with an empty special
list; that behavior is incompatible. No donor implements the local source-bound
sealed projection; extend the existing validated adapter rather than add another
recognizer. Existing licenses/notices remain unchanged.

New pre-edit RED recorded 9 failures and 23 passes. Positive/schema failures are
not misrepresented as historical negative bugs. The completed focused run passes
188 tests, including 41 support-policy checks, quantity/geometry, source images,
support input, and fee/control alias guards. A synthetic image observer → engine
→ guarded mouse run completes with ordinary deployment and zero support events;
populated legacy support still selects and deploys once. Fresh source changes,
unsupported states, malformed policy, stale seals and exact support cardinality
are covered. Canonical and exact-export results belong to the immutable handoff.

### Concrete full-profile fit, not merely a helper fit

The private four-still assessment now constructs and loads the entire canonical
schema-3 profile with `runtime=False`, then invokes the actual `FrameObserver`
on copies of the four exact hash-verified stills. Both Army observations equal
the sealed source inventory/categories/context and yield 3 / 169 / hero 1 / 4.
The hero identity, ordinary/spell fields, complete EMPTY support, LOCKED siege,
category capacities/status, global witnesses and independent final Attack
control pass simultaneously. Home Battle and independent Home witnesses pass;
normal/ranked menu witnesses, whole 550-Gold fee and Find control pass. Removing
the opt-in still reproduces the legacy EMPTY-policy rejection. All 26 previously
derived real-frame hard negatives remain rejecting.

Predicate disposition: hero identity/status and screen roles were calibration
gaps; the isolated-field probe and incomplete fixture assembly were harness gaps;
heterogeneous complete geometry and the now-authorized EMPTY projection were
minimal grammar gaps, now resolved. No additional evidence gap is asserted for
this supported precommit state and no repeat footage is requested.

Fit verdict: **PROFILE_RECALIBRATION_SUFFICIENT**, strictly for those supplied
precommit states on this repaired software. Positive source/control/quantity
evidence comes from supplied pixels, not expected counts. This is fitted, not
held-out evidence. Unobserved alternatives, Battle/result and blocker artwork
are explicitly synthetic negative-only scaffolding in the private test pack;
installation binding/intervention are inert test doubles, not native proof.
No runtime profile is admitted or supplied. No postcommit Battle/deployment,
result, fee-debit timing, tutorial/dynamic-state or native compatibility is claimed.

Lazier alternative used: preserve the observer and executor and vary only the
sealed source-to-roster cardinality; no special support-skip execution path.

## C: one bounded correction of B's ownership/absence findings

B independently reviewed commit `86f3298922cbf4567161d17976a2c6ade75b5c8a`, tree
`6517244ee9d2e2b2f21654fef0e7a33be3786bcc`, with CHANGES_REQUIRED. The positive
four-still fit did not waive two negative defects. C reproduced all six supplied
regressions before production edits. Exact C identities, logs and final D verdict
belong to Kanban, not this status document.

- B1: explicit geometry now records only semantic global owners. Every semantic
  witness remains scanned even if its asset key is also a quantity variant;
  numeric membership cannot suppress a role or authorize it in a count field.
  Raw digits remain field-owned, not globally unique. Legacy ownership is unchanged.
- B2: whole-background correlation is retained but is no longer absence proof by
  itself. Every BGR channel of every pixel must also lie within a sealed absolute
  difference bound against ONE complete same-sized template. The same check is
  used for background tiles and vacant slots in explicit geometry. No residual
  is averaged away, removed by component area, or accepted by a pixelwise union
  of multiple alternatives. Non-geometry legacy behavior is unchanged.

Each geometry optionally declares `maximum_pixel_delta`, an exact integer from
0 to 16, default 0 (pixel equality). Background locators optionally declare
`alternatives`; slot fields optionally declare `vacant_alternatives`. Each is a
list of at most seven existing asset keys, in addition to the original template.
All absence templates must be whole-field, three-channel, same-sized images.
Canonical profile bytes, asset hashes, frozen copies and source-plan authority
bind these parameters and alternatives. The existing .93 similarity requirement
and complete non-overlapping rectangle coverage remain mandatory.

The tolerance is a declared calibration noise ceiling, not permission to ignore
small objects. Even one channel of one pixel beyond it rejects, irrespective of
field size. Differences within a nonzero bound are necessarily indistinguishable
from the admitted noise; use zero unless separately evidenced calibration needs
noise tolerance. The supplied private fit uses ZERO tolerance and separate exact
whole-background crops from the two already-supplied Army frames. These are fitted
observations, not held-out validation or evidence of dynamic/alternate states.
No private artwork is tracked or delivered in the public-safe review package.

Donor-first correction comparison: pinned BasePilot's strict preprocessing and
component seam remains retained; it offers no complete pixel-local absence proof.
CoC_Bot's `detect_troop_positions -> deploy_troops` texture/gap/default-count path
does not prove an empty category. ClashAutomation's `detect_special_troops` returns
an empty list on missing evidence/error and is incompatible. Preserve those earlier
donor adaptations and licenses; repair the existing local ownership boundary,
not a new recognizer. No new donor code or OCR fallback was imported in C.
DOC GAP: none of those donors provides sealed complete-source absence evidence.
The installed OpenCV [array operations](https://docs.opencv.org/4.x/d2/de8/group__core__array.html)
`absdiff` primitive supplies per-element absolute difference without uint8 wrap;
the local maximum check does not claim correlation is an absence certificate.

C's new synthetic suite recorded 21 failures and 18 passes before the correction:
behavioral false acceptance and positive new-schema failures are distinguished in
the saved log; already-rejecting negatives are not manufactured RED history.
Private pre-edit mutations reproduced localized false acceptance in support,
locked siege and unused hero/spell backgrounds on both frames. The same mutations
reject after correction, with all four original positive observer states retained.
No route, deployment, receipts, input, runtime admission or main changes are made.

Lazier alternative used: one shared whole-field residual check around existing
matching; no segmentation engine, local-window heuristic or permissive OCR.
