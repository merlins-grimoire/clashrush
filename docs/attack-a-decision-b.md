# Attack A — Decision B implementation contract

Status: implementation in progress; NOT review PASS or native/live admission.
Owner authority: clashrush-mvp:t_4b4717dd, attachment 622, sections Revised MVP
Admission Contract, Roots to Complete and Required Test Coverage. This scoped
amendment supersedes strict-package requirements in earlier readiness documents;
those documents and prior FAIL/partial/bake-off packages remain historical evidence.

Base commit 7df3c6633523f3cc0befd15da4efa4f03f14fe94; base tree
424f407d65f3bd5c1e14097e7c5e3f09e03f2e78.
A1_STRICT_GUEST_BUILD_PROVIDER_DEFERRED_BY_OWNER, not implemented or PASS.
No direct-adbd implementation, ADB server startup/query, package installation,
account changes, gameplay run, or expanded image permission belongs to this work.

## Frozen amendment

### Instance-centric owner policy

The later owner decision on the same A card (attachment 627) supersedes the
account-identity portions of Decision B. Final research disposition is
`HOME_ACCOUNT_NAME_RUNTIME_GATE_RETIRED_BY_OWNER`, not matcher PASS: revision 003
rejected 4 of 9 legitimate development positives, accepted 74 altered ambiguous
cases, and lacked the required corpus. Those are owner-supplied historical
results, not a fresh experiment. All research remains preserved; the sealed
session remains unopened. No comparator/threshold or generic `match_unique` edit.

Normal owner convention is one operational game account per registered emulator
instance. Registry schema 2 uses exactly five explicit `slot_id`/`internal_name`
pairs; `display_label` is private metadata. There is no schema-1 display-name
fallback in the revised policy provider. Provider schema 2 removes marker/ROI/
threshold authority and declares `IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED`.
Opaque `account_ref` in older receipts is a routing reference, not a Player Tag.
New `INSTANCE_SLOT_HOME` facts require fresh supported Home and exact run binding;
old `SAME_ACCOUNT_HOME` receipts retain their historical byte/validation meaning.
Neither fact alone can mint gameplay SUCCESS. Unknown/update/login/reconnect/
tutorial/unsupported states remain denied. Optional package metadata is never
invented; contradictory supplied metadata still rejects.

Legacy Setup/tag-export routes remain separate, unpromoted routes; this draft
does not migrate their required tag fields or make them ordinary policy admission.
Unresolved outcomes/ACTIVE/predecessor closure still block admission. A manual
account switch must never erase or replay an uncertain prior transaction.

Future queue records target stable slot/internal instance and configuration
generation. On a fresh supported observation, replace stale builder/resource/
capability data with a new observation generation, recalculate `next_check_at`,
persist/read back, then retire normally. Cached state is only a hint, never
transaction authority. A known switch/reset/reinstall/clone/reassignment marks
`NEEDS_REFRESH` and due-now; future scheduling needs bounded maximum refresh age.
No scheduler framework, observation-state migration or fleet code is added here.

Future Discord/operator commands resolve a label to exact slot/internal instance,
generation and authenticated context. They enter a durable local idempotent inbox,
never gameplay directly. `RUN_NEXT` is one-shot priority after the active/unresolved
run reaches its safe terminal boundary; it does not change real timers or interrupt.
`REFRESH_NOW` requests observation/recalculation. `PAUSE_SLOT`/`RESUME_SLOT` only
change eligibility. `CANCEL_PRIORITY` removes pending, not claimed work. `STATUS`
reports durable truth. Requests retain command ID, scope/generation, issuer/time,
expiry, state and result reference. Resolve active/unresolved work first, then a
valid manual priority, then due/fair scheduling. Lost acknowledgments never replay
work; Discord availability cannot change local gameplay truth. Gems stay forbidden.

Partial draft status: instance-policy tests and synthetic normal-success exact-Je
retirement/READY/closure tests pass. This is NOT A2/A3 acceptance. Production
calibration observer/private sink/native mapping and owner-event/Status wiring
remain incomplete; legacy optional metadata migration is not certified. No native
calibration, installed-path validation, complete candidate freeze, whole-candidate
independent default review or A PASS exists. A normal retirement draft only runs
after complete durable gameplay success and worker retirement before D; failures
and calibration never use it to kill Je or claim gameplay success. The independent
review must include cross-store failure ordering and concrete native retirement.

The unattended worker's desktop focus request was refused before delivery by the
approval system. Metadata listing succeeded, but was not interactive approval.
No alternative input/capture route was used. Resume native preparation through an
approval-capable interactive session; do not restore name/package research gates.

### Retained non-account requirements

Missing package/version is represented as unavailable, not fabricated identity.
Available contradictory metadata rejects as PACKAGE_METADATA_CONFLICT. An old
package fact never supplies UI, account, window, profile or run authority. Preserve
exact slot/internal-instance/configuration, retained process/Je ownership and Jw
exclusion, root/render HWND and native geometry, target-host profile, candidate,
run generation, revocation and fresh supported English UI. A visible colorful
window is not sufficient. Unknown/update/store/login/
reconnect/tutorial states block without click-through or recovery input.

Use the existing P/W topology, issuer, channel, authority and journal. Calibration
has no input owner or executor. Native render dimensions are distinct from the
1280x720 framebuffer; calibration must name the exact observed dimensions and
mapping, not silently resize. Frames remain transient; only explicitly allowlisted
narrow evidence may persist with protected path confinement and independent readback.
An evidence digest is not completed calibration, held-out verification or gameplay
SUCCESS. A normal-success receipt cannot clear ACTIVE until worker, player family,
windows, handles and durable readback are proved; failure does not kill Je/player.

Owner intervention is a typed durable local event, idempotent for exact evidence,
rejecting conflicting evidence. It has no gameplay retry method. Discord delivery
is pending unless an existing configured/reviewed adapter is available; transport
failure never reopens admission. Output excludes names/tags/topology and pixels.

## Consumer map

- mvp_calibration: scope/capability, optional parent metadata, observation brackets.
- mvp_policy_run: parent ready message, calibration/runtime worker dispatch,
  finalization and retirement.
- mvp_policy_ingress: protected approval, scope/specification, reservation barriers.
- mvp_policy_sources: schema-2 instance provider, configuration, fresh Home and profile.
- mvp_deployment_integration: InstalledProfileReader, build_policy_executor,
  build_native_executor and per-event evidence adapter.
- mvp_deployment_profile: InstallationEvidence, require_installation, FrameObserver.
- mvp_policy_evidence / mvp_session_authority: BUILD_CHECK historical field semantics,
  receipts, calibration facts, closure and restart denial.
- tools/attack_native_readiness.py: historical query specification and missing-input
  report must not reintroduce mandatory package proof into the revised path.
- CLI/status, calibration-only-contract.md and attack-native-readiness.md: historical
  strict-provider gates do not override this current amendment.

## Reuse and reference boundary

Reuse the pinned donor trace in calibration-only-contract.md (BasePilot then
CoC_Bot then ClashAutomation) and existing retained-family/window/PrintWindow,
private file and P/W journal seams. No donor provides the required exact ownership,
authority or safe parent success-retirement contract; their blind/global/name-based
shutdown remains incompatible. Do not transplant ADB or gameplay cleanup.
Authoritative project workflow: development-workflow.md sections 1A, Delivery and
evidence, 3A and 4A. The target-host profile and reconciled atlas hashes are
bec50bfe3c12866ea56012d26e01295eedfcdf1311ec7825b77e0b031cc77528 and
d7567698db7a27762303113aa067e6474e7f5493377ea4423852e3c3642ec1db.
Atlas development evidence is not production calibration or current-account proof.

Whole-candidate separate default review, exact-export canonical tests and harmless
Windows topology verification remain required. One correction per established root;
no new lane or retry reset. No completion claim until every required root is verified.
