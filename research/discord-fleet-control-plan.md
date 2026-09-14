# Discord fleet control and due-account scheduling plan

## Purpose

Add Discord control after the local runner has stable Setup, Run, Pause, Resume, Stop, and Status operations. Discord is a remote control and reporting adapter; it never owns BlueStacks processes, chooses gameplay actions, or becomes the scheduler source of truth.

This plan supports multiple installations. Each installation is one uniquely named **troop runner** with exactly five configured account slots. Troop runners on different computers may operate concurrently, while each runner still permits exactly one local BlueStacks instance at a time.

## Private server configuration

The server topology was inspected privately and is sufficient for explicit troop-to-five-channel mapping. Counts, names, missing-role state, and all actual guild, category, channel, role, user, runner, and account identifiers remain in ignored private configuration or a secret store; none belong in source, examples, tests, logs, or Git history.

The old repository contains a dedicated Discord runtime and durable publishing tests. It is historical evidence only. Do not copy its hard-coded guild/channel constants, global environment switches, account labels, or implicit single-fleet assumptions. Candidate concepts such as exact channel validation, durable outbox delivery, idempotency markers, restricted mentions, and safe-test routing must be compared with current Discord APIs and re-proved in this architecture.

## Owner-facing behavior

### Commands

Every control command requires an explicit troop option with Discord autocomplete:

- `/run troop:<name>` — starts that troop immediately without a second confirmation.
- `/pause troop:<name>` — safely finishes the admitted transaction, closes the owned emulator, then remains idle until `/resume`.
- `/resume troop:<name>` — resumes a paused scheduler.
- `/stop troop:<name>` — safely finishes the admitted transaction, closes the owned emulator, exits the run, and remains stopped until `/run`.
- `/status troop:<name>` — returns a sanitized ephemeral response to the requesting captain.
- `/captain add user:<mention-or-id> troop:<name>` — assigns Troop Captain access for one troop.
- `/captain remove user:<mention-or-id> troop:<name>` — removes that assignment.

Status includes runner connectivity, control state, current sanitized slot index, queue depth, next due time, blocked reason, and last outcome. It excludes account names, player tags, local paths, credentials, PIDs, HWNDs, and raw traces.

### Roles and authorization

- A Discord server owner or Administrator bootstraps the configured `Troop Captain` and `Fleet Captain` roles and the first assignments.
- Troop Captains may control only troops explicitly assigned to them in the central private authorization store.
- Fleet Captains may control every troop.
- Existing authorized captains may add or remove Troop Captains for troops they are allowed to manage.
- Fleet Captain membership remains server-owner/Administrator managed.
- Authorization is re-read from Discord membership plus the private troop-assignment store for every command. A role name alone is not authority.
- Gold/Elixir resource permissions remain local Setup decisions. Discord may report their sanitized enabled/disabled state but cannot change them.

## Topology

### Central Discord control service

Run one always-on service using the dedicated Clash Rush Discord application/bot that already exists. It owns:

- Discord slash-command registration and autocomplete;
- guild/role/member authorization checks;
- private troop registry and captain-to-troop assignments;
- runner connection registry and heartbeats;
- durable command delivery and acknowledgement;
- pending offline Pause/Stop desired state;
- durable notification outbox;
- exact channel validation immediately before every send.

It does **not** receive BlueStacks ownership handles, perform gameplay planning, or become authoritative for account deadlines.

### Troop runner agent

Each installation owns:

- one private troop name/key;
- exactly five private BlueStacks slot bindings;
- an explicit slot-to-Discord-channel-ID map;
- its local durable scheduler and transaction journal;
- BlueStacks lifecycle ownership;
- one private runner credential used only for an outbound authenticated connection to the central service.

The runner opens an outbound TLS connection to the central service. A one-time enrollment flow exchanges a short-lived enrollment token for a revocable per-runner credential. The Discord bot token is never copied to troop runners.

### Command protocol

Every command carries a unique command ID, target troop key, issuer identity, requested state, creation time, expiry policy, and monotonically increasing control epoch. The runner persists accepted commands before applying them and returns a terminal acknowledgement. Duplicate delivery is idempotent.

Pause and Stop are desired-state controls, not one-shot signals. If a runner is offline, the central service retains the latest Pause/Stop state. On reconnect, the runner authenticates, receives that state, persists it, and checks it before any launch or new action. Run and Resume fail while a runner is offline instead of being queued invisibly.

If the control connection drops during operation, the runner completes only its already admitted transaction, releases held input, safely closes the owned emulator, and pauses before another account. It resumes only after the central connection and authority are re-established.

## Private setup and public packaging

The public repository contains only schemas and placeholder examples. One-time Setup writes actual values beneath ignored private roots or the operating-system credential store:

- central service: bot credential, guild ID, role IDs, troop registry, captain assignments, and runner credential hashes;
- runner: central URL, troop key, runner credential, five slot bindings, and five explicit account-channel IDs.

Setup validates that:

1. the configured guild is exact;
2. the troop category exists;
3. all five mapped channels exist, are text channels, belong to that exact category, and are distinct;
4. the bot can send messages and attachments there;
5. the configured troop key is unique;
6. the runner has exactly five distinct local slots;
7. no private identifier is written outside the ignored private root.

Autocomplete displays configured troop labels but command payloads use immutable private troop keys. Ordinary Run, Pause, Resume, Stop, and Status never require editing source or JSON.

## Local due-account scheduler

### Trigger

For each account, the earliest known builder completion across Home Village and Builder Base schedules the whole account visit. Star Laboratory completion does not independently wake the runner; laboratory research, upgrades, and attacks are handled during the builder-triggered visit.

When the first builder becomes available:

1. choose one uniform random delay from 0 through 60 minutes;
2. persist the chosen delay and absolute due time once;
3. never re-randomize that availability event after restart, reconnect, or queue reconstruction;
4. post the next due time to that account's mapped Discord channel.

If a builder is already free when first observed, use the observation time as the availability lower bound. If no trustworthy next-builder time can be read, schedule a bounded reconciliation visit within 60 minutes and mark the deadline as reconciliation-derived.

### Queue selection

- Account jobs and deadlines are authoritative only in the runner's local durable store.
- Select the oldest currently eligible due account first.
- Break equal-deadline ties with the restart-safe round-robin slot cursor.
- Never start a job before its persisted randomized deadline.
- A due account receives the existing hard ten-minute visit: Home then Builder Base, initially reserving about five minutes for each lane.
- Unfinished work remains for the next visit.
- After a visit, observe the next earliest builder completion and enqueue exactly one successor availability event.
- While no account is due, keep BlueStacks stopped and wait.
- Multiple troop runners may process their local queues simultaneously; each still enforces one local emulator and complete stop proof before its next slot.

### Eligibility, defer, and quarantine

Each job retains its original availability event and due time plus an explicit eligibility state:

- `WAITING` — deadline has not arrived;
- `ELIGIBLE` — deadline has arrived and the job participates in oldest-due selection;
- `DEFERRED(reconcile_at, yield_set)` — lane-incomplete or uncertain observation; excluded until both a persisted future retry time and one bounded fairness barrier are satisfied;
- `QUARANTINED` — an explicit human/safety lock; excluded until an authorized captain clears it;
- `ADMITTED(visit_nonce, generation)` — selected exactly once and unavailable to another worker.

Oldest-due ordering applies only among `ELIGIBLE` jobs. Every deferral persists `reconcile_at` at least five and no more than 60 minutes in the future plus a `yield_set` containing the generation of every other job that is eligible when deferral occurs. The deferred job cannot re-enter `ELIGIBLE` until the time passes and every yield-set generation has received one terminal admission or has independently become waiting/quarantined. Under the ten-minute account cap, the four-peer barrier fits inside the 60-minute reconciliation bound; failure to clear it because lifecycle cleanup or the fleet is blocked becomes an explicit intervention rather than another admission of the failing job. A quarantined job cannot block other due work, cannot disappear, remains visible in Status and its account channel, and has no automatic gameplay attempt until cleared. A lane-incomplete job is deferred with its pending lane, so Home is not replayed and the account regains priority after yielding one round without monopolizing the fleet.

### Crash-consistent admission and completion

The queue/journal SQLite database and lifecycle `READY/ACTIVE` file remain separate safety domains, coordinated under the same protected lifecycle mutex with one write-ahead visit generation:

1. In one SQLite transaction, select the oldest eligible job, generate one unique 32-lowercase-hex `visit_nonce`, change the job to `ADMITTED`, and insert that nonce, slot, generation, original due event, pending lane, and pre-visit observations.
2. Slice 2 revises the lifecycle start API to accept the explicitly selected slot and admitted `visit_nonce` whenever lifecycle state is `READY`; it removes the old `READY.next_slot == selected_slot` launch precondition and the lifecycle's independent nonce generation on this path. Without changing the lifecycle schema, the transition durably writes `ACTIVE(selected_slot, run_nonce=visit_nonce, ...)` before any process creation.
3. Record every gameplay transaction through the existing non-replayable action journal. Before normal shutdown, durably store the visit result and exactly one successor plan (known builder availability plus its persisted random offset, or a bounded reconciliation deadline). Slice 2 adds an emergency owned-stop path that performs the same Job/process/window absence proof but commits a blocked `ACTIVE` instead of `READY`; use it if the successor-plan write fails. Do not expose `READY` or re-admit the old job.
4. Prove the process tree and windows gone, then let the lifecycle store commit `READY((selected_slot + 1) % 5)`. The numeric field now means equal-deadline tie start only; it never selects or makes an account eligible.
5. In one SQLite transaction, consume the admitted generation, create/update exactly one successor generation from the pre-stop successor plan, and mark the visit finalized. Only then may another admission begin.

Startup reconciliation runs under the same mutex before selection:

- `READY` plus no open visit is normal.
- `READY` plus a pre-`ACTIVE` admitted visit and complete player absence returns that unchanged job generation to `ELIGIBLE`; lifecycle ordering proves no process could have been created before `ACTIVE`.
- `READY` plus a visit that already has a persisted successor plan means stop committed but queue finalization may not have; finalize idempotently from that plan before selecting work.
- `ACTIVE` plus the exact matching open visit permits no launch. Reconcile lifecycle/process absence through the separately approved lifecycle path, then finalize only from recorded action outcomes and the persisted successor plan; never replay an uncertain action.
- Any slot, visit nonce, generation, cursor, process, or journal disagreement blocks the troop for operator reconciliation.

The SQLite unique keys `(slot, generation)` and `visit_nonce`, exact equality between the admitted nonce and lifecycle `ACTIVE.run_nonce`, and idempotent successor finalization cover crashes before `ACTIVE`, during gameplay, after stop proof but before `READY`, after `READY` but before queue finalization, and after finalization acknowledgement.

## Gameplay target localization

Gameplay slices use small reviewed button/control images, color/shape evidence, and restricted OCR to prove and locate controls. They do not blindly replay screen coordinates. Once a control is positively located, the executor converts its detected center to normalized render coordinates and uses the single reviewed physical Windows input path. Owner-calibrated fixed HUD/keybind coordinates are permitted only on a positively identified screen with exact geometry and a verified post-click transition.

The locally owned `CCBackBeat.ttf` copy may render expected game labels for local matching. It stays under the ignored private assets root and is never uploaded, packaged, or committed.

## Discord event routing

Every account event goes only to that account's explicitly mapped private channel:

- visit start and stop;
- confirmed actions and concise result summary;
- blocker/failure reason;
- next persisted due time;
- concise cycle/visit summary.

`/status` is ephemeral and creates no channel message. Troop-wide state changes that do not identify an account are attached to the account currently being serviced; when no account is active, the runner records them locally and the command acknowledgement remains ephemeral.

### Blocker screenshots — explicit privacy exception

The owner explicitly authorizes a full game screenshot for a blocker only when all of these conditions hold:

1. it is uploaded directly from memory to the exact mapped private account channel;
2. guild, category, channel, troop, and slot bindings are revalidated immediately before upload;
3. only assigned Troop Captains are mentioned, with `@everyone`, unrelated roles, and arbitrary user mentions disabled;
4. the frame is never written to local disk, logs, traces, tests, Git, or public artifacts;
5. credentials, Discord tokens, local paths, desktop content outside the exact game render, and non-game windows are never captured;
6. delivery follows the ambiguous-send protocol below rather than assuming a Discord nonce guarantees indefinite deduplication;
7. the message remains until a captain deletes it, as requested.

This is a narrow exception to the former memory-only full-frame rule. Full frames remain prohibited everywhere else. Because Discord retains uploaded attachments, Setup must clearly disclose this behavior before enabling blocker-image delivery.

#### Ambiguous screenshot delivery

Persist only a metadata outbox record and a globally unique high-entropy marker—never pixels or a frame digest—with states `PREPARED`, `UPLOAD_STARTED`, `DELIVERED(message_id)`, or `DELIVERY_UNCERTAIN`. The marker is generated once per blocker generation, bound to that exact outbox intent, and cannot be reused.

1. `PREPARED` may capture and upload only while the same blocker generation and exact destination still validate.
2. Persist `UPLOAD_STARTED` before beginning the HTTP request, include the marker in the same Discord message as exactly one in-memory image attachment whose filename is derived from that intent, and clear frame bytes immediately after the request returns or raises.
3. On a normal Discord response, persist the exact message ID as `DELIVERED`.
4. After a crash or any exception from an upload that reached `UPLOAD_STARTED`, search only the exact mapped channel for the marker. Reconcile to `DELIVERED` only when exactly one message matches and its guild/channel, configured bot application author, blocker generation, exact marker, creation window, and exactly one completed image attachment with the intent-derived filename, expected image content type, and bounded nonzero size all validate. Zero, multiple, wrong-author, marker-only, incomplete-attachment, unauthorized, or failed-history results become `DELIVERY_UNCERTAIN`.
5. Never resend or recapture an `UPLOAD_STARTED`/`DELIVERY_UNCERTAIN` blocker generation. Discord nonce/enforce-nonce behavior is supplementary only, not the exactly-once proof. A later screenshot requires a new blocker generation and new outbox intent.

This intentionally favors a missing alert over a duplicate indefinitely retained private screenshot.

## Failure behavior

- Unknown troop, channel, role, assignment, runner identity, command version, or account mapping fails closed.
- A mismatched channel never falls back to another channel.
- An offline Run/Resume fails visibly; an offline Pause/Stop remains pending.
- Lost control connectivity pauses the runner after safe transaction cleanup.
- A failed screenshot upload does not change the blocker state and never redirects the image.
- A Discord outage cannot bypass local lifecycle, spending, lease, transaction, or privacy checks.
- Central-service compromise cannot authorize gems, Dark Elixir, purchases, rewards, social actions, or account switching because no executor exists for them.

## Delivery sequence

### D0 — finish the current inert lifecycle gate

Diagnose and correct exact BlueStacks window binding, reconcile the current durable `ACTIVE` state under explicit approval, and prove one inert launch/capture/stop visit. Do not let Discord work bypass this gate.

### D1 — local scheduler contract

Implement the durable account job/deadline model, one-time 0–60 minute randomization, explicit eligibility/defer/quarantine states, oldest-currently-eligible selection, round-robin tie-break, write-ahead visit generation, every cross-store crash reconciliation seam, and no-due sleep behavior without BlueStacks input. Explicitly migrate the lifecycle start precondition so due selection—not `READY.next_slot` equality—chooses the slot.

### D2 — local operator controls

Implement and exercise local Setup, Run, Pause, Resume, Stop, and Status. Discord will call this same application service; it must not create a second control path.

### D3 — central router and private enrollment

Build the dedicated Discord service, private schemas, runner enrollment, authenticated outbound connection, heartbeat, exact troop routing, command idempotency, and offline desired-state handling.

### D4 — role and command slice

Add explicit troop autocomplete, ephemeral Status, immediate Run, Pause/Resume/Stop semantics, Troop Captain assignments, Fleet Captain authority, and Administrator bootstrap. RED-test cross-troop denial, stale role state, duplicate commands, and offline commands.

### D5 — account-channel reporting

Add exact five-channel validation, durable outbox delivery, per-account event routing, concise summaries, and blocked/failure notifications. Test that no event can fall back or cross troop/account boundaries.

### D6 — blocker-image exception

Separately review and promote direct in-memory full-frame blocker upload. Test exact destination revalidation, restricted mentions, no local persistence/digest, globally unique per-generation markers, `PREPARED → UPLOAD_STARTED` ordering, exact bot-author plus complete-attachment reconciliation, rejection of copied/marker-only/wrong-channel messages, and fail-closed no-retry after every ambiguous send/crash seam before one owner-approved private-channel test.

### D7 — multi-runner release gate

Run at least two synthetic troop runners concurrently, each with five slots, and prove:

- explicit command routing never crosses runners;
- every local runner remains one-instance-only;
- local queues survive runner and central-service restarts;
- pending Pause/Stop applies before any reconnect action;
- connection loss pauses after safe cleanup;
- all account updates reach only their mapped channel;
- public builds contain no private IDs, names, credentials, screenshots, or local paths.

Each delivery slice follows donor/reference comparison, RED→GREEN TDD, focused tests, full tests, exact-tree privacy scan, independent review, clean commit, and separately approved live promotion.

## Deferred decisions

These do not block the current lifecycle or scheduler slices:

- hosting provider and deployment method for the always-on central service;
- custom domain/TLS termination for runner connections;
- whether captains need manual queue reprioritization later;
- whether screenshot deletion tooling should be added later (the current decision is captain-managed retention).
