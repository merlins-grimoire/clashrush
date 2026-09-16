from __future__ import annotations

import dataclasses
import hashlib

import pytest

from clash_rush_rebuild.discord_control_protocol import TeamKey
from clash_rush_rebuild.runner_enrollment import (
    AuthAction,
    AuthenticationChallenge,
    AuthenticatedRunner,
    AuthenticationError,
    AuthenticationProof,
    AuthenticationResult,
    ChallengeId,
    CredentialId,
    EnrollmentAuthority,
    EnrollmentError,
    EnrollmentGrant,
    EnrollmentSecret,
    EnrollmentTokenId,
    IssuedRunnerCredential,
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


class BytesSubclass(bytes):
    pass


class StringSubclass(str):
    pass


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


def test_session_rejects_forged_and_post_issuance_mutated_runner_identity() -> None:
    service, credential = enrolled_authority()
    forged = AuthenticatedRunner(
        credential_id=CredentialId(credential.credential_id.value),
        team_key=TeamKey(credential.team_key.value),
        authenticated_at=120,
        channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
    )

    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(forged, transport=transport())

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
    object.__setattr__(runner, "authenticated_at", 119)

    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(runner, transport=transport())


def test_nested_bytes_subclasses_are_rejected_at_authentication_boundaries() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    object.__setattr__(credential.secret, "value", BytesSubclass(RUNNER_SECRET))

    with pytest.raises(AuthenticationError, match="proof is invalid"):
        create_authentication_proof(credential, challenge, transport=transport())

    object.__setattr__(credential.secret, "value", RUNNER_SECRET)
    proof = create_authentication_proof(
        credential,
        challenge,
        transport=transport(),
    )
    object.__setattr__(proof, "nonce", BytesSubclass(CHALLENGE_NONCE))

    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=210,
        )


def test_all_secret_channel_and_nonce_ingress_rejects_nested_bytes_subclasses() -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    object.__setattr__(grant.secret, "value", BytesSubclass(TOKEN_SECRET))
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=120)

    service, credential = enrolled_authority()
    connection = transport()
    object.__setattr__(connection, "channel_binding", BytesSubclass(CHANNEL_BINDING))
    with pytest.raises(AuthenticationError, match="could not begin"):
        service.begin_authentication(
            credential.credential_id,
            transport=connection,
            now=200,
            expires_at=230,
        )

    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    object.__setattr__(challenge, "channel_binding_hash", BytesSubclass(b"x" * 32))
    with pytest.raises(AuthenticationError, match="proof is invalid"):
        create_authentication_proof(credential, challenge, transport=transport())

    object.__setattr__(challenge, "channel_binding_hash", hashlib.sha256(CHANNEL_BINDING).digest())
    proof = create_authentication_proof(credential, challenge, transport=transport())
    object.__setattr__(proof.secret, "value", BytesSubclass(RUNNER_SECRET))
    with pytest.raises(AuthenticationError, match="authentication failed"):
        service.complete_authentication(
            challenge,
            proof,
            transport=transport(),
            now=210,
        )

    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(credential, challenge, transport=transport())
    runner = service.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=210,
    ).runner
    object.__setattr__(runner, "channel_binding_hash", BytesSubclass(runner.channel_binding_hash))
    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(runner, transport=transport())


def test_every_external_identity_ingress_revalidates_nested_exact_string_types() -> None:
    malformed_team = TeamKey("local-team")
    object.__setattr__(malformed_team, "value", StringSubclass("local-team"))
    with pytest.raises(EnrollmentError):
        authority().issue_enrollment(malformed_team, now=100, expires_at=160)

    service, credential = enrolled_authority()
    malformed_id = CredentialId(credential.credential_id.value)
    object.__setattr__(malformed_id, "value", StringSubclass(credential.credential_id.value))
    with pytest.raises(EnrollmentError, match="unknown credential"):
        service.credential_record(malformed_id)
    with pytest.raises(AuthenticationError, match="revocation failed"):
        service.revoke(malformed_id, now=200)
    with pytest.raises(AuthenticationError, match="could not begin"):
        service.begin_authentication(
            malformed_id,
            transport=transport(),
            now=200,
            expires_at=230,
        )

    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    object.__setattr__(challenge.team_key, "value", StringSubclass(TEAM.value))
    with pytest.raises(AuthenticationError, match="proof is invalid"):
        create_authentication_proof(credential, challenge, transport=transport())


def test_session_identity_rejects_replacement_alias_and_nested_mutation() -> None:
    service, credential = enrolled_authority()
    challenge = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    proof = create_authentication_proof(credential, challenge, transport=transport())
    runner = service.complete_authentication(
        challenge,
        proof,
        transport=transport(),
        now=210,
    ).runner

    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(dataclasses.replace(runner), transport=transport())

    object.__setattr__(runner.team_key, "value", "other-team")
    with pytest.raises(AuthenticationError, match="inactive"):
        service.validate_session(runner, transport=transport())


def test_revocation_is_idempotent_and_preserves_the_first_revocation_time() -> None:
    service, credential = enrolled_authority()
    service.revoke(credential.credential_id, now=212)
    service.revoke(credential.credential_id, now=220)
    assert service.credential_record(credential.credential_id).revoked_at == 212


def test_duplicate_challenge_generation_fails_without_replacing_pending_challenge() -> None:
    service, credential = enrolled_authority()
    service._random_challenge = lambda: (CHALLENGE_ID, CHALLENGE_NONCE)
    first = service.begin_authentication(
        credential.credential_id,
        transport=transport(),
        now=200,
        expires_at=230,
    )
    with pytest.raises(AuthenticationError, match="could not begin"):
        service.begin_authentication(
            credential.credential_id,
            transport=transport(),
            now=201,
            expires_at=231,
        )
    proof = create_authentication_proof(credential, first, transport=transport())
    assert service.complete_authentication(
        first,
        proof,
        transport=transport(),
        now=210,
    ).action is AuthAction.AUTHENTICATED


def test_credential_id_collision_burns_grant_without_replacing_existing_record() -> None:
    second_token_id = "d" * 32
    ids = iter((TOKEN_ID, CREDENTIAL_ID, second_token_id, CREDENTIAL_ID))
    secrets_source = iter(
        (TOKEN_SECRET, RUNNER_SECRET, OTHER_CHANNEL_BINDING, b"\x99" * 32)
    )
    service = EnrollmentAuthority(
        random_id=ids.__next__,
        random_secret=secrets_source.__next__,
        random_challenge=lambda: (CHALLENGE_ID, CHALLENGE_NONCE),
    )
    first = service.issue_enrollment(TEAM, now=100, expires_at=160)
    credential = service.enroll(first, transport=transport(), now=120)
    second = service.issue_enrollment(TEAM, now=200, expires_at=260)

    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(second, transport=transport(), now=220)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(second, transport=transport(), now=221)

    record = service.credential_record(credential.credential_id)
    assert record.issued_at == 120
    assert record.revoked_at is None


@pytest.mark.parametrize("now", [99, 160])
def test_enrollment_rejects_true_half_open_expiry_boundaries(now: int) -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.enroll(grant, transport=transport(), now=now)


@pytest.mark.parametrize("now", [100, 159])
def test_enrollment_accepts_true_half_open_validity_boundaries(now: int) -> None:
    service = authority()
    grant = service.issue_enrollment(TEAM, now=100, expires_at=160)
    assert service.enroll(grant, transport=transport(), now=now).team_key == TEAM


def test_public_error_drops_internal_credential_id_cause_and_context() -> None:
    service = authority()
    unknown_id = CredentialId("f" * 32)

    with pytest.raises(AuthenticationError) as raised:
        service.revoke(unknown_id, now=200)

    assert str(raised.value) == "credential revocation failed"
    assert unknown_id.value not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_public_failure_matrix_has_no_cause_context_or_opaque_identity() -> None:
    service, credential = enrolled_authority()
    unknown = CredentialId("f" * 32)
    malformed_credential = dataclasses.replace(credential, credential_id=unknown)
    failures = (
        lambda: service.credential_record(unknown),
        lambda: service.revoke(unknown, now=200),
        lambda: service.begin_authentication(
            unknown,
            transport=transport(),
            now=200,
            expires_at=230,
        ),
        lambda: create_authentication_proof(
            malformed_credential,
            object(),  # type: ignore[arg-type]
            transport=transport(),
        ),
        lambda: service.complete_authentication(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            transport=transport(),
            now=210,
        ),
        lambda: service.validate_session(
            AuthenticatedRunner(
                credential_id=unknown,
                team_key=TEAM,
                authenticated_at=200,
                channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
            ),
            transport=transport(),
        ),
    )

    for fail in failures:
        with pytest.raises((EnrollmentError, AuthenticationError)) as raised:
            fail()
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert unknown.value not in str(raised.value)
        assert unknown.value not in repr(raised.value)


def test_enrollment_failure_paths_drop_internal_cause_context_and_identity() -> None:
    opaque = "e" * 32

    def fail_id() -> str:
        raise KeyError(opaque)

    issue_service = EnrollmentAuthority(random_id=fail_id)
    with pytest.raises(EnrollmentError) as issue_error:
        issue_service.issue_enrollment(TEAM, now=100, expires_at=160)

    ids = iter((TOKEN_ID,))

    def token_then_fail() -> str:
        try:
            return next(ids)
        except StopIteration:
            raise KeyError(opaque) from None

    enroll_service = EnrollmentAuthority(
        random_id=token_then_fail,
        random_secret=iter((TOKEN_SECRET, RUNNER_SECRET)).__next__,
    )
    grant = enroll_service.issue_enrollment(TEAM, now=100, expires_at=160)
    with pytest.raises(EnrollmentError) as enroll_error:
        enroll_service.enroll(grant, transport=transport(), now=120)

    for raised in (issue_error, enroll_error):
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert opaque not in str(raised.value)
        assert opaque not in repr(raised.value)


def test_composite_values_revalidate_mutated_nested_wrappers_at_construction() -> None:
    token_id = EnrollmentTokenId(TOKEN_ID)
    object.__setattr__(token_id, "value", StringSubclass(TOKEN_ID))
    with pytest.raises(EnrollmentError):
        EnrollmentGrant(
            token_id=token_id,
            team_key=TEAM,
            secret=EnrollmentSecret(TOKEN_SECRET),
            issued_at=100,
            expires_at=160,
        )

    credential_id = CredentialId(CREDENTIAL_ID)
    object.__setattr__(credential_id, "value", StringSubclass(CREDENTIAL_ID))
    with pytest.raises(AuthenticationError):
        IssuedRunnerCredential(
            credential_id=credential_id,
            team_key=TEAM,
            secret=RunnerSecret(RUNNER_SECRET),
        )

    challenge_id = ChallengeId(CHALLENGE_ID)
    object.__setattr__(challenge_id, "value", StringSubclass(CHALLENGE_ID))
    with pytest.raises(AuthenticationError):
        AuthenticationChallenge(
            challenge_id=challenge_id,
            credential_id=CredentialId(CREDENTIAL_ID),
            team_key=TEAM,
            nonce=CHALLENGE_NONCE,
            channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
            issued_at=200,
            expires_at=230,
        )

    malformed_secret = RunnerSecret(RUNNER_SECRET)
    object.__setattr__(malformed_secret, "value", BytesSubclass(RUNNER_SECRET))
    with pytest.raises(AuthenticationError):
        AuthenticationProof(
            challenge_id=ChallengeId(CHALLENGE_ID),
            credential_id=CredentialId(CREDENTIAL_ID),
            nonce=CHALLENGE_NONCE,
            secret=malformed_secret,
        )

    malformed_team = TeamKey(TEAM.value)
    object.__setattr__(malformed_team, "value", StringSubclass(TEAM.value))
    with pytest.raises(AuthenticationError):
        AuthenticatedRunner(
            credential_id=CredentialId(CREDENTIAL_ID),
            team_key=malformed_team,
            authenticated_at=210,
            channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
        )

    runner = AuthenticatedRunner(
        credential_id=CredentialId(CREDENTIAL_ID),
        team_key=TEAM,
        authenticated_at=210,
        channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
    )
    object.__setattr__(runner, "authenticated_at", True)
    with pytest.raises(AuthenticationError):
        AuthenticationResult(AuthAction.AUTHENTICATED, runner)


def test_composite_construction_sanitizes_deleted_nested_fields() -> None:
    credential_id = CredentialId(CREDENTIAL_ID)
    object.__delattr__(credential_id, "value")
    with pytest.raises(AuthenticationError) as credential_error:
        IssuedRunnerCredential(
            credential_id=credential_id,
            team_key=TEAM,
            secret=RunnerSecret(RUNNER_SECRET),
        )

    runner = AuthenticatedRunner(
        credential_id=CredentialId(CREDENTIAL_ID),
        team_key=TEAM,
        authenticated_at=210,
        channel_binding_hash=hashlib.sha256(CHANNEL_BINDING).digest(),
    )
    object.__delattr__(runner, "team_key")
    with pytest.raises(AuthenticationError) as result_error:
        AuthenticationResult(AuthAction.AUTHENTICATED, runner)

    for raised in (credential_error, result_error):
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


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
