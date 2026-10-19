# Fixed parent package observation contract

## Current continuation status (supersedes implementation claims below)

**PARTIAL CORRECTION CHECKPOINT; full calibration composition remains incomplete.**
No transport PASS token, native qualification, calibration acceptance or A closure.
The original independent FAIL and its history remain authoritative evidence.

The F1/F2/F3 corrective pass now provides:
- a detached original-scope latch after the final current/clock callback, shared
  by issuer checks before spawn and before successful-result acceptance;
- retained read-only/no-delete executable and ancestor handles across metadata,
  observation and child use, rather than treating a digest snapshot as a pin;
- rejection of future/changed/regressing parent facts against trusted worker
  samples, including after fallible window work, and an explicit ordering bracket
  for package observation, window start, capture start and completion. This is
  not a numeric freshness guarantee; no package TTL is invented.

### C1 is fail-unavailable, not qualification

The native fixed-command leaf now returns READ_ONLY_PACKAGE_TRANSPORT_FAILED
without invoking a client. Its closed argv validator remains, with no bypass flag.
Synthetic leaf tests still exercise the P/W fact/authority boundaries; they do
not establish a usable real transport. Ordinary read-only adb commands can perform
server control before the requested operation. A server sample, file pin, child
Job or later output discard cannot exclude this side effect.

Static reference: AOSP packages/modules/adb commit
`1cf2f017d312f73b3dc53bda85ef2610e35a80e9`, client/adb_client.cpp,
`__adb_check_server_version` (lines 254-340) and `adb_connect` (356-378):
https://android.googlesource.com/platform/packages/modules/adb/+/1cf2f017d312f73b3dc53bda85ef2610e35a80e9/client/adb_client.cpp
The file SHA256 is
`48a931322850092e8b67ae1538fd799a169019e2e3c05d3af14ebaa6b111247e`.
Connection failure can launch a server; version mismatch invokes adb_kill_server
then launch_server. Upstream source is not proof of the installed vendor binary.
**DOC GAP:** no verified vendor fail-without-server-control mode or immutable
vendor source equivalence is available for the exact installed bundled client.
No actual adb binary was characterized or executed. That remains behind the
separate software review gate, including disappearance/version-mismatch cuts.

File-pinning reference: Microsoft CreateFileW, Parameters/dwShareMode and
dwFlagsAndAttributes:
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
FILE_SHARE_READ excludes write/delete opens (including existing incompatible
handles); omitted FILE_SHARE_DELETE also prevents rename until handles close.
OPEN_REPARSE_POINT plus retained ancestors/final-path checks reject redirection.
Harmless temporary files and an owned copied Python child exercise this; installed
BlueStacks files are never modified. No vendor/runtime proof follows.

### Remaining I1-I7 delivery obligations

This checkpoint does NOT implement or discharge I1-I7. Existing ingress remains
denied; no new player is launched into an unusable transport path. No blanket
activation, second runner, dummy production observer or invented specification is
introduced. Closed specification, exact selected-instance ingress, supported-screen
observer, protected sink/readback, measured held-out calibration and fresh review
remain required, along with the specified bounded natural startup behavior.

Recovered schema-3 metadata was revalidated: 146/146 referenced asset hashes match;
the profile validates with runtime=False against its own historical tree. It is
not current-tree bound or runtime reviewed. This is metadata only, not absent
evidence and not same-account recognition proof. No private values were printed.

I7 source trace: ParentProcessTopology exposes terminate_worker and non-killing
dispose, not normal player retirement. PolicyParentSession explicitly retains
ACTIVE on revoke/finalize/dispose. LifecycleSupervisor.stop belongs to the legacy
owner and unconditionally terminates its own Job; it is not an already-composed
P/Je successful-retirement source. Do not fabricate a stop proof, transfer legacy
ownership, infer player absence from Jw, or add a termination shortcut. The exact
existing normal-success retirement source and its prerequisites remain unresolved.

All earlier implementation descriptions below are preserved historical subset
context, not a claim that native fixed_command still invokes a client.

Owner's fixed-read-only bundled transport decision supersedes only the prior
transport denial for CALIBRATION_READ_ONLY. Gameplay activation stays denied.
Baseline 5b4a2221fd4a146f2779ba42ad132b34c647feb8,
tree a203dee88c58935f15e1d85a31d648edcd23a856.

## Reuse and authority

Retain the ordered donor trace and exact pins/hashes in calibration-only-contract.md:
BasePilot window identity cannot prove guest identity; CoC_Bot general transport,
reconnect, shell, mutation and minitouch remain rejected; ClashAutomation's
name-based control remains rejected. No donor client/binary is imported.
Reuse mvp_installed_build._source, BuildBinding, native process/config/TCP leaves,
minimal client environment and diagnostic_child_job's owned subprocess boundary.
Do not activate the old InstalledBuildTransport or its daemon/connect behavior.
P alone constructs/invokes the new observer. Existing issuer checks remain intact.
W receives a frozen parsed fact through PlayerReady, never transport/path/endpoint.
No second runner or wire protocol. Je/Jw and player retirement are unchanged.

## Frozen command vocabulary

Executable: HD-Adb.exe beside the trusted exact HD-Player.exe installation path
already used by the parent. Resolve real regular files, reject redirects and
multiple plausible bundled candidates (HD-Adb.exe / adb.exe), pin SHA256 and
record sanitized path class plus native file/product version where available.
Never search PATH, download, install or enable any setting.

Each argv is an internally constructed list with prefix
`[bundled_executable, '-s', exact_configured_loopback_endpoint]`:
- SERIAL: `['get-serialno']`
- PACKAGE: `['shell', 'pm', 'list', 'packages', 'com.supercell.clashofclans']`
- RUNNING: `['shell', 'pidof', 'com.supercell.clashofclans']`

No caller command fragments. SERIAL must equal the exact configured endpoint;
PACKAGE must be exactly one `package:com.supercell.clashofclans` line; RUNNING
must be exactly one positive decimal PID line. Installed presence is not running
proof. Unsupported grammar, extra/duplicate records, stderr, nonzero status,
partial/truncated/oversized output and timeout all block. No retry/fallback.
The existing server must already be present, bundled-image bound and connected
to the selected retained player listener. No explicit start-server/connect command.
Clients remain atomically owned by their diagnostic Job, including unexpected
client descendants; no ownership over a preexisting server is inferred.

## Binding and limits

Use the existing exact instance command-line + configuration digest/mtime +
unique loopback listener/retained process creation identity proof. Require static
`bst.enable_adb_access="1"`. No first-device enumeration. Scope binds slot,
instance, run/generation, commit/tree/profile, expected package and private root;
BuildBinding additionally binds exact configuration and retained player identity.
Check issuer plus source/executable/server identity immediately before each child
spawn and after bounded retirement, before parsing. A callback revocation is
rechecked after all fallible callbacks. Any failure revokes this observer.
Bound each child to five seconds and combined stdout/stderr to 4096 bytes;
stdin DEVNULL, shell False, close_fds True, minimal environment, no raw diagnostics
in exceptions. The whole observation is one-shot and bounded by the scope lease.

## RED and completion gates

Tests: fixed command shape/exact running match; PATH ignored; missing/ambiguous
bundle; executable drift; wrong/duplicate endpoint and instance; stale or revoked
issuer before/during spawn; binding mismatch; malformed/substr/duplicate/absent
output; timeout/nonzero/oversized output; bounded owned-child capture; typed fact
channel roundtrip and wrong-scope rejection; no capture after revocation.
Run affected and canonical tests from exact export; seal archive/full+delta patches
and verify reconstruction/privacy. Parent performs independent review. No child
ADB invocation, BlueStacks startup, game capture or input.

Production calibration capture requires an actual reviewed calibration specification
and protected evidence source; neither may be invented from synthetic tests.
Missing concrete specification/source remains an explicit activation blocker, not
permission to fall through to gameplay or fabricate a successful calibration.

## Exact integration and remaining acceptance boundary

`accept_parent_worker(..., package_binding=...)` performs the P-only observation
after acknowledged player resume and before PlayerReady. The existing codec carries
ParentPackageFact in PlayerReady. `execute_calibration_worker` consumes that fact
into CalibrationSources, which bypasses any package-query leaf and advances only
to window/evidence observation. Neither the fact nor a captured observation grants
SUCCESS, READY, player retirement or calibration acceptance.

The installed `run_policy_command` and `worker_main` remain default-denied and still
refuse the missing production calibration source. No automatic startup handoff is
claimed. A concrete window/supported-screen/private-evidence composition is NOT
implemented in this candidate. PolicyPlayerView cannot simply be reused because
its bind() constructs InputOwner. Its exact native HWND/capture leaves are reusable,
but the approved supported-screen/ROI specification and protected capture writer
must be supplied/reviewed before production composition can be completed. No
calibration-spec.json or calibration.json exists at the documented deployment
paths in either this worktree or the rebuild installation; no private template,
ROI, threshold, or native capture evidence was fabricated.

This candidate therefore supplies a reviewable transport/channel software subset,
not the full owner's post-PASS runtime acceptance token. Parent must review this
explicit limitation rather than treating synthetic window/digest leaves as runnable
production calibration. The existing parent/worker topology is not reconsidered.
