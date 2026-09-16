# Private runner enrollment and credential authentication v1

## Scope and authority boundary

This slice implements the pure central/runner authentication core used before the DIS1 control protocol is accepted. It uses synthetic values and an injected in-memory transport binding only. It opens no socket, contacts no Discord or other network endpoint, reads no private configuration, accesses no operating-system credential store, and owns no scheduler, lifecycle, gameplay, input, or spending capability.

A concrete adapter must supply evidence for a confidential, server-authenticated connection opened outbound by the runner. The adapter must derive the 32-byte channel binding from that exact TLS connection. Boolean flags or channel bytes from an untrusted message are not evidence. Network framing, TLS policy, durable database transactions, and Windows Credential Manager integration remain later adapter work.

The Discord bot credential is central-only and is absent from every enrollment, runner credential, authentication challenge, proof, session, and public module field. A runner receives only its own Team-bound credential.

## Donor inventory and decision

The pinned Clash Rush donors provide no compatible enrollment or runner-authentication seam. Their Discord/webhook paths use plaintext webhook values, mutable configuration, unauthenticated local servers, legacy Discord clients, or direct gameplay coupling. The Discord ecosystem references are presentation/API sources rather than runner-credential authorities. No donor code was copied. This small cryptographic state-machine seam is implemented locally with Python standard-library primitives so the project adds no network framework or secret-store dependency before those adapters are selected.

## One-time enrollment

The central authority snapshots one exact `TeamKey` before invoking either injected random generator, then constructs the outbound grant and central record from separate service-owned copies of that snapshot. A generator therefore cannot retarget enrollment by mutating the caller's Team wrapper. It creates a random 128-bit enrollment token ID and random 256-bit enrollment secret. A grant is valid for a half-open interval no longer than ten minutes. The central record stores only a domain-separated, token-ID-bound SHA-256 verifier, never the enrollment secret.

Enrollment is accepted only over the required outbound transport. Every timestamp is first required to be an exact bounded integer, before comparison, injected generation, or state transition. A malformed enrollment timestamp is rejected without consuming the token or dispatching methods on the supplied value. After transport and exact timestamp validation, the first presented use consumes the token before its secret is evaluated. A bad secret therefore burns the token instead of providing an online guessing oracle. Expired, unknown, consumed, malformed, or wrong-secret grants issue no credential. If credential generation or durable commit fails after consumption, Setup must issue a new enrollment token; the old token is never restored.

Successful enrollment returns once to the runner:

- one random 128-bit credential ID;
- the exact enrolled Team key;
- one random 256-bit runner secret.

The central credential record contains the credential ID, Team key, issue time, optional revocation time, and a domain-separated, credential-ID-bound SHA-256 verifier. It has no runner secret field. Secret-bearing dataclass fields are excluded from representations. The concrete runner adapter must write the returned secret directly to its operating-system credential store and persist only its secret reference in private installation configuration.

## Connection authentication

For an active credential, the central authority creates a random 128-bit challenge ID and 256-bit nonce, bound to the exact credential, Team, channel-binding hash, issue time, and half-open expiry of at most one minute. The pending central challenge and the outbound challenge are structurally independent copies.

The runner verifies the challenge credential, Team, and channel binding, then returns the credential secret with the exact challenge ID, credential ID, and nonce through that same confidential connection. The proof is secret-bearing transport data: its representation suppresses the secret, and adapters must never serialize it into logs, Status, configuration, or exceptions. The central authority exact-validates the completion timestamp before consuming the challenge; a malformed timestamp is rejected without consumption or untrusted comparison dispatch. It then consumes the challenge before evaluating the response, requires the response nonce to equal the stored challenge nonce, derives the credential verifier from the supplied secret, and compares it in constant time. A malformed, expired, replayed, wrong-channel, wrong-Team, revoked, or wrong-secret attempt returns only a bounded sanitized authentication failure with no retained cause or context. Sanitization never formats an injected exception.

The resulting authenticated identity contains only credential ID, Team key, authentication time, and channel-binding hash. The authority retains the exact returned object plus a separate immutable, non-aliasing snapshot; replacement, forged, pre-issuance, or post-return-mutated objects are not authenticated sessions even when their visible fields compare equal. Before accepting each later protocol message, the central adapter must validate that exact service-issued identity against its snapshot, the current credential record, and the exact live transport. This revalidation makes revocation invalidate both new authentication and already authenticated sessions. Authentication grants routing identity only; DIS3 owns exact Team routing, heartbeats, and generation-bound command persistence.

The raw runner secret is deliberately presented inside the authenticated TLS channel rather than using the stored verifier as an HMAC key. A stolen verifier is therefore not directly replayable as a runner credential. This design relies on 256-bit random secrets and the concrete TLS adapter's confidentiality and server authentication; it is not a password protocol and must not accept human-chosen secrets.

## Durability and concurrency requirements for the adapter

`EnrollmentAuthority` is an in-memory reference core. A production central adapter must preserve these transitions in serializable durable transactions:

1. enrollment issue inserts one unused token record;
2. enrollment attempt atomically consumes the token and, only on success, inserts one credential record;
3. challenge issue inserts one unused channel-bound challenge;
4. authentication attempt atomically consumes that challenge before returning any result;
5. revocation atomically sets the first revocation time and never clears it.

Uniqueness is required for token IDs, credential IDs, and challenge IDs. Concurrent uses of one token or challenge permit at most one state transition. A crash after a token/challenge consumption is ambiguous and fails closed; neither value is reused. A revocation racing authentication wins before the authenticated session may authorize a protocol message because session validation re-reads current credential state.

## Proof map

`tests/test_runner_enrollment.py` covers one-time exchange, verifier-only central storage, secret-safe representations, transport prerequisites and post-construction revalidation, failed-transport non-consumption, malformed/wrong-secret burn, true half-open expiry boundaries, exact timestamp validation before comparison/generation/consumption, credential-issuance time and ID-collision failure, Team retarget attempts through both enrollment generators, channel- and nonce-bound one-attempt challenges, challenge-generation collision without replacement, no challenge aliasing, raising exception formatters, wrong-secret sanitization, replay rejection, idempotent revocation of pending/new/existing authentication, exact service-owned session identity, forged/replacement/pre-issuance/post-return mutation rejection, exact nested scalar and 256-bit byte types at every authentication ingress, corrupted-object sanitization, exception cause/context privacy, and absence of any Discord bot-token surface.
