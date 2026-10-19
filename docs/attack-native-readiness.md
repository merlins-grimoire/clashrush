# Attack/native-readiness preparation — offline only

Current policy override: `t_4b4717dd` retires in-game account/name/tag/marker
admission and defers strict guest-package/build provenance. Older missing-input
tables and package-provider analysis below are historical evidence, not active
blockers for the instance-centric lane. The helper now distinguishes retained
slot/provider/native/calibration inputs from retired policy. See
[the partial implementation ledger](attack-a-decision-b.md#instance-centric-owner-policy); none of this is
runtime, calibration, matcher or gameplay acceptance.

Status: **PARTIAL / BLOCKED_MISSING_INPUT**, not native readiness or production calibration.
This document belongs only to the owner-authorized isolated A→B→C→D lane. Kanban
owns its current disposition. It changes no production behavior, frozen governance
blob, runtime gate or donor algorithm. No native step below has been executed.

## Exact baseline and scope

Combined baseline commit `8da15d4ebe042f6e116c93e2b619bde9db83d96c`, tree
`6ac6281de91b0d1af565dc244801c43cc2dbeb2b`: the nine owner-listed governance
paths are exact mode/blob/path copies of governance commit
`7525bf9dee3e62ebbfdb5f0d698fc19502589562`; every other tracked entry is from
reviewed attack commit `33e347798bb026577fc46df4c802f6a8e9fbc120`, tree
`f8bca55d26b3e79051ecd70b93cb29465f07ad71`. Result identity belongs to the
external evidence manifest, avoiding a self-referential tree hash in this file.

Only the offline preparation helper, its synthetic tests and this document are
new. No installed wheel, new dependency, account mapping, approval, provider
capability, runtime admission or executable native command is generated.
Vision/OCR remains separate and is not evidence for this lane.

## Source and donor trace

The exact current source, not historical prose, defines these boundaries:

- `mvp_deployment_profile.py:load_profile`: canonical schema-3 profile, exact tree,
  file and asset SHA-256, bounded private reads; use `runtime=False` here. The
  copied profile changes only `reviewed_tree`; `runtime_reviewed` remains false.
- `DeploymentProfile._validate_source` and `_validate_source_geometry`: all five
  categories, quantity alphabet/configuration, independently owned semantic
  witnesses, complete nonoverlapping extents, whole-field absence alternatives.
  `CompiledDeploymentPlan` binds inventory, category states, assets, tree, build,
  mode, fee and empty-support policy. Existing tests exercise Battle projection.
- `FrameObserver`: actual file-frame recognition, transient pixel wiping, source
  inventory/context equality, Home Battle, menu fee/Find a Match and Army Attack.
  `require_installation` denies absent or mismatched evidence before capture.
- `mvp_policy_sources.py:PolicyWorkerSources.__init__/_slot/home_verified`:
  canonical provider schema, exact five-slot mapping and configuration digests,
  separately calibrated same-account Home marker. This native composition is NOT
  constructed for the real private pack in this lane.
- `mvp_installed_build.py:parse_package_record/InstalledBuildTransport/InstalledBuildProvider`:
  exact package `com.supercell.clashofclans`, current guest PackageManager source,
  15-second bracket, 256-KiB command-output limit, two equal complete reads and
  cleanup/source revalidation. `consume` does another fresh bracket, so a normal
  acquire/consume pair performs four package reads, not two total. APK and signing
  certificate cryptographic hashes remain unavailable.
- `mvp_deployment_integration.py:InstalledProfileReader`: actual provider adapter;
  binding object identity, fresh acquire/consume, positive version code, immutable
  source/package baseline, revocation on failure. It is not the inert file-fit
  InstallationEvidence callback used here.
- `mvp_session_authority.py:RunBindingV3`: exact runner/profile/provider/approval/
  account/native-binding references. No reservation or grant is issued here.
- `mvp_account_ready.py:load_private_visual_profile/AccountReadinessController`:
  a separate readiness/profile and input-capable account-export path, NOT an
  offline substitute for the provider account marker. Do not run its controller.

Retain the pinned donor comparison in `docs/updated-ui-source-quantity.md`,
"Donor comparison before design", and the build-source comparison in
`mvp_installed_build.py`'s module header. BasePilot
`4ede1efd220ffc79a5b490cfd3788b44d2584da4` contributes the already-adapted strict
quantity preprocessing; CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` supplies
Battle/instance comparison but its cached/global transport is incompatible;
ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` supplies geometry and
missing-image comparisons, not current build proof. Existing MIT attribution is
unchanged. No new donor behavior or production boundary is designed here.

Authority references: `.hermes.md` (Authority; Revision and review),
`docs/architecture/CURRENT.md` (Completed candidate evidence and preserved
contract), and `docs/governance/development-workflow.md` §§1A, 3A, 4A.
DOC GAP: the exact candidate exposes no authorized native-readonly CLI or bound
native PackageManager capability issuer. `_NativeLeaves.__init__` deliberately
raises. An owner grant alone cannot make that missing implementation exist;
separate bounded implementation/review authority is needed before native use.
Do not bypass that denial with `__new__`, injected real leaves or shell commands.

## Private preparation and evidence classification

`tools/attack_native_readiness.py` copies only the owner-supplied four-frame
provenance and corrected fit profile/assets after digest and code-provenance
checks. Historical scripts are never executed. It creates a NEW ignored
`private/attack-native-readiness-v1` root, rejects links/reparse components and
escapes, refuses overwrites and verifies copied bytes. The original evidence is
read-only. The confidential manifest records original/prepared profile digests,
asset/frame hashes, file geometry, package/query expectations and missing inputs.
No real provider descriptor is manufactured. No account identity is inferred
from generic Home recognition or a test fixture.

The helper's only executable command is offline file processing:

    <existing-python> tools/attack_native_readiness.py --source <authorized-evidence-root> --project <isolated-worktree> --code-root <byte-exact-export> --tree <exact-export-tree>

Run from the exact export with PYTHONPATH explicitly set to its `src` directory.
No install or package-manager operation is needed. Exit 2 means independent
checks passed but required inputs are missing; exit 1 means a check failed.
Neither is readiness PASS. An existing pack is deliberately not overwritten;
subsequent validation may call `validate(pack, code_root)` without copying again.

PASS can cover canonical profile/schema/assets and private confinement, four
supplied PRECOMMIT fits, category/source/plan consistency, quantity policy,
control/fee ownership, runtime-profile denial and missing-installation denial.
Installation/intervention callbacks are **INERT DOUBLES, NOT INSTALLED EVIDENCE**;
Home account verification deliberately remains false. The synthetic suites cover
populated and empty support, ambiguity/overlap, parser failures and exact
provider/account schema. Their fabricated descriptors/builds are never copied
into the real package. Postcommit artwork remains synthetic scaffolding; no
Battle/result, held-out, dynamic, debit-timing or sufficient production-calibration
claim follows from the supplied precommit fit.

## Required missing inputs and affected assertions

| Exact field | Required source | Affected assertion / state |
| --- | --- | --- |
| `provider.account_ref`, `provider.slot` | Owner-supplied selection and account-to-instance mapping | Selected account/instance: BLOCKED_MISSING_INPUT |
| `provider.slots_digest` | Exact authorized `private/slots.json` (`schema=1`, five ordered `display_names`) | Mapping integrity: BLOCKED_MISSING_INPUT |
| `provider.configuration_digest`, `provider.instance_digest` | Authorized supplied configuration snapshot and selected internal instance name; hash exact bytes/name, never display name as substitute | Configuration and instance binding: BLOCKED_MISSING_INPUT |
| `provider.account_template`, `provider.account_roi`, `provider.account_threshold` | Reviewed authorized same-account marker asset plus locator/threshold calibration; marker must be in exact profile assets | Same-account Home: BLOCKED_MISSING_INPUT |
| `source_layout.build`, `provider.version_code` | Future current bound PackageManager `versionName`/`versionCode` evidence | Installed compatibility: FUTURE_NATIVE_UNPROVEN; fitted label is not a version observation |
| `provider.source_tree`, `provider.profile_digest`, `RunBindingV3.provider_digest` | Final exact completed profile/provider bytes; provider uses canonical_v3 WITHOUT the profile's trailing newline | Full provider seal: BLOCKED_MISSING_INPUT; partial profile digest is available privately |
| `RunBindingV3.approval_digest`, `runner_manifest_digest`, `binding_digest`; `BuildBinding.process_identity` | Separately reviewed installed artifact, exact owner envelope and current owned binding | Future authority/native proof: FUTURE_NATIVE_UNPROVEN |

Provider schema is exactly `schema`, `source_tree`, `account_ref`, `profile_digest`,
`slot`, `slots_digest`, `configuration_digest`, `instance_digest`, `version_code`,
`account_template`, `account_roi`, `account_threshold`. Schema is integer 1; slot
is integer 0..4; version code is a positive integer; the three source digests
are lowercase 64-hex; ROI is four exact floats in ordered [0,1]; threshold is an
exact float in [.93,1]. Profile digest hashes complete canonical profile bytes,
including newline. Provider digest hashes complete canonical provider bytes.
Do not insert placeholders in a file named `provider.json` and call it validated.

The supplied corrected deployment directory contains profile/assets, not the
required real binding sources. Other provider/slots files under historical
pytest temporary directories are synthetic fixture outputs, not owner evidence.
Required offline gaps block A completion and release of B; independent preparation
is preserved rather than relabeled all-ready. Later inputs require a new exact
profile seal and verification, not alteration of this recorded verdict.

## FUTURE ONLY — exact ordered native-readonly checklist

Every item is NOT EXECUTED. This is a specification, not a runnable script or
permission. Stop at the first exact blocker; no launch, close, reconnect repair,
settings change, input, gameplay navigation or account switching is a fallback.

1. Verify intended existing BlueStacks instance identity. Require approved exact
   account/slot/configuration selection and a separately authorized existing
   instance; reject ambiguous identity. Do not launch or select a different one.
2. Verify owned process/window binding. Retain exact process creation identity,
   exact title/root/render ownership and unique geometry; PID/title alone cannot
   prove ownership. No guessed process or window is accepted.
3. Read package/version from guest PackageManager using the already reviewed
   transport. Require a separately reviewed capability issuer first. Future token
   prefix is `[reviewed-bundled-client, -P, owned-isolated-server-port]`; transport
   tails are `[nodaemon, server]`, `[devices, -l]`, `[connect, verified-endpoint]`,
   `[-s, discovered-serial, get-serialno]`, and
   `[-s, discovered-serial, shell, dumpsys, package, com.supercell.clashofclans]`.
   These are construction tokens, NEVER direct shell instructions. Retain exact
   listener/daemon/connection ownership, two equal complete package records per
   bracket, finite budget and owned-daemon cleanup preserving existing servers.
   Current absence of an issuer is an exact blocker, not permission to activate ADB.
4. Bind observed build to selected instance/profile/provider. Require fresh source
   and serial rechecks, exact versionName/code comparison and account/config/tree/
   asset seals. Revoke on any change; never substitute a historical/public version.
5. Verify renderer/window geometry. Compare actual bound renderer and window
   geometry with private profile `size` and approved configuration expectations;
   source-file dimensions do not prove current geometry, DPI or native binding.
6. Perform read-only capture. Only under a fresh capture-specific approval, exact
   current binding and privacy rules. Pixels remain transient; no full-frame disk
   persistence or provider upload follows from this checklist.
7. Run observer against current Home/Battle/Army states only when separately
   authorized. Use only already-present authorized states; do not navigate to
   obtain them. Missing state or uncalibrated artwork is a blocker, not a reason to
   click. Battle/postcommit evidence remains absent from the supplied fit.
8. Verify no-input denial paths. With physical-input ports unavailable, prove
   absent/stale/wrong binding/build/profile/approval and ambiguous ownership deny
   before input. Do not test denials by sending real gameplay input.
9. Verify private evidence confinement. Check exact ignored roots, path/reparse
   confinement, approved private sinks and sanitized scalar reports; no images,
   account mappings, calibration payloads or private digests enter review archives.
10. Return NATIVE_READONLY_PASS or exact blocker. PASS requires all authorized
    steps genuinely proved and recorded against the exact immutable bindings.
    Missing authorization, implementation, input or observation yields its exact
    blocker. No PASS here grants runtime, attack, merge, publication or release.

## Verification boundary

Focused synthetic tests and the applicable canonical `pytest tests/` selection
run from a byte-exact Git export, never source-checkout imports. Tests performing
actual process/Job/window inspection, native peers or host process snapshots are
excluded under this card's stricter no-native boundary. The distribution build
probe is excluded to avoid build-isolation dependency installation. The evidence
package records exact deselections and real results, not a claimed full-suite run.
File-only temporary filesystem tests and injected/inert native seams remain
eligible. No old test count is reused as current execution evidence.

The sanitized package contains only tracked source, full/incremental patches,
entry manifest, test logs, comparison/privacy evidence and this checklist. Private
profile/assets/frames and the confidential manifest remain local and separate.
The frozen checkpoint is partial preparation pending required inputs, not an
independent review verdict. No review loop, replacement card or production change
is authorized by this document.
