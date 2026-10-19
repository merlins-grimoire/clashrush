"""Real SQLite policy tests; synthetic reduced evidence is not native proof."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import json
from dataclasses import asdict, replace, FrozenInstanceError

import pytest

from clash_rush_rebuild.mvp_session_authority import (
    RunBindingV3, StoppedProofV3, FactV3, InputV3, SnapshotV3,
    OutcomeV3, RetirementV3, ClosureProofV3, canonical_v3,
)

from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_session_authority import (
    ActionPhase, ControlMode, DurableSessionAuthority, SessionAuthorityError,
)


def legacy_store(tmp_path):
    store = DurableSessionAuthority(tmp_path / "session.sqlite3")
    store.setup(MvpConfiguration("team", "account", "slot-0", "a" * 64))
    grant = store.run(purpose="MVP_ACCOUNT_READINESS", candidate_tree="a" * 40,
                      ready_bytes=b"synthetic-ready", deadline=200, now=100, nonce="1" * 32)
    return store, grant


def test_pending_predecessor_blocks_fresh_run(tmp_path):
    # Baseline branch exercises the existing implementation, not a missing API.
    if not hasattr(DurableSessionAuthority, "reserve_v3"):
        store, _ = legacy_store(tmp_path)
        store.command(ControlMode.STOPPED, command_id="stop")
        denied = False
        try:
            store.run(purpose="MVP_ACCOUNT_READINESS", candidate_tree="a" * 40,
                      ready_bytes=b"synthetic-ready", deadline=250, now=150, nonce="2" * 32)
        except SessionAuthorityError:
            denied = True
        assert denied, "pending predecessor was silently replaced"
    else:
        store, binding = v3_store(tmp_path)
        with pytest.raises(SessionAuthorityError, match="predecessor"):
            store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
        assert store._open().execute("SELECT count(*) FROM run_v3").fetchone()[0] == 1


def test_failed_retirement_never_means_closed(tmp_path):
    if not hasattr(DurableSessionAuthority, "reserve_v3"):
        store, grant = legacy_store(tmp_path)
        store.admit(run_nonce=grant.run_nonce, transaction_ref="tx", purpose="MVP_ACCOUNT_READINESS",
                    candidate_tree="a" * 40, ready_bytes=b"synthetic-ready",
                    configuration_revision=1, control_revision=grant.control_revision, now=101)
        store.transition_action("tx", expected=ActionPhase.PLANNED, target=ActionPhase.FAILED)
        store.record_retirement(grant.run_nonce, succeeded=False)
        assert store.status().final_acknowledged is False, "failed retirement was acknowledged as closed"
    else:
        store, binding = v3_store(tmp_path)
        result = store.finalize_v3(binding, writer_epoch=1, outcome="BLOCKED", reason="PRODUCER_LOST")
        assert result.outcome == "BLOCKED"
        assert store.closure_v3(binding.generation).closed == 0


def stopped():
    return StoppedProofV3(*("a" * 64 for _ in range(8)))


def binding_v3():
    return RunBindingV3(1, "1" * 32, "2" * 32, 1, 2, "a" * 40,
                        "b" * 64, "c" * 64, "d" * 64, "e" * 64, "account",
                        "f" * 64, "synthetic-clock", 100, 200, 0)


def next_binding(binding):
    return replace(binding, generation=binding.generation + 1, run_nonce="3" * 32,
                   transaction_ref="4" * 32, approval_digest="5" * 64, control_revision=3)


def v3_store(tmp_path, *, consume=False):
    store = DurableSessionAuthority(tmp_path / "session.sqlite3")
    store.setup(MvpConfiguration("team", "account", "slot-0", "a" * 64))
    store._migrate_stopped_v3(proof=stopped(), backup_path=tmp_path / "backup.sqlite3")
    binding = binding_v3()
    store.reserve_v3(binding, writer_epoch=1, proof=stopped())
    if consume:
        store.consume_v3(binding, producer_epoch=1, now_ns=101)
    return store, binding


def fact(binding, kind, seq, when, **payload):
    return FactV3(kind, seq, when, canonical_v3({"schema": 3, "binding_digest": binding.digest, **payload}))


def snapshot(binding, *, inputs=None):
    if inputs is None:
        inputs = tuple(InputV3(1, n, kind, "TERMINAL", 6, when, when+1, when+2,
                               f"synthetic-{n}", "a" * 64, "b" * 64, 1, 0, "OBSERVED")
                       for n, kind, when in ((1, "DOWN", 130), (2, "UP", 140)))
    result = fact(binding, "NATURAL_RESULT", 1, 120, observation_digest="1" * 64, natural=True)
    returned = fact(binding, "RETURN_HOME", 1, 150, observation_digest="2" * 64,
                    result_digest=result.digest, gesture_seq=max(i.gesture_seq for i in inputs))
    home = fact(binding, "SAME_ACCOUNT_HOME", 1, 160, observation_digest="3" * 64,
                return_digest=returned.digest, account_ref=binding.account_ref, fresh=True)
    facts = [result, returned, home]
    for n, event in enumerate(inputs, 1):
        facts.extend((fact(binding, "APPROVAL_CHECK", n, event.submission_started_ns-1,
                           approval_digest=binding.approval_digest, revocation_epoch=0),
                      fact(binding, "BUILD_CHECK", n, event.submission_started_ns-1,
                           provider_digest=binding.provider_digest, runner_manifest_digest=binding.runner_manifest_digest,
                           source_tree=binding.source_tree, build_evidence_digest="6"*64),
                      fact(binding, "INPUT_RECEIPT", n, event.completed_ns,
                           input_digest=event.digest, approval_sequence=n, build_sequence=n)))
    seal = fact(binding, "PRE_D_SEAL", 1, 170,
                facts=[[f.kind, f.sequence, f.digest] for f in sorted(facts, key=lambda f: (f.kind, f.sequence))],
                inputs=[[i.gesture_seq, i.event_seq, i.digest] for i in inputs], emergency_release_required=False)
    return SnapshotV3(binding, 1, tuple(facts) + (seal,), inputs)


def success(store, binding):
    return store.finalize_v3(binding, writer_epoch=1, outcome="SUCCESS", reason="PRE_D_COMPLETE")


def retirement(binding, role, *, recovery=False):
    return RetirementV3(role, "7" * 64, canonical_v3({"schema": 3, "binding_digest": binding.digest,
        "identity_digest": "7" * 64, "topology_digest": "8" * 64, "absence_digest": "9" * 64,
        "disposition": "ABSENT" if role == "WORKER" else ("EXPLICIT_RECOVERY_ABSENT" if recovery else "RETIRED_PRE_D"),
        "observed_ns": 250 if role == "WORKER" or recovery else 180}))


def closure_proof(binding, *, recovery=False, emergency=0):
    return ClosureProofV3(binding.digest, "OWNED_RELEASE_NO_PLAYER_KILL_V1", "8" * 64,
                          "ATTEMPTED" if emergency else "NOT_REQUIRED", emergency,
                          "EXPLICIT_RECOVERY_ABSENT" if recovery else "RETIRED_PRE_D",
                          "a" * 64, "b" * 64, "c" * 64 if recovery else None)


def retire(store, binding, *, recovery=False, epoch=1):
    for role in ("WORKER", "PLAYER"):
        store.record_retirement_v3(binding, retirement(binding, role, recovery=recovery), writer_epoch=epoch)


def test_complete_pre_d_success_is_historical_not_closed(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    result = success(store, binding)
    assert result.outcome == "SUCCESS"
    assert success(store, binding) == result
    assert store.closure_v3(1).closed == 0
    with pytest.raises(SessionAuthorityError, match="predecessor"):
        store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
    retire(store, binding)
    closed = store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))
    assert closed.closed == 1
    assert store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding)) == closed
    store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
    assert store._open().execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == 1
    assert store._open().execute("SELECT count(*) FROM run_v3").fetchone()[0] == 2


@pytest.mark.parametrize("terminal,reason", [("FAILED", "GAMEPLAY_FAILED"), ("UNCERTAIN", "INPUT_UNCERTAIN"),
    ("BLOCKED", "PRODUCER_LOST"), ("CANCELED", "CANCELED")])
def test_terminal_non_success_cannot_be_promoted(tmp_path, terminal, reason):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    first = store.finalize_v3(binding, writer_epoch=1, outcome=terminal, reason=reason)
    with pytest.raises(SessionAuthorityError, match="TERMINAL_CONFLICT"):
        success(store, binding)
    assert store.outcome_v3(1) == first
    assert store._open().execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == 1


def test_expired_does_not_override_complete_seal(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    with pytest.raises(SessionAuthorityError, match="EXPIRED_NOT_PROVED"):
        store.finalize_v3(binding, writer_epoch=1, outcome="EXPIRED", reason="DEADLINE_INCOMPLETE")
    assert success(store, binding).outcome == "SUCCESS"


def test_late_evidence_cannot_be_added_after_terminal(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    original = store.finalize_v3(binding, writer_epoch=1, outcome="UNCERTAIN", reason="PRODUCER_LOST")
    with pytest.raises(SessionAuthorityError):
        store.record_snapshot_v3(snapshot(binding))
    assert store.outcome_v3(1) == original
    assert store._open().execute("SELECT count(*) FROM fact_v3").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["deadline", "missing", "duplicate", "account", "approval", "binding", "seal", "input", "natural"])
def test_invalid_seal_transaction_rolls_back_all_rows(tmp_path, change):
    store, binding = v3_store(tmp_path, consume=True)
    snap = snapshot(binding)
    facts = list(snap.facts)
    inputs = snap.inputs
    if change == "deadline":
        facts[0] = replace(facts[0], observed_ns=binding.deadline_ns)
    elif change == "missing":
        facts.pop(0)
    elif change == "duplicate":
        facts.append(facts[0])
    elif change == "input":
        inputs = (replace(inputs[0], uncertainty=1, release_state="UNCERTAIN"), inputs[1])
    else:
        index = {"account": 2, "approval": 3, "binding": 0, "seal": -1, "natural": 0}[change]
        raw = json.loads(facts[index].payload)
        raw.update({"account": {"account_ref": "wrong"}, "approval": {"approval_digest": "0"*64},
                    "binding": {"binding_digest": "0"*64}, "seal": {"emergency_release_required": True},
                    "natural": {"natural": False}}[change])
        facts[index] = replace(facts[index], payload=canonical_v3(raw))
    with pytest.raises(SessionAuthorityError):
        store.record_snapshot_v3(replace(snap, facts=tuple(facts), inputs=inputs))
    for table in ("fact_v3", "input_v3", "outcome_v3", "closure_v3"):
        assert store._open().execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("generation", True), ("started_ns", 1.0), ("run_nonce", "A"*32),
    ("transaction_ref", "legacy-tx"), ("source_tree", "x"*40), ("provider_digest", "0"*63), ("deadline_ns", 100)])
def test_exact_binding_ingress(field, value):
    with pytest.raises(SessionAuthorityError):
        replace(binding_v3(), **{field: value})


def test_frozen_closed_payloads_reject_unknown_boolean_and_noncanonical():
    b = binding_v3()
    with pytest.raises(FrozenInstanceError):
        b.generation = 2
    good = fact(b, "NATURAL_RESULT", 1, 120, observation_digest="a"*64, natural=True)
    raw = json.loads(good.payload)
    for bad in ({**raw, "extra": 1}, {**raw, "schema": True}, {**raw, "natural": 1}):
        with pytest.raises(SessionAuthorityError):
            replace(good, payload=canonical_v3(bad))
    with pytest.raises(SessionAuthorityError):
        replace(good, payload=json.dumps(raw).encode())
    with pytest.raises(SessionAuthorityError):
        canonical_v3({"nan": float("nan")})


def test_readback_failure_preserves_outcome_and_denies_next_run(tmp_path, monkeypatch):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    original = store._readback_v3
    def fault(table, generation):
        if table == "outcome_v3":
            raise OSError("synthetic readback fault")
        return original(table, generation)
    monkeypatch.setattr(store, "_readback_v3", fault)
    with pytest.raises(OSError):
        success(store, binding)
    assert store._open().execute("SELECT outcome FROM outcome_v3").fetchone()[0] == "SUCCESS"
    row = store._open().execute("SELECT closed,outcome_readback_digest FROM closure_v3").fetchone()
    assert tuple(row) == (0, None)
    with pytest.raises(SessionAuthorityError, match="predecessor"):
        store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
    monkeypatch.setattr(store, "_readback_v3", original)
    assert success(store, binding).outcome == "SUCCESS"


def test_closure_requires_player_and_explicit_uncertainty_recovery(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    first = store.finalize_v3(binding, writer_epoch=1, outcome="UNCERTAIN", reason="EMERGENCY_RELEASE")
    store.record_retirement_v3(binding, retirement(binding, "WORKER"), writer_epoch=1)
    with pytest.raises(SessionAuthorityError, match="RETIREMENT_MISSING"):
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding, emergency=1))
    store.record_retirement_v3(binding, retirement(binding, "PLAYER", recovery=True), writer_epoch=1)
    with pytest.raises(SessionAuthorityError):
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))
    store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding, recovery=True, emergency=1))
    assert store.outcome_v3(1) == first
    store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())


def test_uncertain_input_cannot_close_failed_without_recovery(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    uncertain = replace(snapshot(binding).inputs[0], uncertainty=1, release_state="UNCERTAIN")
    store.record_snapshot_v3(SnapshotV3(binding, 1, (), (uncertain,)))
    store.finalize_v3(binding, writer_epoch=1, outcome="FAILED", reason="GAMEPLAY_FAILED")
    retire(store, binding)
    with pytest.raises(SessionAuthorityError, match="RECOVERY"):
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))
    assert store.closure_v3(1).closed == 0


def test_triggers_enforce_immutable_rows_and_legacy_writer_denial(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    success(store, binding)
    retire(store, binding)
    for sql in ("UPDATE outcome_v3 SET outcome='FAILED'", "DELETE FROM outcome_v3", "DELETE FROM fact_v3",
                "UPDATE fact_v3 SET observed_ns=199", "DELETE FROM retirement_v3", "UPDATE run_v3 SET deadline_ns=300",
                "UPDATE input_v3 SET uncertainty=1", "UPDATE control SET mode='RUNNING'", "DELETE FROM configuration",
                "INSERT OR REPLACE INTO outcome_v3 SELECT * FROM outcome_v3",
                "INSERT OR REPLACE INTO input_v3 SELECT * FROM input_v3",
                "INSERT OR REPLACE INTO run_v3 SELECT * FROM run_v3"):
        with pytest.raises(sqlite3.IntegrityError):
            store._open().execute(sql)
    assert store.outcome_v3(1).outcome == "SUCCESS"


def test_event_cas_and_sticky_effect_uncertainty(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    event = InputV3(1, 1, "DOWN", "PROPOSED", 1, None, None, None, "marker", "a"*64, "b"*64, 0, 0, "NOT_REQUIRED")
    store.record_input_v3(binding, event, producer_epoch=1, expected_version=None)
    event = replace(event, phase="AUTHORIZED", state_version=2)
    store.record_input_v3(binding, event, producer_epoch=1, expected_version=1)
    with pytest.raises(SessionAuthorityError, match="CAS"):
        store.record_input_v3(binding, event, producer_epoch=1, expected_version=1)
    event = replace(event, phase="SUBMITTING", state_version=3, submission_started_ns=120, effect_possible=1, release_state="REQUIRED")
    store.record_input_v3(binding, event, producer_epoch=1, expected_version=2)
    event = replace(event, phase="TERMINAL", state_version=4, uncertainty=1, release_state="UNCERTAIN")
    store.record_input_v3(binding, event, producer_epoch=1, expected_version=3)
    with pytest.raises(SessionAuthorityError):
        store.record_input_v3(binding, replace(event, state_version=5, uncertainty=0, release_state="OBSERVED"), producer_epoch=1, expected_version=4)
    assert store._open().execute("SELECT effect_possible,uncertainty FROM input_v3").fetchone()[:] == (1, 1)


def test_exact_binding_and_stale_epoch_rejected(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    for name, value in (("clock_domain", "other-clock"), ("provider_digest", "0"*64), ("control_revision", 9), ("transaction_ref", "9"*32)):
        with pytest.raises(SessionAuthorityError, match="BINDING"):
            success(store, replace(binding, **{name: value}))
    assert store.reconcile_epoch_v3(expected_epoch=1, mutex_ownership_digest="a"*64,
                predecessor_inspection_digest="b"*64, worker_absence_digest="c"*64) == 2
    with pytest.raises(SessionAuthorityError, match="STALE_EPOCH"):
        success(store, binding)
    with pytest.raises(SessionAuthorityError, match="STALE_EPOCH"):
        store.record_snapshot_v3(snapshot(binding))
    result = store.finalize_v3(binding, writer_epoch=2, outcome="SUCCESS", reason="PRE_D_COMPLETE")
    assert result.finalizer_epoch == 2


def test_restart_cannot_restamp_lost_snapshot_with_new_epoch(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.reconcile_epoch_v3(expected_epoch=1, mutex_ownership_digest="a"*64,
                            predecessor_inspection_digest="b"*64, worker_absence_digest="c"*64)
    with pytest.raises(SessionAuthorityError, match="PRODUCER_REVOKED"):
        store.record_snapshot_v3(replace(snapshot(binding), producer_epoch=2))
    assert store._open().execute("SELECT count(*) FROM fact_v3").fetchone()[0] == 0


@pytest.mark.parametrize("observed", [None, 199, 200, 201])
def test_expiry_requires_known_deadline_not_administrative_lateness(tmp_path, observed):
    store, binding = v3_store(tmp_path, consume=True)
    kwargs = dict(writer_epoch=1, outcome="EXPIRED", reason="DEADLINE_INCOMPLETE", expiry_observed_ns=observed)
    if observed is None or observed < 200:
        with pytest.raises(SessionAuthorityError, match="EXPIRED_NOT_PROVED"):
            store.finalize_v3(binding, **kwargs)
        assert store.outcome_v3(1) is None
    else:
        original = store.finalize_v3(binding, **kwargs)
        with pytest.raises(SessionAuthorityError):
            store.record_snapshot_v3(snapshot(binding))
        assert store.finalize_v3(binding, **kwargs) == original
        assert original.outcome == "EXPIRED"
        retire(store, binding)
        assert store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding)).closed == 1


@pytest.mark.parametrize("kind,key,value,reason", [
    ("SAME_ACCOUNT_HOME", "account_ref", "other", "SEMANTIC_CHAIN"),
    ("SAME_ACCOUNT_HOME", "fresh", False, "SEMANTIC_CHAIN"),
    ("RETURN_HOME", "result_digest", "0"*64, "SEMANTIC_CHAIN"),
    ("NATURAL_RESULT", "natural", False, "SEMANTIC_CHAIN"),
    ("BUILD_CHECK", "provider_digest", "0"*64, "APPROVAL_BUILD_BINDING"),
    ("APPROVAL_CHECK", "revocation_epoch", 1, "APPROVAL_BUILD_BINDING"),
    ("INPUT_RECEIPT", "input_digest", "0"*64, "INPUT_RECEIPT_BINDING"),
])
def test_semantic_validation_even_with_rehashed_manifest(tmp_path, kind, key, value, reason):
    store, binding = v3_store(tmp_path, consume=True)
    snap = snapshot(binding)
    facts = []
    for f in snap.facts[:-1]:
        raw = json.loads(f.payload)
        if f.kind == kind:
            raw[key] = value
        facts.append(replace(f, payload=canonical_v3(raw)))
    seal = fact(binding, "PRE_D_SEAL", 1, 170,
                facts=[[f.kind, f.sequence, f.digest] for f in sorted(facts, key=lambda f: (f.kind, f.sequence))],
                inputs=[[i.gesture_seq, i.event_seq, i.digest] for i in snap.inputs], emergency_release_required=False)
    with pytest.raises(SessionAuthorityError, match=reason):
        store.record_snapshot_v3(replace(snap, facts=tuple(facts)+(seal,)))
    assert store.outcome_v3(1) is None


def test_missing_up_row_cannot_close_by_label_alone(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    down = snapshot(binding).inputs[0]  # OBSERVED label is not a matching UP.
    store.record_snapshot_v3(SnapshotV3(binding, 1, (), (down,)))
    store.finalize_v3(binding, writer_epoch=1, outcome="FAILED", reason="GAMEPLAY_FAILED")
    retire(store, binding)
    with pytest.raises(SessionAuthorityError, match="RECOVERY"):
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))


def test_lost_hook_cannot_be_terminal_without_sticky_uncertainty():
    with pytest.raises(SessionAuthorityError, match="TERMINAL_OBSERVATION_REQUIRED"):
        replace(snapshot(binding_v3()).inputs[0], hook_observed_ns=None)


def test_schema_and_pragmas_exact_columns(tmp_path):
    store, _ = v3_store(tmp_path)
    c = store._open()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    expected = {
        "authority_v3": "singleton schema_version writer_epoch current_generation revocation_epoch",
        "run_v3": "generation run_nonce transaction_ref configuration_revision control_revision source_tree runner_manifest_digest profile_digest approval_digest provider_digest account_ref binding_digest clock_domain started_ns deadline_ns admission revocation_epoch legacy_block",
        "fact_v3": "generation kind sequence observed_ns payload payload_digest producer_epoch",
        "input_v3": "generation gesture_seq event_seq event_kind phase state_version submission_started_ns hook_observed_ns completed_ns marker target_digest observation_digest effect_possible uncertainty release_state",
        "outcome_v3": "generation outcome evidence_digest reason finalizer_epoch",
        "retirement_v3": "generation role identity_digest proof proof_digest",
        "closure_v3": "generation outcome_readback_digest worker_retired_digest player_retired_digest lifecycle_readback_digest input_clear_digest closed closure_digest containment_policy topology_digest release_stage emergency_release_required player_disposition recovery_receipt_digest",
    }
    for table, names in expected.items():
        assert [row[1] for row in c.execute(f"PRAGMA table_info({table})")] == names.split()


@pytest.mark.parametrize("wrong_release", [False, True])
def test_key_chord_requires_every_exact_control_release(tmp_path, wrong_release):
    store, binding = v3_store(tmp_path, consume=True)
    keyboard = tuple(InputV3(1, n, kind, "TERMINAL", 6, when, when, when,
                            f"key-{n}", target*64, "b"*64, 1, 0, "OBSERVED")
                     for n, kind, when, target in ((1, "KEY_DOWN", 110, "c"), (2, "KEY_DOWN", 112, "d"),
                                                  (3, "KEY_UP", 114, "d"), (4, "KEY_UP", 116, "c")))
    if wrong_release:
        keyboard = keyboard[:-1] + (replace(keyboard[-1], target_digest="e"*64),)
    click = tuple(replace(i, gesture_seq=2) for i in snapshot(binding).inputs)
    snap = snapshot(binding, inputs=keyboard+click)
    if wrong_release:
        with pytest.raises(SessionAuthorityError, match="RELEASE_PAIR"):
            store.record_snapshot_v3(snap)
    else:
        store.record_snapshot_v3(snap)
        assert success(store, binding).outcome == "SUCCESS"
        retire(store, binding)
        assert store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding)).closed == 1
