"""Real SQLite/common bootstrap + disposable durable host file tests."""
from dataclasses import asdict

import pytest

from canonical_fixture import install_fixture, phase_record
from test_policy_parent_session import compose, prepare
from test_policy_process_topology import ack_for
from clash_rush_rebuild.mvp_session_authority import canonical_v3
from clash_rush_rebuild.mvp_canonical_runtime import CanonicalAdmission, CanonicalRuntimeError


def test_host_intent_precedes_local_reservation_and_both_creates(tmp_path, monkeypatch):
    session, host, state, clock, mutex = compose(tmp_path)
    admission = install_fixture(tmp_path, monkeypatch)
    session.canonical = admission
    events = []
    reserve = session.authority.reserve_v3
    create = host.create_policy_suspended
    def checked_reserve(*a, **kw):
        assert admission.anchor.load()['phase'] == 'PREPARING'
        events.append('reserve')
        return reserve(*a, **kw)
    def checked_create(*a):
        events.append(admission.anchor.load()['phase'])
        assert session.authority.read_run_v3(1) == session.run
        return create(*a)
    session.authority.reserve_v3 = checked_reserve
    host.create_policy_suspended = checked_create
    try:
        offer = prepare(session, mutex)
        record = admission.anchor.load()
        assert events == ['reserve', 'WORKER_PENDING', 'PLAYER_PENDING']
        assert record['phase'] == 'PUBLISHED'
        assert record['parent'] == asdict(offer.parent)
        assert record['worker'] == asdict(offer.worker)
        assert record['player'] == asdict(offer.player)
        assert record['schema'] == 2 and record['offer'] == asdict(offer)
        assert not [c for c in host.calls if c[0] == 'resume']
        session.start_worker(mutex=mutex)
        session.authority.consume_v3(session.run, producer_epoch=1, now_ns=1_000_000_000)
        session.accept_worker(ack_for(offer), mutex=mutex)
    finally:
        session.dispose(mutex=mutex)
        session.authority.close()


@pytest.mark.parametrize('phase', ['PREPARING','WORKER_PENDING','PLAYER_PENDING','PUBLISHED','STOP_INTENT'])
def test_nonterminal_host_barrier_survives_restart_and_absent_pids(tmp_path, monkeypatch, phase):
    session, host, state, clock, mutex = compose(tmp_path)
    admission = install_fixture(tmp_path, monkeypatch)
    record = phase_record(session.run, phase)
    admission.anchor.path.write_bytes(canonical_v3(record))
    reopened = CanonicalAdmission(admission.anchor, 'a'*64, project=tmp_path,
        package='synthetic', executable='synthetic', coverage=lambda:None)
    try:
        with pytest.raises(CanonicalRuntimeError):
            reopened.stopped(session.authority, state)
        assert admission.anchor.load() == record
        assert not host.calls
    finally:
        session.authority.close()


@pytest.mark.parametrize('cut', ['reserve','worker','player','publish'])
def test_common_bootstrap_crash_cut_keeps_host_occupancy(tmp_path, monkeypatch, cut):
    session, host, state, clock, mutex = compose(tmp_path)
    admission = install_fixture(tmp_path, monkeypatch)
    session.canonical = admission
    def crash(*a, **kw): raise RuntimeError('synthetic cut')
    if cut == 'reserve':
        session.authority.reserve_v3 = crash
    elif cut == 'publish':
        admission.published = crash
    else:
        original = host.create_policy_suspended
        count = [0]
        def create(*a):
            count[0] += 1
            if count[0] == (1 if cut == 'worker' else 2): crash()
            return original(*a)
        host.create_policy_suspended = create
    try:
        with pytest.raises(RuntimeError): prepare(session, mutex)
        assert admission.anchor.load()['phase'] == {
            'reserve':'PREPARING','worker':'WORKER_PENDING','player':'PLAYER_PENDING','publish':'PLAYER_PENDING'}[cut]
        with pytest.raises(CanonicalRuntimeError): admission.stopped(session.authority, state)
        assert not [c for c in host.calls if c[0] in ('resume','kill')]
    finally:
        session.dispose(mutex=mutex)
        session.authority.close()


def test_guard_survives_failed_host_readback(tmp_path, monkeypatch):
    session, host, state, clock, mutex = compose(tmp_path)
    admission = install_fixture(tmp_path, monkeypatch)
    anchor = admission.anchor
    before = anchor.load()
    original = anchor._read
    reads = [0]
    def read(path):
        value = original(path)
        if path == anchor.path:
            reads[0] += 1
            if reads[0] == 2: return b'corrupt'
        return value
    anchor._read = read
    try:
        with pytest.raises(CanonicalRuntimeError):
            anchor.transition(before, phase_record(session.run, 'PREPARING'), mutex)
        assert anchor.guard.exists()
        with pytest.raises(CanonicalRuntimeError): anchor.load()
    finally:
        session.authority.close()
