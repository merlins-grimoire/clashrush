"""Sanitized, closed values for the append-only audit stream."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum

from .scheduler_contract import Lane, VisitGeneration


_OPAQUE_REF = re.compile(r"[0-9a-f]{32}")
_ACTION_NONCE = re.compile(r"[0-9a-f]{32}")
_WALL_TIME_UTC = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z"
)
_SCHEMA_VERSION = 1
_MAX_ENCODED_BYTES = 1024
_MAX_INTEGER = 2**63 - 1
_SCHEMA_FIELDS = frozenset(
    {
        "schema_version",
        "event_generation",
        "team",
        "account_ref",
        "visit_generation",
        "lane",
        "action_nonce",
        "kind",
        "reason_code",
        "monotonic_ns",
        "wall_time_utc",
        "outcome",
    }
)


class AuditEventError(ValueError):
    """An audit value is outside the sanitized closed contract."""


def _is_opaque_ref(value: object, expected_type: type[str]) -> bool:
    return (
        type(value) is expected_type
        and _OPAQUE_REF.fullmatch(str.__str__(value)) is not None
    )


def _is_wall_time_utc(value: object) -> bool:
    if type(value) is not str or _WALL_TIME_UTC.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") == value


class TeamRef(str):
    """Opaque 128-bit team partition identity."""

    def __new__(cls, value: str) -> TeamRef:
        if type(value) is not str or _OPAQUE_REF.fullmatch(value) is None:
            raise AuditEventError("team must be an opaque 128-bit lowercase-hex reference")
        return str.__new__(cls, value)


class AccountRef(str):
    """Setup-generated opaque 128-bit account identity."""

    def __new__(cls, value: str) -> AccountRef:
        if type(value) is not str or _OPAQUE_REF.fullmatch(value) is None:
            raise AuditEventError(
                "account_ref must be an opaque 128-bit lowercase-hex reference"
            )
        return str.__new__(cls, value)


class AuditEventKind(StrEnum):
    VISIT = "VISIT"
    DECISION = "DECISION"
    OBSERVATION = "OBSERVATION"
    ACTION_INTENT = "ACTION_INTENT"
    INPUT = "INPUT"
    POSTCONDITION = "POSTCONDITION"
    ACTION_OUTCOME = "ACTION_OUTCOME"
    STATE_TRANSITION = "STATE_TRANSITION"
    CONTROL = "CONTROL"
    BLOCKER = "BLOCKER"


class ReasonCode(StrEnum):
    SCHEDULED = "SCHEDULED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    OBSERVATION_UNKNOWN = "OBSERVATION_UNKNOWN"
    POLICY_SELECTED = "POLICY_SELECTED"
    POLICY_DEFERRED = "POLICY_DEFERRED"
    ACTION_AUTHORIZED = "ACTION_AUTHORIZED"
    PRECONDITION_REJECTED = "PRECONDITION_REJECTED"
    AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
    INPUT_ATTEMPTED = "INPUT_ATTEMPTED"
    INPUT_FAILED = "INPUT_FAILED"
    POSTCONDITION_CONFIRMED = "POSTCONDITION_CONFIRMED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    ACTION_UNCERTAIN = "ACTION_UNCERTAIN"
    LIFECYCLE_BLOCKED = "LIFECYCLE_BLOCKED"
    QUARANTINED = "QUARANTINED"
    LIMIT_REACHED = "LIMIT_REACHED"
    OPERATOR_CONTROL = "OPERATOR_CONTROL"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AuditOutcome(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    SUPPRESSED = "SUPPRESSED"


_LANE_VOCABULARY = (
    (Lane.HOME, "HOME"),
    (Lane.BUILDER, "BUILDER"),
)
_KIND_VOCABULARY = (
    (AuditEventKind.VISIT, "VISIT"),
    (AuditEventKind.DECISION, "DECISION"),
    (AuditEventKind.OBSERVATION, "OBSERVATION"),
    (AuditEventKind.ACTION_INTENT, "ACTION_INTENT"),
    (AuditEventKind.INPUT, "INPUT"),
    (AuditEventKind.POSTCONDITION, "POSTCONDITION"),
    (AuditEventKind.ACTION_OUTCOME, "ACTION_OUTCOME"),
    (AuditEventKind.STATE_TRANSITION, "STATE_TRANSITION"),
    (AuditEventKind.CONTROL, "CONTROL"),
    (AuditEventKind.BLOCKER, "BLOCKER"),
)
_REASON_VOCABULARY = (
    (ReasonCode.SCHEDULED, "SCHEDULED"),
    (ReasonCode.OBSERVATION_RECORDED, "OBSERVATION_RECORDED"),
    (ReasonCode.OBSERVATION_UNKNOWN, "OBSERVATION_UNKNOWN"),
    (ReasonCode.POLICY_SELECTED, "POLICY_SELECTED"),
    (ReasonCode.POLICY_DEFERRED, "POLICY_DEFERRED"),
    (ReasonCode.ACTION_AUTHORIZED, "ACTION_AUTHORIZED"),
    (ReasonCode.PRECONDITION_REJECTED, "PRECONDITION_REJECTED"),
    (ReasonCode.AUDIT_WRITE_FAILED, "AUDIT_WRITE_FAILED"),
    (ReasonCode.INPUT_ATTEMPTED, "INPUT_ATTEMPTED"),
    (ReasonCode.INPUT_FAILED, "INPUT_FAILED"),
    (ReasonCode.POSTCONDITION_CONFIRMED, "POSTCONDITION_CONFIRMED"),
    (ReasonCode.POSTCONDITION_FAILED, "POSTCONDITION_FAILED"),
    (ReasonCode.ACTION_UNCERTAIN, "ACTION_UNCERTAIN"),
    (ReasonCode.LIFECYCLE_BLOCKED, "LIFECYCLE_BLOCKED"),
    (ReasonCode.QUARANTINED, "QUARANTINED"),
    (ReasonCode.LIMIT_REACHED, "LIMIT_REACHED"),
    (ReasonCode.OPERATOR_CONTROL, "OPERATOR_CONTROL"),
    (ReasonCode.INTERNAL_ERROR, "INTERNAL_ERROR"),
)
_OUTCOME_VOCABULARY = (
    (AuditOutcome.PENDING, "PENDING"),
    (AuditOutcome.SUCCEEDED, "SUCCEEDED"),
    (AuditOutcome.FAILED, "FAILED"),
    (AuditOutcome.UNCERTAIN, "UNCERTAIN"),
    (AuditOutcome.DEFERRED, "DEFERRED"),
    (AuditOutcome.BLOCKED, "BLOCKED"),
    (AuditOutcome.SUPPRESSED, "SUPPRESSED"),
)


def _closed_member_text(
    value: object,
    expected_type: type[object],
    vocabulary: tuple[tuple[object, str], ...],
    label: str,
) -> str:
    if type(value) is not expected_type:
        raise AuditEventError(f"{label} must be a closed value")
    for member, text in vocabulary:
        if value is member:
            if (
                type(object.__getattribute__(member, "_name_")) is not str
                or object.__getattribute__(member, "_name_") != text
                or type(object.__getattribute__(member, "_value_")) is not str
                or object.__getattribute__(member, "_value_") != text
            ):
                raise AuditEventError(f"{label} closed value is corrupted")
            return text
    raise AuditEventError(f"{label} must be a closed value")


def _member_from_text(
    text: object,
    vocabulary: tuple[tuple[object, str], ...],
    label: str,
) -> object:
    if type(text) is not str:
        raise AuditEventError(f"{label} must be closed text")
    for member, canonical in vocabulary:
        if text == canonical:
            _closed_member_text(member, type(member), vocabulary, label)
            return member
    raise AuditEventError(f"{label} must be closed text")


class AuditEvent(tuple[object, ...]):
    """One immutable sanitized event; storage assigns generation in append order."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        event_generation: int,
        team: TeamRef,
        account_ref: AccountRef,
        visit_generation: VisitGeneration,
        lane: Lane,
        action_nonce: str,
        kind: AuditEventKind,
        reason_code: ReasonCode,
        monotonic_ns: int,
        wall_time_utc: str,
        outcome: AuditOutcome,
    ) -> AuditEvent:
        if (
            type(event_generation) is not int
            or event_generation <= 0
            or event_generation > _MAX_INTEGER
        ):
            raise AuditEventError("event generation must be a positive integer")
        if not _is_opaque_ref(team, TeamRef) or not _is_opaque_ref(
            account_ref, AccountRef
        ):
            raise AuditEventError("team and account references must be exact opaque values")
        if (
            type(visit_generation) is not VisitGeneration
            or type(visit_generation.value) is not int
            or visit_generation.value <= 0
            or visit_generation.value > _MAX_INTEGER
        ):
            raise AuditEventError("visit generation must be exact")
        lane_text = _closed_member_text(lane, Lane, _LANE_VOCABULARY, "lane")
        if type(action_nonce) is not str or _ACTION_NONCE.fullmatch(action_nonce) is None:
            raise AuditEventError("action nonce must be 32 lowercase hex characters")
        kind_text = _closed_member_text(
            kind, AuditEventKind, _KIND_VOCABULARY, "event kind"
        )
        reason_text = _closed_member_text(
            reason_code, ReasonCode, _REASON_VOCABULARY, "reason code"
        )
        if (
            type(monotonic_ns) is not int
            or monotonic_ns < 0
            or monotonic_ns > _MAX_INTEGER
        ):
            raise AuditEventError("monotonic timestamp must be non-negative integer nanoseconds")
        if not _is_wall_time_utc(wall_time_utc):
            raise AuditEventError("wall timestamp must be canonical UTC with microseconds")
        outcome_text = _closed_member_text(
            outcome, AuditOutcome, _OUTCOME_VOCABULARY, "outcome"
        )
        return tuple.__new__(
            cls,
            (
                event_generation,
                str.__str__(team),
                str.__str__(account_ref),
                visit_generation.value,
                lane_text,
                action_nonce,
                kind_text,
                reason_text,
                monotonic_ns,
                wall_time_utc,
                outcome_text,
            ),
        )

    event_generation = property(lambda self: self[0])
    team = property(lambda self: TeamRef(self[1]))
    account_ref = property(lambda self: AccountRef(self[2]))
    visit_generation = property(lambda self: VisitGeneration(self[3]))
    lane = property(lambda self: _member_from_text(self[4], _LANE_VOCABULARY, "lane"))
    action_nonce = property(lambda self: self[5])
    kind = property(
        lambda self: _member_from_text(self[6], _KIND_VOCABULARY, "event kind")
    )
    reason_code = property(
        lambda self: _member_from_text(self[7], _REASON_VOCABULARY, "reason code")
    )
    monotonic_ns = property(lambda self: self[8])
    wall_time_utc = property(lambda self: self[9])
    outcome = property(
        lambda self: _member_from_text(self[10], _OUTCOME_VOCABULARY, "outcome")
    )


def require_pristine_audit_event(event: object) -> AuditEvent:
    """Rebuild an event so forged tuple storage cannot cross a sink boundary."""

    if type(event) is not AuditEvent:
        raise AuditEventError("event must be an exact AuditEvent")
    try:
        rebuilt = AuditEvent(
            event_generation=event.event_generation,
            team=event.team,
            account_ref=event.account_ref,
            visit_generation=event.visit_generation,
            lane=event.lane,
            action_nonce=event.action_nonce,
            kind=event.kind,
            reason_code=event.reason_code,
            monotonic_ns=event.monotonic_ns,
            wall_time_utc=event.wall_time_utc,
            outcome=event.outcome,
        )
    except (IndexError, TypeError, ValueError):
        raise AuditEventError("audit event storage is invalid") from None
    if tuple(rebuilt) != tuple(event):
        raise AuditEventError("audit event storage is noncanonical")
    return rebuilt


def encode_audit_event(event: AuditEvent) -> bytes:
    """Return one canonical UTF-8 JSON Lines record."""

    event = require_pristine_audit_event(event)
    document = {
        "schema_version": _SCHEMA_VERSION,
        "event_generation": event.event_generation,
        "team": event[1],
        "account_ref": event[2],
        "visit_generation": event.visit_generation.value,
        "lane": event[4],
        "action_nonce": event.action_nonce,
        "kind": event[6],
        "reason_code": event[7],
        "monotonic_ns": event.monotonic_ns,
        "wall_time_utc": event.wall_time_utc,
        "outcome": event[10],
    }
    encoded = (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")
    if len(encoded) > _MAX_ENCODED_BYTES:
        raise AuditEventError("audit record exceeds the maximum encoded size")
    return encoded


def decode_audit_event(record: bytes) -> AuditEvent:
    """Validate and decode exactly one canonical sanitized JSON Lines record."""

    if (
        type(record) is not bytes
        or not record.endswith(b"\n")
        or len(record) > _MAX_ENCODED_BYTES
    ):
        raise AuditEventError("audit record framing is invalid")
    try:
        document = json.loads(record)
        if type(document) is not dict or frozenset(document) != _SCHEMA_FIELDS:
            raise AuditEventError("audit record fields are invalid")
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != _SCHEMA_VERSION
        ):
            raise AuditEventError("audit schema version is invalid")
        event = AuditEvent(
            event_generation=document["event_generation"],
            team=TeamRef(document["team"]),
            account_ref=AccountRef(document["account_ref"]),
            visit_generation=VisitGeneration(document["visit_generation"]),
            lane=_member_from_text(document["lane"], _LANE_VOCABULARY, "lane"),
            action_nonce=document["action_nonce"],
            kind=_member_from_text(
                document["kind"], _KIND_VOCABULARY, "event kind"
            ),
            reason_code=_member_from_text(
                document["reason_code"], _REASON_VOCABULARY, "reason code"
            ),
            monotonic_ns=document["monotonic_ns"],
            wall_time_utc=document["wall_time_utc"],
            outcome=_member_from_text(
                document["outcome"], _OUTCOME_VOCABULARY, "outcome"
            ),
        )
    except AuditEventError:
        raise
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise AuditEventError("audit record content is invalid") from None
    if encode_audit_event(event) != record:
        raise AuditEventError("audit record encoding is noncanonical")
    return event
