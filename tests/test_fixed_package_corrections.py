"""F1/F2/F3 regressions. All actors/files are synthetic; no bundled ADB."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import pytest

from clash_rush_rebuild import mvp_installed_build as m
from clash_rush_rebuild.mvp_calibration import CalibrationSources, ParentPackageFact, WindowObservation
from test_fixed_package_observation import make
from test_calibration_mode import calibrated_session


def test_unqualified_bundled_client_never_spawns(monkeypatch):
    from clash_rush_rebuild import diagnostic_child_job as child
    called = []
    monkeypatch.setattr(child, 'run_owned_diagnostic_child', lambda *a, **kw: called.append(True))
    with pytest.raises(RuntimeError, match='READ_ONLY_PACKAGE_TRANSPORT_FAILED'):
        m._ParentPackageLeaves.fixed_command([m.ADB, '-s', '127.0.0.1:5555', 'get-serialno'],
                                             5, 4096, before_spawn=lambda: None)
    assert called == []


@pytest.mark.parametrize('callback', ['current', 'clock'])
@pytest.mark.parametrize('cut', ['spawn', 'accept'])
@pytest.mark.parametrize('mutation', ['replace', 'in_place', 'revoke'])
def test_final_authority_callback_cannot_change_scope(tmp_path, monkeypatch, callback, cut, mutation):
    observer, low, issuer, binding, outputs, exe = make(tmp_path, monkeypatch)
    spawned = []
    def change():
        if mutation == 'replace': issuer.scope = replace(issuer.scope, slot=1)
        elif mutation == 'in_place': object.__setattr__(issuer.scope, 'slot', 1)
        else: issuer.revoke()
    def arm():
        count = 0
        def callback_value():
            nonlocal count
            count += 1
            # Second issuer check is the final callback latch in before_spawn.
            if count == (2 if callback == 'current' else 4): change()
            return True if callback == 'current' else issuer.scope.issued_ns
        setattr(issuer, '_current' if callback == 'current' else '_clock', callback_value)
    def command(argv, timeout, limit, *, before_spawn):
        if cut == 'spawn': arm()
        before_spawn()
        spawned.append(argv)
        if cut == 'accept': arm()
        return m.CommandResult(outputs[len(spawned)-1], '', 0, True)
    low.fixed_command = command
    with pytest.raises(RuntimeError): observer.observe()
    assert len(spawned) == (0 if cut == 'spawn' else 1)


def test_metadata_write_denied_and_handles_released(tmp_path):
    player = tmp_path / 'HD-Player.exe'; player.write_bytes(b'harmless')
    exe = tmp_path / 'HD-Adb.exe'; exe.write_bytes(b'original')
    attempts = []
    def metadata(path):
        for operation in (lambda: exe.write_bytes(b'changed'), lambda: exe.unlink(),
                          lambda: tmp_path.rename(tmp_path.with_name(tmp_path.name + '-moved'))):
            with pytest.raises(OSError): operation()
            attempts.append('denied')
        return ('UNAVAILABLE', 'UNAVAILABLE')
    result = m.resolve_bundled_executable(player, metadata)
    assert result.sha256 == hashlib.sha256(b'original').hexdigest()
    assert attempts == ['denied'] * 3
    exe.write_bytes(b'after-close'); exe.unlink()


def test_pin_survives_real_owned_creation_and_cleanup(tmp_path):
    import subprocess
    import sys
    # Isolate the permanent CreateProcess wrapper from the pytest controller.
    program = '''
import pathlib, shutil, sys
from clash_rush_rebuild.mvp_installed_build import _pin_bundled_executable
from clash_rush_rebuild.diagnostic_child_job import run_owned_diagnostic_child
root = pathlib.Path(sys.argv[1])
player = root / 'HD-Player.exe'; player.write_bytes(b'harmless placeholder')
exe = root / 'HD-Adb.exe'; shutil.copy2(sys.executable, exe)
(root / 'pyvenv.cfg').write_text('home = ' + sys.base_prefix + '\\n')
attempts = []
def before_spawn():
    for operation in (lambda: exe.write_bytes(b'changed'), lambda: exe.unlink()):
        try: operation()
        except OSError: attempts.append('denied')
        else: raise AssertionError('pinned image writable')
with _pin_bundled_executable(player, lambda _: ('UNAVAILABLE', 'UNAVAILABLE')) as pinned:
    result = run_owned_diagnostic_child([pinned.path, '-c', "print('harmless')"],
        timeout=5, output_limit=4096, before_spawn=before_spawn)
    assert result.operational_failure is False, result
    assert result.child_wait_completed is True and result.cleanup_succeeded is True, result
    assert result.returncode == 0 and result.stdout == 'harmless\\n' and result.stderr == '', result
assert attempts == ['denied', 'denied']
exe.unlink()
'''
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0 and result.stdout == b'' and result.stderr == b'', result.stderr


def test_pinning_rejects_preexisting_writer_and_closes_partial_handles(tmp_path):
    player = tmp_path / 'HD-Player.exe'; player.write_bytes(b'harmless')
    exe = tmp_path / 'HD-Adb.exe'; exe.write_bytes(b'original')
    with exe.open('r+b'):
        with pytest.raises(RuntimeError): m.resolve_bundled_executable(player, lambda _: ('UNAVAILABLE',)*2)
    # Failure unwinds every ancestor, rather than retaining a half-open lease.
    destination = tmp_path.with_name(tmp_path.name + '-moved')
    tmp_path.rename(destination)
    destination.rename(tmp_path)
    assert m.resolve_bundled_executable(player, lambda _: ('UNAVAILABLE',)*2).sha256


@pytest.mark.parametrize('cut', ['server', 'spawn', 'close'])
def test_executable_pin_lasts_through_observation(tmp_path, monkeypatch, cut):
    observer, low, issuer, binding, outputs, exe = make(tmp_path, monkeypatch)
    attempts = []
    def attempt():
        with pytest.raises(OSError): exe.write_bytes(b'changed')
        with pytest.raises(OSError): exe.unlink()
        attempts.append('denied')
    target = {'server': 'server_current', 'spawn': 'fixed_command', 'close': 'close_player'}[cut]
    original = getattr(low, target)
    def wrapped(*args, **kw):
        attempt()
        return original(*args, **kw)
    setattr(low, target, wrapped)
    assert observer.observe().running is True
    assert attempts
    exe.write_bytes(b'after-close')


@pytest.mark.parametrize('fault', ['future', 'window_future', 'window_clock_regression', 'capture_clock_regression'])
def test_fact_window_capture_temporal_bracket(tmp_path, fault):
    session, host, state, clock, mutex, context = calibrated_session(tmp_path)
    calls = []
    scope = context.calibration.scope
    now = [scope.issued_ns + 10]
    fact = ParentPackageFact(scope.digest, context.offer.player, scope.instance_digest,
        scope.package, True, 'a'*64, 'b'*64, 'c'*64,
        scope.expires_ns - 1 if fault == 'future' else scope.issued_ns)
    def window(offer):
        calls.append('window')
        if fault == 'window_future': object.__setattr__(fact, 'observed_ns', scope.expires_ns-1)
        if fault == 'window_clock_regression': now[0] -= 1
        return WindowObservation(offer.player, 12, 13, 1920, 1080, True)
    def capture(*args):
        calls.append('capture')
        if fault == 'capture_clock_regression': now[0] -= 1
        return 'f'*64
    source = CalibrationSources(scope, current=lambda: True, observer=SimpleNamespace(
        window_geometry=window, capture_private_evidence=capture), clock_ns=lambda: now[0], parent_fact=fact)
    try:
        with pytest.raises(RuntimeError): source.observe(context, SimpleNamespace(active=lambda: True))
        assert calls == ([] if fault == 'future' else ['window', 'capture']
                         if fault == 'capture_clock_regression' else ['window'])
    finally:
        session.dispose(mutex=mutex); session.authority.close()


def test_accepted_fact_cannot_regress_via_alias_on_second_accept(tmp_path):
    session, host, state, clock, mutex, context = calibrated_session(tmp_path)
    scope = context.calibration.scope
    fact = ParentPackageFact(scope.digest, context.offer.player, scope.instance_digest,
        scope.package, True, 'a'*64, 'b'*64, 'c'*64, scope.issued_ns + 5)
    observer = SimpleNamespace(window_geometry=lambda *a: pytest.fail('window'),
                               capture_private_evidence=lambda *a: pytest.fail('capture'))
    source = CalibrationSources(scope, current=lambda: True, observer=observer,
                                clock_ns=lambda: scope.issued_ns + 10, parent_fact=fact)
    try:
        source.accept_parent_fact(fact, context)
        object.__setattr__(fact, 'observed_ns', scope.issued_ns)
        with pytest.raises(RuntimeError): source.accept_parent_fact(fact, context)
    finally:
        session.dispose(mutex=mutex); session.authority.close()
