"""Connected history, phase and publication controls for the CLOSED join."""
from dataclasses import asdict, replace
import json

import pytest
from test_closure_identity_binding import closed_session, snapshot
from test_canonical_closure import connected
from test_calibration_retirement import captured, Host
from test_policy_normal_retirement import session_success, RetirementHost
from test_policy_parent_session import compose, prepare
from test_policy_process_topology import ack_for, release_for
from test_policy_finalization_v3 import next_binding, stopped, snapshot as evidence_snapshot
from test_calibration_mode import scope_for
from canonical_fixture import install_fixture
from clash_rush_rebuild import mvp_canonical_runtime as canonical
from clash_rush_rebuild.lifecycle_state import Ready, LifecycleStateStore
from clash_rush_rebuild.mvp_deployment_worker import PolicyParentSession
from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority, RetirementV3, canonical_v3, digest_v3
from clash_rush_rebuild.mvp_calibration import CalibrationObservation, WindowObservation


def second_visit(old, mutex):
    calibration = old.topology.purpose == 'CALIBRATION_READ_ONLY'
    run = next_binding(old.run)
    scope = scope_for(run) if calibration else None
    if scope is not None: run = replace(run, binding_digest=scope.digest, provider_digest=scope.digest)
    host = Host() if calibration else RetirementHost()
    topology = ParentProcessTopology(host, run_nonce=run.run_nonce, generation=run.generation,
                                     purpose=old.topology.purpose)
    s = PolicyParentSession(authority=old.authority, run=run, writer_epoch=old.epoch,
        state=old.state, topology=topology, runner_manifest_bytes=old.runner_manifest_bytes,
        approval_current=lambda _:True, clock=lambda:1.)
    s.canonical = canonical.open_canonical(old.authority._path.parent)
    if calibration: s.calibration_scope = scope
    offer = s.prepare(mutex=mutex, stopped=stopped(), slot=s.state.load().next_slot,
        worker_executable='worker.exe', worker_command='worker', player_executable='player.exe', player_command='player')
    s.start_worker(mutex=mutex)
    s.authority.consume_v3(run, producer_epoch=s.epoch, now_ns=1_000_000_000)
    s.accept_worker(ack_for(offer), mutex=mutex)
    if calibration:
        window = WindowObservation(offer.player, 80, 81, 1280, 720, True, offer.player, 'f'*32)
        topology.pin_capture(window, 'Synthetic')
        notice = CalibrationObservation(scope.digest, digest_v3(canonical_v3(asdict(offer))),
                                        'CAPTURED', 1_000_000_000, 'e'*64)
        s.authority.record_calibration_observation_v3(run, notice, writer_epoch=s.epoch)
        s.calibration_evidence = lambda *args:notice.evidence_digest
    else:
        s.authority.record_snapshot_v3(evidence_snapshot(run))
    topology.terminate_worker(release_for(topology, offer, host))
    s.finalize(mutex=mutex, outcome='BLOCKED' if calibration else 'SUCCESS',
               reason='EVIDENCE_INCOMPLETE' if calibration else 'PRE_D_COMPLETE')
    identity = digest_v3(canonical_v3(asdict(offer.worker)))
    proof = canonical_v3(dict(schema=3, binding_digest=run.digest, identity_digest=identity,
        topology_digest=digest_v3(canonical_v3(asdict(offer))), absence_digest='a'*64,
        disposition='ABSENT', observed_ns=1_000_000_000))
    s.authority.record_retirement_v3(run, RetirementV3('WORKER', identity, proof), writer_epoch=s.epoch)
    (s.retire_calibration if calibration else s.retire_normal_success)(mutex=mutex, expected_title='Synthetic')
    return s, host


def test_multiple_closed_generations_preserve_history_and_latest_join(closed_session):
    old, host, state, mutex, retire = closed_session
    prior = old.authority.closure_v3(1)
    old_host = old.canonical.anchor.load()
    s, second_host = second_visit(old, mutex)
    assert s.authority.status_v3().generations == (1, 2)
    assert s.authority.closure_v3(1) == prior
    latest = s.canonical.stopped(s.authority, state)
    assert latest['run']['generation'] == 2
    assert state.load() == Ready(0 if s.topology.purpose == 'CALIBRATION_READ_ONLY' else 2)
    # Same P/W/E pairs on the second visit are insufficient to accept the old offer.
    for replacement in (old_host, dict(latest, offer=old_host['offer'])):
        s.canonical.anchor.path.write_bytes(canonical_v3(replacement))
        with pytest.raises(RuntimeError): s.canonical.stopped(s.authority, state)
    s.canonical.anchor.path.write_bytes(canonical_v3(latest))
    for current in (None, 0, 1, 3):
        s.authority._open().execute('UPDATE authority_v3 SET current_generation=?', (current,))
        with pytest.raises(RuntimeError): s.canonical.stopped(s.authority, state)
    s.authority._open().execute('UPDATE authority_v3 SET current_generation=2')
    third = replace(next_binding(s.run), run_nonce='6'*32, transaction_ref='7'*32,
                    approval_digest='8'*64, control_revision=4)
    s.authority.reserve_v3(third, writer_epoch=s.epoch, proof=stopped())
    with pytest.raises(RuntimeError): s.canonical.stopped(s.authority, state)
    assert s.authority.closure_v3(1) == prior


@pytest.mark.parametrize('schema', [1, 2])
def test_exact_zero_history_stopped_is_accepted_without_rewrite(tmp_path, monkeypatch, schema):
    s, host, _, clock, mutex = compose(tmp_path)
    from test_lifecycle_state import RecordingFilePort
    state = LifecycleStateStore(tmp_path, RecordingFilePort())
    state.initialize_ready()
    admission = install_fixture(tmp_path, monkeypatch)
    record = admission.anchor.load()
    if schema == 2: record.update(schema=2, offer=None)
    admission.anchor.path.write_bytes(canonical_v3(record))
    try:
        before = admission.anchor.path.read_bytes()
        assert admission.stopped(s.authority, state) == record
        assert admission.anchor.path.read_bytes() == before
        assert not host.calls
    finally: s.authority.close()


@pytest.mark.parametrize('lane', ['normal', 'calibration'])
@pytest.mark.parametrize('role', ['WORKER', 'PLAYER'])
@pytest.mark.parametrize('fault', ['identity', 'topology', 'binding', 'missing'])
def test_preseal_role_conflicts_cannot_publish_closed(connected, monkeypatch, lane, role, fault):
    original = DurableSessionAuthority.record_retirement_v3
    def changed(self, run, proof, **kwargs):
        if proof.role == role:
            if fault == 'missing': return
            raw = json.loads(proof.proof)
            field = {'identity':'identity_digest', 'topology':'topology_digest', 'binding':'binding_digest'}[fault]
            raw[field] = 'b'*64
            proof = RetirementV3(role, raw['identity_digest'], canonical_v3(raw))
        return original(self, run, proof, **kwargs)
    monkeypatch.setattr(DurableSessionAuthority, 'record_retirement_v3', changed)
    s = None
    try:
        with pytest.raises(RuntimeError):
            s, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(connected)
            (s.retire_calibration if lane == 'calibration' else s.retire_normal_success)(mutex=mutex, expected_title='Synthetic')
        raw = json.loads((connected / 'synthetic-host/var/occupancy.json').read_bytes())
        assert raw['phase'] != 'CLOSED'
        if s is not None:
            calls = tuple(host.calls)
            with pytest.raises(RuntimeError):
                (s.retire_calibration if lane == 'calibration' else s.retire_normal_success)(mutex=mutex, expected_title='Synthetic')
            assert tuple(host.calls) == calls
    finally:
        if s is not None: s.authority.close()


@pytest.mark.parametrize('cut', ['before', 'after', 'readback'])
def test_offer_publication_failure_never_resumes(connected, monkeypatch, cut):
    # Common bootstrap path applies equally before either lane's worker dispatch.
    s, host, state, clock, mutex = compose(connected)
    # Imported compose is the unwrapped test helper; attach the real anchor.
    s.canonical = install_fixture(connected, monkeypatch)
    original = s.canonical.published
    def publish(*args):
        if cut != 'before': original(*args)
        raise RuntimeError('synthetic publication cut')
    if cut == 'readback':
        read = s.canonical.anchor._read
        def broken(path):
            value = read(path)
            if path == s.canonical.anchor.path and json.loads(value)['phase'] == 'PUBLISHED':
                return b'corrupt'
            return value
        s.canonical.anchor._read = broken
    else: s.canonical.published = publish
    try:
        with pytest.raises(RuntimeError): prepare(s, mutex)
        assert not [c for c in host.calls if c[0] in ('resume', 'kill')]
        raw = json.loads(s.canonical.anchor.path.read_bytes())
        assert raw['phase'] == ('PLAYER_PENDING' if cut == 'before' else 'PUBLISHED')
        if cut != 'before': assert raw['offer'] == asdict(s.topology.offer)
        if cut == 'readback': assert s.canonical.anchor.guard.exists()
    finally:
        s.dispose(mutex=mutex)
        s.authority.close()
