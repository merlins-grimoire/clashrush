"""Decision B: absent diagnostics are not admission or fabricated identity."""
from dataclasses import replace
from types import SimpleNamespace
import pytest

from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import (
    DeploymentProfile, InstallationEvidence, require_installation,
)
from test_s004_source_images import source_pack


def profile():
    data, assets = source_pack()
    return DeploymentProfile(data, assets, 'b' * 64)


def test_missing_optional_metadata_is_explicitly_unavailable():
    bound = object()
    assert require_installation(profile(), bound, None) is None
    assert require_installation(profile(), bound, lambda _: None) is None


@pytest.mark.parametrize('fault', ['package', 'version', 'binding', 'malformed'])
def test_available_contradiction_is_not_optional(fault):
    p, bound = profile(), object()
    value = InstallationEvidence(bound, 'com.supercell.clashofclans', p.plan.source_context[3])
    if fault == 'package': value = replace(value, package='other.app')
    if fault == 'version': value = replace(value, version='different')
    if fault == 'binding': value = replace(value, binding=object())
    if fault == 'malformed': value = object()
    with pytest.raises(DeploymentError, match='PACKAGE_METADATA_CONFLICT'):
        require_installation(p, bound, lambda _: value)


def test_missing_window_is_still_blocking():
    with pytest.raises(DeploymentError):
        require_installation(profile(), None, None)


def test_calibration_does_not_query_optional_package_metadata(tmp_path):
    from test_calibration_mode import calibrated_session
    from clash_rush_rebuild import mvp_calibration as m
    s, host, state, clock, mutex, context = calibrated_session(tmp_path)
    calls = []
    class Observer:
        def package_query(self, *args):
            raise AssertionError('Decision B must not start a package query')
        def window_geometry(self, offer):
            return m.WindowObservation(offer.player, 12, 13, 1920, 1080, True)
        def capture_private_evidence(self, scope, offer, window):
            calls.append('capture')
            return 'e' * 64
    try:
        sources = m.CalibrationSources(context.calibration.scope, current=lambda: True,
            observer=Observer(), clock_ns=lambda: 1_000_000_000)
        result = sources.observe(context, SimpleNamespace(active=lambda: True))
        assert result.status == 'CAPTURED' and calls == ['capture']
        assert sources.package_status == 'UNAVAILABLE'
        assert 'PACKAGE_QUERY' not in m.OPERATIONS
    finally:
        s.dispose(mutex=mutex)
        s.authority.close()
