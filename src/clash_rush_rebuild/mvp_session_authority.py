"""Purpose-specific durable authority for one local MVP account session.

This store deliberately does not reuse the scheduler database.  It owns only local
Setup/Run/Pause/Resume/Stop/Status, one-use admission, one action phase chain, and
the final game/retirement receipt.  SQLite transactions are short and never wrap
capture, native input, process launch, or process retirement.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from pathlib import Path

from .mvp_local_gameplay import MvpConfiguration


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SLOT = re.compile(r"slot-[0-4]")
_TRANSACTION = _REFERENCE
_MAX_DEADLINE_SECONDS = 300


class SessionAuthorityError(RuntimeError):
    """The durable single-account authority failed closed."""


class ControlMode(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class ActionPhase(StrEnum):
    PLANNED = "PLANNED"
    INTENT_RECORDED = "INTENT_RECORDED"
    INPUT_STARTED = "INPUT_STARTED"
    INPUT_COMPLETED = "INPUT_COMPLETED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class SessionGrant:
    run_nonce: str
    configuration_revision: int
    control_revision: int
    deadline: int


@dataclass(frozen=True, slots=True)
class DurableTransaction:
    transaction_ref: str
    run_nonce: str
    phase: ActionPhase
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    mode: ControlMode
    configured: bool
    run_nonce: str | None
    configuration_revision: int
    control_revision: int
    admission_consumed: bool
    action_phase: ActionPhase | None
    game_outcome: str | None
    retirement_outcome: str | None
    final_acknowledged: bool


_SCHEMA = """
CREATE TABLE configuration (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    team_ref TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    instance_ref TEXT NOT NULL,
    player_tag_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE control (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    mode TEXT NOT NULL CHECK (mode IN ('STOPPED','RUNNING','PAUSED')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    current_run_nonce TEXT
) STRICT;
CREATE TABLE runs (
    run_nonce TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    candidate_tree TEXT NOT NULL,
    ready_sha256 TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL,
    control_revision INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    admission_consumed INTEGER NOT NULL DEFAULT 0 CHECK (admission_consumed IN (0,1)),
    game_outcome TEXT CHECK (game_outcome IN ('CONFIRMED','FAILED','UNCERTAIN')),
    retirement_outcome TEXT CHECK (retirement_outcome IN ('SUCCEEDED','FAILED')),
    final_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (final_acknowledged IN (0,1))
) STRICT;
CREATE TABLE transactions (
    transaction_ref TEXT PRIMARY KEY,
    run_nonce TEXT NOT NULL UNIQUE REFERENCES runs(run_nonce),
    phase TEXT NOT NULL CHECK (phase IN ('PLANNED','INTENT_RECORDED','INPUT_STARTED','INPUT_COMPLETED','CONFIRMED','FAILED','UNCERTAIN')),
    reason_code TEXT
) STRICT;
CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (target IN ('PAUSED','STOPPED'))
) STRICT;
"""


# Schema 3 is opt-in through the stopped-only internal migration below. These
# envelopes authenticate *bindings*, not OS truth: only the trusted, mutex-owned
# parent/worker ingress may supply them. There is deliberately no native callback.
_V3_POLICY = "OWNED_RELEASE_NO_PLAYER_KILL_V1"
_V3_KINDS = ("APPROVAL_CHECK", "BUILD_CHECK", "NATURAL_RESULT", "RETURN_HOME",
             "SAME_ACCOUNT_HOME", "INSTANCE_SLOT_HOME", "INPUT_RECEIPT", "CALIBRATION_SCOPE", "CALIBRATION_OBSERVATION", "PRE_D_SEAL")
_V3_PHASES = ("PROPOSED", "AUTHORIZED", "SUBMITTING", "SUBMITTED", "OBSERVED", "TERMINAL")
_V3_OUTCOMES = {"SUCCESS", "FAILED", "EXPIRED", "BLOCKED", "UNCERTAIN", "CANCELED"}
_V3_REASONS = {"PRE_D_COMPLETE", "GAMEPLAY_FAILED", "DEADLINE_INCOMPLETE", "PRODUCER_LOST",
               "EVIDENCE_INCOMPLETE", "INPUT_UNCERTAIN", "EMERGENCY_RELEASE", "CANCELED"}


def _v3_require(condition: bool, reason: str) -> None:
    if not condition:
        raise SessionAuthorityError(reason)


def _v3_int(value: object, minimum: int = 0) -> None:
    _v3_require(type(value) is int and minimum <= value <= 2**63 - 1, "V3_INTEGER_INVALID")


def _v3_text(value: object, pattern: re.Pattern = _REFERENCE) -> None:
    _v3_require(type(value) is str and pattern.fullmatch(value) is not None, "V3_TEXT_INVALID")


def canonical_v3(value: object) -> bytes:
    """Canonical closed-envelope bytes; schemas are validated at each ingress."""
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SessionAuthorityError("V3_CANONICAL_INVALID") from exc
    _v3_require(len(result) <= 65536, "V3_PAYLOAD_TOO_LARGE")
    return result


def digest_v3(value: bytes) -> str:
    _v3_require(type(value) is bytes, "V3_BYTES_REQUIRED")
    return hashlib.sha256(value).hexdigest()


def _v3_decode(payload: bytes, keys: set[str]) -> dict:
    _v3_require(type(payload) is bytes and 0 < len(payload) <= 65536, "V3_PAYLOAD_INVALID")
    try:
        raw = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise SessionAuthorityError("V3_PAYLOAD_INVALID") from exc
    _v3_require(type(raw) is dict and set(raw) == keys and canonical_v3(raw) == payload,
                "V3_PAYLOAD_NONCANONICAL")
    return raw


@dataclass(frozen=True, slots=True)
class RunBindingV3:
    generation: int
    run_nonce: str
    transaction_ref: str
    configuration_revision: int
    control_revision: int
    source_tree: str
    runner_manifest_digest: str
    profile_digest: str
    approval_digest: str
    provider_digest: str
    account_ref: str
    binding_digest: str
    clock_domain: str
    started_ns: int
    deadline_ns: int
    revocation_epoch: int

    def __post_init__(self) -> None:
        for name in ("generation", "configuration_revision", "control_revision"):
            _v3_int(getattr(self, name), 1)
        for name in ("started_ns", "deadline_ns", "revocation_epoch"):
            _v3_int(getattr(self, name))
        _v3_require(self.deadline_ns > self.started_ns, "V3_DEADLINE_INVALID")
        for name in ("run_nonce", "transaction_ref"):
            _v3_text(getattr(self, name), _HEX_32)
        _v3_text(self.source_tree, _HEX_40)
        for name in ("runner_manifest_digest", "profile_digest", "approval_digest", "provider_digest", "binding_digest"):
            _v3_text(getattr(self, name), _HEX_64)
        _v3_text(self.account_ref)
        _v3_text(self.clock_domain)

    @property
    def digest(self) -> str:
        return digest_v3(canonical_v3(asdict(self)))


@dataclass(frozen=True, slots=True)
class StoppedProofV3:
    """External proof ingress, NOT a caller-supplied eligible boolean.

    Parent must validate these exact protected source bytes under the lifecycle
    mutex immediately before the call. This module cannot prove OS absence or
    ACLs. No native command currently activates this internal migration.
    """
    mutex_ownership_digest: str
    ready_readback_digest: str
    no_transition_digest: str
    worker_absence_digest: str
    player_absence_digest: str
    input_clear_digest: str
    installed_membership_digest: str
    owner_authorization_digest: str

    def __post_init__(self) -> None:
        for field in fields(self):
            _v3_text(getattr(self, field.name), _HEX_64)


@dataclass(frozen=True, slots=True)
class FactV3:
    kind: str
    sequence: int
    observed_ns: int
    payload: bytes

    def __post_init__(self) -> None:
        _v3_require(type(self.kind) is str and self.kind in _V3_KINDS, "V3_FACT_KIND")
        _v3_int(self.sequence, 1)
        _v3_int(self.observed_ns)
        if self.kind == 'CALIBRATION_OBSERVATION':
            from .mvp_calibration import CalibrationObservation
            raw = _v3_decode(self.payload, {'schema', 'binding_digest', 'observation'})
            _v3_require(type(raw['schema']) is int and raw['schema'] == 3, 'V3_FACT_SCHEMA')
            _v3_text(raw['binding_digest'], _HEX_64)
            CalibrationObservation(**raw['observation'])
            return
        if self.kind == 'CALIBRATION_SCOPE':
            from .mvp_calibration import CalibrationScope
            raw = _v3_decode(self.payload, {'schema', 'binding_digest', 'scope'})
            _v3_require(type(raw['schema']) is int and raw['schema'] == 3, 'V3_FACT_SCHEMA')
            _v3_text(raw['binding_digest'], _HEX_64)
            CalibrationScope(**raw['scope'])
            return
        keys = {
            "APPROVAL_CHECK": {"approval_digest", "revocation_epoch"},
            "BUILD_CHECK": {"provider_digest", "runner_manifest_digest", "source_tree", "build_evidence_digest"},
            "NATURAL_RESULT": {"observation_digest", "natural"},
            "RETURN_HOME": {"observation_digest", "result_digest", "gesture_seq"},
            "SAME_ACCOUNT_HOME": {"observation_digest", "return_digest", "account_ref", "fresh"},
            "INSTANCE_SLOT_HOME": {"observation_digest", "return_digest", "account_ref", "fresh",
                                   "identity_policy", "provider_digest"},
            "INPUT_RECEIPT": {"input_digest", "approval_sequence", "build_sequence"},
            "PRE_D_SEAL": {"facts", "inputs", "emergency_release_required"},
        }[self.kind] | {"schema", "binding_digest"}
        raw = _v3_decode(self.payload, keys)
        _v3_require(type(raw["schema"]) is int and raw["schema"] == 3, "V3_FACT_SCHEMA")
        for key, value in raw.items():
            if key.endswith("digest"):
                _v3_text(value, _HEX_64)
            elif key in ("revocation_epoch", "gesture_seq", "approval_sequence", "build_sequence"):
                _v3_int(value, 0 if key == "revocation_epoch" else 1)
            elif key in ("natural", "fresh", "emergency_release_required"):
                _v3_require(type(value) is bool, "V3_FACT_BOOLEAN")
            elif key == "source_tree":
                _v3_text(value, _HEX_40)
            elif key == "account_ref":
                _v3_text(value)
            elif key == "identity_policy":
                _v3_require(type(value) is str and value == 'IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY',
                            'V3_IDENTITY_POLICY')
        if self.kind == "PRE_D_SEAL":
            for key in ("facts", "inputs"):
                _v3_require(type(raw[key]) is list, "V3_SEAL_LIST")
                for item in raw[key]:
                    _v3_require(type(item) is list and len(item) == 3, "V3_SEAL_MEMBER")
                    if key == "facts":
                        _v3_require(type(item[0]) is str and item[0] in _V3_KINDS[:-1], "V3_SEAL_KIND")
                    else:
                        _v3_int(item[0], 1)
                    _v3_int(item[1], 1)
                    _v3_text(item[2], _HEX_64)

    @property
    def digest(self) -> str:
        return digest_v3(self.payload)


@dataclass(frozen=True, slots=True)
class InputV3:
    gesture_seq: int
    event_seq: int
    event_kind: str
    phase: str
    state_version: int
    submission_started_ns: int | None
    hook_observed_ns: int | None
    completed_ns: int | None
    marker: str
    target_digest: str
    observation_digest: str
    effect_possible: int
    uncertainty: int
    release_state: str

    def __post_init__(self) -> None:
        for name in ("gesture_seq", "event_seq", "state_version"):
            _v3_int(getattr(self, name), 1)
        for name in ("submission_started_ns", "hook_observed_ns", "completed_ns"):
            value = getattr(self, name)
            if value is not None:
                _v3_int(value)
        for name in ("effect_possible", "uncertainty"):
            value = getattr(self, name)
            _v3_require(type(value) is int and value in (0, 1), "V3_INPUT_BOOLEAN")
        _v3_require(type(self.event_kind) is str and self.event_kind in ("MOVE", "DOWN", "UP", "KEY_DOWN", "KEY_UP"), "V3_INPUT_KIND")
        _v3_require(type(self.phase) is str and self.phase in _V3_PHASES, "V3_INPUT_PHASE")
        _v3_require(type(self.release_state) is str and self.release_state in ("NOT_REQUIRED", "REQUIRED", "SUBMITTING", "OBSERVED", "UNCERTAIN"), "V3_RELEASE_STATE")
        _v3_text(self.marker)
        _v3_text(self.target_digest, _HEX_64)
        _v3_text(self.observation_digest, _HEX_64)
        if self.phase in ("SUBMITTING", "SUBMITTED", "OBSERVED"):
            _v3_require(self.effect_possible == 1 and self.submission_started_ns is not None, "V3_EFFECT_REQUIRED")
        if self.phase == "OBSERVED":
            _v3_require(self.hook_observed_ns is not None, "V3_HOOK_REQUIRED")
        if self.phase in ("PROPOSED", "AUTHORIZED"):
            _v3_require(not self.effect_possible and not self.uncertainty and
                        all(getattr(self, n) is None for n in ("submission_started_ns", "hook_observed_ns", "completed_ns")), "V3_PRE_SUBMISSION")
        if self.phase == "TERMINAL" and not self.effect_possible:
            _v3_require(self.submission_started_ns is None and self.hook_observed_ns is None
                        and self.release_state == "NOT_REQUIRED", "V3_NOT_SUBMITTED")
        if self.phase == "TERMINAL" and self.effect_possible and not self.uncertainty:
            _v3_require(all(type(t) is int for t in (self.submission_started_ns, self.hook_observed_ns, self.completed_ns)), "V3_TERMINAL_OBSERVATION_REQUIRED")
        if self.uncertainty:
            _v3_require(self.phase == "TERMINAL" and self.release_state == "UNCERTAIN", "V3_UNCERTAINTY_STICKY")
        if self.hook_observed_ns is not None:
            _v3_require(self.submission_started_ns is not None and self.hook_observed_ns >= self.submission_started_ns, "V3_INPUT_TIME")
        if self.completed_ns is not None and self.hook_observed_ns is not None:
            _v3_require(self.completed_ns >= self.hook_observed_ns, "V3_INPUT_TIME")

    @property
    def digest(self) -> str:
        return digest_v3(canonical_v3(asdict(self)))


@dataclass(frozen=True, slots=True)
class SnapshotV3:
    binding: RunBindingV3
    producer_epoch: int
    facts: tuple[FactV3, ...]
    inputs: tuple[InputV3, ...]

    def __post_init__(self) -> None:
        _v3_require(type(self.binding) is RunBindingV3, "V3_BINDING_TYPE")
        _v3_int(self.producer_epoch, 1)
        _v3_require(type(self.facts) is tuple and all(type(f) is FactV3 for f in self.facts), "V3_FACT_TYPE")
        _v3_require(type(self.inputs) is tuple and all(type(i) is InputV3 for i in self.inputs), "V3_INPUT_TYPE")
        _v3_require(len(self.facts) <= 4096 and len(self.inputs) <= 1024, "V3_SNAPSHOT_BOUND")


@dataclass(frozen=True, slots=True)
class AuthorityStatusV3:
    writer_epoch: int
    current_generation: int | None
    revocation_epoch: int
    generations: tuple[int, ...]
    legacy_generations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OutcomeV3:
    generation: int
    outcome: str
    evidence_digest: str
    reason: str
    finalizer_epoch: int

    def __post_init__(self) -> None:
        _v3_int(self.generation, 1)
        _v3_int(self.finalizer_epoch, 1)
        _v3_text(self.evidence_digest, _HEX_64)
        _v3_require(type(self.outcome) is str and self.outcome in _V3_OUTCOMES
                    and type(self.reason) is str and self.reason in _V3_REASONS, "V3_OUTCOME_INVALID")

    @property
    def digest(self) -> str:
        return digest_v3(canonical_v3(asdict(self)))


@dataclass(frozen=True, slots=True)
class RetirementV3:
    """Canonical externally observed absence; no PID/Job-absence invention."""
    role: str
    identity_digest: str
    proof: bytes

    def __post_init__(self) -> None:
        _v3_require(type(self.role) is str and self.role in ("WORKER", "PLAYER"), "V3_RETIREMENT_ROLE")
        _v3_text(self.identity_digest, _HEX_64)
        raw = _v3_decode(self.proof, {"schema", "binding_digest", "identity_digest", "topology_digest",
                                    "absence_digest", "disposition", "observed_ns"})
        _v3_require(type(raw["schema"]) is int and raw["schema"] == 3, "V3_RETIREMENT_SCHEMA")
        for name in ("binding_digest", "identity_digest", "topology_digest", "absence_digest"):
            _v3_text(raw[name], _HEX_64)
        _v3_require(raw["identity_digest"] == self.identity_digest, "V3_RETIREMENT_IDENTITY")
        _v3_int(raw["observed_ns"])
        _v3_require(raw["disposition"] in (("ABSENT",) if self.role == "WORKER" else
                    ("RETIRED_PRE_D", "EXPLICIT_RECOVERY_ABSENT", "LIFECYCLE_VERIFIED")), "V3_PLAYER_NOT_ABSENT")

    @property
    def digest(self) -> str:
        return digest_v3(self.proof)


@dataclass(frozen=True, slots=True)
class ClosureProofV3:
    binding_digest: str
    containment_policy: str
    topology_digest: str
    release_stage: str
    emergency_release_required: int
    player_disposition: str
    lifecycle_readback_digest: str
    input_clear_digest: str
    recovery_receipt_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("binding_digest", "topology_digest", "lifecycle_readback_digest", "input_clear_digest"):
            _v3_text(getattr(self, name), _HEX_64)
        _v3_require(type(self.containment_policy) is str and self.containment_policy == _V3_POLICY, "V3_CONTAINMENT_POLICY")
        _v3_require(type(self.emergency_release_required) is int and self.emergency_release_required in (0, 1), "V3_EMERGENCY_INVALID")
        _v3_require(type(self.release_stage) is str and self.release_stage in ("NOT_REQUIRED", "ATTEMPTED", "NO_AUTHORIZED_RELEASE"), "V3_RELEASE_STAGE")
        _v3_require(type(self.player_disposition) is str and self.player_disposition in ("RETIRED_PRE_D", "EXPLICIT_RECOVERY_ABSENT", "LIFECYCLE_VERIFIED"), "V3_PLAYER_NOT_ABSENT")
        if self.recovery_receipt_digest is not None:
            _v3_text(self.recovery_receipt_digest, _HEX_64)
        _v3_require((self.player_disposition == "EXPLICIT_RECOVERY_ABSENT") == (self.recovery_receipt_digest is not None), "V3_RECOVERY_REQUIRED")


@dataclass(frozen=True, slots=True)
class ClosureV3:
    generation: int
    outcome_readback_digest: str | None
    worker_retired_digest: str | None
    player_retired_digest: str | None
    lifecycle_readback_digest: str | None
    input_clear_digest: str | None
    closed: int
    closure_digest: str | None
    containment_policy: str | None
    topology_digest: str | None
    release_stage: str | None
    emergency_release_required: int | None
    player_disposition: str | None
    recovery_receipt_digest: str | None


_V3_SCHEMA = """
CREATE TABLE authority_v3 (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER CHECK(schema_version=3),
 writer_epoch INTEGER NOT NULL CHECK(writer_epoch>0), current_generation INTEGER,
 revocation_epoch INTEGER NOT NULL CHECK(revocation_epoch>=0)) STRICT;
CREATE TABLE run_v3 (
 generation INTEGER PRIMARY KEY CHECK(generation>0), run_nonce TEXT UNIQUE NOT NULL,
 transaction_ref TEXT UNIQUE NOT NULL, configuration_revision INTEGER NOT NULL,
 control_revision INTEGER NOT NULL, source_tree TEXT NOT NULL, runner_manifest_digest TEXT NOT NULL,
 profile_digest TEXT NOT NULL, approval_digest TEXT NOT NULL, provider_digest TEXT NOT NULL,
 account_ref TEXT NOT NULL, binding_digest TEXT, clock_domain TEXT NOT NULL,
 started_ns INTEGER NOT NULL, deadline_ns INTEGER NOT NULL CHECK(deadline_ns>started_ns),
 admission TEXT NOT NULL CHECK(admission IN ('PENDING','CONSUMED','CANCELED')),
 revocation_epoch INTEGER NOT NULL, legacy_block INTEGER NOT NULL CHECK(legacy_block IN (0,1))) STRICT;
CREATE TABLE fact_v3 (
 generation INTEGER REFERENCES run_v3, kind TEXT, sequence INTEGER,
 observed_ns INTEGER NOT NULL, payload BLOB NOT NULL, payload_digest TEXT NOT NULL,
 producer_epoch INTEGER NOT NULL, PRIMARY KEY(generation,kind,sequence)) STRICT;
CREATE TABLE input_v3 (
 generation INTEGER REFERENCES run_v3, gesture_seq INTEGER, event_seq INTEGER,
 event_kind TEXT CHECK(event_kind IN ('MOVE','DOWN','UP','KEY_DOWN','KEY_UP')),
 phase TEXT, state_version INTEGER NOT NULL, submission_started_ns INTEGER, hook_observed_ns INTEGER,
 completed_ns INTEGER, marker TEXT NOT NULL, target_digest TEXT NOT NULL, observation_digest TEXT NOT NULL,
 effect_possible INTEGER CHECK(effect_possible IN (0,1)), uncertainty INTEGER CHECK(uncertainty IN (0,1)),
 release_state TEXT CHECK(release_state IN ('NOT_REQUIRED','REQUIRED','SUBMITTING','OBSERVED','UNCERTAIN')),
 PRIMARY KEY(generation,gesture_seq,event_seq)) STRICT;
CREATE TABLE outcome_v3 (
 generation INTEGER PRIMARY KEY REFERENCES run_v3,
 outcome TEXT CHECK(outcome IN ('SUCCESS','FAILED','EXPIRED','BLOCKED','UNCERTAIN','CANCELED')),
 evidence_digest TEXT NOT NULL, reason TEXT NOT NULL, finalizer_epoch INTEGER NOT NULL) STRICT;
CREATE TABLE closure_v3 (
 generation INTEGER PRIMARY KEY REFERENCES run_v3, outcome_readback_digest TEXT,
 worker_retired_digest TEXT, player_retired_digest TEXT, lifecycle_readback_digest TEXT,
 input_clear_digest TEXT, closed INTEGER CHECK(closed IN (0,1)), closure_digest TEXT,
 containment_policy TEXT, topology_digest TEXT, release_stage TEXT,
 emergency_release_required INTEGER CHECK(emergency_release_required IN (0,1)),
 player_disposition TEXT, recovery_receipt_digest TEXT) STRICT;
CREATE TABLE retirement_v3 (
 generation INTEGER REFERENCES run_v3, role TEXT CHECK(role IN ('WORKER','PLAYER')),
 identity_digest TEXT NOT NULL, proof BLOB NOT NULL, proof_digest TEXT NOT NULL,
 PRIMARY KEY(generation,role)) STRICT;
CREATE TABLE migration_v3 (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), original_schema INTEGER NOT NULL,
 original_digest TEXT NOT NULL, backup_digest TEXT NOT NULL, proof BLOB NOT NULL,
 proof_digest TEXT NOT NULL) STRICT;
"""


def _v3_triggers(legacy_tables: tuple[str, ...]) -> str:
    statements = []
    keys = {"authority_v3": ("singleton",), "migration_v3": ("singleton",),
            "run_v3": ("generation",), "fact_v3": ("generation", "kind", "sequence"),
            "input_v3": ("generation", "gesture_seq", "event_seq"), "outcome_v3": ("generation",),
            "closure_v3": ("generation",), "retirement_v3": ("generation", "role")}
    for table, names in keys.items():
        match = " AND ".join(f"{name}=NEW.{name}" for name in names)
        if table == "run_v3":
            match += " OR run_nonce=NEW.run_nonce OR transaction_ref=NEW.transaction_ref"
        statements.append(f"CREATE TRIGGER {table}_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {match}) BEGIN SELECT RAISE(ABORT,'v3 replacement denied'); END;")
    for table in ("fact_v3", "outcome_v3", "retirement_v3", "migration_v3"):
        for action in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable v3 row'); END;")
    for table in legacy_tables:
        for action in ("INSERT", "UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER legacy_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'legacy writer disabled'); END;")
    immutable = [f.name for f in fields(RunBindingV3)] + ["legacy_block"]
    conditions = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in immutable)
    statements.append(f"CREATE TRIGGER run_v3_update BEFORE UPDATE ON run_v3 WHEN {conditions} OR NOT (NEW.admission=OLD.admission OR (OLD.admission='PENDING' AND NEW.admission IN ('CONSUMED','CANCELED'))) BEGIN SELECT RAISE(ABORT,'immutable run'); END;")
    for table in ("run_v3", "input_v3", "closure_v3", "authority_v3"):
        statements.append(f"CREATE TRIGGER {table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'v3 deletion denied'); END;")
    conditions = " OR ".join(f"(OLD.{f.name} IS NOT NULL AND NEW.{f.name} IS NOT OLD.{f.name})"
                             for f in fields(ClosureV3) if f.name not in ("closed", "generation"))
    statements.append(f"CREATE TRIGGER closure_v3_update BEFORE UPDATE ON closure_v3 WHEN NEW.generation!=OLD.generation OR NEW.closed<OLD.closed OR {conditions} BEGIN SELECT RAISE(ABORT,'closure conflict'); END;")
    identity = ("generation", "gesture_seq", "event_seq", "event_kind", "marker", "target_digest", "observation_digest")
    conditions = " OR ".join(f"NEW.{n} IS NOT OLD.{n}" for n in identity)
    times = " OR ".join(f"(OLD.{n} IS NOT NULL AND NEW.{n} IS NOT OLD.{n})" for n in ("submission_started_ns", "hook_observed_ns", "completed_ns"))
    transitions = " OR ".join(f"(OLD.phase='{a}' AND NEW.phase='{b}')" for a, b in zip(_V3_PHASES, _V3_PHASES[1:]))
    statements.append(f"CREATE TRIGGER input_v3_update BEFORE UPDATE ON input_v3 WHEN {conditions} OR {times} OR NEW.state_version!=OLD.state_version+1 OR NEW.effect_possible<OLD.effect_possible OR NEW.uncertainty<OLD.uncertainty OR OLD.phase='TERMINAL' OR NOT ({transitions} OR NEW.phase='TERMINAL') OR (OLD.release_state='UNCERTAIN' AND NEW.release_state!='UNCERTAIN') BEGIN SELECT RAISE(ABORT,'input transition conflict'); END;")
    statements.append("CREATE TRIGGER input_v3_sealed BEFORE UPDATE ON input_v3 WHEN EXISTS(SELECT 1 FROM fact_v3 WHERE generation=OLD.generation AND kind='PRE_D_SEAL') OR EXISTS(SELECT 1 FROM outcome_v3 WHERE generation=OLD.generation) BEGIN SELECT RAISE(ABORT,'sealed input'); END;")
    for table in ("fact_v3", "input_v3"):
        statements.append(f"CREATE TRIGGER {table}_sealed_insert BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM fact_v3 WHERE generation=NEW.generation AND kind='PRE_D_SEAL') OR EXISTS(SELECT 1 FROM outcome_v3 WHERE generation=NEW.generation) BEGIN SELECT RAISE(ABORT,'sealed evidence'); END;")
    return "\n".join(statements)


def require_legacy_command(path: Path, *, missing_allowed: bool = False) -> None:
    """Read-only policy fence, before any legacy native/context construction."""
    if type(missing_allowed) is not bool:
        raise SessionAuthorityError('POLICY_COMMAND_MODE')
    path = Path(path)
    if missing_allowed and not path.exists() and not path.is_symlink():
        return
    authority = DurableSessionAuthority(path, read_only=True)
    try:
        if authority._open().execute('PRAGMA user_version').fetchone()[0] == 3:
            raise SessionAuthorityError('POLICY_GENERATION_LEGACY_COMMAND_DENIED')
    finally:
        authority.close()


class DurableSessionAuthority:
    """Single SQLite authority; legacy APIs and explicit schema-three policy."""

    def __init__(
        self, path: Path, *, nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        read_only: bool = False
    ) -> None:
        self._path = Path(path)
        self._nonce_factory = nonce_factory
        if type(read_only) is not bool:
            raise SessionAuthorityError("exact read-only mode required")
        self._read_only = read_only
        if not callable(nonce_factory):
            raise SessionAuthorityError("nonce factory is required")
        if self._path.exists() and self._path.is_symlink():
            raise SessionAuthorityError("session database path must not redirect")
        if not read_only:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(
                self._path.resolve().as_uri()+'?mode=ro' if read_only else self._path,
                uri=read_only, timeout=2.0, isolation_level=None, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 2000")
            mode = self._connection.execute("PRAGMA journal_mode" if read_only else "PRAGMA journal_mode = WAL").fetchone()[0]
            if read_only:
                self._connection.execute("PRAGMA query_only = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            if str(mode).lower() != "wal":
                raise SessionAuthorityError("session database WAL mode is unavailable")
            self._initialize()
        except BaseException as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            if isinstance(exc, SessionAuthorityError):
                raise
            raise SessionAuthorityError("session database is unavailable") from exc

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _open(self) -> sqlite3.Connection:
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise SessionAuthorityError("session database is closed")
        return connection

    def _initialize(self) -> None:
        connection = self._open()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        manifest = connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        if version == 3:
            self._validate_schema_v3()
            return
        if type(version) is not int or version not in (0, 1, 2):
            raise SessionAuthorityError("session database version is unsupported")
        if version == 0:
            if self._read_only:
                raise SessionAuthorityError("session database is uninitialized")
            if manifest:
                raise SessionAuthorityError("session database schema is noncanonical")
            connection.executescript("BEGIN IMMEDIATE;" + _SCHEMA + "PRAGMA user_version = 1; COMMIT;")
        expected = [
            ("table", "commands", "commands"),
            ("table", "configuration", "configuration"),
            ("table", "control", "control"),
            ("table", "runs", "runs"),
            ("table", "transactions", "transactions"),
        ]
        if version == 2:
            expected.insert(3, ("table", "deployment_receipts", "deployment_receipts"))
        actual = connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        if [tuple(row) for row in actual] != expected:
            raise SessionAuthorityError("session database schema is noncanonical")

        from .mvp_deployment_receipt import validate_schema
        validate_schema(connection, _SCHEMA)

    @staticmethod
    def _schema_manifest_v3(connection: sqlite3.Connection) -> tuple:
        return tuple(tuple(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))

    def _validate_schema_v3(self) -> None:
        c = self._open()
        _v3_require(c.execute("PRAGMA user_version").fetchone()[0] == 3, "V3_SCHEMA_REQUIRED")
        reference = sqlite3.connect(":memory:")
        try:
            # Only fresh schema-3 configuration has absent optional tag metadata.
            definition = c.execute("SELECT sql FROM sqlite_master WHERE name='configuration'").fetchone()[0]
            instance_schema = 'player_tag_sha256 TEXT CHECK(player_tag_sha256 IS NULL)' in definition
            reference.executescript(_SCHEMA.replace('player_tag_sha256 TEXT NOT NULL',
                'player_tag_sha256 TEXT CHECK(player_tag_sha256 IS NULL)') if instance_schema else _SCHEMA)
            legacy = ("commands", "configuration", "control", "runs", "transactions")
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='deployment_receipts'").fetchone():
                from .mvp_deployment_receipt import _CREATE
                reference.execute(_CREATE)
                legacy += ("deployment_receipts",)
            reference.executescript(_V3_SCHEMA + _v3_triggers(legacy))
            _v3_require(self._schema_manifest_v3(c) == self._schema_manifest_v3(reference), "V3_SCHEMA_NONCANONICAL")
            _v3_require(tuple(c.execute("PRAGMA integrity_check").fetchone()) == ("ok",)
                        and not c.execute("PRAGMA foreign_key_check").fetchall(), "V3_INTEGRITY")
            row = c.execute("SELECT * FROM authority_v3").fetchall()
            _v3_require(len(row) == 1 and row[0]["schema_version"] == 3, "V3_AUTHORITY_MISSING")
        finally:
            reference.close()

    def _commit_v3(self) -> None:
        # One named seam for real SQLite COMMIT/ack-loss fault tests, no callback.
        self._open().execute("COMMIT")

    @contextmanager
    def _write_v3(self, epoch: int):
        _v3_int(epoch, 1)
        c = self._open()
        try:
            c.execute("BEGIN IMMEDIATE")
            _v3_require(c.execute("PRAGMA user_version").fetchone()[0] == 3, "V3_SCHEMA_REQUIRED")
            row = c.execute("SELECT * FROM authority_v3 WHERE singleton=1").fetchone()
            _v3_require(row is not None and row["writer_epoch"] == epoch, "V3_STALE_EPOCH")
            yield c
            self._commit_v3()
        except BaseException as exc:
            if c.in_transaction:
                c.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise SessionAuthorityError("V3_SQLITE_WRITE_FAILED") from exc
            raise

    def _readback_v3(self, table: str, generation: int) -> dict | None:
        _v3_require(table in ("run_v3", "outcome_v3", "closure_v3"), "V3_READBACK_TABLE")
        _v3_int(generation, 1)
        # A fresh read-only connection cannot see our uncommitted state. Failure
        # here must propagate even if the preceding COMMIT was durable.
        c = sqlite3.connect(self._path.resolve().as_uri() + "?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            row = c.execute(f"SELECT * FROM {table} WHERE generation=?", (generation,)).fetchone()
            return None if row is None else dict(row)
        finally:
            c.close()

    def _migrate_stopped_v3(self, *, proof: StoppedProofV3, backup_path: Path) -> None:
        """Internal, explicit, owner-authorized ingress; never called by Run/Status.

        Caller must have proved all StoppedProofV3 sources under its protected
        mutex and provisioned a private backup directory. Consumed legacy runs
        are deliberately refused, not reinterpreted as observed v3 facts.
        """
        _v3_require(type(proof) is StoppedProofV3, "V3_STOPPED_PROOF_REQUIRED")
        _v3_require(sqlite3.sqlite_version_info >= (3, 37, 0), "V3_SQLITE_STRICT_UNAVAILABLE")
        backup_path = Path(backup_path)
        _v3_require(backup_path.parent.resolve() == self._path.parent.resolve()
                    and not backup_path.exists() and not backup_path.is_symlink(), "V3_BACKUP_PATH")
        self._initialize()
        c = self._open()
        version = c.execute("PRAGMA user_version").fetchone()[0]
        _v3_require(version in (1, 2), "V3_MIGRATION_VERSION")
        try:
            c.execute("BEGIN IMMEDIATE")
            control = c.execute("SELECT mode FROM control WHERE singleton=1").fetchone()
            _v3_require(control is not None and control[0] == "STOPPED", "V3_MIGRATION_REQUIRES_STOPPED")
            _v3_require(not c.execute("SELECT 1 FROM runs WHERE admission_consumed=1").fetchone()
                        and not c.execute("SELECT 1 FROM transactions").fetchone(), "V3_LEGACY_REQUIRES_RECOVERY")
            source_digest = digest_v3("\n".join(c.iterdump()).encode())
            # The IMMEDIATE lock prevents writers between backup and migration.
            with backup_path.open("xb"):
                pass
            reader = sqlite3.connect(self._path.resolve().as_uri() + "?mode=ro", uri=True)
            backup = sqlite3.connect(backup_path)
            try:
                reader.backup(backup)
                backup.commit()
                _v3_require(digest_v3("\n".join(backup.iterdump()).encode()) == source_digest,
                            "V3_BACKUP_READBACK_MISMATCH")
                _v3_require(backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "V3_BACKUP_INTEGRITY")
            finally:
                reader.close()
                backup.close()
            backup_digest = digest_v3(backup_path.read_bytes())
            legacy = ("commands", "configuration", "control", "runs", "transactions")
            if version == 2:
                legacy += ("deployment_receipts",)
            # execute() rather than executescript(): no implicit pre-DDL COMMIT.
            statement = ""
            for line in (_V3_SCHEMA + _v3_triggers(legacy)).splitlines(True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ""
            _v3_require(not statement.strip(), "V3_DDL_INCOMPLETE")
            raw = canonical_v3(asdict(proof))
            c.execute("INSERT INTO migration_v3 VALUES(1,?,?,?,?,?)",
                      (version, source_digest, backup_digest, raw, digest_v3(raw)))
            c.execute("INSERT INTO authority_v3 VALUES(1,3,1,NULL,0)")
            # These are explicitly untrusted references, not reconstructed clocks,
            # approvals or facts. Every one remains a permanent legacy barrier.
            rows = c.execute("SELECT * FROM runs ORDER BY run_nonce").fetchall()
            account = c.execute("SELECT account_ref FROM configuration").fetchone()[0]
            for generation, row in enumerate(rows, 1):
                legacy_ref = hashlib.sha256(row["run_nonce"].encode()).hexdigest()[:32]
                c.execute("INSERT INTO run_v3 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CANCELED',0,1)",
                          (generation, row["run_nonce"], legacy_ref, row["configuration_revision"],
                           row["control_revision"], row["candidate_tree"], "0" * 64, "0" * 64,
                           "0" * 64, "0" * 64, account, None, "LEGACY_UNTRUSTED", 0, 1))
            if rows:
                c.execute("UPDATE authority_v3 SET current_generation=?", (len(rows),))
            c.execute("PRAGMA user_version=3")
            self._commit_v3()
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        self._validate_schema_v3()

    def reconcile_epoch_v3(self, *, expected_epoch: int, mutex_ownership_digest: str,
                           predecessor_inspection_digest: str, worker_absence_digest: str) -> int:
        """Trusted exclusive restart ingress. Invalidates every live old producer."""
        for value in (mutex_ownership_digest, predecessor_inspection_digest, worker_absence_digest):
            _v3_text(value, _HEX_64)
        self._validate_schema_v3()
        with self._write_v3(expected_epoch) as c:
            c.execute("UPDATE authority_v3 SET writer_epoch=writer_epoch+1,current_generation=NULL WHERE singleton=1")
        row = self._open().execute("SELECT writer_epoch FROM authority_v3").fetchone()
        _v3_require(row[0] == expected_epoch + 1, "V3_EPOCH_READBACK")
        return row[0]

    def _bound_v3(self, binding: RunBindingV3, *, producer: bool = False) -> sqlite3.Row:
        _v3_require(type(binding) is RunBindingV3, "V3_BINDING_TYPE")
        row = self._open().execute("SELECT * FROM run_v3 WHERE generation=?", (binding.generation,)).fetchone()
        _v3_require(row is not None and not row["legacy_block"]
                    and all(row[f.name] == getattr(binding, f.name) for f in fields(binding)), "V3_BINDING_MISMATCH")
        if producer:
            authority = self._open().execute("SELECT * FROM authority_v3").fetchone()
            _v3_require(row["admission"] == "CONSUMED" and authority["current_generation"] == binding.generation
                        and authority["revocation_epoch"] == binding.revocation_epoch, "V3_PRODUCER_REVOKED")
        return row

    def reserve_v3(self, binding: RunBindingV3, *, writer_epoch: int, proof: StoppedProofV3) -> RunBindingV3:
        _v3_require(type(binding) is RunBindingV3 and type(proof) is StoppedProofV3, "V3_RESERVATION_INGRESS")
        with self._write_v3(writer_epoch) as c:
            for row in c.execute("SELECT generation,legacy_block FROM run_v3 ORDER BY generation"):
                _v3_require(not row["legacy_block"], "unresolved legacy predecessor")
                self._require_closed_v3(row["generation"])
            state = c.execute("SELECT * FROM authority_v3").fetchone()
            generation = c.execute("SELECT COALESCE(MAX(generation),0)+1 FROM run_v3").fetchone()[0]
            _v3_require(binding.generation == generation and binding.revocation_epoch == state["revocation_epoch"], "V3_GENERATION_OR_REVOCATION")
            config = c.execute("SELECT * FROM configuration").fetchone()
            _v3_require(config is not None and binding.configuration_revision == config["revision"]
                        and binding.account_ref == config["account_ref"], "V3_CONFIGURATION_MISMATCH")
            previous_control = c.execute("SELECT MAX(revision) FROM (SELECT revision FROM control UNION ALL SELECT control_revision AS revision FROM run_v3)").fetchone()[0]
            _v3_require(binding.control_revision == previous_control + 1, "V3_CONTROL_REVISION")
            _v3_require(not c.execute("SELECT 1 FROM run_v3 WHERE approval_digest=?", (binding.approval_digest,)).fetchone(), "V3_APPROVAL_REPLAY")
            values = asdict(binding) | {"admission": "PENDING", "legacy_block": 0}
            c.execute(f"INSERT INTO run_v3 ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            c.execute("UPDATE authority_v3 SET current_generation=? WHERE singleton=1", (generation,))
            expected = dict(c.execute("SELECT * FROM run_v3 WHERE generation=?", (generation,)).fetchone())
        _v3_require(self._readback_v3("run_v3", generation) == expected, "V3_RUN_READBACK")
        return binding

    def record_calibration_scope_v3(self, binding, scope, *, writer_epoch):
        from .mvp_calibration import CalibrationScope
        _v3_require(type(scope) is CalibrationScope, 'V3_CALIBRATION_SCOPE')
        scope.require_run(binding)
        payload = canonical_v3(dict(schema=3, binding_digest=binding.digest, scope=asdict(scope)))
        with self._write_v3(writer_epoch) as c:
            row = self._bound_v3(binding)
            _v3_require(row['admission'] == 'PENDING', 'V3_CALIBRATION_ADMISSION')
            _v3_require(not c.execute('SELECT 1 FROM fact_v3 WHERE generation=?', (binding.generation,)).fetchone(), 'V3_CALIBRATION_REPLAY')
            c.execute('INSERT INTO fact_v3 VALUES(?,?,?,?,?,?,?)',
                (binding.generation, 'CALIBRATION_SCOPE', 1, binding.started_ns,
                 payload, digest_v3(payload), writer_epoch))
        _v3_require(self.calibration_scope_v3(binding) == scope, 'V3_CALIBRATION_READBACK')

    def calibration_scope_v3(self, binding):
        from .mvp_calibration import CalibrationScope
        self._bound_v3(binding)
        row = self._open().execute("SELECT * FROM fact_v3 WHERE generation=? AND kind='CALIBRATION_SCOPE'", (binding.generation,)).fetchone()
        if row is None:
            return None
        fact = FactV3(row['kind'], row['sequence'], row['observed_ns'], row['payload'])
        raw = json.loads(fact.payload)
        _v3_require(fact.digest == row['payload_digest'] and raw['binding_digest'] == binding.digest, 'V3_CALIBRATION_READBACK')
        scope = CalibrationScope(**raw['scope'])
        scope.require_run(binding)
        return scope

    def calibration_observation_v3(self, binding):
        from .mvp_calibration import CalibrationObservation
        self._bound_v3(binding)
        row = self._open().execute("SELECT * FROM fact_v3 WHERE generation=? AND kind='CALIBRATION_OBSERVATION'", (binding.generation,)).fetchone()
        if row is None:
            return None
        fact = FactV3(row['kind'], row['sequence'], row['observed_ns'], row['payload'])
        raw = json.loads(fact.payload)
        _v3_require(fact.digest == row['payload_digest'] and raw['binding_digest'] == binding.digest, 'V3_CALIBRATION_READBACK')
        observation = CalibrationObservation(**raw['observation'])
        scope = self.calibration_scope_v3(binding)
        _v3_require(scope is not None and observation.scope_digest == scope.digest
            and scope.issued_ns <= observation.observed_ns < scope.expires_ns, 'V3_CALIBRATION_BINDING')
        return observation

    def record_calibration_observation_v3(self, binding, observation, *, writer_epoch):
        from .mvp_calibration import CalibrationObservation
        _v3_require(type(observation) is CalibrationObservation, 'V3_CALIBRATION_OBSERVATION')
        CalibrationObservation(**asdict(observation))
        scope = self.calibration_scope_v3(binding)
        _v3_require(scope is not None and observation.scope_digest == scope.digest
            and scope.issued_ns <= observation.observed_ns < scope.expires_ns, 'V3_CALIBRATION_BINDING')
        payload = canonical_v3(dict(schema=3, binding_digest=binding.digest, observation=asdict(observation)))
        with self._write_v3(writer_epoch) as c:
            row = self._bound_v3(binding)
            _v3_require(row['admission'] == 'CONSUMED', 'V3_CALIBRATION_UNCONSUMED')
            prior = self.calibration_observation_v3(binding)
            if prior is not None:
                _v3_require(prior == observation, 'V3_CALIBRATION_CONFLICT')
            else:
                _v3_require(self.outcome_v3(binding.generation) is None, 'V3_CALIBRATION_FINALIZED')
                c.execute('INSERT INTO fact_v3 VALUES(?,?,?,?,?,?,?)',
                    (binding.generation, 'CALIBRATION_OBSERVATION', 1, observation.observed_ns,
                     payload, digest_v3(payload), writer_epoch))
        _v3_require(self.calibration_observation_v3(binding) == observation, 'V3_CALIBRATION_READBACK')

    def consume_v3(self, binding: RunBindingV3, *, producer_epoch: int, now_ns: int) -> None:
        _v3_int(now_ns)
        with self._write_v3(producer_epoch) as c:
            row = self._bound_v3(binding)
            authority = c.execute("SELECT * FROM authority_v3").fetchone()
            _v3_require(row["admission"] == "PENDING" and authority["current_generation"] == binding.generation
                        and authority["revocation_epoch"] == binding.revocation_epoch, "V3_ADMISSION_CONSUMED_OR_REVOKED")
            _v3_require(binding.started_ns <= now_ns < binding.deadline_ns, "V3_ADMISSION_DEADLINE")
            c.execute("UPDATE run_v3 SET admission='CONSUMED' WHERE generation=?", (binding.generation,))
            expected = dict(c.execute("SELECT * FROM run_v3 WHERE generation=?", (binding.generation,)).fetchone())
        _v3_require(self._readback_v3("run_v3", binding.generation) == expected, "V3_RUN_READBACK")

    def revoke_v3(self, *, writer_epoch: int) -> int:
        with self._write_v3(writer_epoch) as c:
            c.execute("UPDATE authority_v3 SET revocation_epoch=revocation_epoch+1 WHERE singleton=1")
            value = c.execute("SELECT revocation_epoch FROM authority_v3").fetchone()[0]
        return value

    def record_input_v3(self, binding: RunBindingV3, event: InputV3, *, producer_epoch: int,
                        expected_version: int | None) -> None:
        """Durable event CAS; importing a complete live snapshot is a separate seam."""
        _v3_require(type(event) is InputV3, "V3_INPUT_TYPE")
        _v3_require(self.calibration_scope_v3(binding) is None, 'V3_CALIBRATION_NO_INPUT')
        if expected_version is not None:
            _v3_int(expected_version, 1)
        with self._write_v3(producer_epoch) as c:
            self._bound_v3(binding, producer=True)
            key = (binding.generation, event.gesture_seq, event.event_seq)
            prior = c.execute("SELECT * FROM input_v3 WHERE generation=? AND gesture_seq=? AND event_seq=?", key).fetchone()
            if prior is None:
                _v3_require(expected_version is None and event.phase == "PROPOSED" and event.state_version == 1, "V3_INPUT_INITIAL")
                c.execute("INSERT INTO input_v3 VALUES(" + ",".join("?" for _ in range(15)) + ")", (binding.generation, *asdict(event).values()))
            else:
                _v3_require(prior["state_version"] == expected_version and event.state_version == expected_version + 1, "V3_INPUT_CAS")
                if event.phase == "TERMINAL":
                    if prior["phase"] in ("PROPOSED", "AUTHORIZED"):
                        _v3_require(not event.effect_possible and not event.uncertainty, "V3_INPUT_NOT_SUBMITTED")
                    elif prior["phase"] in ("SUBMITTING", "SUBMITTED"):
                        _v3_require(event.uncertainty == 1, "V3_INPUT_OBSERVATION_MISSING")
                release_next = {
                    "NOT_REQUIRED": {"NOT_REQUIRED", "REQUIRED", "SUBMITTING", "OBSERVED", "UNCERTAIN"},
                    "REQUIRED": {"REQUIRED", "SUBMITTING", "OBSERVED", "UNCERTAIN"},
                    "SUBMITTING": {"SUBMITTING", "OBSERVED", "UNCERTAIN"},
                    "OBSERVED": {"OBSERVED"}, "UNCERTAIN": {"UNCERTAIN"},
                }
                _v3_require(event.release_state in release_next[prior["release_state"]], "V3_RELEASE_REGRESSION")
                values = asdict(event)
                c.execute("UPDATE input_v3 SET " + ",".join(f"{k}=?" for k in values) + " WHERE generation=? AND gesture_seq=? AND event_seq=?", (*values.values(), *key))

    def _evidence_v3(self, binding: RunBindingV3) -> tuple[tuple[FactV3, ...], tuple[InputV3, ...], str]:
        c = self._open()
        facts = []
        members = []
        for row in c.execute("SELECT * FROM fact_v3 WHERE generation=? ORDER BY kind,sequence", (binding.generation,)):
            fact = FactV3(row["kind"], row["sequence"], row["observed_ns"], row["payload"])
            _v3_require(fact.digest == row["payload_digest"] and row["producer_epoch"] > 0, "V3_FACT_DIGEST")
            _v3_require(json.loads(fact.payload)["binding_digest"] == binding.digest, "V3_FACT_BINDING")
            facts.append(fact)
            members.append([fact.kind, fact.sequence, fact.observed_ns, fact.digest, row["producer_epoch"]])
        inputs = tuple(InputV3(**{f.name: row[f.name] for f in fields(InputV3)}) for row in
                       c.execute("SELECT * FROM input_v3 WHERE generation=? ORDER BY gesture_seq,event_seq", (binding.generation,)))
        digest = digest_v3(canonical_v3({"binding": binding.digest, "facts": members,
                                        "inputs": [i.digest for i in inputs]}))
        return tuple(facts), inputs, digest

    @staticmethod
    def _complete_v3(binding: RunBindingV3, facts: tuple[FactV3, ...], inputs: tuple[InputV3, ...]) -> None:
        by_key = {(f.kind, f.sequence): f for f in facts}
        _v3_require(len(by_key) == len(facts) and bool(inputs), "V3_INCOMPLETE_EVIDENCE")
        # Old seals remain byte-canonical; new seals name the adopted semantics.
        # Exact cardinality rejects mixed legacy/instance evidence.
        home_kind = 'INSTANCE_SLOT_HOME' if ('INSTANCE_SLOT_HOME', 1) in by_key else 'SAME_ACCOUNT_HOME'
        mandatory = {("NATURAL_RESULT", 1), ("RETURN_HOME", 1), (home_kind, 1), ("PRE_D_SEAL", 1)}
        mandatory |= {(kind, seq) for seq in range(1, len(inputs) + 1)
                      for kind in ("APPROVAL_CHECK", "BUILD_CHECK", "INPUT_RECEIPT")}
        _v3_require(set(by_key) == mandatory, "V3_FACT_CARDINALITY")
        _v3_require(all(binding.started_ns <= f.observed_ns < binding.deadline_ns
                        and json.loads(f.payload)["binding_digest"] == binding.digest for f in facts), "V3_PRE_D_FACT_REQUIRED")
        seal = by_key["PRE_D_SEAL", 1]
        raw = json.loads(seal.payload)
        _v3_require(raw["emergency_release_required"] is False, "V3_EMERGENCY_EXCLUDES_SUCCESS")
        _v3_require(raw["facts"] == [[f.kind, f.sequence, f.digest] for f in sorted(facts, key=lambda x: (x.kind, x.sequence)) if f.kind != "PRE_D_SEAL"]
                    and raw["inputs"] == [[i.gesture_seq, i.event_seq, i.digest] for i in inputs], "V3_SEAL_MEMBERSHIP")
        _v3_require(seal.observed_ns >= max(f.observed_ns for f in facts), "V3_SEAL_TIME")
        result, returned, home = (by_key[k, 1] for k in ("NATURAL_RESULT", "RETURN_HOME", home_kind))
        r, h = json.loads(returned.payload), json.loads(home.payload)
        if home_kind == 'INSTANCE_SLOT_HOME':
            _v3_require(h['identity_policy'] == 'IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY'
                        and h['provider_digest'] == binding.provider_digest, 'V3_INSTANCE_POLICY_BINDING')
        _v3_require(json.loads(result.payload)["natural"] is True and h["fresh"] is True
                    and h["account_ref"] == binding.account_ref and r["result_digest"] == result.digest
                    and h["return_digest"] == returned.digest
                    and result.observed_ns < returned.observed_ns < home.observed_ns, "V3_SEMANTIC_CHAIN")
        gestures = sorted({i.gesture_seq for i in inputs})
        _v3_require(gestures == list(range(1, len(gestures) + 1)), "V3_INPUT_CONTIGUITY")
        markers = set()
        last_time = binding.started_ns
        for gesture in gestures:
            events = [i for i in inputs if i.gesture_seq == gesture]
            _v3_require([i.event_seq for i in events] == list(range(1, len(events) + 1)), "V3_INPUT_CONTIGUITY")
            held = {}
            for event in events:
                _v3_require(event.phase == "TERMINAL" and event.effect_possible == 1 and not event.uncertainty
                            and event.release_state in ("NOT_REQUIRED", "OBSERVED"), "V3_INPUT_UNCERTAIN")
                _v3_require(all(type(t) is int and binding.started_ns <= t < binding.deadline_ns
                                for t in (event.submission_started_ns, event.hook_observed_ns, event.completed_ns)), "V3_PRE_D_INPUT_REQUIRED")
                _v3_require(last_time <= event.submission_started_ns <= event.hook_observed_ns <= event.completed_ns, "V3_INPUT_ORDER")
                last_time = event.completed_ns
                _v3_require(event.marker not in markers, "V3_INPUT_MARKER_REPLAY")
                markers.add(event.marker)
                control = (event.event_kind.startswith("KEY_"), event.target_digest)
                if event.event_kind in ("DOWN", "KEY_DOWN"):
                    _v3_require(control not in held and event.release_state == "OBSERVED", "V3_HELD_INPUT")
                    held[control] = event
                elif event.event_kind in ("UP", "KEY_UP"):
                    down = held.pop(control, None)
                    _v3_require(down is not None and event.event_kind == ("UP" if down.event_kind == "DOWN" else "KEY_UP")
                                and event.release_state == "OBSERVED", "V3_RELEASE_PAIR")
                else:
                    _v3_require(event.release_state == "NOT_REQUIRED", "V3_MOVE_RELEASE")
            _v3_require(not held, "V3_HELD_INPUT")
        return_events = [i for i in inputs if i.gesture_seq == r["gesture_seq"]]
        _v3_require(bool(return_events) and any(i.event_kind == "UP" for i in return_events)
                    and result.observed_ns <= return_events[0].submission_started_ns
                    and return_events[-1].completed_ns <= returned.observed_ns, "V3_RETURN_INPUT")
        _v3_require(last_time <= home.observed_ns, "V3_POST_HOME_INPUT")
        for sequence, event in enumerate(inputs, 1):
            approval = by_key["APPROVAL_CHECK", sequence]
            build = by_key["BUILD_CHECK", sequence]
            receipt = by_key["INPUT_RECEIPT", sequence]
            a, b, i = (json.loads(f.payload) for f in (approval, build, receipt))
            _v3_require(a["approval_digest"] == binding.approval_digest and a["revocation_epoch"] == binding.revocation_epoch
                        and b["provider_digest"] == binding.provider_digest and b["source_tree"] == binding.source_tree
                        and b["runner_manifest_digest"] == binding.runner_manifest_digest, "V3_APPROVAL_BUILD_BINDING")
            _v3_require(i["input_digest"] == event.digest and i["approval_sequence"] == sequence and i["build_sequence"] == sequence
                        and max(approval.observed_ns, build.observed_ns) <= event.submission_started_ns
                        and event.completed_ns <= receipt.observed_ns, "V3_INPUT_RECEIPT_BINDING")

    def record_snapshot_v3(self, snapshot: SnapshotV3) -> None:
        """Trusted *live original producer* may hand off immutable pre-D memory after D.

        Restart cannot call this to recreate lost facts: producer_epoch is fenced
        by exclusive reconciliation. This operation grants no physical authority.
        """
        _v3_require(type(snapshot) is SnapshotV3, "V3_SNAPSHOT_TYPE")
        binding = snapshot.binding
        _v3_require(self.calibration_scope_v3(binding) is None, 'V3_CALIBRATION_NO_GAMEPLAY')
        facts = tuple(sorted(snapshot.facts, key=lambda f: (f.kind == "PRE_D_SEAL", f.kind, f.sequence)))
        _v3_require(len({(f.kind, f.sequence) for f in facts}) == len(facts)
                    and len({(i.gesture_seq, i.event_seq) for i in snapshot.inputs}) == len(snapshot.inputs), "V3_DUPLICATE_EVIDENCE")
        with self._write_v3(snapshot.producer_epoch) as c:
            self._bound_v3(binding, producer=True)
            for event in snapshot.inputs:
                row = c.execute("SELECT * FROM input_v3 WHERE generation=? AND gesture_seq=? AND event_seq=?",
                                (binding.generation, event.gesture_seq, event.event_seq)).fetchone()
                if row is None:
                    _v3_require(event.phase == "TERMINAL", "V3_SNAPSHOT_TERMINAL_ONLY")
                    c.execute("INSERT INTO input_v3 VALUES(" + ",".join("?" for _ in range(15)) + ")", (binding.generation, *asdict(event).values()))
                else:
                    _v3_require(all(row[f.name] == getattr(event, f.name) for f in fields(event)), "V3_INPUT_SNAPSHOT_CONFLICT")
            for fact in facts:
                _v3_require(json.loads(fact.payload)["binding_digest"] == binding.digest, "V3_FACT_BINDING")
                values = (binding.generation, fact.kind, fact.sequence, fact.observed_ns, fact.payload, fact.digest, snapshot.producer_epoch)
                prior = c.execute("SELECT * FROM fact_v3 WHERE generation=? AND kind=? AND sequence=?", values[:3]).fetchone()
                if prior is None:
                    c.execute("INSERT INTO fact_v3 VALUES(?,?,?,?,?,?,?)", values)
                else:
                    _v3_require(tuple(prior) == values, "V3_FACT_CONFLICT")
            stored_facts, stored_inputs, _ = self._evidence_v3(binding)
            if any(f.kind == "PRE_D_SEAL" for f in stored_facts):
                self._complete_v3(binding, stored_facts, stored_inputs)

    def finalize_v3(self, binding: RunBindingV3, *, writer_epoch: int, outcome: str,
                    reason: str, expiry_observed_ns: int | None = None) -> OutcomeV3:
        """SQLite-only terminal CAS. Expiry time is in binding.clock_domain.

        Expiry ingress must be a live producer's known-incomplete observation,
        never a restarted wall-clock sweep; producer loss uses UNCERTAIN/BLOCKED.
        No time parameter is required or consulted for historical SUCCESS.
        """
        _v3_require(type(outcome) is str and outcome in _V3_OUTCOMES and type(reason) is str and reason in _V3_REASONS, "V3_OUTCOME_INVALID")
        _v3_require((outcome == "SUCCESS") == (reason == "PRE_D_COMPLETE")
                    and (reason != "EMERGENCY_RELEASE" or outcome == "UNCERTAIN"), "V3_OUTCOME_REASON")
        if expiry_observed_ns is not None:
            _v3_int(expiry_observed_ns)
        with self._write_v3(writer_epoch) as c:
            run = self._bound_v3(binding)
            facts, inputs, evidence_digest = self._evidence_v3(binding)
            if outcome == "SUCCESS":
                _v3_require(self.calibration_scope_v3(binding) is None, 'V3_CALIBRATION_NO_SUCCESS')
                _v3_require(run["admission"] == "CONSUMED", "V3_SUCCESS_UNCONSUMED")
                self._complete_v3(binding, facts, inputs)
            prior = c.execute("SELECT * FROM outcome_v3 WHERE generation=?", (binding.generation,)).fetchone()
            if outcome == "EXPIRED" and prior is None:
                state = c.execute("SELECT current_generation FROM authority_v3").fetchone()[0]
                _v3_require(expiry_observed_ns is not None and expiry_observed_ns >= binding.deadline_ns
                            and state == binding.generation and reason == "DEADLINE_INCOMPLETE"
                            and not any(f.kind == "PRE_D_SEAL" for f in facts)
                            and not any(i.effect_possible for i in inputs), "V3_EXPIRED_NOT_PROVED")
            if prior is not None:
                _v3_require(prior["outcome"] == outcome and prior["evidence_digest"] == evidence_digest and prior["reason"] == reason, "V3_TERMINAL_CONFLICT")
                expected = dict(prior)
            else:
                expected = asdict(OutcomeV3(binding.generation, outcome, evidence_digest, reason, writer_epoch))
                c.execute("INSERT INTO outcome_v3 VALUES(?,?,?,?,?)", tuple(expected.values()))
                c.execute("INSERT INTO closure_v3(generation,closed) VALUES(?,0)", (binding.generation,))
                if run["admission"] == "PENDING":
                    c.execute("UPDATE run_v3 SET admission='CANCELED' WHERE generation=?", (binding.generation,))
        readback = self._readback_v3("outcome_v3", binding.generation)
        _v3_require(readback == expected, "V3_OUTCOME_READBACK")
        result = OutcomeV3(**readback)
        with self._write_v3(writer_epoch) as c:
            self._bound_v3(binding)
            c.execute("UPDATE closure_v3 SET outcome_readback_digest=? WHERE generation=?", (result.digest, binding.generation))
        closure = self._readback_v3("closure_v3", binding.generation)
        _v3_require(closure is not None and closure["outcome_readback_digest"] == result.digest, "V3_ACK_READBACK")
        return result

    def record_retirement_v3(self, binding: RunBindingV3, proof: RetirementV3, *, writer_epoch: int) -> None:
        _v3_require(type(proof) is RetirementV3, "V3_RETIREMENT_TYPE")
        raw = json.loads(proof.proof)
        _v3_require(raw["binding_digest"] == binding.digest, "V3_RETIREMENT_BINDING")
        if raw["disposition"] in ("RETIRED_PRE_D", "LIFECYCLE_VERIFIED"):
            _v3_require(binding.started_ns <= raw["observed_ns"] < binding.deadline_ns, "V3_RETIREMENT_DEADLINE")
        with self._write_v3(writer_epoch) as c:
            self._bound_v3(binding)
            values = (binding.generation, proof.role, proof.identity_digest, proof.proof, proof.digest)
            prior = c.execute("SELECT * FROM retirement_v3 WHERE generation=? AND role=?", values[:2]).fetchone()
            if prior is None:
                c.execute("INSERT INTO retirement_v3 VALUES(?,?,?,?,?)", values)
            else:
                _v3_require(tuple(prior) == values, "V3_RETIREMENT_CONFLICT")
            column = "worker_retired_digest" if proof.role == "WORKER" else "player_retired_digest"
            c.execute(f"UPDATE closure_v3 SET {column}=? WHERE generation=?", (proof.digest, binding.generation))

    def status_v3(self) -> AuthorityStatusV3:
        """Read-only discovery for administrative restart; never eligibility."""
        c = self._open()
        try:
            c.execute("BEGIN")
            self._validate_schema_v3()
            state = c.execute("SELECT * FROM authority_v3").fetchone()
            runs = c.execute("SELECT * FROM run_v3 ORDER BY generation").fetchall()
            for row in runs:
                if row["legacy_block"]:
                    continue
                binding = RunBindingV3(**{f.name: row[f.name] for f in fields(RunBindingV3)})
                facts, inputs, digest = self._evidence_v3(binding)
                if any(f.kind == "PRE_D_SEAL" for f in facts):
                    self._complete_v3(binding, facts, inputs)
                terminal = c.execute("SELECT * FROM outcome_v3 WHERE generation=?", (binding.generation,)).fetchone()
                if terminal is not None:
                    _v3_require(OutcomeV3(**dict(terminal)).evidence_digest == digest, "V3_OUTCOME_EVIDENCE_CHANGED")
            result = AuthorityStatusV3(state["writer_epoch"], state["current_generation"], state["revocation_epoch"],
                                       tuple(r["generation"] for r in runs),
                                       tuple(r["generation"] for r in runs if r["legacy_block"]))
            c.execute("COMMIT")
            return result
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise

    def read_run_v3(self, generation: int) -> RunBindingV3:
        row = self._readback_v3("run_v3", generation)
        _v3_require(row is not None and not row["legacy_block"], "V3_RUN_UNAVAILABLE_OR_LEGACY")
        return RunBindingV3(**{f.name: row[f.name] for f in fields(RunBindingV3)})

    def outcome_v3(self, generation: int) -> OutcomeV3 | None:
        raw = self._readback_v3("outcome_v3", generation)
        return None if raw is None else OutcomeV3(**raw)

    def closure_v3(self, generation: int) -> ClosureV3:
        raw = self._readback_v3("closure_v3", generation)
        _v3_require(raw is not None, "unresolved predecessor closure missing")
        return ClosureV3(**raw)

    @staticmethod
    def _inputs_clear_v3(binding: RunBindingV3, inputs: tuple[InputV3, ...]) -> bool:
        held = {}
        previous = {}
        last = binding.started_ns
        for event in inputs:
            if (event.phase != "TERMINAL" or event.uncertainty
                    or event.event_seq != previous.get(event.gesture_seq, 0) + 1):
                return False
            previous[event.gesture_seq] = event.event_seq
            if not event.effect_possible:
                if event.release_state != "NOT_REQUIRED":
                    return False
                continue
            times = (event.submission_started_ns, event.hook_observed_ns, event.completed_ns)
            if (not all(type(t) is int and binding.started_ns <= t < binding.deadline_ns for t in times)
                    or not last <= times[0] <= times[1] <= times[2]):
                return False
            last = times[2]
            key = (event.gesture_seq, event.event_kind.startswith("KEY_"), event.target_digest)
            if event.event_kind in ("DOWN", "KEY_DOWN"):
                if key in held or event.release_state != "OBSERVED":
                    return False
                held[key] = event.event_kind
            elif event.event_kind in ("UP", "KEY_UP"):
                expected = "DOWN" if event.event_kind == "UP" else "KEY_DOWN"
                if held.pop(key, None) != expected or event.release_state != "OBSERVED":
                    return False
            elif event.release_state != "NOT_REQUIRED":
                return False
        return not held

    def _closure_evidence_v3(self, binding: RunBindingV3, proof: ClosureProofV3, outcome: OutcomeV3) -> tuple[dict, tuple[RetirementV3, ...]]:
        _v3_require(type(proof) is ClosureProofV3 and proof.binding_digest == binding.digest, "V3_CLOSURE_BINDING")
        _v3_require((outcome.reason != "EMERGENCY_RELEASE" or proof.emergency_release_required == 1)
                    and (not proof.emergency_release_required or outcome.outcome == "UNCERTAIN"), "V3_EMERGENCY_HISTORY")
        rows = self._open().execute("SELECT * FROM retirement_v3 WHERE generation=? ORDER BY role", (binding.generation,)).fetchall()
        _v3_require(len(rows) == 2 and {r["role"] for r in rows} == {"WORKER", "PLAYER"}, "V3_RETIREMENT_MISSING")
        retirement = {}
        validated_retirements = []
        for row in rows:
            record = RetirementV3(row["role"], row["identity_digest"], row["proof"])
            raw = json.loads(record.proof)
            _v3_require(record.digest == row["proof_digest"] and raw["binding_digest"] == binding.digest
                        and raw["topology_digest"] == proof.topology_digest, "V3_RETIREMENT_JOIN")
            if record.role == "PLAYER":
                _v3_require(raw["disposition"] == proof.player_disposition, "V3_PLAYER_DISPOSITION")
                if raw["disposition"] in ("RETIRED_PRE_D", "LIFECYCLE_VERIFIED"):
                    _v3_require(binding.started_ns <= raw["observed_ns"] < binding.deadline_ns, "V3_RETIREMENT_DEADLINE")
            retirement[record.role] = record.digest
            validated_retirements.append(record)
        facts, inputs, digest = self._evidence_v3(binding)
        _v3_require(digest == outcome.evidence_digest, "V3_OUTCOME_EVIDENCE_CHANGED")
        if outcome.outcome == "SUCCESS":
            self._complete_v3(binding, facts, inputs)
            _v3_require(not proof.emergency_release_required, "V3_EMERGENCY_EXCLUDES_SUCCESS")
        uncertain = not self._inputs_clear_v3(binding, inputs)
        calibration_closed = proof.player_disposition == 'LIFECYCLE_VERIFIED'
        if calibration_closed:
            observation = self.calibration_observation_v3(binding)
            _v3_require(observation is not None and observation.status == 'CAPTURED'
                and observation.offer_digest == proof.topology_digest
                and (outcome.outcome, outcome.reason) == ('BLOCKED', 'EVIDENCE_INCOMPLETE')
                and not inputs and {f.kind for f in facts} == {'CALIBRATION_SCOPE', 'CALIBRATION_OBSERVATION'}
                and not proof.emergency_release_required and proof.release_stage == 'NOT_REQUIRED',
                'V3_CALIBRATION_CLOSURE')
        if uncertain or (outcome.outcome in ("UNCERTAIN", "BLOCKED") and not calibration_closed) or proof.emergency_release_required or proof.release_stage != "NOT_REQUIRED":
            _v3_require(proof.recovery_receipt_digest is not None, "V3_EXPLICIT_RECOVERY_REQUIRED")
        values = {"generation": binding.generation, "outcome_readback_digest": outcome.digest,
                  "worker_retired_digest": retirement["WORKER"], "player_retired_digest": retirement["PLAYER"],
                  "closed": 1, **{k: v for k, v in asdict(proof).items() if k != "binding_digest"}}
        values["closure_digest"] = digest_v3(canonical_v3(values))
        return values, tuple(validated_retirements)

    def close_v3(self, binding: RunBindingV3, *, writer_epoch: int, proof: ClosureProofV3) -> ClosureV3:
        outcome = self.outcome_v3(binding.generation)
        _v3_require(outcome is not None, "V3_OUTCOME_MISSING")
        with self._write_v3(writer_epoch) as c:
            self._bound_v3(binding)
            row = c.execute("SELECT * FROM closure_v3 WHERE generation=?", (binding.generation,)).fetchone()
            _v3_require(row is not None and row["outcome_readback_digest"] == outcome.digest, "V3_OUTCOME_READBACK_REQUIRED")
            values, _retirements = self._closure_evidence_v3(binding, proof, outcome)
            c.execute("UPDATE closure_v3 SET " + ",".join(f"{k}=?" for k in values) + " WHERE generation=?", (*values.values(), binding.generation))
        _v3_require(self._readback_v3("closure_v3", binding.generation) == values, "V3_CLOSURE_READBACK")
        return ClosureV3(**values)

    def _require_closed_v3(self, generation: int) -> tuple[RunBindingV3, ClosureV3, tuple[RetirementV3, ...]]:
        raw = self._readback_v3("closure_v3", generation)
        _v3_require(raw is not None and raw["closed"] == 1, "unresolved predecessor closure")
        row = self._open().execute("SELECT * FROM run_v3 WHERE generation=?", (generation,)).fetchone()
        _v3_require(row is not None and not row['legacy_block'], 'V3_RUN_UNAVAILABLE_OR_LEGACY')
        binding = RunBindingV3(**{f.name: row[f.name] for f in fields(RunBindingV3)})
        outcome = self.outcome_v3(generation)
        _v3_require(outcome is not None, "unresolved predecessor outcome")
        proof = ClosureProofV3(binding_digest=binding.digest, **{f.name: raw[f.name] for f in fields(ClosureProofV3) if f.name != "binding_digest"})
        values, retirements = self._closure_evidence_v3(binding, proof, outcome)
        _v3_require(raw == values, "unresolved predecessor readback")
        return binding, ClosureV3(**values), retirements

    @staticmethod
    def _configuration(configuration: object) -> MvpConfiguration:
        if type(configuration) is not MvpConfiguration:
            raise SessionAuthorityError("exact local MVP configuration required")
        values = (configuration.team_ref, configuration.account_ref)
        if (
            any(type(value) is not str or _REFERENCE.fullmatch(value) is None for value in values)
            or type(configuration.instance_ref) is not str
            or _SLOT.fullmatch(configuration.instance_ref) is None
            or type(configuration.player_tag_sha256) is not str
            or _HEX_64.fullmatch(configuration.player_tag_sha256) is None
        ):
            raise SessionAuthorityError("one exact Team, account, and instance are required")
        return configuration

    def setup_instance_v3(self, *, team_ref: str, slot_id: str, instance_ref: str) -> None:
        """Fresh-only storage seam. Caller owns stopped/absence/mutex proof.

        No historical row is migrated, removed, or relabeled. account_ref is the
        existing opaque envelope field, now carrying the registered stable slot.
        """
        _v3_require(type(team_ref) is str and _REFERENCE.fullmatch(team_ref) is not None
                    and type(slot_id) is str and re.fullmatch(r'slot-[a-z0-9][a-z0-9_-]{0,63}', slot_id) is not None
                    and type(instance_ref) is str and _SLOT.fullmatch(instance_ref) is not None,
                    'V3_INSTANCE_CONFIGURATION')
        c = self._open()
        try:
            c.execute('BEGIN IMMEDIATE')
            _v3_require(c.execute('PRAGMA user_version').fetchone()[0] == 1,
                        'V3_FRESH_SETUP_ONLY')
            legacy = ('commands', 'configuration', 'control', 'runs', 'transactions')
            _v3_require(all(not c.execute(f'SELECT 1 FROM {table}').fetchone() for table in legacy),
                        'V3_FRESH_SETUP_ONLY')
            c.execute('DROP TABLE configuration')
            configuration_sql = _SCHEMA.split('CREATE TABLE control')[0].replace(
                'player_tag_sha256 TEXT NOT NULL', 'player_tag_sha256 TEXT CHECK(player_tag_sha256 IS NULL)')
            c.execute(configuration_sql)
            c.execute('INSERT INTO configuration VALUES(1,1,?,?,?,NULL)', (team_ref, slot_id, instance_ref))
            c.execute("INSERT INTO control VALUES(1,'STOPPED',1,NULL)")
            statement = ''
            for line in (_V3_SCHEMA + _v3_triggers(legacy)).splitlines(True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ''
            _v3_require(not statement.strip(), 'V3_DDL_INCOMPLETE')
            c.execute('INSERT INTO authority_v3 VALUES(1,3,1,NULL,0)')
            c.execute('PRAGMA user_version=3')
            self._commit_v3()
        except BaseException:
            if c.in_transaction:
                c.execute('ROLLBACK')
            raise
        self._validate_schema_v3()

    def setup(self, configuration: MvpConfiguration) -> SessionStatus:
        config = self._configuration(configuration)
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM configuration").fetchone() is not None:
                raise SessionAuthorityError("local MVP is already configured")
            connection.execute(
                "INSERT INTO configuration VALUES (1,1,?,?,?,?)",
                (config.team_ref, config.account_ref, config.instance_ref, config.player_tag_sha256),
            )
            connection.execute("INSERT INTO control VALUES (1,'STOPPED',1,NULL)")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def rebind_stopped_instance(
        self, *, expected_instance_ref: str, instance_ref: str
    ) -> SessionStatus:
        if (
            type(expected_instance_ref) is not str
            or _SLOT.fullmatch(expected_instance_ref) is None
            or type(instance_ref) is not str
            or _SLOT.fullmatch(instance_ref) is None
        ):
            raise SessionAuthorityError("exact instance references are required")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute(
                "SELECT mode FROM control WHERE singleton=1"
            ).fetchone()
            if (
                control is None
                or control["mode"] != ControlMode.STOPPED.value
            ):
                raise SessionAuthorityError("instance rebind requires STOPPED mode")
            configuration = connection.execute(
                "SELECT instance_ref FROM configuration WHERE singleton=1"
            ).fetchone()
            if configuration is None:
                raise SessionAuthorityError("local MVP is not configured")
            if configuration["instance_ref"] != expected_instance_ref:
                raise SessionAuthorityError("configured instance changed")
            if instance_ref == expected_instance_ref:
                raise SessionAuthorityError("configured instance is already selected")
            connection.execute(
                "UPDATE configuration SET revision=revision+1,instance_ref=? WHERE singleton=1",
                (instance_ref,),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def import_stopped_legacy(self, path: Path) -> SessionStatus:
        if self._open().execute("SELECT 1 FROM configuration").fetchone() is not None:
            raise SessionAuthorityError("local MVP is already configured")
        try:
            payload = Path(path).read_bytes()
            raw = json.loads(payload.decode("utf-8"))
            if (
                type(raw) is not dict
                or set(raw) != {"schema", "mode", "configuration"}
                or type(raw["schema"]) is not int
                or raw["schema"] != 2
                or raw["mode"] != "STOPPED"
                or type(raw["configuration"]) is not dict
                or set(raw["configuration"]) != set(asdict(MvpConfiguration("a", "a", "slot-0", "a" * 64)))
            ):
                raise SessionAuthorityError("legacy control is not an exact stopped state")
            canonical = (
                json.dumps(raw, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
                + b"\n"
            )
            if canonical != payload:
                raise SessionAuthorityError("legacy control is not canonical")
            configuration = MvpConfiguration(**raw["configuration"])
        except SessionAuthorityError:
            raise
        except BaseException as exc:
            raise SessionAuthorityError("legacy control is unavailable") from exc
        return self.setup(configuration)

    @staticmethod
    def _binding(
        purpose: object, candidate_tree: object, ready_bytes: object, deadline: object, now: object
    ) -> tuple[str, str, str, int, int]:
        if (
            type(purpose) is not str
            or _REFERENCE.fullmatch(purpose) is None
            or type(candidate_tree) is not str
            or _HEX_40.fullmatch(candidate_tree) is None
            or type(ready_bytes) is not bytes
            or not ready_bytes
            or type(deadline) is not int
            or type(now) is not int
            or not 0 <= now < deadline <= now + _MAX_DEADLINE_SECONDS
        ):
            raise SessionAuthorityError("run admission binding is malformed")
        return purpose, candidate_tree, hashlib.sha256(ready_bytes).hexdigest(), deadline, now

    def _begin_run(
        self,
        *,
        expected_mode: ControlMode,
        purpose: str,
        candidate_tree: str,
        ready_bytes: bytes,
        deadline: int,
        now: int,
        nonce: str | None = None,
        deployment_sha256: str | None = None,
    ) -> SessionGrant:
        purpose, tree, ready_digest, deadline, _ = self._binding(
            purpose, candidate_tree, ready_bytes, deadline, now
        )
        run_nonce = self._nonce_factory() if nonce is None else nonce
        if type(run_nonce) is not str or _HEX_32.fullmatch(run_nonce) is None:
            raise SessionAuthorityError("run nonce is malformed")
        connection = self._open()
        from .mvp_deployment_receipt import upgrade_schema
        upgrade_schema(connection, _SCHEMA)
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            configuration = connection.execute(
                "SELECT revision FROM configuration WHERE singleton=1"
            ).fetchone()
            if control is None or configuration is None:
                raise SessionAuthorityError("local MVP is not configured")
            unresolved = connection.execute(
                "SELECT 1 FROM runs WHERE admission_consumed=1 AND (game_outcome IS NULL OR game_outcome='UNCERTAIN' OR retirement_outcome IS NULL OR retirement_outcome='FAILED') LIMIT 1"
            ).fetchone()
            if unresolved is not None:
                raise SessionAuthorityError("an unresolved run blocks new attack authority")
            if control["mode"] != expected_mode.value:
                raise SessionAuthorityError(f"run requires {expected_mode.value} mode")
            revision = control["revision"] + 1
            connection.execute(
                "INSERT INTO runs (run_nonce,purpose,candidate_tree,ready_sha256,configuration_revision,control_revision,deadline) VALUES (?,?,?,?,?,?,?)",
                (run_nonce, purpose, tree, ready_digest, configuration["revision"], revision, deadline),
            )
            from .mvp_deployment_receipt import bind_profile
            bind_profile(connection, run_nonce, purpose, deployment_sha256)
            connection.execute(
                "UPDATE control SET mode='RUNNING',revision=?,current_run_nonce=? WHERE singleton=1",
                (revision, run_nonce),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return SessionGrant(run_nonce, configuration["revision"], revision, deadline)

    def run(self, **binding: object) -> SessionGrant:
        return self._begin_run(expected_mode=ControlMode.STOPPED, **binding)  # type: ignore[arg-type]

    def resume(self, **binding: object) -> SessionGrant:
        return self._begin_run(expected_mode=ControlMode.PAUSED, **binding)  # type: ignore[arg-type]

    def command(self, target: ControlMode, *, command_id: str) -> SessionStatus:
        if type(target) is not ControlMode or target not in (
            ControlMode.PAUSED,
            ControlMode.STOPPED,
        ):
            raise SessionAuthorityError("invalid control command")
        if type(command_id) is not str or _REFERENCE.fullmatch(command_id) is None:
            raise SessionAuthorityError("control command id is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT target FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if existing is not None:
                if existing["target"] != target.value:
                    raise SessionAuthorityError("control command id conflicts")
                connection.execute("COMMIT")
                return self.status()
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            if control is None:
                raise SessionAuthorityError("local MVP is not configured")
            current = ControlMode(control["mode"])
            effective = (
                ControlMode.STOPPED
                if current is ControlMode.STOPPED or target is ControlMode.STOPPED
                else ControlMode.PAUSED
            )
            if current is not ControlMode.RUNNING and not (
                current is ControlMode.PAUSED and target is ControlMode.STOPPED
            ) and effective is not current:
                raise SessionAuthorityError("control command requires an active run")
            connection.execute(
                "INSERT INTO commands VALUES (?,?)", (command_id, target.value)
            )
            connection.execute(
                "UPDATE control SET mode=?,revision=revision+1 WHERE singleton=1",
                (effective.value,),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def admit(
        self,
        *,
        run_nonce: str,
        transaction_ref: str,
        purpose: str,
        candidate_tree: str,
        ready_bytes: bytes,
        configuration_revision: int,
        control_revision: int,
        now: int,
    ) -> DurableTransaction:
        if type(transaction_ref) is not str or _TRANSACTION.fullmatch(transaction_ref) is None:
            raise SessionAuthorityError("transaction reference is malformed")
        if type(now) is not int or type(configuration_revision) is not int or type(control_revision) is not int:
            raise SessionAuthorityError("admission values are malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            run = connection.execute("SELECT * FROM runs WHERE run_nonce=?", (run_nonce,)).fetchone()
            if control is None or run is None or control["mode"] != "RUNNING" or control["current_run_nonce"] != run_nonce:
                raise SessionAuthorityError("run nonce is not current")
            if run["admission_consumed"]:
                raise SessionAuthorityError("run admission was already consumed")
            if run["purpose"] != purpose or run["candidate_tree"] != candidate_tree:
                raise SessionAuthorityError("run purpose or candidate tree mismatch")
            if hashlib.sha256(ready_bytes).hexdigest() != run["ready_sha256"]:
                raise SessionAuthorityError("READY bytes mismatch")
            if (
                run["configuration_revision"] != configuration_revision
                or run["control_revision"] != control_revision
                or control["revision"] != control_revision
            ):
                raise SessionAuthorityError("configuration or control revision mismatch")
            if not 0 <= now < run["deadline"]:
                raise SessionAuthorityError("run admission deadline expired")
            connection.execute(
                "INSERT INTO transactions VALUES (?,?,?,NULL)",
                (transaction_ref, run_nonce, ActionPhase.PLANNED.value),
            )
            connection.execute(
                "UPDATE runs SET admission_consumed=1 WHERE run_nonce=?", (run_nonce,)
            )
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise SessionAuthorityError("transaction identity was already used") from exc
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.transaction(transaction_ref)

    def transaction(self, transaction_ref: str) -> DurableTransaction:
        row = self._open().execute(
            "SELECT * FROM transactions WHERE transaction_ref=?", (transaction_ref,)
        ).fetchone()
        if row is None:
            raise SessionAuthorityError("transaction is unavailable")
        return DurableTransaction(
            row["transaction_ref"], row["run_nonce"], ActionPhase(row["phase"]), row["reason_code"]
        )

    def transition_action(
        self,
        transaction_ref: str,
        *,
        expected: ActionPhase,
        target: ActionPhase,
        reason_code: str | None = None,
    ) -> DurableTransaction:
        allowed = {
            ActionPhase.PLANNED: {ActionPhase.INTENT_RECORDED, ActionPhase.FAILED},
            ActionPhase.INTENT_RECORDED: {ActionPhase.INPUT_STARTED, ActionPhase.FAILED},
            ActionPhase.INPUT_STARTED: {ActionPhase.INPUT_COMPLETED, ActionPhase.FAILED, ActionPhase.UNCERTAIN},
            ActionPhase.INPUT_COMPLETED: {ActionPhase.CONFIRMED, ActionPhase.FAILED, ActionPhase.UNCERTAIN},
        }
        if type(expected) is not ActionPhase or type(target) is not ActionPhase or target not in allowed.get(expected, set()):
            raise SessionAuthorityError("invalid action phase transition")
        if reason_code is not None and (type(reason_code) is not str or _REFERENCE.fullmatch(reason_code) is None):
            raise SessionAuthorityError("action reason is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transactions WHERE transaction_ref=?", (transaction_ref,)
            ).fetchone()
            if row is None or row["phase"] != expected.value:
                raise SessionAuthorityError(f"action is not {expected.value}")
            connection.execute(
                "UPDATE transactions SET phase=?,reason_code=? WHERE transaction_ref=?",
                (target.value, reason_code, transaction_ref),
            )
            if target in (ActionPhase.CONFIRMED, ActionPhase.FAILED, ActionPhase.UNCERTAIN):
                connection.execute(
                    "UPDATE runs SET game_outcome=? WHERE run_nonce=?",
                    (target.value, row["run_nonce"]),
                )
                self._finalize_ack(connection, row["run_nonce"])
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.transaction(transaction_ref)

    @staticmethod
    def _finalize_ack(connection: sqlite3.Connection, run_nonce: str) -> None:
        connection.execute(
            "UPDATE runs SET final_acknowledged=1 WHERE run_nonce=? AND game_outcome IS NOT NULL AND retirement_outcome IS NOT NULL",
            (run_nonce,),
        )

    def record_retirement(self, run_nonce: str, *, succeeded: bool) -> SessionStatus:
        if type(succeeded) is not bool:
            raise SessionAuthorityError("retirement outcome is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_nonce=?", (run_nonce,)).fetchone()
            if row is None:
                raise SessionAuthorityError("run is unavailable")
            outcome = "SUCCEEDED" if succeeded else "FAILED"
            if row["retirement_outcome"] not in (None, outcome):
                raise SessionAuthorityError("retirement outcome conflicts")
            connection.execute(
                "UPDATE runs SET retirement_outcome=? WHERE run_nonce=?", (outcome, run_nonce)
            )
            self._finalize_ack(connection, run_nonce)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def status(self) -> SessionStatus:
        connection = self._open()
        if connection.execute("PRAGMA user_version").fetchone()[0] == 3:
            raise SessionAuthorityError("legacy status is unavailable for schema 3; read outcome_v3/closure_v3")
        configuration = connection.execute(
            "SELECT revision FROM configuration WHERE singleton=1"
        ).fetchone()
        control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
        if configuration is None or control is None:
            raise SessionAuthorityError("local MVP is not configured")
        run = None
        transaction = None
        if control["current_run_nonce"] is not None:
            run = connection.execute(
                "SELECT * FROM runs WHERE run_nonce=?", (control["current_run_nonce"],)
            ).fetchone()
            transaction = connection.execute(
                "SELECT * FROM transactions WHERE run_nonce=?", (control["current_run_nonce"],)
            ).fetchone()
        return SessionStatus(
            ControlMode(control["mode"]),
            True,
            control["current_run_nonce"],
            configuration["revision"],
            control["revision"],
            bool(run["admission_consumed"]) if run is not None else False,
            ActionPhase(transaction["phase"]) if transaction is not None else None,
            run["game_outcome"] if run is not None else None,
            run["retirement_outcome"] if run is not None else None,
            bool(run["final_acknowledged"]) if run is not None else False,
        )

    def configuration(self) -> MvpConfiguration:
        row = self._open().execute(
            "SELECT team_ref,account_ref,instance_ref,player_tag_sha256 FROM configuration WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise SessionAuthorityError("local MVP is not configured")
        return self._configuration(
            MvpConfiguration(
                row["team_ref"],
                row["account_ref"],
                row["instance_ref"],
                row["player_tag_sha256"],
            )
        )


def run_reversible_transaction(
    authority: DurableSessionAuthority,
    transaction_ref: str,
    *,
    execute: Callable[[], bool],
    confirm: Callable[[], bool],
) -> DurableTransaction:
    """Exercise one injected transaction without holding SQLite across callbacks."""
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.PLANNED,
        target=ActionPhase.INTENT_RECORDED,
    )
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INTENT_RECORDED,
        target=ActionPhase.INPUT_STARTED,
    )
    try:
        executed = execute()
    except BaseException:
        authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_STARTED,
            target=ActionPhase.UNCERTAIN,
            reason_code="EXECUTOR_EXCEPTION",
        )
        raise
    if executed is not True:
        return authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_STARTED,
            target=ActionPhase.FAILED,
            reason_code="INPUT_FAILED",
        )
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INPUT_STARTED,
        target=ActionPhase.INPUT_COMPLETED,
    )
    try:
        confirmed = confirm()
    except BaseException:
        return authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_COMPLETED,
            target=ActionPhase.UNCERTAIN,
            reason_code="CONFIRMATION_UNAVAILABLE",
        )
    return authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INPUT_COMPLETED,
        target=(
            ActionPhase.CONFIRMED if confirmed is True else ActionPhase.UNCERTAIN
        ),
        reason_code=None if confirmed is True else "POSTCONDITION_UNCONFIRMED",
    )


__all__ = [
    "ActionPhase",
    "ControlMode",
    "DurableSessionAuthority",
    "DurableTransaction",
    "SessionAuthorityError",
    "SessionGrant",
    "SessionStatus",
    "run_reversible_transaction",
    "RunBindingV3",
    "StoppedProofV3",
    "FactV3",
    "InputV3",
    "SnapshotV3",
    "AuthorityStatusV3",
    "OutcomeV3",
    "RetirementV3",
    "ClosureProofV3",
    "ClosureV3",
    "canonical_v3",
    "digest_v3",
]
