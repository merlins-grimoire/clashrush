from __future__ import annotations

import json

import pytest

from clash_rush_rebuild.configuration_v2 import ConfigurationGeneration
from clash_rush_rebuild.discord_control_protocol import (
    PROTOCOL_VERSION,
    AcknowledgementDisposition,
    AcknowledgementReason,
    AppliedControl,
    CommandId,
    CommandAcknowledgement,
    ControlCommand,
    ControlEpoch,
    DeliveryAction,
    DeliveryResult,
    DesiredControlState,
    IssuerRef,
    ProtocolValidationError,
    RegistryDecision,
    TeamKey,
    classify_registration,
    decode_acknowledgement,
    decode_command,
    encode_acknowledgement,
    encode_command,
    evaluate_delivery,
)


GENERATION = ConfigurationGeneration("0123456789abcdef0123456789abcdef")


def command() -> ControlCommand:
    return ControlCommand(
        command_id=CommandId("a" * 32),
        team_key=TeamKey("synthetic-team"),
        issuer=IssuerRef("b" * 32),
        desired_state=DesiredControlState.PAUSED,
        issued_at=1_000,
        expires_at=1_300,
        control_epoch=ControlEpoch(7),
        configuration_generation=GENERATION,
    )


def test_command_freezes_every_required_identity_and_control_field() -> None:
    value = command()

    assert PROTOCOL_VERSION == 1
    assert value.command_id == CommandId("a" * 32)
    assert value.team_key == TeamKey("synthetic-team")
    assert value.issuer == IssuerRef("b" * 32)
    assert value.desired_state is DesiredControlState.PAUSED
    assert value.issued_at == 1_000
    assert value.expires_at == 1_300
    assert value.control_epoch == ControlEpoch(7)
    assert value.configuration_generation == GENERATION

    with pytest.raises(AttributeError):
        value.desired_state = DesiredControlState.RUNNING  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", CommandId("c" * 32)),
        ("team_key", TeamKey("other-team")),
        ("issuer", IssuerRef("d" * 32)),
        ("desired_state", DesiredControlState.RUNNING),
        ("issued_at", 1_001),
        ("expires_at", 1_299),
        ("control_epoch", ControlEpoch(8)),
        ("configuration_generation", ConfigurationGeneration("f" * 32)),
    ],
)
def test_command_identity_includes_every_frozen_field(field: str, value: object) -> None:
    original = command()
    values = {name: getattr(original, name) for name in ControlCommand.__slots__}
    values[field] = value
    changed = ControlCommand(**values)

    assert changed != original


@pytest.mark.parametrize("value", ["A" * 32, "a" * 31, "g" * 32, "", 1, True])
def test_command_id_is_exact_canonical_128_bit_hex(value: object) -> None:
    with pytest.raises(ProtocolValidationError, match="command ID"):
        CommandId(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["Team One", "-team", "team-", "", "a" * 65, 1])
def test_team_key_is_exact_lower_ascii_slug(value: object) -> None:
    with pytest.raises(ProtocolValidationError, match="team key"):
        TeamKey(value)  # type: ignore[arg-type]


def test_issuer_is_an_opaque_reference_not_a_discord_user_id_field() -> None:
    assert IssuerRef("b" * 32).value == "b" * 32
    assert "discord" not in " ".join(ControlCommand.__slots__).lower()


@pytest.mark.parametrize("value", [0, -1, True, 1.0, 2**63])
def test_control_epoch_is_positive_bounded_exact_integer(value: object) -> None:
    with pytest.raises(ProtocolValidationError, match="control epoch"):
        ControlEpoch(value)  # type: ignore[arg-type]


def test_command_requires_exact_types_and_bounded_expiry() -> None:
    values = {name: getattr(command(), name) for name in ControlCommand.__slots__}
    invalid = (
        ("command_id", "a" * 32),
        ("team_key", "synthetic-team"),
        ("issuer", "b" * 32),
        ("desired_state", "PAUSED"),
        ("issued_at", True),
        ("expires_at", 1_000),
        ("expires_at", 1_301),
        ("control_epoch", 7),
        ("configuration_generation", "0" * 32),
    )
    for field, value in invalid:
        candidate = dict(values)
        candidate[field] = value
        with pytest.raises(ProtocolValidationError):
            ControlCommand(**candidate)


def test_newer_live_command_produces_one_terminal_applied_ack_and_record() -> None:
    result = evaluate_delivery(
        command(),
        current=None,
        active_team=TeamKey("synthetic-team"),
        active_generation=GENERATION,
        now=1_050,
    )

    assert result.action is DeliveryAction.PERSIST_AND_ACK
    assert type(result.replacement) is AppliedControl
    assert result.replacement.command == command()
    assert result.acknowledgement == result.replacement.acknowledgement
    assert result.acknowledgement.disposition is AcknowledgementDisposition.APPLIED
    assert result.acknowledgement.reason is AcknowledgementReason.APPLIED
    assert result.acknowledgement.command_id == command().command_id
    assert result.acknowledgement.team_key == command().team_key
    assert result.acknowledgement.issuer == command().issuer
    assert result.acknowledgement.desired_state is DesiredControlState.PAUSED
    assert result.acknowledgement.issued_at == command().issued_at
    assert result.acknowledgement.expires_at == command().expires_at
    assert result.acknowledgement.control_epoch == command().control_epoch
    assert result.acknowledgement.configuration_generation == GENERATION


def test_exact_duplicate_replays_byte_equivalent_terminal_ack_without_persistence() -> None:
    first = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    duplicate = evaluate_delivery(
        command(),
        current=first.replacement,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=9_999,
    )

    assert duplicate.action is DeliveryAction.REPLAY_ACK
    assert duplicate.replacement is None
    assert duplicate.acknowledgement is not first.replacement.acknowledgement
    assert encode_acknowledgement(duplicate.acknowledgement) == encode_acknowledgement(
        first.replacement.acknowledgement
    )


@pytest.mark.parametrize(
    ("active_team", "active_generation", "reason"),
    [
        (TeamKey("other-team"), GENERATION, AcknowledgementReason.TEAM_MISMATCH),
        (
            TeamKey("synthetic-team"),
            ConfigurationGeneration("f" * 32),
            AcknowledgementReason.CONFIGURATION_MISMATCH,
        ),
    ],
)
def test_exact_duplicate_revalidates_active_team_and_generation_before_replay(
    active_team: TeamKey,
    active_generation: ConfigurationGeneration,
    reason: AcknowledgementReason,
) -> None:
    first = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )

    duplicate = evaluate_delivery(
        command(),
        current=first.replacement,
        active_team=active_team,
        active_generation=active_generation,
        now=1_050,
    )

    assert duplicate.action is DeliveryAction.REJECT
    assert duplicate.acknowledgement.reason is reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team_key", TeamKey("other-team")),
        ("issuer", IssuerRef("c" * 32)),
        ("desired_state", DesiredControlState.STOPPED),
        ("issued_at", 1_001),
        ("expires_at", 1_299),
        ("control_epoch", ControlEpoch(8)),
        ("configuration_generation", ConfigurationGeneration("f" * 32)),
    ],
)
def test_reused_command_id_with_any_changed_field_is_conflict(
    field: str, value: object
) -> None:
    first = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    values = {name: getattr(command(), name) for name in ControlCommand.__slots__}
    values[field] = value
    conflicting = ControlCommand(**values)

    result = evaluate_delivery(
        conflicting,
        current=first.replacement,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )

    assert result.action is DeliveryAction.REJECT
    assert result.replacement is None
    assert result.acknowledgement.disposition is AcknowledgementDisposition.REJECTED
    assert result.acknowledgement.reason is AcknowledgementReason.COMMAND_CONFLICT


@pytest.mark.parametrize(
    ("mutation", "now", "active_team", "active_generation", "reason"),
    [
        ({}, 1_300, TeamKey("synthetic-team"), GENERATION, AcknowledgementReason.EXPIRED),
        ({}, 999, TeamKey("synthetic-team"), GENERATION, AcknowledgementReason.NOT_YET_VALID),
        ({}, 1_050, TeamKey("other-team"), GENERATION, AcknowledgementReason.TEAM_MISMATCH),
        (
            {},
            1_050,
            TeamKey("synthetic-team"),
            ConfigurationGeneration("f" * 32),
            AcknowledgementReason.CONFIGURATION_MISMATCH,
        ),
    ],
)
def test_semantically_invalid_delivery_is_terminally_rejected_without_state_change(
    mutation: dict[str, object],
    now: int,
    active_team: TeamKey,
    active_generation: ConfigurationGeneration,
    reason: AcknowledgementReason,
) -> None:
    values = {name: getattr(command(), name) for name in ControlCommand.__slots__}
    values.update(mutation)
    result = evaluate_delivery(
        ControlCommand(**values),
        current=None,
        active_team=active_team,
        active_generation=active_generation,
        now=now,
    )

    assert result.action is DeliveryAction.REJECT
    assert result.replacement is None
    assert result.acknowledgement.reason is reason


def test_different_command_must_advance_epoch_strictly() -> None:
    first = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    values = {name: getattr(command(), name) for name in ControlCommand.__slots__}
    values["command_id"] = CommandId("c" * 32)
    stale = ControlCommand(**values)

    rejected = evaluate_delivery(
        stale,
        current=first.replacement,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    assert rejected.acknowledgement.reason is AcknowledgementReason.STALE_EPOCH

    values["control_epoch"] = ControlEpoch(8)
    advanced = evaluate_delivery(
        ControlCommand(**values),
        current=first.replacement,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    assert advanced.action is DeliveryAction.PERSIST_AND_ACK
    assert advanced.replacement.control_epoch == ControlEpoch(8)  # type: ignore[union-attr]


@pytest.mark.parametrize("now", [True, 1.0, -1, 2**63])
def test_delivery_rejects_malformed_clock_before_comparison(now: object) -> None:
    with pytest.raises(ProtocolValidationError, match="current time"):
        evaluate_delivery(
            command(),
            current=None,
            active_team=command().team_key,
            active_generation=GENERATION,
            now=now,  # type: ignore[arg-type]
        )


def test_command_wire_encoding_is_canonical_versioned_and_round_trips() -> None:
    encoded = encode_command(command())

    assert encoded.endswith(b"\n")
    assert decode_command(encoded) == command()
    assert json.loads(encoded) == {
        "command_id": "a" * 32,
        "configuration_generation": GENERATION.value,
        "control_epoch": 7,
        "desired_state": "PAUSED",
        "expires_at": 1_300,
        "issued_at": 1_000,
        "issuer": "b" * 32,
        "message_type": "CONTROL_COMMAND",
        "protocol_version": 1,
        "team_key": "synthetic-team",
    }


def test_acknowledgement_wire_encoding_binds_the_full_command_identity() -> None:
    result = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    encoded = encode_acknowledgement(result.acknowledgement)

    assert decode_acknowledgement(encoded) == result.acknowledgement
    assert json.loads(encoded) == {
        "command_id": "a" * 32,
        "configuration_generation": GENERATION.value,
        "control_epoch": 7,
        "desired_state": "PAUSED",
        "disposition": "APPLIED",
        "expires_at": 1_300,
        "issued_at": 1_000,
        "issuer": "b" * 32,
        "message_type": "COMMAND_ACKNOWLEDGEMENT",
        "protocol_version": 1,
        "reason": "APPLIED",
        "team_key": "synthetic-team",
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{' + b'"protocol_version":1,' * 2 + b'"x":1}',
        encode_command(command()).replace(b'"protocol_version":1', b'"protocol_version":2'),
        encode_command(command()).replace(b'"PAUSED"', b'"UNKNOWN"'),
        encode_command(command()).replace(b'"team_key":"synthetic-team"', b'"team_key":"other","extra":1'),
        encode_command(command()).replace(b'"control_epoch":7', b'"control_epoch":true'),
        b"x" * 4097,
        "not-bytes",
    ],
)
def test_command_decoder_rejects_malformed_noncanonical_or_unknown_wire_values(
    payload: object,
) -> None:
    with pytest.raises(ProtocolValidationError, match="command payload"):
        decode_command(payload)  # type: ignore[arg-type]


def test_acknowledgement_rejects_disposition_reason_disagreement() -> None:
    with pytest.raises(ProtocolValidationError, match="disagree"):
        CommandAcknowledgement(
            command_id=command().command_id,
            team_key=command().team_key,
            issuer=command().issuer,
            desired_state=command().desired_state,
            issued_at=command().issued_at,
            expires_at=command().expires_at,
            control_epoch=command().control_epoch,
            configuration_generation=GENERATION,
            disposition=AcknowledgementDisposition.APPLIED,
            reason=AcknowledgementReason.EXPIRED,
        )


def test_rejected_acknowledgement_round_trips_without_becoming_applied() -> None:
    rejected = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_300,
    ).acknowledgement

    decoded = decode_acknowledgement(encode_acknowledgement(rejected))
    assert decoded.disposition is AcknowledgementDisposition.REJECTED
    assert decoded.reason is AcknowledgementReason.EXPIRED


def test_central_registry_deduplicates_exact_command_and_rejects_id_reuse() -> None:
    assert classify_registration(None, command()) is RegistryDecision.NEW
    assert classify_registration(command(), command()) is RegistryDecision.DUPLICATE

    values = {name: getattr(command(), name) for name in ControlCommand.__slots__}
    values["desired_state"] = DesiredControlState.STOPPED
    with pytest.raises(ProtocolValidationError, match="immutable command ID"):
        classify_registration(command(), ControlCommand(**values))


def test_protocol_surface_has_no_gameplay_spending_or_lifecycle_authority() -> None:
    import clash_rush_rebuild.discord_control_protocol as protocol

    forbidden_fragments = (
        "gameplay",
        "spend",
        "executor",
        "schedulerport",
        "lifecycleport",
        "process",
        "inputport",
        "discordclient",
    )
    public_names = tuple(name.lower() for name in protocol.__dict__ if not name.startswith("_"))

    assert not any(
        fragment in name for name in public_names for fragment in forbidden_fragments
    )


def test_delivery_rejects_structurally_corrupted_exact_values_before_comparison() -> None:
    corrupted = command()
    object.__setattr__(corrupted, "command_id", object())
    with pytest.raises(ProtocolValidationError):
        evaluate_delivery(
            corrupted,
            current=None,
            active_team=TeamKey("synthetic-team"),
            active_generation=GENERATION,
            now=1_050,
        )

    valid = command()
    first = evaluate_delivery(
        valid,
        current=None,
        active_team=valid.team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    object.__setattr__(first.replacement.acknowledgement, "reason", object())  # type: ignore[union-attr]
    with pytest.raises(ProtocolValidationError):
        evaluate_delivery(
            valid,
            current=first.replacement,
            active_team=valid.team_key,
            active_generation=GENERATION,
            now=1_050,
        )


def test_applied_control_construction_sanitizes_hostile_ack_equality() -> None:
    class HostileEquality:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("raw-eq-leak")

    first = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    acknowledgement = first.acknowledgement
    object.__setattr__(acknowledgement.command_id, "value", HostileEquality())

    with pytest.raises(ProtocolValidationError) as raised:
        AppliedControl(command(), acknowledgement)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    with pytest.raises(ProtocolValidationError) as replay_raised:
        DeliveryResult(DeliveryAction.REPLAY_ACK, acknowledgement, first.replacement)
    assert replay_raised.value.__cause__ is None
    assert replay_raised.value.__context__ is None


def test_acknowledgement_identity_values_do_not_alias_applied_command_values() -> None:
    result = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    assert result.replacement is not None
    outbound_acknowledgement = result.acknowledgement
    acknowledgement = result.replacement.acknowledgement
    stored_command = result.replacement.command

    assert outbound_acknowledgement is not acknowledgement
    assert encode_acknowledgement(outbound_acknowledgement) == encode_acknowledgement(
        acknowledgement
    )
    assert acknowledgement.command_id is not stored_command.command_id
    assert acknowledgement.team_key is not stored_command.team_key
    assert acknowledgement.issuer is not stored_command.issuer
    assert acknowledgement.control_epoch is not stored_command.control_epoch
    assert (
        acknowledgement.configuration_generation
        is not stored_command.configuration_generation
    )

    object.__setattr__(acknowledgement.command_id, "value", "c" * 32)
    assert stored_command.command_id == CommandId("a" * 32)
    assert outbound_acknowledgement.command_id == CommandId("a" * 32)
    with pytest.raises(ProtocolValidationError):
        AppliedControl(stored_command, acknowledgement)


def test_public_ingress_failures_retain_no_private_exception_context() -> None:
    class SecretProperty:
        @property
        def value(self) -> object:
            raise RuntimeError("PRIVATE-CANARY-DO-NOT-LEAK")

    corrupted_command = command()
    object.__setattr__(corrupted_command, "command_id", SecretProperty())
    valid = evaluate_delivery(
        command(),
        current=None,
        active_team=command().team_key,
        active_generation=GENERATION,
        now=1_050,
    )
    corrupted_acknowledgement = valid.acknowledgement
    object.__setattr__(corrupted_acknowledgement, "command_id", SecretProperty())

    operations = (
        lambda: encode_command(corrupted_command),
        lambda: classify_registration(None, corrupted_command),
        lambda: evaluate_delivery(
            corrupted_command,
            current=None,
            active_team=TeamKey("synthetic-team"),
            active_generation=GENERATION,
            now=1_050,
        ),
        lambda: encode_acknowledgement(corrupted_acknowledgement),
        lambda: decode_command(b"not-json"),
        lambda: decode_acknowledgement(b"not-json"),
    )
    for operation in operations:
        with pytest.raises(ProtocolValidationError) as raised:
            operation()
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert "PRIVATE-CANARY" not in str(raised.value)
