"""R1 callback cuts: synthetic observers only, no native/game actions."""
from types import SimpleNamespace
import pytest
from test_calibration_mode import scope_for, calibrated_session
from test_policy_finalization_v3 import binding_v3
from clash_rush_rebuild.mvp_calibration import (
    CalibrationIssuer, CalibrationSources, PackageObservation, WindowObservation, OPERATIONS,
)
from clash_rush_rebuild.mvp_policy_run import WorkerLease


@pytest.mark.parametrize('operation', ['issue', 'consume', 'check'])
@pytest.mark.parametrize('cut', ['current', 'first-clock', 'second-clock'])
def test_issuer_callback_revocation_denies(operation, cut):
    scope = scope_for(binding_v3())
    armed = False
    ticks = 0
    def current():
        if armed and cut == 'current':
            issuer.revoke()
        return True
    def clock():
        nonlocal ticks
        ticks += 1
        if armed and ((cut == 'first-clock' and ticks == 1) or
                      (cut == 'second-clock' and ticks == 2)):
            issuer.revoke()
        return scope.issued_ns
    issuer = CalibrationIssuer(scope, current=current, clock_ns=clock)
    cap = issuer.issue() if operation == 'consume' else None
    armed, ticks = True, 0
    with pytest.raises(RuntimeError):
        if operation == 'consume':
            issuer.consume(cap, scope=scope, operations=OPERATIONS)
        else:
            getattr(issuer, operation)()
    assert issuer._used is False
    if operation == 'issue':
        assert issuer._capability is None


@pytest.mark.parametrize('cut', ['channel', 'clock'])
def test_pre_capture_lease_callback_revocation_prevents_capture(tmp_path, cut):
    session, host, state, clock, mutex, context = calibrated_session(tmp_path)
    calls = []
    ticks = {'channel': 0, 'clock': 0}
    class Channel:
        @property
        def no_more_gameplay(self):
            ticks['channel'] += 1
            if cut == 'channel' and ticks['channel'] == 3:
                lease.revoke()
            return False
    channel = Channel()
    channel.context = context
    def lease_clock():
        ticks['clock'] += 1
        if cut == 'clock' and ticks['clock'] == 3:
            lease.revoke()
        return 1.0
    lease = WorkerLease(channel, clock=lease_clock)
    class Observer:
        def package_query(self, scope, offer):
            calls.append('package')
            return PackageObservation(scope.instance_digest, scope.package, offer.player, True)
        def window_geometry(self, offer):
            calls.append('window')
            return WindowObservation(offer.player, 12, 13, 1920, 1080, True)
        def capture_private_evidence(self, *args):
            calls.append('capture')
            return 'e' * 64
    source = CalibrationSources(context.calibration.scope, current=lambda: True,
                                observer=Observer(), clock_ns=lambda: 1_000_000_000)
    try:
        with pytest.raises(RuntimeError):
            source.observe(context, lease)
        assert calls == ['window']  # Decision B: no package query.
    finally:
        session.dispose(mutex=mutex)
        session.authority.close()


@pytest.mark.parametrize('kind', ['issuer', 'worker'])
def test_already_revoked_guard_does_not_invoke_callbacks(kind):
    def forbidden():
        pytest.fail('callback after known revocation')
    if kind == 'issuer':
        guard = CalibrationIssuer(scope_for(binding_v3()), current=forbidden, clock_ns=forbidden)
        guard.revoke()
        with pytest.raises(RuntimeError):
            guard.check()
    else:
        class Channel:
            context = SimpleNamespace(run=binding_v3())
            no_more_gameplay = property(lambda self: forbidden())
        guard = WorkerLease(Channel(), clock=forbidden)
        guard.revoke()
        assert guard.active() is False


@pytest.mark.parametrize('now', [True, False, float('nan'), float('inf'), -float('inf'), -1, '1', None, 2.0])
def test_worker_lease_denies_invalid_or_expired_clock(now):
    run = SimpleNamespace(deadline_ns=2_000_000_000)
    lease = WorkerLease(SimpleNamespace(context=SimpleNamespace(run=run), no_more_gameplay=False),
                        clock=lambda: now)
    assert lease.active() is False


@pytest.mark.parametrize('closed', [None, 0, '', True])
def test_worker_channel_requires_exact_false(closed):
    lease = WorkerLease(SimpleNamespace(context=SimpleNamespace(run=SimpleNamespace(deadline_ns=2_000_000_000)), no_more_gameplay=closed),
                        clock=lambda: 1.0)
    assert lease.active() is False


@pytest.mark.parametrize('now', [1, 1.0])
def test_valid_worker_lease_preserves_held_and_release_semantics(now):
    lease = WorkerLease(SimpleNamespace(context=SimpleNamespace(run=SimpleNamespace(deadline_ns=2_000_000_000)), no_more_gameplay=False),
                        clock=lambda: now)
    assert lease.active() is True
    lease.set_held(True)
    lease.revoke()
    assert lease.active() is False
    with pytest.raises(RuntimeError):
        lease.set_held(True)
    lease.set_held(False)
    assert lease.held is False
