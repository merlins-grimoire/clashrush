from __future__ import annotations

import dataclasses

import pytest

from clash_rush_rebuild.discord_control_protocol import TeamKey
from clash_rush_rebuild.runner_enrollment import (
    AuthAction,
    AuthenticatedRunner,
    AuthenticationError,
    CredentialId,
    EnrollmentAuthority,
    EnrollmentError,
    EnrollmentSecret,
    RunnerSecret,
    TransportBinding,
    create_authentication_proof,
)


TEAM = TeamKey("synthetic-team")
OTHER_TEAM = TeamKey("other-team")
TOKEN_ID = "1" * 32
TOKEN_SECRET = bytes.fromhex("2" * 64)
CREDENTIAL_ID = "3" * 32
RUNNER_SECRET = bytes.fromhex("4" * 64)
CHALLENGE_ID = "5" * 32
CHALLENGE_NONCE = bytes.fromhex("6" * 64)
NEXT_CHALLENGE_ID = "a" * 32
NEXT_CHALLENGE_NONCE = bytes.fromhex("b" * 64)
CHANNEL_BINDING = bytes.fromhex("7" * 64)
OTHER_CHANNEL_BINDING = bytes.fromhex("8" * 64)


def transport(binding: bytes = CHANNEL_BINDING) -> TransportBinding:
    return TransportBinding(
        outbound_from_runner=True,
        server_authenticated=True,
        confidential=True,
        channel_binding=binding,
    )


def authority() -> EnrollmentAuthority:
    return EnrollmentAuthority(
        random_id=lambda: TOKEN_ID,
        random_secret=lambda: TOKEN_SECRET,
        random_challenge=lambda: (CHALLENGE_ID, CHALLENGE_NONCE),
    )


def enrolled_authority() -> tuple[EnrollmentAuthority, object]:
    challenges = iter(
        (
            (CHALLENGE_ID, CHALLENGE_NONCE),
            (NEXT_CHALLENGE_ID, NEXT_CHALLENGE_NONCE),
        )
    )
    service = EnrollmentAuthority(
        random_id=iter((TOKEN_ID, CREDENTIAL_ID)).__next__,
        random_secret=iter((TOKEN_SECRET, RUNNER_SECRET)).__next__,
        random_challenge=challenges.__next__,
    )
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    credential = service.enroll(grant, transport=transport(), now=120)
    return service, credential


def test_private_one_time_enrollment_exchanges_token_for_runner_only_credential() -> None:
    service, credential = enrolled_authority()

    assert credential.team_key == TEAM
    assert credential.credential_id.value == CREDENTIAL_ID
    assert credential.secret.value == RUNNER_SECRET
    assert "RunnerSecret" not in repr(credential)
    assert service.credential_record(CredentialId(CREDENTIAL_ID)).team_key == TEAM
    assert not hasattr(service.credential_record(CredentialId(CREDENTIAL_ID)), "secret")


def test_enrollment_token_is_consumed_exactly_once() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    service.enroll(grant, transport=transport(), now=120)

    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=121)


@pytest.mark.parametrize(
    "connection",
    [
        TransportBinding(False, True, True, CHANNEL_BINDING),
        TransportBinding(True, False, True, CHANNEL_BINDING),
        TransportBinding(True, True, False, CHANNEL_BINDING),
    ],
)
def test_enrollment_requires_confidential_server_authenticated_runner_outbound_transport(
    connection: TransportBinding,
) -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)

    with pytest.raises(EnrollmentError, match="authenticated outbound transport"):
        service.enroll(grant, transport=connection, now=120)


def test_failed_transport_does_not_consume_enrollment_token() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)

    with pytest.raises(EnrollmentError):
        service.enroll(
            grant,
            transport=TransportBinding(True, False, True, CHANNEL_BINDING),
            now=120,
        )
    credential = service.enroll(grant, transport=transport(), now=121)
    assert credential.team_key == TEAM


def test_expired_or_wrong_enrollment_secret_never_issues_a_credential() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    wrong = dataclasses.replace(grant, secret=EnrollmentSecret(bytes.fromhex("9" * 64)))

    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(wrong, transport=transport(), now=120)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=160)
    with pytest.raises(EnrollmentError, match="unknown credential"):
        service.credential_record(CredentialId(CREDENTIAL_ID))


def test_bad_enrollment_attempt_burns_token_to_prevent_online_guessing() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    wrong = dataclasses.replace(grant, secret=EnrollmentSecret(bytes.fromhex("9" * 64)))

    with pytest.raises(EnrollmentError):
        service.enroll(wrong, transport=transport(), now=120)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=121)


def test_malformed_enrollment_secret_attempt_still_burns_token() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    object.__setattr__(grant.secret, "value", object())

    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=120)
    object.__setattr__(grant.secret, "value", TOKEN_SECRET)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=121)


def test_runner_proves_credential_on_the_exact_outbound_channel() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )
    assert "RunnerSecret" not in repr(proof)

    result = service.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=210,
    )

    assert result.action is AuthAction.AUTHENTICATED
    assert result.runner == AuthenticatedRunner(
        credential_id=credential.credential_id,
        team_key=TEAM,
        authenticated_at=210,
        channel_binding_hash=result.runner.channel_binding_hash,
    )
    assert len(result.runner.channel_binding_hash) == 32


def test_authentication_proof_fails_on_another_channel_and_challenge_is_one_time() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )

    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(OTHER_CHANNEL_BINDING),
            now=210,
        )
    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=211,
        )


def test_authentication_proof_binds_the_exact_challenge_nonce() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    object.__setattr__(challenge, "nonce", OTHER_CHANNEL_BINDING)
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )

    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            dataclasses.replace(challenge, nonce=CHALLENGE_NONCE),
            proof,
            transport=transport(),
            now=210,
        )


def test_outbound_challenge_does_not_alias_the_central_pending_record() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )
    pristine = dataclasses.replace(
        challenge, team_key=TeamKey(challenge.team_key.value)
    )
    object.__setattr__(challenge.team_key, "value", "other-team")

    result = service.complete_authentication(
        pristine,
        proof,
        transport=transport(),
        now=210,
    )
    assert result.runner.team_key == TEAM


def test_wrong_runner_secret_fails_without_leaking_secret_context() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    wrong_credential = dataclasses.replace(
        credential, secret=RunnerSecret(bytes.fromhex("9" * 64))
    )
    proof = create_authentication_proof(
        wrong_credential,
        challenge,
        transport=transport(),
    )

    with pytest.raises(AuthenticationError) as raised:
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=210,
        )
    assert str(raised.value) == "runner authentication failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_expired_challenge_fails_and_cannot_be_replayed() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )

    with pytest.raises(AuthenticationError):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=230,
        )
    with pytest.raises(AuthenticationError):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=220,
        )


def test_revocation_blocks_pending_new_and_existing_authenticated_sessions() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )
    runner = service.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=210,
    ).runner
    next_challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=211,
        expires_at=230,
    )
    next_proof = create_authentication_proof(
        credential,
        next_challenge,
        transport=transport(),
    )

    service.revoke(credential.credential_id, now=212)

    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(runner, transport=transport())
    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            next_challenge,
            next_proof,
            transport=transport(),
            now=213,
        )
    with pytest.raises(AuthenticationError, match="inactive"):
        service.begin_authentication(
            credential.credential_id,
            transport=transport(),
            now=213,
            expires_at=230,
        )


def test_authentication_cannot_predate_credential_issuance() -> None:
    service, credential = enrolled_authority()

    with pytest.raises(AuthenticationError, match="inactive"):
        service.begin_authentication(
            credential.credential_id,
            transport=transport(),
            now=119,
            expires_at=120,
        )


def test_session_is_bound_to_exact_team_credential_and_channel() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )
    runner = service.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=210,
    ).runner

    assert service.validate_session(runner, transport=transport()) == TEAM
    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(runner, transport=transport(OTHER_CHANNEL_BINDING))
    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(
            dataclasses.replace(runner, team_key=OTHER_TEAM),
            transport=transport(),
        )


@pytest.mark.parametrize("value", [b"", b"x" * 31, b"x" * 33, "x" * 32, True])
def test_secret_and_channel_binding_values_are_exact_256_bit_bytes(value: object) -> None:
    with pytest.raises((EnrollmentError, AuthenticationError)):
        EnrollmentSecret(value)  # type: ignore[arg-type]
    with pytest.raises((EnrollmentError, AuthenticationError)):
        RunnerSecret(value)  # type: ignore[arg-type]
    with pytest.raises((EnrollmentError, AuthenticationError)):
        TransportBinding(True, True, True, value)  # type: ignore[arg-type]


def test_transport_evidence_is_revalidated_after_construction() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    connection = transport()
    object.__setattr__(connection, "server_authenticated", 1)

    with pytest.raises(EnrollmentError, match="authenticated outbound transport"):
        service.enroll(grant, transport=connection, now=120)


def test_runner_installation_surface_contains_no_discord_bot_token() -> None:
    import clash_rush_rebuild.runner_enrollment as enrollment

    public_names = tuple(name.lower() for name in enrollment.__dict__ if not name.startswith("_"))
    runner_fields = tuple(
        field.name.lower()
        for value in enrollment.__dict__.values()
        if isinstance(value, type) and dataclasses.is_dataclass(value)
        for field in dataclasses.fields(value)
    )

    assert not any("discord" in name or "bot_token" in name for name in public_names)
    assert not any("discord" in name or "bot_token" in name for name in runner_fields)


def test_public_boundaries_reject_corrupted_values_without_private_exception_context() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    object.__setattr__(credential.secret, "value", object())

    with pytest.raises(AuthenticationError) as raised:
        create_authentication_proof(credential, challenge, transport=transport())
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
