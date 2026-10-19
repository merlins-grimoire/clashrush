"""Exercise the actual observer-only native binding/capture composition."""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from clash_rush_rebuild.lifecycle import PlayerBinding
from test_calibration_mode import calibrated_session


def test_exact_native_view_has_no_input_owner_and_checks_both_capture_cuts(tmp_path):
    from clash_rush_rebuild.mvp_calibration import CalibrationWindowView
    s, host, state, clock, mutex, context = calibrated_session(tmp_path)
    binding = PlayerBinding(context.offer.player, context.offer.player, 12, 13, 640, 360, 'f'*32)
    host.jobs[context.offer.remote_player_job_query] = [context.offer.player.pid]
    calls = []
    host.bind_exact = lambda *a: binding
    host._require_capture_binding = lambda b,j: calls.append('binding')
    host._api = SimpleNamespace(input_desktop_identity=lambda: (1,2,'synthetic','desktop'))
    host.identity_from_handle = lambda h,p: context.offer.player
    host.wait_process = lambda *a: False
    host.job_limit_flags = lambda j: 0
    host.is_process_in_job = lambda *a: True
    host.capture_bgra = lambda b, before_capture: (before_capture() or (640,360,bytes(640*360*4)))
    view = CalibrationWindowView(host=host, offer=context.offer,
        current=lambda: True, clock_ns=lambda: 1_000_000_000,
        scope=context.calibration.scope, native_size=(640,360))
    try:
        assert view.bind('Synthetic') == binding
        assert not hasattr(view, 'owner')
        frame = view.capture_bgr()
        assert frame.shape == (360,640,3) and frame.flags.writeable
        assert len(calls) >= 3
        frame.fill(0)
    finally:
        # The view borrows duplicates; the worker's existing finally closes them.
        s.authority.close()


@pytest.mark.parametrize('fault', ['geometry', 'revoked', 'stale', 'capture-revoked'])
def test_native_view_rejects_before_accepting_pixels(tmp_path, fault):
    from clash_rush_rebuild.mvp_calibration import CalibrationWindowView
    s, host, state, clock, mutex, context = calibrated_session(tmp_path)
    b = PlayerBinding(context.offer.player, context.offer.player, 12,13,640,360,'f'*32)
    host.jobs[context.offer.remote_player_job_query] = [context.offer.player.pid]
    active = [True]
    host.bind_exact = lambda *a: b
    host._require_capture_binding = lambda *a: None
    host._api = SimpleNamespace(input_desktop_identity=lambda: (1,2,'synthetic','desktop'))
    host.identity_from_handle = lambda h,p: context.offer.player
    host.wait_process = lambda *a: False
    host.job_limit_flags = lambda j: 0
    host.is_process_in_job = lambda *a: True
    def capture(b, before_capture):
        before_capture()
        if fault == 'capture-revoked': active[0] = False
        return 640,360,bytes(640*360*4)
    host.capture_bgra = capture
    view = CalibrationWindowView(host=host,offer=context.offer,scope=context.calibration.scope,
        current=lambda:active[0],clock_ns=lambda:1_000_000_000,
        native_size=(641,360) if fault=='geometry' else (640,360))
    try:
        if fault == 'geometry':
            with pytest.raises(RuntimeError): view.bind('Synthetic')
        else:
            view.bind('Synthetic')
            if fault == 'revoked': active[0] = False
            if fault == 'stale': host.wait_process = lambda *a: True
            with pytest.raises(RuntimeError): view.capture_bgr()
    finally:
        s.authority.close()
