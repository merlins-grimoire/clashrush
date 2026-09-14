# Discord fleet control and due-account scheduling plan

## Purpose

Add Discord control after the local runner has stable Setup, Run, Pause, Resume, Stop, and Status operations. Discord is a remote control and reporting adapter; it never owns BlueStacks processes, chooses gameplay actions, or becomes the scheduler source of truth.

This plan supports multiple installations. Each installation is one uniquely named **team runner**. The reviewed Slice 1 remains exactly five slots; a separate schema-v2 migration later derives team size from the configured account array. Runners on different computers may operate concurrently. Multiple logical runners on one computer share a host-wide arbiter and cannot launch or physically drive BlueStacks at the same time.

## Guided private configuration

Setup presents one guided configuration experience. It writes non-secret topology to ignored `private/installation.toml`, stores bot/API/runner token values in Windows Credential Manager, and activates a validated immutable configuration generation. The tracked `config/installation.example.toml` contains synthetic placeholders and secret references only. Actual guild, category, channel, role, user, clan, runner, account, BlueStacks, and credential values never belong in source, examples, tests, ordinary audit logs, Status, support exports, crash reports, or Git history. The only local text exception is the protected ignored operator-log/debug sink defined below; the only image exception is the separately governed blocker upload.

Configuration moves through `DRAFT → VALIDATED → STAGED → ACTIVE(generation)`. Activation requires no running player, no open visit/action, and no unresolved transition. Every remote command and notification is bound to the active generation. A partial or mismatched activation leaves the prior generation active and blocks remote actions/image delivery.

Configuration and lifecycle are not falsely treated as one filesystem transaction. Under the host mutex, Setup stages an immutable private configuration generation and its matching schema-v2 mutable lifecycle file, validates and read-backs the initial lifecycle seed, then atomically replaces one write-through `var/active-generation.json` pointer. The pointer binds the immutable configuration path/hash and the mutable lifecycle path/schema/configuration generation; it deliberately does **not** bind the lifecycle content hash after activation. That pointer is the sole v2 activation commit point. Runtime follows neither staged artifact independently.

The old repository contains a dedicated Discord runtime and durable publishing tests. It is historical evidence only. Do not copy its hard-coded guild/channel constants, global environment switches, account labels, or implicit single-fleet assumptions. Candidate concepts such as exact channel validation, durable outbox delivery, idempotency markers, restricted mentions, and safe-test routing must be compared with current Discord APIs and re-proved in this architecture.

## Owner-facing behavior

### Commands

Every control command requires an explicit team option with Discord autocomplete:

- `/run team:<name>` — starts that team immediately without a second confirmation.
- `/pause team:<name>` — safely finishes the admitted transaction, closes the owned emulator, then remains idle until `/resume`.
- `/resume team:<name>` — resumes a paused scheduler.
- `/stop team:<name>` — safely finishes the admitted transaction, closes the owned emulator, exits the run, and remains stopped until `/run`.
- `/status team:<name>` — returns a sanitized ephemeral response to the requesting captain.
- `/debug start team:<name> minutes:<2-5>` — when Setup explicitly allows remote diagnostics, enables a bounded local diagnostic session for an already-running team; it never starts or resumes automation.
- `/debug status team:<name>` — reports only whether bounded diagnostics are active and their remaining time.
- `/debug stop team:<name>` — disables diagnostics without changing Run/Pause/Stop state.
- `/quarantine team:<name> account:<account> reason:<code>` — prevents new work for one account while preserving its due event and pending lane.
- `/unquarantine team:<name> account:<account>` — restores the preserved job; it never starts a run or authorizes spending.
- `/blocker respond blocker:<id> response:<allowed-action>` — resolves one blocker generation using only that blocker class's closed response vocabulary.
- `/captain add user:<mention-or-id> team:<name>` — assigns Captain access for one team.
- `/captain remove user:<mention-or-id> team:<name>` — removes that assignment.

Status includes runner connectivity, control state, current sanitized slot index, queue depth, next due time, blocked reason, and last outcome. It excludes account names, player tags, local paths, credentials, PIDs, HWNDs, and raw traces.

### Roles and authorization

- A Discord server owner or Administrator bootstraps the configured `Captain` and `Fleet Captain` roles and the first assignments.
- Captains may control only teams explicitly assigned to them in the central private authorization store.
- Fleet Captains may control every team.
- Existing authorized captains may add or remove Captains for teams they are allowed to manage.
- Fleet Captain membership remains server-owner/Administrator managed.
- Setup and operator views label a team's ordered assignments `Captain 1`, `Captain 2`, and `Captain 3`; these are display positions under one `Captain` Discord role, not separate roles or different authority levels.
- Authorization is re-read from Discord membership plus the private team-assignment store for every command. A role name alone is not authority.
- Gold/Elixir resource permissions remain local Setup decisions. Discord may report their sanitized enabled/disabled state but cannot change them.

## Topology

### Central Discord control service

Run one always-on service using the dedicated Clash Rush Discord application/bot that already exists. It owns:

- Discord slash-command registration and autocomplete;
- guild/role/member authorization checks;
- private team registry and captain-to-team assignments;
- runner connection registry and heartbeats;
- durable command delivery and acknowledgement;
- pending offline Pause/Stop desired state;
- durable notification outbox;
- exact channel validation immediately before every send.

It does **not** receive BlueStacks ownership handles, perform gameplay planning, or become authoritative for account deadlines.

### Team runner agent

Each installation owns:

- one private team name/key;
- the configured private BlueStacks account bindings (exactly five until schema v2 is promoted);
- one explicit account-to-Discord-channel-ID map entry per configured account;
- its local durable scheduler and transaction journal;
- BlueStacks lifecycle ownership;
- one private runner credential used only for an outbound authenticated connection to the central service.

The runner opens an outbound TLS connection to the central service. A one-time enrollment flow exchanges a short-lived enrollment token for a revocable per-runner credential. The Discord bot token is never copied to team runners.

### Command protocol

Every command carries a unique command ID, target team key, issuer identity, requested state, creation time, expiry policy, and monotonically increasing control epoch. The runner persists accepted commands before applying them and returns a terminal acknowledgement. Duplicate delivery is idempotent.

Pause and Stop are desired-state controls, not one-shot signals. If a runner is offline, the central service retains the latest Pause/Stop state. On reconnect, the runner authenticates, receives that state, persists it, and checks it before any launch or new action. Run and Resume fail while a runner is offline instead of being queued invisibly.

If the control connection drops during operation, the runner completes only its already admitted transaction, releases held input, safely closes the owned emulator, and pauses before another account. It resumes only after the central connection and authority are re-established.

## Private setup and public packaging

The public repository contains only schemas and placeholder examples. One-time guided Setup writes actual values beneath ignored private roots or the operating-system credential store:

- central service: bot credential, guild ID, role IDs, team registry, captain assignments, and runner credential hashes;
- runner: central URL, team key, runner secret reference, account bindings, and one explicit account-channel ID per account.

Setup validates that:

1. the configured guild is exact;
2. the team category exists;
3. every mapped account channel exists, is a text channel, belongs to that exact category, and is distinct;
4. the bot can send messages and attachments there;
5. the configured team key is unique;
6. cardinality matches the active schema (exactly five for v1; 1–10 account entries for v2, default five) and every local binding is distinct;
7. each account has one Setup-generated random 128-bit `account_ref`, distinct and not derived from its configured account name, player tag, slot, channel, or BlueStacks display name;
8. Home Gold, Home Elixir, Home Dark Elixir, Builder Gold, and Builder Elixir each have an explicit boolean owner choice; Discord cannot mutate them, and a resource choice grants no authority without a separately promoted transaction executor;
9. no private identifier is written outside the ignored private root.

Autocomplete displays configured team labels but command payloads use immutable private team keys. Ordinary Run, Pause, Resume, Stop, Status, and Debug never require editing source or JSON. Remote Debug is disabled by default in Setup; a local Debug button remains the primary control.

### Protected local operator logs and debug

The owner-facing local log and diagnostic preview include the configured account name, player tag, and BlueStacks display name. They are written/rendered only after the active account binding is proved and only from validated private configuration—not unrestricted OCR. The file sink is confined to ignored `var/private-logs/` and `var/private-debug/` and requires either a DACL limited to the current operator plus `SYSTEM` or operator-bound encryption. If protection cannot be proved, the runner emits only the sanitized `account_ref` audit and does not mirror private identifiers to console output.

The sanitized append-only audit remains the sole source for Status, Discord summaries, telemetry, crash reports, and support/public exports. Sensitive-log filenames and retention metadata never contain names, tags, or instance names. Debug sessions last two to five minutes and auto-disable on timeout, Pause, Stop, disconnect, blocker, or process exit. Local Debug and remote `/debug` call the same application service and cannot start/resume automation, grant resource permission, bypass transaction gates, select arbitrary screenshot ROIs, or widen Discord image delivery.

## Local due-account scheduler

### Trigger

For each account, the earliest known builder completion across Home Village and Builder Base schedules the whole account visit. Star Laboratory completion does not independently wake the runner; laboratory research, upgrades, and attacks are handled during the builder-triggered visit.

When a newly completed future builder first becomes available:

1. choose one uniform random delay from 0 through 60 minutes;
2. persist the chosen delay and absolute due time once;
3. never re-randomize that availability event after restart, reconnect, or queue reconstruction;
4. post the next due time to that account's mapped Discord channel.

If a builder is already free when first observed, use the observation time as the availability lower bound. If no trustworthy next-builder time can be read, schedule a bounded reconciliation visit within 60 minutes and mark the deadline as reconciliation-derived.

### Queue selection

- Account jobs and deadlines are authoritative only in the runner's local durable store.
- Select the oldest currently eligible due account first.
- Break equal-deadline ties with the restart-safe round-robin slot cursor.
- Never start a future-builder job before its persisted randomized deadline.
- A due account receives the existing hard ten-minute visit: Home then Builder Base, initially reserving about five minutes for each lane.
- At the final trusted observation, a positively confirmed free builder creates one immediate successor with `due_at = visit_finished_at` and no random offset. Reset its ordering timestamp to the visit finish so older waiting accounts run first; do not let a perpetually free account monopolize the queue.
- If no builder is free, observe the next earliest future builder completion and enqueue exactly one successor with one persisted 0–60 minute offset.
- An unknown/conflicting final builder observation becomes bounded `DEFERRED`; it never guesses immediate readiness or redraws a random event.
- Unfinished work remains for the next visit and global run/failure limits cover consecutive revisits.
- While no account is due, keep BlueStacks stopped and wait.
- Runners on different hosts may process queues simultaneously. Runners sharing one host serialize the complete launch/visit/stop/commit boundary through the host arbiter.

### Eligibility, defer, and quarantine

Each job retains its original availability event and due time plus an explicit eligibility state:

- `WAITING` — deadline has not arrived;
- `ELIGIBLE` — deadline has arrived and the job participates in oldest-due selection;
- `DEFERRED(reconcile_at, yield_set)` — lane-incomplete or uncertain observation; excluded until both a persisted future retry time and one bounded fairness barrier are satisfied;
- `QUARANTINED` — an explicit human/safety lock; excluded until an authorized captain clears it;
- `ADMITTED(visit_nonce, generation)` — selected exactly once and unavailable to another worker.

Oldest-due ordering applies only among `ELIGIBLE` jobs. Every uncertainty deferral persists `reconcile_at` at least five and no more than 60 minutes in the future plus a `yield_set` containing the generation of every other job eligible when deferral occurs. The deferred job cannot re-enter `ELIGIBLE` until the time passes and every yield-set generation has received one terminal admission or independently become waiting/quarantined. Failure to clear the barrier because lifecycle cleanup or the host is blocked becomes explicit intervention rather than repeated admission. A quarantined job cannot block other due work, cannot disappear, remains visible in Status and its account channel, and has no automatic gameplay attempt until cleared. A lane-incomplete job is deferred with its pending lane, so Home is not replayed and the account regains priority after yielding one round without monopolizing the team.

### Crash-consistent admission and completion

The queue/journal SQLite database and lifecycle `READY/ACTIVE` file remain separate safety domains, coordinated under the same protected lifecycle mutex with one write-ahead visit generation:

1. In one SQLite transaction, select the oldest eligible job, generate one unique 32-lowercase-hex `visit_nonce`, change the job to `ADMITTED`, and insert that nonce, stable account key, slot-index snapshot, configuration generation, job generation, original due event, pending lane, and pre-visit observations.
2. The reviewed v1 `LifecycleSupervisor.start` and its `READY.next_slot == selected_slot` authorization remain sealed and unchanged. A stopped-only schema-v2 migration creates a new versioned `start_admitted(account_key, configuration_generation, visit_nonce)` boundary keyed by stable account and active configuration. Only that v2 boundary accepts the due-selected account independently of the tie cursor and durably writes matching `ACTIVE(..., run_nonce=visit_nonce, ...)` before process creation. V1 never runs the due scheduler through a weakened start contract.
3. Record every gameplay transaction through the existing non-replayable action journal. Before normal shutdown, durably store the visit result and exactly one successor plan: immediate fair-tail requeue for a positively free builder, future availability plus its persisted random offset, or a bounded reconciliation deadline. An emergency owned-stop path performs the same Job/process/window absence proof but commits blocked `ACTIVE` instead of `READY` if successor planning fails. Do not expose `READY` or re-admit the old job.
4. Prove the process tree and windows gone, then advance only the equal-deadline tie cursor. V1 retains `READY((selected_slot + 1) % 5)`; schema v2 replaces it with a stable account-key cursor bound to the configuration generation. Neither form selects or makes an account eligible.
5. In one SQLite transaction, consume the admitted generation, create/update exactly one successor generation from the pre-stop successor plan, and mark the visit finalized. Only then may another admission begin.

Startup reconciliation runs under the same mutex before selection:

- `READY` plus no open visit is normal.
- `READY` plus a pre-`ACTIVE` admitted visit and complete player absence returns that unchanged job generation to `ELIGIBLE`; lifecycle ordering proves no process could have been created before `ACTIVE`.
- `READY` plus a visit that already has a persisted successor plan means stop committed but queue finalization may not have; finalize idempotently from that plan before selecting work.
- `ACTIVE` plus the exact matching open visit permits no launch. Reconcile lifecycle/process absence through the separately approved lifecycle path, then finalize only from recorded action outcomes and the persisted successor plan; never replay an uncertain action.
- Any slot, visit nonce, generation, cursor, process, or journal disagreement blocks the team for operator reconciliation.

The SQLite unique keys `(account_key, configuration_generation, job_generation)` and `visit_nonce`, exact equality between the admitted nonce and lifecycle `ACTIVE.run_nonce`, and idempotent successor finalization cover crashes before `ACTIVE`, during gameplay, after stop proof but before `READY`, after `READY` but before queue finalization, and after finalization acknowledgement.

### Crash-consistent v1-to-v2 migration

Migration is allowed only from canonical v1 `READY`, with no transition guard, player, open visit/action, blocker upload, or unresolved queue state. While holding the host mutex:

1. Generate the target configuration generation and deterministic v1-index-to-account-key mapping. Stage the immutable configuration at a generation-specific private path and create the operative mutable schema-v2 lifecycle file at a generation-specific `var/` path with `READY(tie_after_account_key, configuration_generation)`; flush and read-back both.
2. Durably write and read back `migration.json` with the exact source-v1 state hash, target generation, immutable configuration hash, initial lifecycle-seed hash, both paths, and `PREPARED`. The sealed v1 state remains untouched.
3. Revalidate the source v1 state, absence proofs, staged configuration hash, initial lifecycle-seed hash, paths, and mapping. Atomically write-through replace and read back `active-generation.json` selecting schema v2. The pointer stores target generation, configuration path/hash, and lifecycle path/schema/generation—but no invariant lifecycle content hash. This single pointer replacement is the activation commit point.
4. Before any v2 admission, reconcile the migration record: require the pointer, immutable configuration hash, and still-initial lifecycle seed to match; mark `POINTER_COMMITTED`, validate the pointer-selected lifecycle's schema/configuration generation, then archive the migration record. Only after this finishes may normal lifecycle transitions mutate that same file through its existing guard/write-through/read-back protocol. Acknowledgement cleanup is not part of activation authority.

Startup always reads the activation pointer before choosing a lifecycle implementation. With no v2 pointer, a complete `PREPARED` record plus unchanged v1 source and complete absence proof is rolled back by removing only unselected staged artifacts and retaining sealed v1; any mismatch blocks. With a valid v2 pointer and an open migration record, startup requires the referenced immutable configuration hash and initial lifecycle-seed hash, selects v2, and idempotently completes migration before admission. After migration bookkeeping is archived, startup requires the immutable configuration hash plus a pointer-selected mutable lifecycle file whose own durable loader proves exact schema/configuration generation and transition-guard integrity; its changing state bytes are not compared with the original seed hash. A malformed pointer, missing/mismatched artifact, conflicting generation, changed v1 source before commit, or ambiguous migration state blocks all launch for explicit reconciliation. Later resize/reorder uses the same stage-and-single-pointer generation protocol and never edits the active configuration generation in place.

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

Blockers are immutable generations with a class-specific action matrix. The Captain response vocabulary is closed to `ACKNOWLEDGE_KEEP_PAUSED`, `REOBSERVE_AFTER_HUMAN_CORRECTION`, and `QUARANTINE_ACCOUNT`; there is no generic Retry. Re-observation performs no transaction replay and is exposed only where the blocker class permits fresh read-only evidence after a declared human correction. Lifecycle ownership, stop-proof, identity mismatch, CAPTCHA, and uncertain action blockers allow only acknowledge/keep-paused or quarantine. The first authorized terminal response wins atomically; later conflicting responses are rejected. Free text can explain but never authorize an action.

`/status` is ephemeral and creates no channel message. Team-wide state changes that do not identify an account are attached to the account currently being serviced; when no account is active, the runner records them locally and the command acknowledgement remains ephemeral.

### Blocker screenshots — explicit privacy exception

The owner explicitly authorizes a full game screenshot for a blocker only when all of these conditions hold:

1. it is uploaded directly from memory to the exact mapped private account channel;
2. guild, category, channel, team, and slot bindings are revalidated immediately before upload;
3. only assigned Captains are mentioned, with `@everyone`, unrelated roles, and arbitrary user mentions disabled;
4. the frame is never written to local disk, logs, traces, tests, Git, or public artifacts;
5. credentials, Discord tokens, local paths, desktop content outside the exact game render, and non-game windows are never captured;
6. delivery follows the ambiguous-send protocol below rather than assuming a Discord nonce guarantees indefinite deduplication;
7. the message remains until a captain deletes it, as requested.

This is a narrow exception to the former memory-only full-frame rule. Full frames remain prohibited everywhere else. Because Discord retains uploaded attachments, Setup must clearly disclose this behavior before enabling blocker-image delivery.

#### Ambiguous screenshot delivery

Persist only a metadata outbox record and a globally unique high-entropy marker—never pixels or a frame digest—with states `PREPARED`, `UPLOAD_STARTED`, `DELIVERED(message_id)`, or `DELIVERY_UNCERTAIN`. The marker is generated once per blocker generation, bound to that exact outbox intent, and cannot be reused. The prepared intent declares an exact attachment manifest: the full blocker frame and, when enabled, one in-memory OCR diagnostic panel composed only from approved narrow crops, interpreted values/confidence, state labels, and sanitized reason codes.

1. `PREPARED` may capture and upload only while the same blocker generation and exact destination still validate.
2. Persist `UPLOAD_STARTED` before beginning the HTTP request, include the marker and exact prepared attachment manifest in one Discord message, and clear all frame/panel bytes immediately after the request returns or raises. No attachment is written locally.
3. On a normal Discord response, persist the exact message ID as `DELIVERED`.
4. After a crash or any exception from an upload that reached `UPLOAD_STARTED`, search only the exact mapped channel for the marker. Reconcile to `DELIVERED` only when exactly one message matches and its guild/channel, configured bot application author, blocker generation, marker, creation window, and every attachment in the predeclared manifest (filename, content type, bounded nonzero size, completed state) validate with no extras. Zero, multiple, wrong-author, marker-only, missing/extra/incomplete attachment, unauthorized, or failed-history results become `DELIVERY_UNCERTAIN`.
5. Never resend or recapture an `UPLOAD_STARTED`/`DELIVERY_UNCERTAIN` blocker generation. Discord nonce/enforce-nonce behavior is supplementary only, not the exactly-once proof. A later screenshot requires a new blocker generation and new outbox intent.

This intentionally favors a missing alert over a duplicate indefinitely retained private screenshot.

## Failure behavior

- Unknown team, channel, role, assignment, runner identity, command version, or account mapping fails closed.
- A mismatched channel never falls back to another channel.
- An offline Run/Resume fails visibly; an offline Pause/Stop remains pending.
- Lost control connectivity pauses the runner after safe transaction cleanup.
- A failed screenshot upload does not change the blocker state and never redirects the image.
- A Discord outage cannot bypass local lifecycle, spending, lease, transaction, or privacy checks.
- Central-service compromise cannot alter any local resource authorization, including Dark Elixir. First-release Dark Elixir scope is limited to separately promoted hero-upgrade and laboratory-research executors; pet upgrades are deferred, and the current slice has no Dark Elixir executor. Gems, purchases, rewards, social actions, and account switching remain unimplemented and unauthorized.

## Delivery sequence

### D0 — finish the current inert lifecycle gate

Diagnose and correct exact BlueStacks window binding, reconcile the current durable `ACTIVE` state under explicit approval, and prove one inert launch/capture/stop visit. Do not let Discord work bypass this gate.

### D1 — local scheduler contract

First add the stopped-only schema-v2 migration from sealed exact-five v1 indexes to 1–10 stable account keys plus a new versioned admission boundary; do not edit or weaken v1 start. Then implement the durable account job/deadline model, one-time 0–60 minute randomization only for future builder completions, immediate fair-tail requeue for a positively free builder after a ten-minute visit, explicit eligibility/defer/quarantine states, oldest-currently-eligible selection, round-robin ties, write-ahead visit generation, every cross-store crash seam, and no-due sleep behavior without BlueStacks input. Due selection—not the v2 lifecycle tie cursor—chooses the account.

### D2 — local operator controls

Implement and exercise local Setup, Run, Pause, Resume, Stop, and Status. Discord will call this same application service; it must not create a second control path.

### D3 — central router and private enrollment

Build the dedicated Discord service, private schemas, runner enrollment, authenticated outbound connection, heartbeat, exact team routing, command idempotency, and offline desired-state handling.

### D4 — role and command slice

Add explicit team/account autocomplete, ephemeral Status, immediate Run, Pause/Resume/Stop, account quarantine/unquarantine, blocker-response actions, Captain assignments, Fleet Captain authority, and Administrator bootstrap. RED-test cross-team denial, stale roles/config generations, duplicate/conflicting commands, active-visit quarantine, and offline commands.

### D5 — account-channel reporting

Add exact configured-cardinality channel validation, durable outbox delivery, per-account event routing, concise summaries, and blocked/failure notifications. Test that no event can fall back or cross team/account boundaries.

### D6 — blocker-image exception

Separately review and promote direct in-memory full-frame blocker upload. Test exact destination revalidation, restricted mentions, no local persistence/digest, globally unique per-generation markers, `PREPARED → UPLOAD_STARTED` ordering, exact bot-author plus complete-attachment reconciliation, rejection of copied/marker-only/wrong-channel messages, and fail-closed no-retry after every ambiguous send/crash seam before one owner-approved private-channel test.

### D7 — multi-runner release gate

Run at least two synthetic team runners concurrently with different configured cardinalities and prove:

- explicit command routing never crosses runners;
- runners on distinct hosts can proceed independently, while same-host runners serialize the entire launch/visit/stop boundary through one fair host arbiter;
- local queues survive runner and central-service restarts;
- pending Pause/Stop applies before any reconnect action;
- connection loss pauses after safe cleanup;
- all account updates reach only their mapped channel;
- public builds contain no private IDs, names, credentials, screenshots, or local paths.

### D8 — read-only API/CWL/statistics

Add an isolated central `ClashDataProvider` using the official API for CWL/war, roster, donation counters, player profiles, and rankings. End users configure per-clan channels, mention mode, reminder thresholds, and cooldowns; `@everyone` is off by default and enabled only after exact permission validation. Use a durable occurrence outbox and no-mention test preview. Optional ClashKing historical data is attributed and cannot publish runner commands.

Each delivery slice follows donor/reference comparison, RED→GREEN TDD, focused tests, full tests, exact-tree privacy scan, independent review, clean commit, and separately approved live promotion.

## Deferred decisions

These do not block the current lifecycle or scheduler slices:

- hosting provider and deployment method for the always-on central service;
- custom domain/TLS termination for runner connections;
- whether captains need manual queue reprioritization later;
- whether screenshot deletion tooling should be added later (the current decision is captain-managed retention).
