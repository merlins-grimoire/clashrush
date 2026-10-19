"""SQLite-only subprocess crashes; no Win32/game/input/process-topology claims."""
from __future__ import annotations

from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_session_authority import (
    ControlMode, DurableSessionAuthority, SessionAuthorityError, SnapshotV3,
)
from test_policy_finalization_v3 import (
    binding_v3, closure_proof, legacy_store, next_binding, retire, snapshot,
    stopped, success, v3_store,
)


def crash_writer(path, binding, stage, after_commit, pipe):
    store = DurableSessionAuthority(Path(path))
    original = store._commit_v3
    def crash_commit():
        if after_commit:
            original()
        pipe.send("COMMITTED" if after_commit else "UNCOMMITTED")
        assert pipe.recv() == "CRASH"
        os._exit(23)  # Real abrupt SQLite writer loss, no cleanup/rollback code.
    store._commit_v3 = crash_commit
    if stage == "snapshot":
        store.record_snapshot_v3(snapshot(binding))
    elif stage == "outcome":
        success(store, binding)
    elif stage == "closure":
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))
    else:
        store._migrate_stopped_v3(proof=stopped(), backup_path=Path(path).with_name("backup.sqlite3"))


def finalizer_peer(path, binding, outcome, barrier, pipe):
    store = DurableSessionAuthority(Path(path))
    barrier.wait(timeout=20)
    try:
        result = store.finalize_v3(binding, writer_epoch=1, outcome=outcome,
                                   reason="PRE_D_COMPLETE" if outcome == "SUCCESS" else "GAMEPLAY_FAILED")
        pipe.send(("OK", result))
    except SessionAuthorityError as exc:
        pipe.send(("DENIED", str(exc)))
    finally:
        store.close()


def join_peer(process):
    process.join(20)
    assert not process.is_alive(), "SQLite-only peer did not reach its bounded exit"


@pytest.mark.parametrize("stage", ["snapshot", "outcome", "closure", "migration"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_actual_writer_exit_at_transaction_boundaries(tmp_path, stage, after_commit):
    path = tmp_path / "session.sqlite3"
    if stage == "migration":
        store = DurableSessionAuthority(path)
        store.setup(MvpConfiguration("team", "account", "slot-0", "a" * 64))
        binding = binding_v3()
    else:
        store, binding = v3_store(tmp_path, consume=True)
        if stage in ("outcome", "closure"):
            store.record_snapshot_v3(snapshot(binding))
        if stage == "closure":
            success(store, binding)
            retire(store, binding)
    store.close()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=crash_writer, args=(str(path), binding, stage, after_commit, child))
    process.start()
    try:
        assert parent.poll(20), "writer never reached transaction barrier"
        assert parent.recv() == ("COMMITTED" if after_commit else "UNCOMMITTED")
        assert process.is_alive()
        parent.send("CRASH")
        join_peer(process)
        assert process.exitcode == 23
    finally:
        parent.close()
        child.close()
    reopened = DurableSessionAuthority(path)
    c = reopened._open()
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    if stage == "migration":
        assert c.execute("PRAGMA user_version").fetchone()[0] == (3 if after_commit else 1)
        assert bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='authority_v3'").fetchone()) is after_commit
        backup = sqlite3.connect(tmp_path / "backup.sqlite3")
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        backup.close()
    elif stage == "snapshot":
        assert c.execute("SELECT count(*) FROM fact_v3 WHERE kind='PRE_D_SEAL'").fetchone()[0] == int(after_commit)
        assert c.execute("SELECT count(*) FROM input_v3").fetchone()[0] == (2 if after_commit else 0)
        if after_commit:
            assert success(reopened, binding).outcome == "SUCCESS"
        else:
            with pytest.raises(SessionAuthorityError):
                success(reopened, binding)
            result = reopened.finalize_v3(binding, writer_epoch=1, outcome="UNCERTAIN", reason="PRODUCER_LOST")
            assert result.outcome == "UNCERTAIN"
    elif stage == "outcome":
        assert c.execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == int(after_commit)
        assert c.execute("SELECT count(*) FROM closure_v3").fetchone()[0] == int(after_commit)
        if after_commit:
            assert c.execute("SELECT outcome_readback_digest FROM closure_v3").fetchone()[0] is None
        assert success(reopened, binding).outcome == "SUCCESS"
        assert reopened.closure_v3(1).closed == 0
    else:
        assert reopened.outcome_v3(1).outcome == "SUCCESS"
        assert reopened.closure_v3(1).closed == int(after_commit)
        if after_commit:
            reopened.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
        else:
            with pytest.raises(SessionAuthorityError, match="predecessor"):
                reopened.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
    reopened.close()


@pytest.mark.parametrize("conflicting", [False, True])
def test_real_concurrent_finalizer_processes(tmp_path, conflicting):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    pairs = [context.Pipe() for _ in range(2)]
    processes = [context.Process(target=finalizer_peer, args=(str(store._path), binding, outcome, barrier, pair[1]))
                 for outcome, pair in zip(("SUCCESS", "FAILED" if conflicting else "SUCCESS"), pairs)]
    for process in processes:
        process.start()
    barrier.wait(timeout=20)
    results = []
    for pair, process in zip(pairs, processes):
        assert pair[0].poll(20)
        results.append(pair[0].recv())
        join_peer(process)
        assert process.exitcode == 0
        pair[0].close()
        pair[1].close()
    assert store._open().execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == 1
    assert store._open().execute("SELECT count(*) FROM closure_v3").fetchone()[0] == 1
    if conflicting:
        assert sorted(r[0] for r in results) == ["DENIED", "OK"]
        assert "TERMINAL_CONFLICT" in next(r[1] for r in results if r[0] == "DENIED")
    else:
        assert results[0] == results[1]
        assert results[0][0] == "OK"


@pytest.mark.parametrize("after_commit", [False, True])
def test_commit_error_never_claims_success(tmp_path, monkeypatch, after_commit):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    commit = store._commit_v3
    def error():
        if after_commit:
            commit()
        raise sqlite3.OperationalError("synthetic commit acknowledgement failure")
    monkeypatch.setattr(store, "_commit_v3", error)
    with pytest.raises(SessionAuthorityError, match="SQLITE_WRITE_FAILED"):
        success(store, binding)
    store.close()
    reopened = DurableSessionAuthority(tmp_path / "session.sqlite3")
    assert reopened._open().execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == int(after_commit)
    assert success(reopened, binding).outcome == "SUCCESS"
    assert reopened.closure_v3(1).closed == 0


def test_closure_readback_error_requires_fresh_read_before_admission(tmp_path, monkeypatch):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    success(store, binding)
    retire(store, binding)
    original = store._readback_v3
    def error(table, generation):
        if table == "closure_v3":
            raise OSError("synthetic closure readback error")
        return original(table, generation)
    monkeypatch.setattr(store, "_readback_v3", error)
    with pytest.raises(OSError):
        store.close_v3(binding, writer_epoch=1, proof=closure_proof(binding))
    assert store._open().execute("SELECT closed FROM closure_v3").fetchone()[0] == 1
    with pytest.raises(OSError):
        store.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())
    assert store._open().execute("SELECT count(*) FROM run_v3").fetchone()[0] == 1
    store.close()
    reopened = DurableSessionAuthority(tmp_path / "session.sqlite3")
    reopened.reserve_v3(next_binding(binding), writer_epoch=1, proof=stopped())


def test_lost_in_memory_seal_is_not_reconstructed(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    lost = snapshot(binding)
    store.record_snapshot_v3(replace(lost, facts=tuple(f for f in lost.facts if f.kind != "PRE_D_SEAL")))
    store.close()
    reopened = DurableSessionAuthority(tmp_path / "session.sqlite3")
    reopened.reconcile_epoch_v3(expected_epoch=1, mutex_ownership_digest="a"*64,
                               predecessor_inspection_digest="b"*64, worker_absence_digest="c"*64)
    with pytest.raises(SessionAuthorityError, match="STALE_EPOCH"):
        reopened.record_snapshot_v3(lost)
    with pytest.raises(SessionAuthorityError, match="CARDINALITY"):
        reopened.finalize_v3(binding, writer_epoch=2, outcome="SUCCESS", reason="PRE_D_COMPLETE")
    result = reopened.finalize_v3(binding, writer_epoch=2, outcome="UNCERTAIN", reason="PRODUCER_LOST")
    assert result.outcome == "UNCERTAIN"
    assert reopened.closure_v3(1).closed == 0


def test_stopped_migration_preserves_legacy_and_blocks_old_writers(tmp_path):
    store, _ = legacy_store(tmp_path)
    with pytest.raises(SessionAuthorityError, match="STOPPED"):
        store._migrate_stopped_v3(proof=stopped(), backup_path=tmp_path / "backup.sqlite3")
    store.command(ControlMode.STOPPED, command_id="stop")
    before = tuple(store._open().execute("SELECT * FROM runs").fetchone())
    old = DurableSessionAuthority(tmp_path / "session.sqlite3")
    store._migrate_stopped_v3(proof=stopped(), backup_path=tmp_path / "backup.sqlite3")
    assert tuple(store._open().execute("SELECT * FROM runs").fetchone()) == before
    assert store._open().execute("SELECT legacy_block FROM run_v3").fetchone()[0] == 1
    assert store._open().execute("SELECT count(*) FROM fact_v3").fetchone()[0] == 0
    with pytest.raises(SessionAuthorityError, match="legacy predecessor"):
        store.reserve_v3(next_binding(binding_v3()), writer_epoch=1, proof=stopped())
    with pytest.raises(sqlite3.IntegrityError, match="legacy writer disabled"):
        old.command(ControlMode.STOPPED, command_id="old-command")
    backup = sqlite3.connect(tmp_path / "backup.sqlite3")
    assert tuple(backup.execute("SELECT * FROM runs").fetchone()) == before
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
    backup.close()


def test_migration_refuses_consumed_legacy_even_if_booleans_look_complete(tmp_path):
    store, grant = legacy_store(tmp_path)
    store.admit(run_nonce=grant.run_nonce, transaction_ref="tx", purpose="MVP_ACCOUNT_READINESS",
                candidate_tree="a"*40, ready_bytes=b"synthetic-ready", configuration_revision=1,
                control_revision=grant.control_revision, now=101)
    store.command(ControlMode.STOPPED, command_id="stop")
    with pytest.raises(SessionAuthorityError, match="LEGACY_REQUIRES_RECOVERY"):
        store._migrate_stopped_v3(proof=stopped(), backup_path=tmp_path / "backup.sqlite3")
    assert store._open().execute("PRAGMA user_version").fetchone()[0] == 2
    assert not (tmp_path / "backup.sqlite3").exists()


def test_schema_tampering_denies_reopen(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    store._open().execute("DROP TRIGGER fact_v3_update")
    store._open().execute("UPDATE fact_v3 SET payload_digest=? WHERE kind='NATURAL_RESULT'", ("0"*64,))
    with pytest.raises(SessionAuthorityError, match="FACT_DIGEST"):
        success(store, binding)
    store.close()
    with pytest.raises(SessionAuthorityError, match="SCHEMA_NONCANONICAL"):
        DurableSessionAuthority(tmp_path / "session.sqlite3")


def test_status_does_not_migrate_legacy(tmp_path):
    store = DurableSessionAuthority(tmp_path / "session.sqlite3")
    store.setup(MvpConfiguration("team", "account", "slot-0", "a"*64))
    before = tuple(store._open().iterdump())
    store.status()
    store.close()
    reopened = DurableSessionAuthority(tmp_path / "session.sqlite3")
    reopened.status()
    assert tuple(reopened._open().iterdump()) == before


def test_real_outcome_transaction_can_cross_synthetic_deadline(tmp_path, monkeypatch):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    entered, release = threading.Event(), threading.Event()
    now = [binding.deadline_ns - 1]
    commit = store._commit_v3
    results = []
    errors = []
    def paused_commit():
        if not entered.is_set():
            assert store._open().in_transaction
            entered.set()
            assert release.wait(10)
            assert now[0] > binding.deadline_ns
        commit()
    monkeypatch.setattr(store, "_commit_v3", paused_commit)
    def finish():
        try:
            results.append(success(store, binding))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=finish)
    thread.start()
    assert entered.wait(10)
    with sqlite3.connect(store._path) as reader:
        assert reader.execute("SELECT count(*) FROM outcome_v3").fetchone()[0] == 0
    now[0] = binding.deadline_ns + 1
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert errors == []
    assert len(results) == 1 and results[0].outcome == "SUCCESS"
    assert store.closure_v3(1).closed == 0


def test_reopened_finalizer_has_no_native_dependency(tmp_path, monkeypatch):
    import builtins
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    store.close()
    original = builtins.__import__
    forbidden = ("ctypes", "win32", "mvp_local_native", "mvp_deployment_input", "mvp_deployment_worker")
    def deny_native(name, *args, **kwargs):
        if any(token in name for token in forbidden):
            raise AssertionError("native dependency reached by pure finalizer: " + name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", deny_native)
    reopened = DurableSessionAuthority(tmp_path / "session.sqlite3")
    assert success(reopened, binding).outcome == "SUCCESS"
    assert reopened.closure_v3(1).closed == 0


def test_schema3_status_is_read_only_and_discovers_restart_binding(tmp_path):
    store, binding = v3_store(tmp_path, consume=True)
    store.record_snapshot_v3(snapshot(binding))
    before = tuple(store._open().iterdump())
    status = store.status_v3()
    assert status.writer_epoch == 1
    assert status.current_generation == 1
    assert status.generations == (1,)
    assert status.legacy_generations == ()
    assert store.read_run_v3(status.generations[0]) == binding
    with pytest.raises(SessionAuthorityError, match="legacy status"):
        store.status()
    assert tuple(store._open().iterdump()) == before
