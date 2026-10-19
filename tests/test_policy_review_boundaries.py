"""Card B regressions: real policy/SQLite, synthetic files and OS/IPC leaves only."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest

from test_policy_sources import prepared_sources
from test_policy_process_topology import InertHost
from test_policy_finalization_v3 import next_binding, stopped
from clash_rush_rebuild.lifecycle_state import Active, BlockReason, LifecycleStateStore
from clash_rush_rebuild.mvp_policy_run import bootstrap_parent, accept_parent_worker
from clash_rush_rebuild.mvp_deployment_worker import consume_policy_handoff
from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
from clash_rush_rebuild.win32_state_io import Win32StateFilePort, NativeWin32StateApi
from clash_rush_rebuild.win32_runtime import Win32Runtime


@contextmanager
def owned(api):
    lock = Win32Runtime(api).acquire_mutex()
    try:
        yield lock
    finally:
        lock.release()


def bootstrap(ingress, state, host, sources, clock):
    with ingress.prepare_run() as prepared:
        return bootstrap_parent(prepared, state=state, host=host, sources=sources,
            listener=SimpleNamespace(parent=host.identities[99]),
            worker_executable='synthetic-worker', worker_command='worker',
            player_executable='synthetic-player', player_command='player', clock=clock)


def consume(session, context, sources, api):
    host = InertHost()
    host.current_process_handle = lambda: context.offer.worker.pid
    host.current_process_id = host.current_process_handle
    host.identities[context.offer.worker.pid] = context.offer.worker
    host.identities[context.offer.remote_player_handle] = context.offer.player
    host.handle_is_inheritable = lambda _: False
    host.flags[context.offer.remote_player_job_query] = 0
    host.jobs[context.offer.remote_player_job_query] = [context.offer.remote_player_handle]
    with owned(api) as lock:
        sources.require_context(context)
        return consume_policy_handoff(authority=session.authority, run=context.run,
            producer_epoch=1, offer=context.offer, host=host, mutex=lock,
            runner_manifest_bytes=context.runner_manifest_bytes, clock=lambda: 100.)


@pytest.mark.parametrize('change', [
    'unchanged', 'revoke', 'expire', 'approval', 'profile', 'provider', 'config', 'slots',
    'topology-revoke', 'validation-deadline', 'validation-expire', 'validation-revoke',
])
def test_parent_current_sources_after_ack_and_topology(tmp_path, monkeypatch, change):
    from clash_rush_rebuild import mvp_policy_ingress as ingress_module
    from clash_rush_rebuild.mvp_policy_channel import PlayerReady
    sources, _, ingress, api, _ = prepared_sources(tmp_path, monkeypatch)
    host = InertHost()
    state = LifecycleStateStore(tmp_path, Win32StateFilePort(tmp_path, NativeWin32StateApi()))
    clock = SimpleNamespace(now=100.)
    session, context = bootstrap(ingress, state, host, sources, lambda: clock.now)
    authority = DurableSessionAuthority(tmp_path/'var/mvp-session.sqlite3')
    session.authority = authority
    sent = []; closed = []
    approval = tmp_path/'var/approvals/policy-v3.json'
    try:
        ack = consume(session, context, sources, api)
        if change == 'revoke':
            approval.unlink()
        elif change == 'expire':
            monkeypatch.setattr(ingress_module.time, 'time', lambda: 200.)
        elif change in ('approval', 'profile', 'provider', 'config', 'slots'):
            paths = dict(approval='var/approvals/policy-v3.json',
                profile='private/deployment/profile.json', provider='private/deployment/provider.json',
                config='synthetic.conf', slots='private/slots.json')
            path = tmp_path/paths[change]
            path.write_bytes(path.read_bytes() + b' ')
        elif change == 'topology-revoke':
            original = host.stable_job_members
            def members(job):
                result = original(job)
                approval.unlink(missing_ok=True)
                return result
            host.stable_job_members = members
        elif change.startswith('validation-'):
            original = sources._slot
            def slot():
                result = original()
                if change == 'validation-deadline':
                    clock.now = context.run.deadline_ns / 1e9
                elif change == 'validation-expire':
                    monkeypatch.setattr(ingress_module.time, 'time', lambda: 200.)
                else:
                    approval.unlink(missing_ok=True)
                return result
            sources._slot = slot
        channel = SimpleNamespace(receive=lambda **kw: ack, send=sent.append,
                                  close=lambda: closed.append(True))
        listener = SimpleNamespace(accept=lambda **kw: channel)
        if change == 'unchanged':
            accept_parent_worker(session, context, listener,
                mutex_factory=lambda: owned(api), clock=lambda: clock.now)
            assert sent == [PlayerReady(context.offer)]
            assert ('resume', session.topology.player.thread_handle) in host.calls
        else:
            with pytest.raises(RuntimeError):
                accept_parent_worker(session, context, listener,
                    mutex_factory=lambda: owned(api), clock=lambda: clock.now)
            assert sent == [] and closed == [True]
            assert ('resume', session.topology.player.thread_handle) not in host.calls
            assert session.topology.blocked
            assert state.load() == Active(0, context.run.run_nonce, BlockReason.STOP_PROOF)
            assert authority.outcome_v3(1) is None
            with pytest.raises(RuntimeError, match='unresolved predecessor closure'):
                authority.reserve_v3(next_binding(context.run), writer_epoch=1, proof=stopped())
            assert authority._open().execute('SELECT count(*) FROM run_v3').fetchone()[0] == 1
    finally:
        with owned(api) as lock:
            session.dispose(mutex=lock)
        authority.close()
    assert not [c for c in host.calls if c[0] == 'kill']


@pytest.mark.parametrize('boundary', ['worker-create', 'player-create', 'worker-resume'])
def test_parent_adjacent_bootstrap_boundaries_recheck_sources(tmp_path, monkeypatch, boundary):
    sources, _, ingress, api, _ = prepared_sources(tmp_path, monkeypatch)
    host = InertHost()
    state = LifecycleStateStore(tmp_path, Win32StateFilePort(tmp_path, NativeWin32StateApi()))
    approval = tmp_path/'var/approvals/policy-v3.json'
    method = {'worker-create': 'set_non_killing_job', 'player-create': 'assign_to_job',
              'worker-resume': 'duplicate_query_job_to_worker'}[boundary]
    original = getattr(host, method)
    def revoke(*args):
        result = original(*args)
        approval.unlink(missing_ok=True)
        return result
    setattr(host, method, revoke)
    with pytest.raises(RuntimeError):
        bootstrap(ingress, state, host, sources, lambda: 100.)
    assert not [c for c in host.calls if c[0] in ('resume', 'kill')]
    assert len([c for c in host.calls if c[0] == 'create']) == (
        0 if boundary == 'worker-create' else 1 if boundary == 'player-create' else 2)
    assert state.load() == Active(0, sources.run.run_nonce, BlockReason.STOP_PROOF)
    authority = DurableSessionAuthority(tmp_path/'var/mvp-session.sqlite3')
    try:
        assert authority.read_run_v3(1) == sources.run
        with pytest.raises(RuntimeError, match='unresolved predecessor closure'):
            authority.reserve_v3(next_binding(sources.run), writer_epoch=1, proof=stopped())
        assert authority._open().execute('SELECT count(*) FROM run_v3').fetchone()[0] == 1
    finally:
        authority.close()


@pytest.mark.parametrize('leaf', ['GetDC', 'CreateCompatibleDC', 'CreateCompatibleBitmap', 'SelectObject'])
@pytest.mark.parametrize('when', [9., 10., 11.])
def test_printwindow_guard_after_each_preparation_leaf(leaf, when):
    from test_policy_player_view import view
    from test_win32_lifecycle_host import FakeCaptureUser, FakeCaptureGdi
    from clash_rush_rebuild.win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
    v, host, clock = view()
    user, gdi = FakeCaptureUser(), FakeCaptureGdi()
    target = user if leaf == 'GetDC' else gdi
    original = getattr(target, leaf)
    def delayed(*args):
        result = original(*args)
        clock.now = when
        return result
    setattr(target, leaf, delayed)
    api = object.__new__(NativeLifecycleApi)
    api._user32, api._gdi32 = user, gdi
    native_host = object.__new__(Win32LifecycleHost)
    native_host._api = api
    host.capture_bgra = native_host.capture_bgra
    if when < 10.:
        assert v.capture_bgr().shape == (360, 640, 3)
        assert len([c for c in user.calls if c[0] == 'print']) == 1
    else:
        with pytest.raises(RuntimeError) as failure:
            v.capture_bgr()
        assert not [c for c in user.calls if c[0] == 'print']
        assert not [c for c in gdi.calls if c[0] == 'bits']
        tb = failure.value.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'capture_printwindow_bgra':
                assert tb.tb_frame.f_locals['buffer'] is None
            tb = tb.tb_next
    assert ('select', 200, 400) in gdi.calls
    assert gdi.calls[-2:] == [('delete-object', 300), ('delete-dc', 200)]
    assert user.calls[-1] == ('release-dc', 101, 100)


@pytest.mark.parametrize('fault', ['revoked', 'deadline', 'guard-error'])
def test_capture_denial_still_attempts_all_cleanup_after_cleanup_failure(fault):
    from test_policy_player_view import view
    from test_win32_lifecycle_host import FakeCaptureUser, FakeCaptureGdi
    from clash_rush_rebuild.win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
    v, host, clock = view()
    user, gdi = FakeCaptureUser(), FakeCaptureGdi(raise_cleanup_select=True)
    allocate = gdi.CreateCompatibleBitmap
    def delayed(*args):
        result = allocate(*args)
        if fault == 'deadline':
            clock.now = 10.
        elif fault == 'revoked':
            v.enabled = lambda: False
        else:
            def unavailable():
                raise OSError('synthetic authority read failure')
            v.enabled = unavailable
        return result
    gdi.CreateCompatibleBitmap = delayed
    api = object.__new__(NativeLifecycleApi)
    api._user32, api._gdi32 = user, gdi
    native_host = object.__new__(Win32LifecycleHost)
    native_host._api = api
    host.capture_bgra = native_host.capture_bgra
    with pytest.raises(RuntimeError, match='capture resource cleanup failed'):
        v.capture_bgr()
    assert not [c for c in user.calls if c[0] == 'print']
    assert not [c for c in gdi.calls if c[0] == 'bits']
    assert ('select', 200, 400) in gdi.calls
    assert gdi.calls[-2:] == [('delete-object', 300), ('delete-dc', 200)]
    assert user.calls[-1] == ('release-dc', 101, 100)
