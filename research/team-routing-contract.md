# Authenticated Team routing and heartbeat persistence

## Scope

This slice composes the frozen DIS1 canonical control protocol in `discord_control_protocol.py` with the DIS2 service-owned authenticated session boundary in `runner_enrollment.py`. It is an in-memory reference core for a later transactional adapter. It opens no socket, contacts no Discord or network endpoint, reads no private configuration or credential store, and owns no scheduler, lifecycle, gameplay, native-input, spending, Setup activation, or resource authority.

A heartbeat proves only that the current authenticated connection recently reported the already service-active configuration generation. It cannot select a Team, activate a generation, create a command, change desired state, invoke local work, or authorize any other capability.

## Donor and reuse decision

The pinned donor inventory was rechecked from the tracked exact-seal assessments because ignored donor checkouts are absent from this isolated worktree and its parent:

- BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4`, `app/ui/qt/bot_controller.py`, MIT: its relevant facade constructs and controls gameplay objects from mutable UI/thread state; it has no authenticated runner identity, Team generation, heartbeat, or durable command ledger.
- CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24`, `app/app.py` and `src/utils.py`, MIT: its HTTP routes accept mutable identifiers/status and directly control global instance objects; adopting this seam would violate the authority and no-network boundaries.
- ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612`, `main.py`, MIT: its top-level loop directly owns process/gameplay orchestration and has no compatible remote-control persistence seam.
- Historical Clash Rush `949497bf0a543a43ec6ef39a8c897e366bc10362`, `src/clash_rush/cli.py`, no verified source-license grant: behavioral evidence only, with no compatible authenticated Team router.

No donor code is copied. The largest compatible proven seam is already local: DIS1 supplies exact canonical command/acknowledgement decoding, immutable identity, generation binding, monotonic runner application, and idempotent acknowledgement replay; DIS2 supplies credential revocation, exact channel binding, and service-owned non-forgeable session identity. `team_routing.py` adds only the missing composition, freshness, central ledger, and runner persistence reference behavior.

## Central routes and sessions

`CentralTeamRouter` snapshots a nonempty closed set of exact `TeamRoute` values and a heartbeat timeout of 1–300 seconds. It accepts only an exact `EnrollmentAuthority`. Every attach, heartbeat, delivery, and acknowledgement revalidates the exact returned `AuthenticatedRunner` against the authority and exact transport by class-qualified dispatch. The Team returned by that service-owned validation is authoritative; no caller Team value participates in routing.

Each successful attach allocates the next bounded Team connection epoch and atomically replaces the current connection object for that Team. A prior connection is stale immediately, even if its credential remains active. Forged, copied, post-return-mutated, wrong-channel, revoked, unknown-Team, and non-current session objects receive only the sanitized `authenticated connection is inactive` failure before any command bytes or acknowledgement mutation.

## Heartbeats and generation changes

A `ConnectionHeartbeat` carries only protocol version 1 and the runner's exact active configuration generation. The central service uses its own receipt time; no caller timestamp can make a connection fresh. Receipt requires the current authenticated session, exact protocol version, exact service-active generation, and a non-regressing bounded clock. Invalid reports do not refresh prior freshness.

Freshness is half-open: a heartbeat received at `t` is valid only while `t <= now < t + timeout`. Delivery and acknowledgement acceptance both revalidate the current authenticated session and this freshness. Activating a new `TeamRoute` generation clears the heartbeat immediately. A runner must report that exact new generation before routing resumes. Activation never rewrites commands or resets Team command epochs.

## Central command persistence

`register_command` accepts only DIS1 canonical bytes. Unsupported protocol versions, malformed/noncanonical data, unknown Teams, and inactive configuration generations fail before insertion. The service stores an independent decoded command and exact canonical bytes.

Command IDs are globally immutable. Exact byte duplicates return `DUPLICATE`; changed fields under one ID reject. Each new command must strictly exceed the greatest previously registered epoch for that Team, including across generation changes. Only one command per Team may be delivered-but-unacknowledged. Ambiguous delivery returns the exact same bytes. Later commands remain queued until the in-flight command has one terminal acknowledgement.

Delivery derives the Team from the exact authenticated current session, requires a fresh generation-matching heartbeat, and rechecks the stored command generation against the current service route before returning bytes. An old-generation command remains immutable and blocks rather than being silently rewritten, skipped, or delivered.

Acknowledgement acceptance strictly decodes canonical DIS1 bytes, derives Team from the current service session, requires a fresh heartbeat, and matches every repeated command identity field against the exact in-flight record. The exact terminal bytes are persisted before `RECORDED` returns. An exact retry is `DUPLICATE`; a changed terminal result, wrong Team, wrong command, wrong generation, malformed protocol, or non-in-flight acknowledgement cannot alter the ledger.

A production adapter must preserve route activation, connection epochs, last accepted heartbeat receipt/generation, immutable commands, per-Team greatest epoch, one-in-flight identity, and terminal acknowledgement bytes in serializable transactions. The in-memory core is test evidence, not a claim of crash durability across process restart.

## Runner command persistence

`RunnerCommandPersistence` snapshots one exact Team and configuration generation. It strictly decodes command bytes, delegates all semantic and idempotency decisions to DIS1 `evaluate_delivery`, and stores an independent complete `AppliedControl` before returning an `APPLIED` acknowledgement. Exact duplicates replay the stored byte-equivalent acknowledgement without replacement. Wrong Team, wrong generation, stale epoch, not-yet-valid, and expired commands return DIS1 terminal rejection without changing the applied record. Malformed or wrong-protocol bytes produce no acknowledgement and no mutation.

Persistence means desired-control state only. No method in this module calls or grants scheduler admission, lifecycle launch, gameplay, input, spending, Setup activation, Discord delivery, or resource authority.

## Proof map

`tests/test_team_routing.py` uses only synthetic credentials, identities, generations, and in-memory transport bindings. It proves authenticated exact-Team delivery; service-owned identity; forged, stale, revoked, wrong-channel, mutated/retargeted session rejection; heartbeat half-open expiry; invalid heartbeat protocol/generation without refresh; generation retarget invalidation; wrong command/ack protocol rejection before mutation; central global command-ID idempotency, monotonic Team epochs, and one-in-flight retry; generation-bound runner persistence and duplicate replay; exact acknowledgement matching/idempotency; sanitized exceptions; and absence of prohibited authority from the public module surface.
