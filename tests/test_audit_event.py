from __future__ import annotations

import json

import pytest

from clash_rush_rebuild.audit_event import (
    AccountRef,
    AuditEvent,
    AuditEventError,
    AuditEventKind,
    AuditOutcome,
    ReasonCode,
    TeamRef,
    decode_audit_event,
    encode_audit_event,
)
from clash_rush_rebuild.scheduler_contract import Lane, VisitGeneration


TEAM = TeamRef("1" * 32)
ACCOUNT = AccountRef("2" * 32)
ACTION_NONCE = "3" * 32


def event() -> AuditEvent:
    return AuditEvent(
        event_generation=1,
        team=TEAM,
        account_ref=ACCOUNT,
        visit_generation=VisitGeneration(4),
        lane=Lane.HOME,
        action_nonce=ACTION_NONCE,
        kind=AuditEventKind.ACTION_INTENT,
        reason_code=ReasonCode.POLICY_SELECTED,
        monotonic_ns=123_456_789,
        wall_time_utc="2030-01-02T03:04:05.000006Z",
        outcome=AuditOutcome.PENDING,
    )


def test_canonical_event_line_contains_only_the_sanitized_closed_schema() -> None:
    encoded = encode_audit_event(event())

    assert encoded.endswith(b"\n")
    assert len(encoded) <= 1024
    assert json.loads(encoded) == {
        "account_ref": "2" * 32,
        "action_nonce": "3" * 32,
        "event_generation": 1,
        "kind": "ACTION_INTENT",
        "lane": "HOME",
        "monotonic_ns": 123_456_789,
        "outcome": "PENDING",
        "reason_code": "POLICY_SELECTED",
        "schema_version": 1,
        "team": "1" * 32,
        "visit_generation": 4,
        "wall_time_utc": "2030-01-02T03:04:05.000006Z",
    }


def test_canonical_event_line_round_trips_to_an_exact_event() -> None:
    original = event()

    assert decode_audit_event(encode_audit_event(original)) == original
    assert type(decode_audit_event(encode_audit_event(original))) is AuditEvent


@pytest.mark.parametrize(
    ("factory", "private_value"),
    [
        (TeamRef, "configured-team-name"),
        (AccountRef, "player-tag-ABC123"),
        (AccountRef, "bluestacks-instance-name"),
    ],
)
def test_opaque_references_reject_private_identifier_shapes(
    factory: type[TeamRef] | type[AccountRef], private_value: str
) -> None:
    with pytest.raises(AuditEventError, match="opaque 128-bit"):
        factory(private_value)


def test_forged_opaque_reference_is_rejected_at_event_boundary() -> None:
    forged_team = str.__new__(TeamRef, "configured-private-team")

    with pytest.raises(AuditEventError, match="team"):
        AuditEvent(
            event_generation=1,
            team=forged_team,
            account_ref=ACCOUNT,
            visit_generation=VisitGeneration(4),
            lane=Lane.HOME,
            action_nonce=ACTION_NONCE,
            kind=AuditEventKind.ACTION_INTENT,
            reason_code=ReasonCode.POLICY_SELECTED,
            monotonic_ns=123,
            wall_time_utc="2030-01-02T03:04:05.000006Z",
            outcome=AuditOutcome.PENDING,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("event_generation", True),
        ("event_generation", 2**63),
        ("monotonic_ns", True),
        ("monotonic_ns", -1),
        ("monotonic_ns", 2**63),
        ("wall_time_utc", "2030-02-30T03:04:05.000006Z"),
        ("wall_time_utc", "2030-01-02T03:04:05+00:00"),
        ("action_nonce", "not-an-action-nonce"),
    ],
)
def test_malformed_scalar_fields_fail_closed(field_name: str, value: object) -> None:
    values = {
        "event_generation": 1,
        "team": TEAM,
        "account_ref": ACCOUNT,
        "visit_generation": VisitGeneration(4),
        "lane": Lane.HOME,
        "action_nonce": ACTION_NONCE,
        "kind": AuditEventKind.ACTION_INTENT,
        "reason_code": ReasonCode.POLICY_SELECTED,
        "monotonic_ns": 123,
        "wall_time_utc": "2030-01-02T03:04:05.000006Z",
        "outcome": AuditOutcome.PENDING,
    }
    values[field_name] = value

    with pytest.raises(AuditEventError):
        AuditEvent(**values)  # type: ignore[arg-type]


def test_forged_or_oversized_visit_generation_fails_at_event_boundary() -> None:
    for visit_generation in (VisitGeneration(2**63), VisitGeneration(4)):
        if visit_generation.value == 4:
            object.__setattr__(visit_generation, "value", True)
        values = {
            "event_generation": 1,
            "team": TEAM,
            "account_ref": ACCOUNT,
            "visit_generation": visit_generation,
            "lane": Lane.HOME,
            "action_nonce": ACTION_NONCE,
            "kind": AuditEventKind.ACTION_INTENT,
            "reason_code": ReasonCode.POLICY_SELECTED,
            "monotonic_ns": 123,
            "wall_time_utc": "2030-01-02T03:04:05.000006Z",
            "outcome": AuditOutcome.PENDING,
        }

        with pytest.raises(AuditEventError, match="visit generation"):
            AuditEvent(**values)


@pytest.mark.parametrize(
    ("field_name", "member"),
    [
        ("lane", Lane.HOME),
        ("kind", AuditEventKind.ACTION_INTENT),
        ("reason_code", ReasonCode.POLICY_SELECTED),
        ("outcome", AuditOutcome.PENDING),
    ],
)
@pytest.mark.parametrize("internal_field", ["_value_", "_name_"])
def test_mutated_exact_enum_cannot_create_a_private_text_channel(
    field_name: str, member: object, internal_field: str
) -> None:
    original = object.__getattribute__(member, internal_field)
    try:
        object.__setattr__(member, internal_field, "configured-private-label")
        values = {
            "event_generation": 1,
            "team": TEAM,
            "account_ref": ACCOUNT,
            "visit_generation": VisitGeneration(4),
            "lane": Lane.HOME,
            "action_nonce": ACTION_NONCE,
            "kind": AuditEventKind.ACTION_INTENT,
            "reason_code": ReasonCode.POLICY_SELECTED,
            "monotonic_ns": 123,
            "wall_time_utc": "2030-01-02T03:04:05.000006Z",
            "outcome": AuditOutcome.PENDING,
        }
        values[field_name] = member

        with pytest.raises(AuditEventError, match="closed|lane"):
            AuditEvent(**values)  # type: ignore[arg-type]
    finally:
        object.__setattr__(member, internal_field, original)


def test_decode_rejects_noncanonical_or_extended_records() -> None:
    canonical = json.loads(encode_audit_event(event()))

    extended = dict(canonical, screenshot="raw-pixel-material")
    duplicated = encode_audit_event(event()).replace(
        b'"team":"11111111111111111111111111111111"',
        b'"team":"11111111111111111111111111111111","team":"11111111111111111111111111111111"',
    )
    reformatted = json.dumps(canonical, sort_keys=True, indent=2).encode("ascii")

    for record in (
        json.dumps(extended, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n",
        duplicated,
        reformatted + b"\n",
    ):
        with pytest.raises(AuditEventError):
            decode_audit_event(record)


def test_forged_event_storage_cannot_be_serialized() -> None:
    forged_values = list(event())
    forged_values[1] = str.__new__(TeamRef, "configured-private-team")
    forged = tuple.__new__(AuditEvent, forged_values)

    with pytest.raises(AuditEventError, match="storage"):
        encode_audit_event(forged)
