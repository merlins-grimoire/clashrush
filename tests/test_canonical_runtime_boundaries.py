"""Offline contract probes: fake kernel only, never native player creation."""
import ctypes

import pytest

from clash_rush_rebuild.win32_runtime import NativeWin32Api


def test_concrete_creation_leaf_denies_without_canonical_admission():
    calls = []
    class Kernel:
        def CreateProcessW(self, executable, command, sa, ta, inherit, flags, env, cwd, startup, info):
            calls.append(executable)
            info._obj.hProcess = 501
            info._obj.hThread = 502
            info._obj.dwProcessId = 503
            return True
    api = object.__new__(NativeWin32Api)
    api._kernel32 = Kernel()
    with pytest.raises(RuntimeError, match='CANONICAL'):
        api.create_process_w('C:\\Synthetic\\HD-Player.exe',
            ctypes.create_unicode_buffer('synthetic'), False, 4)
    assert calls == []


def test_capture_record_requires_render_creation_identity():
    from clash_rush_rebuild.mvp_calibration import WindowObservation
    assert 'render' in WindowObservation.__dataclass_fields__
    assert 'capture_nonce' in WindowObservation.__dataclass_fields__


@pytest.mark.parametrize('lane', ['calibration', 'normal'])
def test_final_durable_read_cannot_overrun_deadline_and_still_stop(tmp_path, lane, monkeypatch):
    from test_calibration_retirement import captured
    from test_policy_normal_retirement import session_success
    session, host, state, clock, mutex = (captured if lane == 'calibration' else session_success)(tmp_path)
    from clash_rush_rebuild import mvp_frozen_stop as stop
    monkeypatch.setattr(stop, '_monotonic_ns', lambda:int(clock.now*1e9))
    monkeypatch.setattr(stop, '_wall_ns', lambda:1_000_000_000)
    original = session.authority.status_v3
    reads = 0
    def slow_read():
        nonlocal reads
        result = original()
        reads += 1
        if reads == 2:
            clock.now = 20.
        return result
    session.authority.status_v3 = slow_read
    try:
        with pytest.raises(RuntimeError):
            (session.retire_calibration if lane == 'calibration' else session.retire_normal_success)(
                mutex=mutex, expected_title='Synthetic')
        assert ('kill', 'job1') not in host.calls
    finally:
        session.authority.close()


@pytest.mark.parametrize('changed', [False, True])
def test_captured_distinct_render_is_pinned_not_any_current_child(tmp_path, monkeypatch, changed):
    from dataclasses import replace
    from test_calibration_retirement import captured
    from test_policy_process_topology import release_for
    from clash_rush_rebuild.lifecycle import ProcessIdentity
    from clash_rush_rebuild.mvp_calibration import WindowObservation
    from clash_rush_rebuild import mvp_frozen_stop as stop
    monkeypatch.setattr(stop, '_monotonic_ns', lambda:1_000_000_000)
    monkeypatch.setattr(stop, '_wall_ns', lambda:1_000_000_000)
    session, host, state, clock, mutex = captured(tmp_path, retired=False)
    child = ProcessIdentity(12, 120)
    host.identities[12] = child
    host.jobs['job1'].append(12)
    original = host.bind_exact
    host.bind_exact = lambda *a:replace(original(*a), render_identity=host.identities[12], capture_nonce='b'*32)
    window = WindowObservation(session.topology.offer.player,80,81,1280,720,True,child,'a'*32)
    session.topology.pin_capture(window, 'Synthetic')
    # Distinct bind nonce is expected, not a window generation comparison.
    assert session.topology._captured_window.capture_nonce == 'a'*32
    session.topology.terminate_worker(release_for(session.topology, session.topology.offer, host))
    del session.capture_window
    if changed:
        host.identities[12] = ProcessIdentity(12, 121)
    try:
        if changed:
            with pytest.raises(RuntimeError): session.retire_calibration(mutex=mutex,expected_title='Synthetic')
            assert ('kill','job1') not in host.calls
        else:
            session.retire_calibration(mutex=mutex,expected_title='Synthetic')
            assert ('kill','job1') in host.calls
            assert session.authority.outcome_v3(1).outcome == 'BLOCKED'
    finally:
        session.authority.close()


def test_effectful_production_retirement_clock_is_rejected_before_invocation():
    from test_policy_normal_retirement import ready
    topology, host = ready()
    calls = []
    with pytest.raises(RuntimeError,match='effectful'):
        topology.retire_normal_player(expected_title='Synthetic',deadline=10.,
            clock=lambda:calls.append('clock'),before_retire=lambda b:None)
    assert calls == [] and ('kill','job1') not in host.calls


def test_source_composition_cannot_choose_an_alternate_host_anchor(tmp_path,monkeypatch):
    from clash_rush_rebuild import mvp_canonical_runtime as canonical
    calls=[]
    monkeypatch.setattr(canonical,'machine_root',lambda:calls.append('locator'))
    with pytest.raises(RuntimeError,match='CANONICAL'):canonical.open_canonical(tmp_path)
    assert calls==[]


def test_real_one_use_creation_consumer_joins_durable_bootstrap(tmp_path,monkeypatch):
    from canonical_fixture import install_fixture
    from test_policy_parent_session import compose,prepare
    from clash_rush_rebuild.mvp_canonical_runtime import consume_native_creation
    session,host,state,clock,mutex=compose(tmp_path)
    session.canonical=install_fixture(tmp_path,monkeypatch)
    original=host.create_policy_suspended
    calls=[]
    def create(executable,command):
        consume_native_creation(executable,command,False,4)
        calls.append(executable)
        with pytest.raises(RuntimeError):consume_native_creation(executable,command,False,4)
        return original(executable,command)
    host.create_policy_suspended=create
    try:
        prepare(session,mutex)
        assert calls==['worker.exe','player.exe']
        with pytest.raises(RuntimeError):consume_native_creation('player.exe','player',False,4)
    finally:
        session.dispose(mutex=mutex);session.authority.close()


@pytest.mark.parametrize('executable',['C:/Synthetic/HD-Player.exe','C:/Synthetic/helper.exe'])
def test_auxiliary_process_owner_cannot_bypass_player_admission(executable):
    from clash_rush_rebuild.diagnostic_child_job import run_owned_diagnostic_child
    calls=[]
    result=run_owned_diagnostic_child([executable,'synthetic'],timeout=1,
        runtime_factory=lambda:calls.append('runtime'),popen_factory=lambda *a,**kw:calls.append('create'))
    assert result.reason.value=='PARENT_OWNERSHIP' and result.returncode is None and calls==[]
