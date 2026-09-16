from __future__ import annotations

import dataclasses
import json

import pytest

from clash_rush_rebuild.configuration_v2 import ConfigurationGeneration
from clash_rush_rebuild.discord_control_protocol import (
    AcknowledgementDisposition,
    CommandId,
    ControlCommand,
    ControlEpoch,
    DesiredControlState,
    IssuerRef,
    RegistryDecision,
    TeamKey,
    decode_acknowledgement,
    encode_acknowledgement,
    encode_command,
)
from clash_rush_rebuild.runner_enrollment import (
    AuthenticationError,
    CredentialId,
    EnrollmentAuthority,
    TransportBinding,
    create_authentication_proof,
)
from clash_rush_rebuild.team_routing import (
    AcknowledgementDecision,
    CentralTeamRouter,
    ConnectionHeartbeat,
    RunnerCommandPersistence,
    TeamRoute,
    TeamRoutingError,
)


TEAM = TeamKey("synthetic-team")
OTHER_TEAM = TeamKey("other-team")
GENERATION = ConfigurationGeneration("1" * 32)
NEXT_GENERATION = ConfigurationGeneration("2" * 32)
CHANNEL = bytes.fromhex("3" * 64)
OTHER_CHANNEL = bytes.fromhex("4" * 64)


def transport(channel: bytes = CHANNEL) -> TransportBinding:
    return TransportBinding(True, True, True, channel)


def authenticated_runner(
    *, team: TeamKey = TEAM, channel: bytes = CHANNEL, suffix: str = "5"
) -> tuple[EnrollmentAuthority, object, object]:
    ids = iter((suffix * 32, chr(ord(suffix) + 1) * 32))
    secrets = iter((bytes([ord(suffix)]) * 32, bytes([ord(suffix) + 1]) * 32))
    challenges = iter(
        (
            (chr(ord(suffix) + 2) * 32, bytes([ord(suffix) + 2]) * 32),
            (chr(ord(suffix) + 3) * 32, bytes([ord(suffix) + 3]) * 32),
        )
    )
    authority = EnrollmentAuthority(
        random_id=ids.__next__,
        random_secret=secrets.__next__,
        random_challenge=challenges.__next__,
    )
    connection = transport(channel)
    grant = authority.issue_enrollment(team, now=10, expires_at=70)
    credential = authority.enroll(grant, transport=connection, now=20)
    challenge = authority.begin_authentication(
        credential.credential_id,
        transport=connection,
        now=30,
        expires_at=60,
    )
    proof = create_authentication_proof(credential, challenge, transport=connection)
    runner = authority.complete_authentication(
        challenge,
        proof,
        transport=connection,
        now=40,
    ).runner
    return authority, credential, runner


def command(
    *,
    team: TeamKey = TEAM,
    generation: ConfigurationGeneration = GENERATION,
    command_id: str = "a" * 32,
    epoch: int = 1,
    state: DesiredControlState = DesiredControlState.PAUSED,
) -> ControlCommand:
    return ControlCommand(
        command_id=CommandId(command_id),
        team_key=team,
        issuer=IssuerRef("b" * 32),
        desired_state=state,
        issued_at=100,
        expires_at=200,
        control_epoch=ControlEpoch(epoch),
        configuration_generation=generation,
    )


def connected_router() -> tuple[CentralTeamRouter, EnrollmentAuthority, object, object]:
    authority, credential, runner = authenticated_runner()
    router = CentralTeamRouter(
        authority,
        routes=(TeamRoute(TEAM, GENERATION),),
        heartbeat_timeout_seconds=30,
    )
    router.attach(runner, transport=transport())
    router.record_heartbeat(
        runner,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(GENERATION),
        now=100,
    )
    return router, authority, credential, runner


def test_authenticated_team_route_delivers_and_persists_one_exact_generation_command() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())

    assert router.register_command(payload) is RegistryDecision.NEW
    assert router.deliver(runner, transport=transport(), now=101) == payload

    runner_store = RunnerCommandPersistence(TEAM, GENERATION)
    acknowledgement = runner_store.accept_delivery(payload, now=101)
    assert decode_acknowledgement(acknowledgement).disposition is AcknowledgementDisposition.APPLIED
    assert runner_store.applied_command_bytes() == payload
    assert router.accept_acknowledgement(
        runner,
        transport=transport(),
        payload=acknowledgement,
        now=102,
    ) is AcknowledgementDecision.RECORDED
    assert router.terminal_acknowledgement(command().command_id) == acknowledgement


def test_unknown_forged_and_wrong_channel_sessions_fail_before_delivery() -> None:
    router, authority, _, runner = connected_router()
    router.register_command(encode_command(command()))
    forged = dataclasses.replace(runner)

    for candidate, connection in (
        (forged, transport()),
        (runner, transport(OTHER_CHANNEL)),
    ):
        with pytest.raises(TeamRoutingError, match="authenticated connection is inactive"):
            router.deliver(candidate, transport=connection, now=101)

    with pytest.raises(AuthenticationError):
        authority.validate_session(forged, transport=transport())


def test_new_authenticated_connection_makes_prior_team_session_stale() -> None:
    router, authority, credential, first = connected_router()
    challenge = authority.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=50,
        expires_at=80,
    )
    proof = create_authentication_proof(credential, challenge, transport=transport())
    second = authority.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=55,
    ).runner
    router.attach(second, transport=transport())
    router.record_heartbeat(
        second,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(GENERATION),
        now=100,
    )
    router.register_command(encode_command(command()))

    with pytest.raises(TeamRoutingError, match="authenticated connection is inactive"):
        router.deliver(first, transport=transport(), now=101)
    assert router.deliver(second, transport=transport(), now=101) == encode_command(command())


def test_revocation_blocks_heartbeat_delivery_and_acknowledgement() -> None:
    router, authority, credential, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    assert router.deliver(runner, transport=transport(), now=101) == payload
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )
    authority.revoke(credential.credential_id, now=60)

    operations = (
        lambda: router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=102,
        ),
        lambda: router.deliver(runner, transport=transport(), now=102),
        lambda: router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=102,
        ),
    )
    for operation in operations:
        with pytest.raises(TeamRoutingError, match="authenticated connection is inactive"):
            operation()


def test_mutated_or_retargeted_authenticated_identity_never_changes_service_route() -> None:
    router, _, _, runner = connected_router()
    router.register_command(encode_command(command()))
    object.__setattr__(runner.team_key, "value", OTHER_TEAM.value)

    with pytest.raises(TeamRoutingError, match="authenticated connection is inactive"):
        router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=101,
        )
    with pytest.raises(TeamRoutingError, match="authenticated connection is inactive"):
        router.deliver(runner, transport=transport(), now=101)


def test_heartbeat_is_freshness_only_and_expires_at_exact_deadline() -> None:
    router, _, _, runner = connected_router()
    router.register_command(encode_command(command()))

    assert router.deliver(runner, transport=transport(), now=129) == encode_command(command())
    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=130)
    assert not hasattr(ConnectionHeartbeat(GENERATION), "desired_state")
    assert not hasattr(ConnectionHeartbeat(GENERATION), "command")


@pytest.mark.parametrize("operation", ("heartbeat", "delivery", "acknowledgement"))
def test_expiry_observation_prevents_clock_rollback_freshness_resurrection(
    operation: str,
) -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )

    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=130)

    if operation == "heartbeat":
        action = lambda: router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=129,
        )
    elif operation == "delivery":
        action = lambda: router.deliver(runner, transport=transport(), now=129)
    else:
        action = lambda: router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=129,
        )

    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        action()
    assert router.terminal_acknowledgement(command().command_id) is None


@pytest.mark.parametrize("operation", ("heartbeat", "delivery", "acknowledgement"))
def test_successful_time_ingress_rejects_later_clock_regression(operation: str) -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )

    if operation == "heartbeat":
        router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=110,
        )
        action = lambda: router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=109,
        )
    elif operation == "delivery":
        assert router.deliver(runner, transport=transport(), now=110) == payload
        action = lambda: router.deliver(runner, transport=transport(), now=109)
    else:
        router.deliver(runner, transport=transport(), now=101)
        assert router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=110,
        ) is AcknowledgementDecision.RECORDED
        action = lambda: router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=109,
        )

    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        action()


def test_rejected_time_bearing_ingress_still_advances_service_clock() -> None:
    router, _, _, runner = connected_router()

    with pytest.raises(TeamRoutingError, match="heartbeat is invalid"):
        router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION, protocol_version=2),
            now=120,
        )
    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=119,
        )


@pytest.mark.parametrize("operation", ("heartbeat", "delivery", "acknowledgement"))
@pytest.mark.parametrize("now", (True, -1, 2**63, 1.0, None))
def test_service_clock_rejects_non_exact_or_out_of_range_values(
    operation: str, now: object
) -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )

    if operation == "heartbeat":
        action = lambda: router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=now,  # type: ignore[arg-type]
        )
    elif operation == "delivery":
        action = lambda: router.deliver(
            runner, transport=transport(), now=now  # type: ignore[arg-type]
        )
    else:
        action = lambda: router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=now,  # type: ignore[arg-type]
        )

    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        action()
    assert router.terminal_acknowledgement(command().command_id) is None


def test_service_clock_accepts_signed_64_maximum_only_without_regression() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    maximum = 2**63 - 1

    router.record_heartbeat(
        runner,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(GENERATION),
        now=maximum,
    )
    assert router.deliver(runner, transport=transport(), now=maximum) == payload
    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        router.deliver(runner, transport=transport(), now=maximum - 1)


def test_monotonic_clock_survives_connection_replacement_and_recovers_with_newer_heartbeat() -> None:
    router, authority, credential, runner = connected_router()
    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=130)

    challenge = authority.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=50,
        expires_at=80,
    )
    proof = create_authentication_proof(credential, challenge, transport=transport())
    replacement = authority.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=55,
    ).runner
    router.attach(replacement, transport=transport())

    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        router.record_heartbeat(
            replacement,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(GENERATION),
            now=129,
        )
    assert router.record_heartbeat(
        replacement,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(GENERATION),
        now=131,
    ) == 2
    assert router.deliver(replacement, transport=transport(), now=131) is None


def test_monotonic_clock_survives_route_activation_and_recovers_with_newer_heartbeat() -> None:
    router, _, _, runner = connected_router()
    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=130)
    router.activate_route(TeamRoute(TEAM, NEXT_GENERATION))

    with pytest.raises(TeamRoutingError, match="current time is invalid"):
        router.record_heartbeat(
            runner,
            transport=transport(),
            heartbeat=ConnectionHeartbeat(NEXT_GENERATION),
            now=129,
        )
    router.record_heartbeat(
        runner,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(NEXT_GENERATION),
        now=131,
    )
    assert router.deliver(runner, transport=transport(), now=131) is None


def test_expired_heartbeat_rejects_ack_before_terminal_persistence() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )

    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=130,
        )
    assert router.terminal_acknowledgement(command().command_id) is None


def test_wrong_heartbeat_generation_and_wrong_protocol_fail_without_refresh() -> None:
    router, _, _, runner = connected_router()
    for heartbeat in (
        ConnectionHeartbeat(NEXT_GENERATION),
        ConnectionHeartbeat(GENERATION, protocol_version=2),
    ):
        with pytest.raises(TeamRoutingError, match="heartbeat is invalid"):
            router.record_heartbeat(
                runner,
                transport=transport(),
                heartbeat=heartbeat,
                now=120,
            )

    router.register_command(encode_command(command()))
    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=130)


def test_generation_retarget_invalidates_old_heartbeat_command_and_duplicate_replay() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    router.activate_route(TeamRoute(TEAM, NEXT_GENERATION))

    with pytest.raises(TeamRoutingError, match="heartbeat is stale"):
        router.deliver(runner, transport=transport(), now=102)
    with pytest.raises(TeamRoutingError, match="configuration generation is inactive"):
        router.register_command(payload)

    router.record_heartbeat(
        runner,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(NEXT_GENERATION),
        now=103,
    )
    with pytest.raises(TeamRoutingError, match="configuration generation is inactive"):
        router.deliver(runner, transport=transport(), now=104)


def test_historical_terminal_acknowledgement_is_rejected_by_active_generation_gate() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    )
    assert router.accept_acknowledgement(
        runner,
        transport=transport(),
        payload=acknowledgement,
        now=102,
    ) is AcknowledgementDecision.RECORDED

    router.activate_route(TeamRoute(TEAM, NEXT_GENERATION))
    router.record_heartbeat(
        runner,
        transport=transport(),
        heartbeat=ConnectionHeartbeat(NEXT_GENERATION),
        now=103,
    )

    with pytest.raises(TeamRoutingError, match="configuration generation is inactive"):
        router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=104,
        )
    assert router.terminal_acknowledgement(command().command_id) == acknowledgement


def test_wrong_protocol_command_and_ack_are_rejected_before_state_change() -> None:
    router, _, _, runner = connected_router()
    bad_command = encode_command(command()).replace(
        b'"protocol_version":1', b'"protocol_version":2'
    )
    with pytest.raises(TeamRoutingError, match="command payload is invalid"):
        router.register_command(bad_command)
    assert router.pending_command_count(TEAM) == 0

    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    acknowledgement = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(
        payload, now=101
    ).replace(b'"protocol_version":1', b'"protocol_version":2')
    with pytest.raises(TeamRoutingError, match="acknowledgement payload is invalid"):
        router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=acknowledgement,
            now=102,
        )
    assert router.terminal_acknowledgement(command().command_id) is None


def test_central_idempotency_epoch_order_and_one_in_flight_are_persistent() -> None:
    router, _, _, runner = connected_router()
    first = encode_command(command())
    second = encode_command(command(command_id="c" * 32, epoch=2))

    assert router.register_command(first) is RegistryDecision.NEW
    assert router.register_command(first) is RegistryDecision.DUPLICATE
    assert router.register_command(second) is RegistryDecision.NEW
    with pytest.raises(TeamRoutingError, match="epoch is not monotonic"):
        router.register_command(
            encode_command(command(command_id="d" * 32, epoch=2))
        )

    assert router.deliver(runner, transport=transport(), now=101) == first
    assert router.deliver(runner, transport=transport(), now=102) == first
    first_ack = RunnerCommandPersistence(TEAM, GENERATION).accept_delivery(first, now=101)
    router.accept_acknowledgement(
        runner,
        transport=transport(),
        payload=first_ack,
        now=103,
    )
    assert router.deliver(runner, transport=transport(), now=104) == second


def test_runner_persistence_replays_exact_ack_and_rejects_wrong_identity_without_replacement() -> None:
    store = RunnerCommandPersistence(TEAM, GENERATION)
    payload = encode_command(command())
    first = store.accept_delivery(payload, now=101)
    duplicate = store.accept_delivery(payload, now=999)

    assert duplicate == first
    assert store.applied_command_bytes() == payload

    wrong_generation = encode_command(
        command(command_id="c" * 32, epoch=2, generation=NEXT_GENERATION)
    )
    rejected = decode_acknowledgement(store.accept_delivery(wrong_generation, now=101))
    assert rejected.disposition is AcknowledgementDisposition.REJECTED
    assert store.applied_command_bytes() == payload


def test_ack_must_match_exact_in_flight_command_and_is_idempotent() -> None:
    router, _, _, runner = connected_router()
    payload = encode_command(command())
    router.register_command(payload)
    router.deliver(runner, transport=transport(), now=101)
    store = RunnerCommandPersistence(TEAM, GENERATION)
    acknowledgement = store.accept_delivery(payload, now=101)

    other_values = json.loads(acknowledgement)
    other_values["command_id"] = "c" * 32
    wrong_ack = (json.dumps(other_values, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    with pytest.raises(TeamRoutingError, match="acknowledgement does not match"):
        router.accept_acknowledgement(
            runner,
            transport=transport(),
            payload=wrong_ack,
            now=102,
        )

    assert router.accept_acknowledgement(
        runner,
        transport=transport(),
        payload=acknowledgement,
        now=102,
    ) is AcknowledgementDecision.RECORDED
    assert router.accept_acknowledgement(
        runner,
        transport=transport(),
        payload=acknowledgement,
        now=103,
    ) is AcknowledgementDecision.DUPLICATE


def test_public_failures_are_sanitized_and_expose_no_private_values() -> None:
    router, _, _, runner = connected_router()
    private_canary = "f" * 32
    malformed = CredentialId(private_canary)
    object.__setattr__(runner, "credential_id", malformed)

    with pytest.raises(TeamRoutingError) as raised:
        router.deliver(runner, transport=transport(), now=101)
    assert str(raised.value) == "authenticated connection is inactive"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert private_canary not in repr(raised.value)


def test_routing_surface_has_no_scheduler_lifecycle_gameplay_input_or_spending_authority() -> None:
    import clash_rush_rebuild.team_routing as routing

    forbidden = ("scheduler", "lifecycle", "gameplay", "input", "spend", "discordclient")
    public_names = tuple(name.lower() for name in routing.__dict__ if not name.startswith("_"))
    assert not any(fragment in name for name in public_names for fragment in forbidden)
