# Current architecture index

## Purpose and authority
This page indexes revision domains, as-built navigation and document status under
[project law](../../.hermes.md). It is a status boundary, not a new implementation
design, task graph, runtime admission or release approval. Explicit current owner
instructions and the current gate with its attached exact decision package take
precedence. Static audit findings are not product acceptance.

## Current gate and milestone
The later owner policy on `clashrush-mvp:t_4b4717dd` overrides in-game account
identity requirements for its isolated instance-centric lane: stable registered
slot/internal instance is the unit; names/tags/account-marker OCR are not admission
authority. Strict guest-build/package provenance is deferred, not proved. See
[the current partial implementation ledger](../attack-a-decision-b.md#instance-centric-owner-policy). This
override does not accept its unreviewed draft, manufacture calibration, retire
native/review/deadline/input gates, or authorize gameplay. The A2/A4 gates remain
open; historical account-centric wording below is scoped historical evidence.

**`clashrush-mvp:t_df11c35e` remains blocked; no new implementation/native/live lane
is authorized by this documentation adoption.** Its frozen REASSESS status coexists
with later explicitly scoped offline exceptions: comments 171/196 record PLAN_READY
implementation and offline PASS, while comments 197/198 retain NATIVE_BLOCKED and
UI assessment. The subsequent quantity A→B→C→D→E lane is complete; B required
changes, C used the single correction, D `t_edd630fa` passed, E `t_7394eb97` briefed.
Do not erase those completed exceptions or treat them as a standing execution grant.

The current product milestone is the supported one-instance Home path from installed
entrypoint through startup, deployment, observed natural result, supported Home,
owned shutdown and truthful completion, without Hermes gameplay rescue. The offline
repair is complete; native/profile/installed-build/live acceptance is not.

Governance adoption is documentation-only, pending fresh independent exact-tree
review and separately authorized integration. Its standing policies live in
[development workflow](../governance/development-workflow.md) and
[spending policy](../product-policy/spending.md); the [live template](../governance/live-development-envelope-template.md)
remains inactive. No code-candidate integration follows by implication.

## Revision domains
Observed clean repository identities for this adoption baseline follow. A later
documentation candidate has its own frozen handoff identity, not software acceptance.
Historical as-built code identity is retained separately from current main HEAD.

| Domain | Exact identity | Status |
| --- | --- | --- |
| Main at governance-adoption baseline | Commit `9e6aa1ccefd18b8c957bcc2bf62327bffdb92d38`; tree `2c41f26d39be1e86f1db6dd063b16e2b176e6c72` | Clean `main`; authority documentation integrated, not the repaired attack candidate. |
| Completed quantity/profile candidate | Commit `33e347798bb026577fc46df4c802f6a8e9fbc120`; tree `f8bca55d26b3e79051ecd70b93cb29465f07ad71` | Clean `fix/updated-ui-source-quantity`; final D offline PASS, E briefing complete; native/live gates open. |
| Earlier PLAN_READY candidate | Commit `6c48ef1e63d93acc3d5e09e88373311119b656d9`; tree `6bbc5731e5cf302778e6af1f32eb6f1580ddaa15` | Clean `feat/plan-ready-finalization`; earlier scoped offline PASS, not latest profile repair. |
| Historical as-built code baseline | Commit `195dddadcfe5a184e839b33f4d7d82792cf5bfe0`; tree `f5eea1d195d6e016569ff10532a1c17ce8ff5ba4` | Code unchanged by authority documentation through baseline main; older route/receipt/finalization, including immediate End Battle after depletion. Not current HEAD. |
| Accepted historical S004 candidate | Tree `30a166be889531a4b286a291d466bc4ef1e6d45f` | Scoped offline software acceptance for later S004 route/support work; not main, not runtime/native/live admission, not final MVP acceptance. |
| Corrected B0 preservation/specification | Tree `460dfeeeb040493e5ab5f3eb54b24daeb083bbfd` | Preserved experiment/specification evidence; rejected/unaccepted as software. |
| Original B0 | Tree `6b66f114db97efc90693c88b561da7dd78056750` | Historical predecessor evidence only. |

Never combine behavior or acceptance across these domains because filenames or
symbols look similar. Git identity is not a review verdict.

## As-built main
The completed REPOSITORY_REALITY_AUDIT package, especially the final reconciliation
in REPOSITORY_REALITY_MAP.md and the CURRENT_RUNTIME_MAP.md trace, supplies static
evidence for this index. The source links below refer to the as-built main domain,
not later S004 or B0 behavior.

- [Packaging](../../pyproject.toml) declares one console entrypoint through
  `clash_rush_rebuild.cli:main`; module execution delegates to the same function.
- `mvp-run` creates a durable grant; it does not itself run the attack.
  `mvp-visit-one` launches the supervised deployment worker.
- `DeploymentEngine._navigate` routes **HOME → MATCH → ARMY → BATTLE**.
  Complete inventory is currently discovered on Battle, not at the Army gate.
- `DeploymentEngine._exit` immediately ends the battle after depletion, then follows
  confirmation/result/return Home. This is not the accepted later natural-result
  S004 path. The older receipt/finalization behavior is not B0's proposed closure.
- Scheduler, schema-v2 lifecycle, Discord and application-service libraries are
  shipped/discoverable under source packaging, but not wired into the current
  operator attack composition. Packaging eligibility is not a freshly verified
  wheel manifest, and parser reachability is not runtime approval.

## Acceptance and unresolved evidence
| Evidence | Boundary |
| --- | --- |
| Current main | As-built; unaccepted as complete MVP. |
| S004 candidate | Scoped offline acceptance only, at its exact tree above. |
| Corrected B0 | Rejected/unaccepted specification evidence, not accepted software. |
| R9B | UNKNOWN; do not retrofit HOME-readiness acceptance. |
| DBG2 / audit stream | Owner-parked/unresolved; not accepted finalization authority. |
| Broad fleet / Builder / Discord plans | Deferred/historical product roadmap, not current MVP tasks. |

## Current MVP execution map
Navigation only, not acceptance or permission to execute. Symbols identify the
static chain documented by the audit; the links use actual current-main filenames.

| Stage | Source and seam |
| --- | --- |
| Console / grant / visit dispatch | [cli.py](../../src/clash_rush_rebuild/cli.py): `main`, `_start_mvp_session`, `run_native_mvp_visit` |
| Durable session grant / admission | [mvp_session_authority.py](../../src/clash_rush_rebuild/mvp_session_authority.py): `DurableSessionAuthority` |
| Supervised worker | [mvp_deployment_worker.py](../../src/clash_rush_rebuild/mvp_deployment_worker.py): `run_supervised_attack`; [diagnostic_child_job.py](../../src/clash_rush_rebuild/diagnostic_child_job.py): owned-child boundary |
| Native composition | [mvp_local_native.py](../../src/clash_rush_rebuild/mvp_local_native.py): `run_native_mvp_visit` |
| Player lifecycle / Win32 boundaries | [lifecycle.py](../../src/clash_rush_rebuild/lifecycle.py), [win32_lifecycle_host.py](../../src/clash_rush_rebuild/win32_lifecycle_host.py), [win32_runtime.py](../../src/clash_rush_rebuild/win32_runtime.py), [win32_state_io.py](../../src/clash_rush_rebuild/win32_state_io.py), [win32_primitives.py](../../src/clash_rush_rebuild/win32_primitives.py) |
| Executor integration | [mvp_deployment_integration.py](../../src/clash_rush_rebuild/mvp_deployment_integration.py): `build_native_executor`, `NativeDeploymentExecutor` |
| Profile / observer / vision | [mvp_deployment_profile.py](../../src/clash_rush_rebuild/mvp_deployment_profile.py): `FrameObserver`; [mvp_deployment_vision.py](../../src/clash_rush_rebuild/mvp_deployment_vision.py) |
| Deployment / guarded input | [mvp_deployment.py](../../src/clash_rush_rebuild/mvp_deployment.py): `DeploymentEngine`; [mvp_deployment_input.py](../../src/clash_rush_rebuild/mvp_deployment_input.py): `GuardedDeploymentInput` |
| Rich receipt | [mvp_deployment_receipt.py](../../src/clash_rush_rebuild/mvp_deployment_receipt.py): `record_proof` |
| Legacy transaction / observation adapter | [mvp_local_gameplay.py](../../src/clash_rush_rebuild/mvp_local_gameplay.py): `LocalMvpBot`; [mvp_local_runtime.py](../../src/clash_rush_rebuild/mvp_local_runtime.py): `ConcreteAttackVisitPorts` (legacy deployment executor is retired, not a fallback) |

## Document status index
Statuses delimit authority, not permission to delete or move evidence. Compatible
safety/provenance remains useful; no retained document is a fresh implementation
instruction. This includes all partially superseded, historical and unknown tracked
Markdown documents identified by the audit, not only the seven banner targets.

| Document | Status / retained boundary |
| --- | --- |
| [README.md](../../README.md) | CURRENT NAVIGATION; product vision and canonical pointers, not architecture/runtime approval. |
| [development-workflow.md](../governance/development-workflow.md) | Canonical standing offline autonomy, privacy and future-live policy under project law. |
| [spending.md](../product-policy/spending.md) | Canonical owner currency policy; separate from action/executor/run authority. |
| [live-development-envelope-template.md](../governance/live-development-envelope-template.md) | DRAFT / INACTIVE / NOT AUTHORIZED; all bindings unset. |
| [hermes-settings-observations.md](../reference/hermes-settings-observations.md) | Version-qualified reference snapshot, not effective-runtime certification. |
| [complete-donor-spine.md](../complete-donor-spine.md) | PARTIALLY SUPERSEDED; earlier composition/provenance. |
| [deployment-repair-contract.md](../deployment-repair-contract.md) | PARTIALLY SUPERSEDED; main-candidate/history evidence, not accepted S004 End Battle policy. |
| [mvp-slice-003-account-ready.md](../mvp-slice-003-account-ready.md) | PARTIALLY SUPERSEDED; separate readiness-only path, not universal attack admission. |
| [mvp1-local-donor-manifest.md](../mvp1-local-donor-manifest.md) | HISTORICAL_ONLY; frozen donor checkpoint. |
| [mvp2-local-donor-manifest.md](../mvp2-local-donor-manifest.md) | PARTIALLY_SUPERSEDED; older composition/blocker. |
| [startup-debug-transplant.md](../startup-debug-transplant.md) | PARTIALLY_SUPERSEDED; SCOPED HISTORICAL EXCEPTION, exact exception scope unresolved. Local full-frame opt-in does not authorize ordinary retention or future tasks. |
| [ambiguous-windows-launch-contract-r11f.md](../../research/ambiguous-windows-launch-contract-r11f.md) | PARTIALLY_SUPERSEDED; historical implementation contract, standard subprocess prescription replaced by later owned-child work. |
| [delivery-guardrails.md](../../research/delivery-guardrails.md) | PARTIALLY_SUPERSEDED; safety/privacy useful, old sequencing/full-release scope not current authority. |
| [integration-plan.md](../../research/integration-plan.md) | PARTIALLY_SUPERSEDED; deferred product roadmap. |
| [production-backlog.md](../../research/production-backlog.md) | PARTIALLY_SUPERSEDED; deferred ideas, not ready work. |
| [discord-fleet-control-plan.md](../../research/discord-fleet-control-plan.md) | PARTIALLY_SUPERSEDED; deferred fleet/Builder/Discord scope. |
| [audit-stream-contract.md](../../research/audit-stream-contract.md) | UNKNOWN / DBG2 owner-parked; unresolved finalization evidence. |
| [scalar-only-readiness-helper-design-r10a.md](../../research/scalar-only-readiness-helper-design-r10a.md) | UNKNOWN; unresolved design, not HOME-readiness acceptance or implementation authority. |
| [home-diagnostic-r5.md](../../research/home-diagnostic-r5.md) | HISTORICAL_ONLY; diagnostic report. |
| [home-source-diagnostic-r9b.md](../../research/home-source-diagnostic-r9b.md) | HISTORICAL_ONLY report; R9B outcome remains UNKNOWN. |
| [no-scalar-diagnostic-r11d.md](../../research/no-scalar-diagnostic-r11d.md) | HISTORICAL_ONLY; R11 diagnostic report. |
| [window-binding-r1.md](../../research/window-binding-r1.md) | HISTORICAL_ONLY; earlier diagnosis, not current gate. |
| [additional-donor-assessment.md](../../research/additional-donor-assessment.md) | PARTIALLY_SUPERSEDED; donor/license evidence, old broad roadmap/status. |
| [home-readiness-design-r10.md](../../research/home-readiness-design-r10.md) | PARTIALLY_SUPERSEDED; parked design lineage, not HOME acceptance. |
| [runner-comparison.md](../../research/runner-comparison.md) | PARTIALLY_SUPERSEDED; seam-specific donor evidence, not whole-product architecture selection. |
| [slice-1-contract.md](../../research/slice-1-contract.md) | PARTIALLY_SUPERSEDED; version-specific inert foundation only. |
| [slice-1-lifecycle-decision.md](../../research/slice-1-lifecycle-decision.md) | PARTIALLY_SUPERSEDED; historical proposal/status, retained rationale. |

## Completed candidate evidence and preserved contract
D's `UPDATED_UI_QUANTITY_D_REVIEW_PACKAGE.zip` (task `t_edd630fa`) has SHA-256
`dc0bd078efa0c9f75c76d5b21ad974f63b10cbd4665f0c6d07de79d41e17763a`.
The actual D report/result and E briefing, not merely done status, establish offline
PASS: 2,823 canonical passes and 28 independent probes were reported by D; its 105
cross-review focused passes overlap coverage, not an additive unique total. This
adoption did not rerun production tests. Candidate-internal “D pending” text is
frozen chronology; do not edit that immutable candidate to refresh it.

`PROFILE_RECALIBRATION_SUFFICIENT` means fit to four supplied precommit stills only.
The fit has 36 rejecting derived negatives; held-out/dynamic support is unproved.
Unobserved states and postcommit artwork are synthetic negative-only scaffolding;
installation/intervention ports were inert doubles. Native account/build/geometry,
exact installed artifact, held-out/dynamic calibration and exact-profile admission,
postcommit Battle/deployment/result, fee-debit timing, and separate live authority
remain open. Recognition of a fee is not proof of its debit timing.

Source-bound support remains settled: positively **EMPTY → zero CLAN entries and
no support input**; supported **POPULATED → exactly one semantic CLAN entry**;
unknown/missing/ambiguous evidence rejects. Keep complete source geometry and fresh
source-to-Battle reconciliation. Do not reopen mandatory-populated reserves, revert
to an obsolete single-page roster, or expand into arbitrary armies/scrolling.

The later accepted candidate preserves natural Result → Return → fresh same-account
Home and truthful owned shutdown, not immediate End/Surrender/restart fallback.
It retains receipt schema 4 separately from SQLite finalization schema 3. Preserve
the gate's P-created player, P-owned non-killing Je and separate automation-only Jw
(Je/Jw, not inherited/nested kill-on-close containment). P has no physical-input
rescue. At/after deadline D, only the sole worker's live, exact same-run proved
OWNED_HELD obligation may perform its one minimum inverse release; no guessed UP,
MOVE/focus/DOWN/capture or emulator termination as administrative cleanup. Such a
post-D emergency release leaves UNCERTAIN and a recovery barrier, never SUCCESS.
Worker retirement is not player absence. Administrative finalization from complete
immutable pre-D facts remains distinct from gameplay; it may occur after D without
inventing a persistence-before-D deadline. These are preserved accepted candidate
constraints, not claims about the older code in this documentation worktree.

## 2A — Finite future Home acceptance
After all native/profile/installed-build prerequisites pass and a separate live
envelope is explicitly adopted, the planned finite Home acceptance is:

- three complete successful visits;
- at least one clean runner restart among those visits;
- one separate predeployment Stop test;
- every attempt recorded, including failures;
- no statistical reliability claim from these small counts.

This is a future acceptance contract, not current live permission or an attempt
budget. Use the installed runner without Hermes gameplay rescue, label assisted
exploration separately, preserve exact-five installation assumptions until reviewed
migration, and report either accepted supported behavior or the precise remaining
defect. The [inactive envelope](../governance/live-development-envelope-template.md)
must not inherit numeric attempt/time defaults from this target or old drafts.

## Next step and later roadmap
Vision/OCR remains **paused before creation**: no cards, branch or worktree exist.
The saved setup script must not be executed unchanged. After governance is freshly
reviewed and separately integrated, a later specifically authorized step may plan
and create a fresh lane from the adopted exact baseline. There is no lane to resume
or rebase. Reuse sealed D fit evidence, preserve held-out distinctions, and do not
implicitly integrate the attack candidate or grant capture/provider authority.

Home progression, Builder Base, multi-account and product operations remain the
[existing deferred architecture sequence](../../research/integration-plan.md), with
[backlog/review blockers](../../research/production-backlog.md). OBS05 and roadmap
research remain CHANGES_REQUESTED despite done cards; not CORE_STATIC_READY or
implementation-ready. Local P1 observability remains required before production;
optional remote/fleet features are later. No new task graph is created here.
