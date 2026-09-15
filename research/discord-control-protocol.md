# Central-service / runner control protocol v1

## Scope and authority boundary

This contract freezes the authenticated control messages exchanged by the future central service and one Team runner. It is a pure data, validation, acknowledgement, and idempotency contract. It opens no socket and owns no Discord client, scheduler, lifecycle supervisor, process, input adapter, gameplay planner, transaction executor, resource authority, screenshot, or private-path capability.

Discord remains an adapter to the reviewed local application service. A protocol `APPLIED` acknowledgement means only that the runner durably replaced its local desired-control record. It never means that a player launched, a visit ran, a gameplay action occurred, a resource was authorized or spent, or a lifecycle transition completed. Those effects remain local and pass through their separately reviewed admission and safety boundaries.

No donor supplies a compatible complete seam. The local `application_service.py` contract supplies the closed `RUNNING | PAUSED | STOPPED` meaning and sole adapter-facing authority boundary; `configuration_v2.py` supplies the existing 128-bit immutable configuration-generation type. The protocol is implemented locally rather than adopting donor Discord/webhook code that combines transport, credentials, mutable globals, or gameplay ownership.

## Required immutable command

Protocol version 1 uses one canonical `CONTROL_COMMAND` object:

| Field | Frozen representation | Rule |
|---|---|---|
| `protocol_version` | JSON integer `1` | Exact integer; booleans and other versions reject. |
| `message_type` | `CONTROL_COMMAND` | Exact closed discriminator. |
| `command_id` | 32 lowercase hex characters | Random 128-bit identity, globally unique. Once registered, every other field is immutable under this ID. |
| `team_key` | 1–64 character lowercase ASCII slug | Exact private Team routing key, never a display label. |
| `issuer` | 32 lowercase hex characters | Opaque central issuer reference. Raw Discord user IDs remain only in the central private authorization store and never cross to a runner. |
| `desired_state` | `RUNNING`, `PAUSED`, or `STOPPED` | Desired control state only. It is not a gameplay/lifecycle instruction. |
| `issued_at` | signed-64 non-negative integer | UTC Unix seconds. |
| `expires_at` | signed-64 non-negative integer | Must be after `issued_at` and at most 300 seconds later. Validity is half-open: `issued_at <= now < expires_at`. |
| `control_epoch` | signed-64 positive integer | Allocated monotonically and uniquely per Team by the central service. A different command must strictly exceed the runner's applied epoch. |
| `configuration_generation` | 32 lowercase hex characters | Must exactly match the runner's active immutable configuration generation. |

The wire form is ASCII JSON with exactly the listed keys, sorted lexicographically, compact separators, and one trailing newline. The maximum message size is 4096 bytes. Duplicate keys, unknown or missing keys, non-ASCII bytes, noncanonical whitespace/order, JSON booleans in integer fields, malformed values, and unsupported versions fail closed before semantic evaluation.

`command_id` is the idempotency key; it is not an ordering key. `control_epoch` is the ordering key; it is not reusable as command identity. `configuration_generation` prevents an authorized command for an old Team mapping from controlling a replacement mapping.

## Terminal acknowledgement

Every syntactically valid evaluated command produces one canonical `COMMAND_ACKNOWLEDGEMENT`. It repeats `command_id`, `team_key`, `issuer`, `desired_state`, `issued_at`, `expires_at`, `control_epoch`, and `configuration_generation`, then adds:

- `disposition`: `APPLIED` or `REJECTED`;
- `reason`: `APPLIED`, `COMMAND_CONFLICT`, `TEAM_MISMATCH`, `CONFIGURATION_MISMATCH`, `NOT_YET_VALID`, `EXPIRED`, or `STALE_EPOCH`.

`APPLIED` disposition is valid only with `APPLIED` reason. Every other reason requires `REJECTED`. The acknowledgement uses the same canonical JSON, exact-key, version, ASCII, trailing-newline, and 4096-byte rules as the command.

Malformed bytes do not contain trustworthy routing or command identity and receive no protocol acknowledgement. The authenticated connection may close with a transport-level sanitized protocol error instead.

## Central durability and idempotency

The central service owns one durable command registry and terminal-acknowledgement ledger:

1. Reauthorize the Discord member, exact Team assignment, active configuration generation, requested transition, and runner connectivity as applicable.
2. In one transaction allocate the next Team control epoch and a new random command ID, freeze all command fields, and insert the immutable row before delivery. Enforce uniqueness on `command_id` and `(team_key, control_epoch)`.
3. Keep at most one delivered-but-unacknowledged command per Team. A delivery with an ambiguous outcome is retried byte-for-byte; a later Team epoch is not delivered until that ambiguity is resolved.
4. Looking up an existing command ID and receiving byte-equivalent fields is a duplicate. Return or redeliver the original record. Any field divergence under that ID is `COMMAND_CONFLICT` and never mutates the original row.
5. Persist the exact terminal acknowledgement before reporting command completion to Discord. A later duplicate interaction returns that stored acknowledgement without creating another command or epoch.

Run/Resume (`RUNNING`) are rejected while the runner is offline. Pause/Stop desired states may supersede earlier *undelivered* offline desired state in the central queue by allocating a new command and epoch; on reconnect the service issues only the latest desired state with a fresh five-minute validity window. It never sends an expired queued command. Once delivery may have reached a runner, the one-in-flight rule applies until its acknowledgement is reconciled.

## Runner durability and idempotency

DIS2 will authenticate the outbound connection before this protocol is accepted. Authentication itself is outside this module. For an authenticated delivery, the runner:

1. Strictly decodes the canonical command and compares exact Team key and active configuration generation.
2. Revalidate the command's exact Team key and configuration generation against the active runner identity on every delivery. If they match and the command ID exactly matches its current applied command with every immutable field unchanged, return a byte-equivalent fresh copy of the stored acknowledgement even after command expiry. It performs no second persistence or downstream handoff. The outbound acknowledgement and its nested identity values never alias the persisted command or persisted acknowledgement. An old-Team or old-generation duplicate rejects instead of replaying an `APPLIED` acknowledgement into a different active identity.
3. If the same ID has changed fields, rejects `COMMAND_CONFLICT`. A different ID at the current or a lower epoch rejects `STALE_EPOCH`.
4. Rejects Team/configuration mismatch, not-yet-valid, or expired commands without changing desired state.
5. For a valid higher epoch, atomically persists the complete immutable command as the new desired-control record together with its terminal `APPLIED` acknowledgement. Only after read-back may it transmit that acknowledgement.
6. The local scheduler/application coordinator consults that durable desired state before every new admission. It completes only already-admitted cleanup at Pause/Stop or control disconnection; transport code never calls lifecycle/gameplay code directly.

The pure `evaluate_delivery` function returns `PERSIST_AND_ACK`, `REPLAY_ACK`, or `REJECT`. Only `PERSIST_AND_ACK` carries a replacement record; replay and rejection cannot expose or replace the current record. The caller is responsible for the durable transaction and must not send a new `APPLIED` acknowledgement before the replacement record is committed. `classify_registration` freezes the central immutable-ID decision (`NEW` or exact `DUPLICATE`; changed fields reject).

## Offline and crash rules

- A crash before central command insertion means no command exists and nothing may be delivered.
- A crash after insertion but before/while sending retries the exact bytes under the same command ID.
- A runner crash before its atomic desired-state commit produces no `APPLIED` acknowledgement; retry evaluates again.
- A runner crash after commit but before acknowledgement replays the stored acknowledgement without reapplying.
- A central crash after receiving but before recording an acknowledgement queries/retries the same command; it never allocates a replacement epoch to guess the outcome.
- Expiry never rolls back an already applied desired state. It only prevents a not-yet-applied command from becoming current.
- Configuration activation never rewrites a command. Commands bound to the previous generation reject and the central service must reauthorize and issue a new ID/epoch for the new generation.

## Proof map

`tests/test_discord_control_protocol.py` proves exact immutable values, five-minute half-open expiry, strict epoch progression, exact duplicate replay, changed-field command-ID conflict, Team/configuration rejection, acknowledgement binding, canonical versioned wire round trips, malformed/duplicate/unknown wire rejection, exact numeric types, structural-corruption rejection, central registration idempotency, and absence of gameplay/spending/lifecycle authority from the public protocol surface.
