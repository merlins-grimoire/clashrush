"""Private runner enrollment and channel-bound credential authentication.

This module is transport-agnostic.  A caller must provide evidence from a
server-authenticated, confidential connection opened outbound by the runner.
It owns no Discord credential, socket, scheduler, lifecycle, or gameplay
capability.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, TypeVar

from .discord_control_protocol import TeamKey


_MAX_SIGNED_64 = 2**63 - 1
_MAX_ENROLLMENT_LIFETIME_SECONDS = 600
_MAX_CHALLENGE_LIFETIME_SECONDS = 60
_OPAQUE_ID = re.compile(r"[0-9a-f]{32}")
_SECRET_BYTES = 32
_DOMAIN_ENROLLMENT = b"clash-rush/enrollment-verifier/v1\x00"
_DOMAIN_CREDENTIAL = b"clash-rush/runner-credential-verifier/v1\x00"


class EnrollmentError(ValueError):
    """Enrollment was malformed, unavailable, or unsafe."""


class AuthenticationError(ValueError):
    """Runner authentication failed closed."""


_Result = TypeVar("_Result")


def _sanitized(operation: Callable[[], _Result], message: str, error: type[ValueError]) -> _Result:
    try:
        return operation()
    except Exception:
        pass
    raise error(message)


def _valid_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SIGNED_64


def _secret_bytes(value: object, *, label: str, error: type[ValueError]) -> None:
    if type(value) is not bytes or len(value) != _SECRET_BYTES:
        raise error(f"{label} must be exactly 32 bytes")


def _opaque_id(value: object, *, label: str, error: type[ValueError]) -> None:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        raise error(f"{label} must be exactly 32 lowercase hex characters")


def _valid_id_wrapper(value: object, expected_type: type[object]) -> bool:
    if type(value) is not expected_type:
        return False
    try:
        raw_value = value.value
    except Exception:
        return False
    return type(raw_value) is str and _OPAQUE_ID.fullmatch(raw_value) is not None


def _valid_team_wrapper(value: object) -> bool:
    if type(value) is not TeamKey:
        return False
    try:
        raw_value = value.value
        if type(raw_value) is not str:
            return False
        TeamKey(raw_value)
    except Exception:
        return False
    return True


@dataclass(frozen=True, slots=True)
class EnrollmentTokenId:
    value: str

    def __post_init__(self) -> None:
        _opaque_id(self.value, label="enrollment token ID", error=EnrollmentError)


@dataclass(frozen=True, slots=True)
class CredentialId:
    value: str

    def __post_init__(self) -> None:
        _opaque_id(self.value, label="credential ID", error=AuthenticationError)


@dataclass(frozen=True, slots=True)
class ChallengeId:
    value: str

    def __post_init__(self) -> None:
        _opaque_id(self.value, label="challenge ID", error=AuthenticationError)


@dataclass(frozen=True, slots=True)
class EnrollmentSecret:
    value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _secret_bytes(
            self.value, label="enrollment secret", error=EnrollmentError
        )


@dataclass(frozen=True, slots=True)
class RunnerSecret:
    value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _secret_bytes(self.value, label="runner secret", error=AuthenticationError)


@dataclass(frozen=True, slots=True)
class TransportBinding:
    """Evidence supplied by the concrete TLS/WebSocket transport adapter."""

    outbound_from_runner: bool
    server_authenticated: bool
    confidential: bool
    channel_binding: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.outbound_from_runner) is not bool
            or type(self.server_authenticated) is not bool
            or type(self.confidential) is not bool
        ):
            raise AuthenticationError("transport flags must be exact booleans")
        _secret_bytes(
            self.channel_binding,
            label="channel binding",
            error=AuthenticationError,
        )


@dataclass(frozen=True, slots=True)
class EnrollmentGrant:
    token_id: EnrollmentTokenId
    team_key: TeamKey
    secret: EnrollmentSecret = field(repr=False)
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.token_id, EnrollmentTokenId):
            raise EnrollmentError("enrollment token ID is invalid")
        if not _valid_team_wrapper(self.team_key):
            raise EnrollmentError("enrollment Team is invalid")
        if type(self.secret) is not EnrollmentSecret:
            raise EnrollmentError("enrollment secret is invalid")
        _secret_bytes(
            self.secret.value,
            label="enrollment secret",
            error=EnrollmentError,
        )
        if not _valid_time(self.issued_at) or not _valid_time(self.expires_at):
            raise EnrollmentError("enrollment validity is invalid")
        if not self.issued_at < self.expires_at:
            raise EnrollmentError("enrollment expiry must follow issue time")
        if self.expires_at - self.issued_at > _MAX_ENROLLMENT_LIFETIME_SECONDS:
            raise EnrollmentError("enrollment lifetime exceeds ten minutes")


@dataclass(frozen=True, slots=True)
class IssuedRunnerCredential:
    """The runner-only result returned once after enrollment."""

    credential_id: CredentialId
    team_key: TeamKey
    secret: RunnerSecret = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.credential_id, CredentialId):
            raise AuthenticationError("runner credential ID is invalid")
        if not _valid_team_wrapper(self.team_key):
            raise AuthenticationError("runner credential Team is invalid")
        if type(self.secret) is not RunnerSecret:
            raise AuthenticationError("runner credential secret is invalid")
        _secret_bytes(
            self.secret.value,
            label="runner secret",
            error=AuthenticationError,
        )


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """Central record: a verifier only, never the runner secret."""

    credential_id: CredentialId
    team_key: TeamKey
    verifier: bytes = field(repr=False)
    issued_at: int
    revoked_at: int | None = None

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.credential_id, CredentialId):
            raise AuthenticationError("credential record ID is invalid")
        if not _valid_team_wrapper(self.team_key):
            raise AuthenticationError("credential record Team is invalid")
        _secret_bytes(self.verifier, label="credential verifier", error=AuthenticationError)
        if not _valid_time(self.issued_at):
            raise AuthenticationError("credential issue time is invalid")
        if self.revoked_at is not None:
            if not _valid_time(self.revoked_at) or self.revoked_at < self.issued_at:
                raise AuthenticationError("credential revocation time is invalid")


@dataclass(frozen=True, slots=True)
class AuthenticationChallenge:
    challenge_id: ChallengeId
    credential_id: CredentialId
    team_key: TeamKey
    nonce: bytes = field(repr=False)
    channel_binding_hash: bytes = field(repr=False)
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.challenge_id, ChallengeId):
            raise AuthenticationError("challenge ID is invalid")
        if not _valid_id_wrapper(self.credential_id, CredentialId):
            raise AuthenticationError("challenge credential is invalid")
        if not _valid_team_wrapper(self.team_key):
            raise AuthenticationError("challenge Team is invalid")
        _secret_bytes(self.nonce, label="challenge nonce", error=AuthenticationError)
        _secret_bytes(
            self.channel_binding_hash,
            label="channel binding hash",
            error=AuthenticationError,
        )
        if not _valid_time(self.issued_at) or not _valid_time(self.expires_at):
            raise AuthenticationError("challenge validity is invalid")
        if not self.issued_at < self.expires_at:
            raise AuthenticationError("challenge expiry must follow issue time")
        if self.expires_at - self.issued_at > _MAX_CHALLENGE_LIFETIME_SECONDS:
            raise AuthenticationError("challenge lifetime exceeds one minute")


@dataclass(frozen=True, slots=True)
class AuthenticationProof:
    challenge_id: ChallengeId
    credential_id: CredentialId
    nonce: bytes = field(repr=False)
    secret: RunnerSecret = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.challenge_id, ChallengeId):
            raise AuthenticationError("proof challenge ID is invalid")
        if not _valid_id_wrapper(self.credential_id, CredentialId):
            raise AuthenticationError("proof credential ID is invalid")
        _secret_bytes(self.nonce, label="proof nonce", error=AuthenticationError)
        if type(self.secret) is not RunnerSecret:
            raise AuthenticationError("authentication proof secret is invalid")
        _secret_bytes(
            self.secret.value,
            label="runner secret",
            error=AuthenticationError,
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedRunner:
    credential_id: CredentialId
    team_key: TeamKey
    authenticated_at: int
    channel_binding_hash: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not _valid_id_wrapper(self.credential_id, CredentialId):
            raise AuthenticationError("authenticated credential is invalid")
        if not _valid_team_wrapper(self.team_key):
            raise AuthenticationError("authenticated Team is invalid")
        if not _valid_time(self.authenticated_at):
            raise AuthenticationError("authentication time is invalid")
        _secret_bytes(
            self.channel_binding_hash,
            label="authenticated channel binding hash",
            error=AuthenticationError,
        )


class AuthAction(Enum):
    AUTHENTICATED = "AUTHENTICATED"


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    action: AuthAction
    runner: AuthenticatedRunner

    def __post_init__(self) -> None:
        if type(self.action) is not AuthAction or self.action is not AuthAction.AUTHENTICATED:
            raise AuthenticationError("authentication result action is invalid")
        if type(self.runner) is not AuthenticatedRunner:
            raise AuthenticationError("authentication result runner is invalid")
        runner_valid = True
        try:
            AuthenticatedRunner(
                credential_id=self.runner.credential_id,
                team_key=self.runner.team_key,
                authenticated_at=self.runner.authenticated_at,
                channel_binding_hash=self.runner.channel_binding_hash,
            )
        except Exception:
            runner_valid = False
        if not runner_valid:
            raise AuthenticationError("authentication result runner is invalid")


def _copy_team_key(value: object, *, error: type[ValueError]) -> TeamKey:
    if type(value) is not TeamKey or type(value.value) is not str:
        raise error("Team identity is invalid")
    try:
        return TeamKey(value.value)
    except Exception:
        raise error("Team identity is invalid") from None


def _copy_enrollment_token_id(value: object) -> EnrollmentTokenId:
    if type(value) is not EnrollmentTokenId:
        raise EnrollmentError("enrollment token ID is invalid")
    return EnrollmentTokenId(value.value)


def _copy_credential_id(value: object) -> CredentialId:
    if type(value) is not CredentialId:
        raise AuthenticationError("credential ID is invalid")
    return CredentialId(value.value)


def _copy_challenge_id(value: object) -> ChallengeId:
    if type(value) is not ChallengeId:
        raise AuthenticationError("challenge ID is invalid")
    return ChallengeId(value.value)


def _copy_enrollment_grant(value: object) -> EnrollmentGrant:
    if type(value) is not EnrollmentGrant:
        raise EnrollmentError("enrollment grant is invalid")
    token_id = _copy_enrollment_token_id(value.token_id)
    team_key = _copy_team_key(value.team_key, error=EnrollmentError)
    if type(value.secret) is not EnrollmentSecret:
        raise EnrollmentError("enrollment secret is invalid")
    _secret_bytes(value.secret.value, label="enrollment secret", error=EnrollmentError)
    return EnrollmentGrant(
        token_id=token_id,
        team_key=team_key,
        secret=EnrollmentSecret(bytes(value.secret.value)),
        issued_at=value.issued_at,
        expires_at=value.expires_at,
    )


def _copy_issued_credential(value: object) -> IssuedRunnerCredential:
    if type(value) is not IssuedRunnerCredential:
        raise AuthenticationError("runner credential is invalid")
    credential_id = _copy_credential_id(value.credential_id)
    team_key = _copy_team_key(value.team_key, error=AuthenticationError)
    if type(value.secret) is not RunnerSecret:
        raise AuthenticationError("runner credential secret is invalid")
    _secret_bytes(value.secret.value, label="runner secret", error=AuthenticationError)
    return IssuedRunnerCredential(
        credential_id=credential_id,
        team_key=team_key,
        secret=RunnerSecret(bytes(value.secret.value)),
    )


def _copy_authentication_challenge(value: object) -> AuthenticationChallenge:
    if type(value) is not AuthenticationChallenge:
        raise AuthenticationError("authentication challenge is invalid")
    _secret_bytes(value.nonce, label="challenge nonce", error=AuthenticationError)
    _secret_bytes(
        value.channel_binding_hash,
        label="channel binding hash",
        error=AuthenticationError,
    )
    return AuthenticationChallenge(
        challenge_id=_copy_challenge_id(value.challenge_id),
        credential_id=_copy_credential_id(value.credential_id),
        team_key=_copy_team_key(value.team_key, error=AuthenticationError),
        nonce=bytes(value.nonce),
        channel_binding_hash=bytes(value.channel_binding_hash),
        issued_at=value.issued_at,
        expires_at=value.expires_at,
    )


def _copy_authentication_proof(value: object) -> AuthenticationProof:
    if type(value) is not AuthenticationProof:
        raise AuthenticationError("authentication proof is invalid")
    _secret_bytes(value.nonce, label="proof nonce", error=AuthenticationError)
    if type(value.secret) is not RunnerSecret:
        raise AuthenticationError("authentication proof secret is invalid")
    _secret_bytes(value.secret.value, label="runner secret", error=AuthenticationError)
    return AuthenticationProof(
        challenge_id=_copy_challenge_id(value.challenge_id),
        credential_id=_copy_credential_id(value.credential_id),
        nonce=bytes(value.nonce),
        secret=RunnerSecret(bytes(value.secret.value)),
    )


def _copy_authenticated_runner(value: object) -> AuthenticatedRunner:
    if type(value) is not AuthenticatedRunner:
        raise AuthenticationError("authenticated runner is invalid")
    _secret_bytes(
        value.channel_binding_hash,
        label="authenticated channel binding hash",
        error=AuthenticationError,
    )
    return AuthenticatedRunner(
        credential_id=_copy_credential_id(value.credential_id),
        team_key=_copy_team_key(value.team_key, error=AuthenticationError),
        authenticated_at=value.authenticated_at,
        channel_binding_hash=bytes(value.channel_binding_hash),
    )


@dataclass(frozen=True, slots=True)
class _EnrollmentRecord:
    token_id: EnrollmentTokenId
    team_key: TeamKey
    verifier: bytes = field(repr=False)
    issued_at: int
    expires_at: int
    consumed: bool


@dataclass(frozen=True, slots=True)
class _ChallengeRecord:
    challenge: AuthenticationChallenge
    consumed: bool


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    runner: AuthenticatedRunner
    snapshot: AuthenticatedRunner


def _transport_hash(value: TransportBinding) -> bytes:
    if type(value) is not TransportBinding:
        raise AuthenticationError("authenticated outbound transport is required")
    if (
        type(value.outbound_from_runner) is not bool
        or type(value.server_authenticated) is not bool
        or type(value.confidential) is not bool
        or not value.outbound_from_runner
        or not value.server_authenticated
        or not value.confidential
    ):
        raise AuthenticationError("authenticated outbound transport is required")
    _secret_bytes(
        value.channel_binding,
        label="channel binding",
        error=AuthenticationError,
    )
    return hashlib.sha256(value.channel_binding).digest()


def _verifier(domain: bytes, identifier: str, secret: bytes) -> bytes:
    return hashlib.sha256(domain + identifier.encode("ascii") + b"\x00" + secret).digest()


def _copy_challenge(challenge: AuthenticationChallenge) -> AuthenticationChallenge:
    return AuthenticationChallenge(
        challenge_id=ChallengeId(challenge.challenge_id.value),
        credential_id=CredentialId(challenge.credential_id.value),
        team_key=TeamKey(challenge.team_key.value),
        nonce=bytes(challenge.nonce),
        channel_binding_hash=bytes(challenge.channel_binding_hash),
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
    )


def create_authentication_proof(
    credential: IssuedRunnerCredential,
    challenge: AuthenticationChallenge,
    *,
    transport: TransportBinding,
) -> AuthenticationProof:
    """Create a proof locally without exposing the runner secret."""

    def create() -> AuthenticationProof:
        credential_snapshot = _copy_issued_credential(credential)
        challenge_snapshot = _copy_authentication_challenge(challenge)
        if credential_snapshot.credential_id != challenge_snapshot.credential_id:
            raise ValueError
        if credential_snapshot.team_key != challenge_snapshot.team_key:
            raise ValueError
        if _transport_hash(transport) != challenge_snapshot.channel_binding_hash:
            raise ValueError
        return AuthenticationProof(
            challenge_id=ChallengeId(challenge_snapshot.challenge_id.value),
            credential_id=CredentialId(challenge_snapshot.credential_id.value),
            nonce=bytes(challenge_snapshot.nonce),
            secret=RunnerSecret(bytes(credential_snapshot.secret.value)),
        )

    return _sanitized(create, "runner authentication proof is invalid", AuthenticationError)


class EnrollmentAuthority:
    """In-memory authority core for a durable central adapter.

    Persistence and network I/O are intentionally outside this object.  The
    concrete service must commit equivalent token, credential, revocation, and
    challenge-consumption records transactionally before returning success.
    """

    def __init__(
        self,
        *,
        random_id: Callable[[], str] = lambda: secrets.token_hex(16),
        random_secret: Callable[[], bytes] = lambda: secrets.token_bytes(32),
        random_challenge: Callable[[], tuple[str, bytes]] = lambda: (
            secrets.token_hex(16),
            secrets.token_bytes(32),
        ),
    ) -> None:
        if not callable(random_id) or not callable(random_secret) or not callable(random_challenge):
            raise EnrollmentError("random sources must be callable")
        self._random_id = random_id
        self._random_secret = random_secret
        self._random_challenge = random_challenge
        self._enrollments: dict[str, _EnrollmentRecord] = {}
        self._credentials: dict[str, CredentialRecord] = {}
        self._challenges: dict[str, _ChallengeRecord] = {}
        self._sessions: dict[int, _SessionRecord] = {}

    def issue_enrollment(
        self, team_key: TeamKey, *, now: int, expires_at: int
    ) -> EnrollmentGrant:
        def issue() -> EnrollmentGrant:
            team_snapshot = _copy_team_key(team_key, error=EnrollmentError)
            token_id = EnrollmentTokenId(self._random_id())
            secret = EnrollmentSecret(self._random_secret())
            grant = EnrollmentGrant(token_id, team_snapshot, secret, now, expires_at)
            if token_id.value in self._enrollments:
                raise ValueError
            self._enrollments[token_id.value] = _EnrollmentRecord(
                token_id=EnrollmentTokenId(token_id.value),
                team_key=TeamKey(team_key.value),
                verifier=_verifier(
                    _DOMAIN_ENROLLMENT, token_id.value, secret.value
                ),
                issued_at=now,
                expires_at=expires_at,
                consumed=False,
            )
            return grant

        return _sanitized(issue, "enrollment grant could not be issued", EnrollmentError)

    def enroll(
        self,
        grant: EnrollmentGrant,
        *,
        transport: TransportBinding,
        now: int,
    ) -> IssuedRunnerCredential:
        transport_error = False
        try:
            _transport_hash(transport)
            if type(grant) is not EnrollmentGrant:
                raise ValueError
            token_id = _copy_enrollment_token_id(grant.token_id)
            record = self._enrollments.get(token_id.value)
            if record is None or record.consumed or not record.issued_at <= now < record.expires_at:
                raise ValueError
            # Burn any attempted use after transport validation, including a bad secret.
            self._enrollments[record.token_id.value] = replace(record, consumed=True)
            grant_snapshot = _copy_enrollment_grant(grant)
            if (
                not _valid_time(now)
                or grant_snapshot.token_id != record.token_id
                or grant_snapshot.team_key != record.team_key
                or grant_snapshot.issued_at != record.issued_at
                or grant_snapshot.expires_at != record.expires_at
            ):
                raise ValueError
            supplied = _verifier(
                _DOMAIN_ENROLLMENT,
                grant_snapshot.token_id.value,
                grant_snapshot.secret.value,
            )
            if not hmac.compare_digest(record.verifier, supplied):
                raise ValueError
            credential_id = CredentialId(self._random_id())
            secret = RunnerSecret(self._random_secret())
            if credential_id.value in self._credentials:
                raise ValueError
            central_record = CredentialRecord(
                credential_id=CredentialId(credential_id.value),
                team_key=TeamKey(record.team_key.value),
                verifier=_verifier(
                    _DOMAIN_CREDENTIAL, credential_id.value, secret.value
                ),
                issued_at=now,
            )
            self._credentials[credential_id.value] = central_record
            return IssuedRunnerCredential(
                credential_id=credential_id,
                team_key=TeamKey(record.team_key.value),
                secret=secret,
            )
        except AuthenticationError:
            transport_error = True
        except Exception:
            pass
        if transport_error:
            raise EnrollmentError("authenticated outbound transport is required")
        raise EnrollmentError("enrollment token is unavailable")

    def credential_record(self, credential_id: CredentialId) -> CredentialRecord:
        try:
            credential_snapshot = _copy_credential_id(credential_id)
            record = self._credentials[credential_snapshot.value]
            return CredentialRecord(
                credential_id=CredentialId(record.credential_id.value),
                team_key=TeamKey(record.team_key.value),
                verifier=bytes(record.verifier),
                issued_at=record.issued_at,
                revoked_at=record.revoked_at,
            )
        except Exception:
            pass
        raise EnrollmentError("unknown credential")

    def revoke(self, credential_id: CredentialId, *, now: int) -> None:
        failed = False
        try:
            credential_snapshot = _copy_credential_id(credential_id)
            if not _valid_time(now):
                raise ValueError
            record = self._credentials[credential_snapshot.value]
            if now < record.issued_at:
                raise ValueError
            if record.revoked_at is None:
                self._credentials[credential_snapshot.value] = replace(
                    record, revoked_at=now
                )
        except Exception:
            failed = True
        if failed:
            raise AuthenticationError("credential revocation failed")

    def begin_authentication(
        self,
        credential_id: CredentialId,
        *,
        transport: TransportBinding,
        now: int,
        expires_at: int,
    ) -> AuthenticationChallenge:
        inactive = False
        try:
            binding_hash = _transport_hash(transport)
            credential_snapshot = _copy_credential_id(credential_id)
            record = self._credentials[credential_snapshot.value]
            if record.revoked_at is not None or not _valid_time(now) or now < record.issued_at:
                raise AuthenticationError("runner credential is inactive")
            raw_id, raw_nonce = self._random_challenge()
            challenge = AuthenticationChallenge(
                challenge_id=ChallengeId(raw_id),
                credential_id=CredentialId(record.credential_id.value),
                team_key=TeamKey(record.team_key.value),
                nonce=raw_nonce,
                channel_binding_hash=binding_hash,
                issued_at=now,
                expires_at=expires_at,
            )
            if challenge.challenge_id.value in self._challenges:
                raise ValueError
            self._challenges[challenge.challenge_id.value] = _ChallengeRecord(
                challenge=_copy_challenge(challenge),
                consumed=False,
            )
            return challenge
        except AuthenticationError as exc:
            inactive = str(exc) == "runner credential is inactive"
        except Exception:
            pass
        if inactive:
            raise AuthenticationError("runner credential is inactive")
        raise AuthenticationError("runner authentication could not begin")

    def complete_authentication(
        self,
        challenge: AuthenticationChallenge,
        proof: AuthenticationProof,
        *,
        transport: TransportBinding,
        now: int,
    ) -> AuthenticationResult:
        try:
            if type(challenge) is not AuthenticationChallenge:
                raise ValueError
            challenge_id = _copy_challenge_id(challenge.challenge_id)
            stored = self._challenges.get(challenge_id.value)
            if stored is None or stored.consumed:
                raise ValueError
            # A challenge is single-attempt, even if the proof or channel is wrong.
            self._challenges[challenge.challenge_id.value] = _ChallengeRecord(
                challenge=stored.challenge,
                consumed=True,
            )
            challenge_snapshot = _copy_authentication_challenge(challenge)
            proof_snapshot = _copy_authentication_proof(proof)
            if challenge_snapshot != stored.challenge:
                raise ValueError
            if (
                not _valid_time(now)
                or not challenge_snapshot.issued_at <= now < challenge_snapshot.expires_at
            ):
                raise ValueError
            binding_hash = _transport_hash(transport)
            if not hmac.compare_digest(
                binding_hash, challenge_snapshot.channel_binding_hash
            ):
                raise ValueError
            if (
                proof_snapshot.challenge_id != challenge_snapshot.challenge_id
                or proof_snapshot.credential_id != challenge_snapshot.credential_id
                or not hmac.compare_digest(
                    proof_snapshot.nonce, challenge_snapshot.nonce
                )
            ):
                raise ValueError
            record = self._credentials[challenge_snapshot.credential_id.value]
            if (
                record.revoked_at is not None
                or record.team_key != challenge_snapshot.team_key
                or now < record.issued_at
            ):
                raise ValueError
            supplied_verifier = _verifier(
                _DOMAIN_CREDENTIAL,
                proof_snapshot.credential_id.value,
                proof_snapshot.secret.value,
            )
            if not hmac.compare_digest(supplied_verifier, record.verifier):
                raise ValueError
            runner = AuthenticatedRunner(
                credential_id=CredentialId(record.credential_id.value),
                team_key=TeamKey(record.team_key.value),
                authenticated_at=now,
                channel_binding_hash=bytes(binding_hash),
            )
            self._sessions[id(runner)] = _SessionRecord(
                runner=runner,
                snapshot=_copy_authenticated_runner(runner),
            )
            return AuthenticationResult(AuthAction.AUTHENTICATED, runner)
        except Exception:
            pass
        raise AuthenticationError("runner authentication failed")

    def validate_session(
        self,
        runner: AuthenticatedRunner,
        *,
        transport: TransportBinding,
    ) -> TeamKey:
        try:
            runner_snapshot = _copy_authenticated_runner(runner)
            session = self._sessions[id(runner)]
            if session.runner is not runner or session.snapshot != runner_snapshot:
                raise ValueError
            record = self._credentials[runner_snapshot.credential_id.value]
            binding_hash = _transport_hash(transport)
            if (
                record.revoked_at is not None
                or record.team_key != runner_snapshot.team_key
                or not hmac.compare_digest(
                    binding_hash, runner_snapshot.channel_binding_hash
                )
            ):
                raise ValueError
            return TeamKey(record.team_key.value)
        except Exception:
            pass
        raise AuthenticationError("runner credential or session is inactive")
