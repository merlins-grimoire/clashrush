# Sanitized audit-event contract

## Scope and donor decision

This slice defines only the immutable event value and its canonical JSON Lines encoding. It does not open files, append records, capture frames, issue input, touch lifecycle state, read private configuration, send network/Discord traffic, or authorize gameplay or spending.

The pinned donor inventory in `research/scheduler-contract.md` assigns scheduler, safety, and audit to local implementation. BasePilot, CoC_Bot, ClashAutomation, the historical Clash Rush tree, Auto Farmer, keshav-x/coc-bot, and NX-ClashClient provide no compatible append-only schema that binds the local schema-v3 scheduler action identity while excluding private identifiers and pixels. No donor source is copied and no new third-party notice is required. The already-local `Lane` and `VisitGeneration` types are reused so the audit stream cannot invent a competing visit identity.

## Version 1 record

Each record is one canonical ASCII JSON object followed by exactly one newline. Keys are sorted, whitespace is forbidden, unknown or duplicate keys are rejected, and the encoded record is bounded to 1024 bytes. The exact fields are:

| Field | Contract |
|---|---|
| `schema_version` | Exact integer `1` |
| `event_generation` | Positive exact integer through `2^63 - 1`, assigned in append order by the future sink |
| `team` | Opaque 128-bit lowercase-hex `TeamRef`; never a configured label or key |
| `account_ref` | Setup-generated opaque 128-bit lowercase-hex `AccountRef`; never derived from an account name, player tag, slot, channel, or instance |
| `visit_generation` | Positive exact scheduler `VisitGeneration` through `2^63 - 1`; its nested value is revalidated |
| `lane` | Closed scheduler `HOME | BUILDER` value |
| `action_nonce` | Exact 32-lowercase-hex scheduler action nonce |
| `kind` | Closed `AuditEventKind` value |
| `reason_code` | Closed `ReasonCode` value |
| `monotonic_ns` | Non-negative exact integer monotonic timestamp in nanoseconds through `2^63 - 1` |
| `wall_time_utc` | Calendar-valid canonical UTC timestamp with exactly six fractional digits and trailing `Z` |
| `outcome` | Closed `AuditOutcome` value |

`AuditEventKind` is limited to `VISIT`, `DECISION`, `OBSERVATION`, `ACTION_INTENT`, `INPUT`, `POSTCONDITION`, `ACTION_OUTCOME`, `STATE_TRANSITION`, `CONTROL`, and `BLOCKER`.

`AuditOutcome` is limited to `PENDING`, `SUCCEEDED`, `FAILED`, `UNCERTAIN`, `DEFERRED`, `BLOCKED`, and `SUPPRESSED`.

`ReasonCode` is the bounded vocabulary implemented in `audit_event.py`. Adding a reason is a schema review, not a caller-supplied string path.

## Privacy boundary

There is deliberately no free-form message, arbitrary metadata map, path, filename, account key, configuration label, slot name, account name, player tag, BlueStacks display/internal name, Discord or platform ID, PID/HWND/process identity, exception text, OCR text, target coordinates, image dimension, crop, frame, screenshot, pixel buffer, or digest field. Unknown fields fail decoding, so private material cannot be smuggled into a valid event envelope.

The normal audit stream is the only source eligible for Status, Discord summaries, telemetry, crash reports, and support/public exports. A later optional sensitive local sink may join validated private labels only after its separate protection gate; it must never alter or broaden this event schema.

## Append-only ownership

This module defines and validates records but does not claim filesystem append durability. DBG2 owns the durable append operation and must allocate `event_generation` monotonically per stream, persist intent before input, persist outcome after input, suppress input when the intent append fails, and mark an already-started action uncertain when the outcome append fails. Existing records are never updated in place.

Every sink boundary must call `require_pristine_audit_event` or `encode_audit_event`; tuple-backed storage snapshots canonical scalar text rather than retaining mutable enum internals and is rebuilt before serialization, so forged or corrupted nested values fail closed. Every reader must call `decode_audit_event`, which accepts only the exact canonical version-1 line.

## Verification

`tests/test_audit_event.py` proves canonical encoding and round-trip decoding, exact field closure, opaque-reference enforcement, forged-reference, forged-storage, mutated-enum, and mutated-visit-generation rejection, bounded exact integer handling, calendar-valid canonical timestamps, action-nonce validation, and rejection of extended, duplicate-key, or reformatted records. The tests use synthetic opaque values only and contain no images or private identifiers.
