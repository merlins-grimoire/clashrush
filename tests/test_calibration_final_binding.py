"""A2: private readback cannot outlive the window it observed (synthetic)."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from clash_rush_rebuild import mvp_calibration as m
from test_calibration_mode import calibrated_session


@pytest.mark.parametrize('change', ['root_hwnd', 'render_hwnd', 'width', 'height', 'supported_visible',
                                    'aliased_window', 'boolean_integer', 'player'])
def test_private_readback_requires_final_exact_window(tmp_path, change):
    s, host, state, clock, mutex, context = calibrated_session(tmp_path)
    window = m.WindowObservation(context.offer.player, 12, 13, 640, 360, True)
    calls = []

    class Observer:
        def window_geometry(self, offer):
            calls.append('window')
            return window

        def capture_private_evidence(self, scope, offer, observed):
            nonlocal window
            calls.append('readback')
            if change == 'aliased_window':
                object.__setattr__(window, 'root_hwnd', 999)
            elif change == 'boolean_integer':
                window = replace(window, supported_visible=1)
            elif change == 'player':
                window = replace(window, player=replace(window.player, creation_time_100ns=999))
            else:
                window = replace(window, **{change: False if change == 'supported_visible' else 999})
            return 'e' * 64

    try:
        sources = m.CalibrationSources(context.calibration.scope, current=lambda: True,
            observer=Observer(), clock_ns=lambda: 1_000_000_000)
        with pytest.raises(RuntimeError, match='CALIBRATION_AUTHORITY_DENIED'):
            sources.observe(context, SimpleNamespace(active=lambda: True))
        assert calls == ['window', 'readback', 'window']
        # Failure consumes the observation; never replay capture or evidence writes.
        with pytest.raises(RuntimeError):
            sources.observe(context, SimpleNamespace(active=lambda: True))
        assert calls.count('readback') == 1
    finally:
        s.dispose(mutex=mutex)
        s.authority.close()
