"""Donor width test plus synthetic Job-bound normalization regressions."""
from dataclasses import replace

import pytest
from test_lifecycle import FakeHost, FakeStateStore

from clash_rush_rebuild.lifecycle import (
    AcquiredMutexLease,
    PlayerBinding,
    ProcessIdentity,
)
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.startup_geometry import (
    GeometryError,
    StartupGeometrySupervisor,
    _corrected_root_width,
    normalize_window_geometry,
)


def binding(width=1501, height=805):
    identity = ProcessIdentity(100, 9001)
    return PlayerBinding(identity, identity, 101, 102, width, height, "a" * 32)


class Geometry:
    def __init__(self, current=None):
        self.current = current or binding()
        self.events = []
        self.resizes = []
        self.fail = None
        self.converge = True

    def restore(self, root):
        self.events.append("restore")
        assert root == 101
        if self.fail == "restore":
            raise GeometryError("RESTORE_FAILED")

    def bounds(self, root):
        assert root == 101
        return (10, 20, 1805, 865)

    def resize(self, root, left, top, width, height):
        self.events.append("resize")
        self.resizes.append((root, left, top, width, height))
        if self.fail == "resize":
            raise GeometryError("RESIZE_FAILED")
        if self.converge:
            self.current = replace(self.current, width=1431)

    def rebind(self):
        self.events.append("rebind")
        return self.current

    def run(self, gate=lambda: True):
        return normalize_window_geometry(
            binding(), self, self.rebind, gate=gate, wait=lambda _: None,
        )


def test_donor_width_math_accounts_for_host_chrome():
    assert _corrected_root_width(1795, 1501, 805, 1280 / 720) == 1725
    with pytest.raises(GeometryError):
        _corrected_root_width(1795, 0, 805, 1280 / 720)


def test_observed_geometry_normalizes_before_return():
    geometry = Geometry()
    result = geometry.run()
    assert (result.width, result.height) == (1431, 805)
    assert geometry.resizes == [(101, 10, 20, 1725, 845)]
    assert geometry.events.index("restore") < geometry.events.index("resize")
    assert geometry.events[-1] == "rebind"


def test_already_good_performs_no_resize():
    geometry = Geometry(binding(1280, 720))
    assert geometry.run().width == 1280
    assert not geometry.resizes


@pytest.mark.parametrize("field,value", [
    ("root_hwnd", 103), ("render_hwnd", 104),
    ("identity", ProcessIdentity(100, 9002)),
    ("render_identity", ProcessIdentity(100, 9002)),
])
def test_changed_identity_or_render_rejected(field, value):
    geometry = Geometry(replace(binding(), **{field: value}))
    with pytest.raises(GeometryError):
        geometry.run()
    assert not geometry.resizes


def test_missing_or_ambiguous_rebind_fails():
    geometry = Geometry()
    geometry.current = None
    with pytest.raises(GeometryError):
        geometry.run()
    assert not geometry.resizes


@pytest.mark.parametrize("failure", ["restore", "resize"])
def test_native_failure_fails_closed(failure):
    geometry = Geometry()
    geometry.fail = failure
    with pytest.raises(GeometryError):
        geometry.run()


def test_four_attempt_limit():
    geometry = Geometry()
    geometry.converge = False
    with pytest.raises(GeometryError):
        geometry.run()
    assert len(geometry.resizes) == 4


@pytest.mark.parametrize("gate", [lambda: False, lambda: 1])
def test_authorization_precedes_window_changes(gate):
    geometry = Geometry()
    with pytest.raises(GeometryError):
        geometry.run(gate)
    assert not geometry.events


class OwnedHost(FakeHost):
    def __init__(self, events, geometry):
        super().__init__(events)
        self.geometry = geometry

    def bind_exact(self, identity, expected_title, members):
        self.events.append("window:bind")
        return self.geometry.current

    def _require_capture_binding(self, current, job):
        assert current == self.geometry.current
        assert job == "job"
        self.events.append("identity:verified")
        if self.geometry.fail == "locked":
            raise GeometryError("LOCKED")


def supervisor(geometry):
    events = []
    host = OwnedHost(events, geometry)
    state = FakeStateStore(Ready(0), events)
    subject = StartupGeometrySupervisor(
        host, state, AcquiredMutexLease("Global\\ClashRushRebuildLifecycle-v1"),
        nonce_factory=lambda: "b" * 32, geometry_api=geometry,
        geometry_wait=lambda _: None, preserve_ready_cursor=True,
    )
    return subject, host, state, events


def test_normalized_binding_used_by_capture_and_stop():
    geometry = Geometry()
    subject, host, state, events = supervisor(geometry)
    result = subject.start(Slot(0, "Pie64", "Example A", 1280, 720, 240))
    assert result.width == 1431
    assert subject.capture_owned(result)[:2] == (1431, 805)
    subject.stop(result, None)
    assert state.state == Ready(0)
    assert not host.running
    assert events.count("members:close") == events.count("job:members")


@pytest.mark.parametrize("failure", ["restore", "resize", "locked"])
def test_normalization_failure_stops_owned_job(failure):
    geometry = Geometry()
    geometry.fail = failure
    subject, host, state, events = supervisor(geometry)
    with pytest.raises(GeometryError):
        subject.start(Slot(0, "Pie64", "Example A", 1280, 720, 240))
    assert "job:terminate" in events
    assert not host.running
    assert state.state == Ready(0)
    assert "capture:bgra" not in events


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720)])
def test_donor_scaled_render_policy(size):
    geometry = Geometry(binding(*size))
    assert (geometry.run().width, geometry.run().height) == size
    assert not geometry.resizes


def test_failure_of_stop_proof_keeps_active():
    from clash_rush_rebuild.lifecycle_state import Active, BlockReason
    geometry = Geometry()
    geometry.fail = "resize"
    subject, host, state, events = supervisor(geometry)
    host.fail_job_termination = True
    with pytest.raises(RuntimeError):
        subject.start(Slot(0, "Pie64", "Example A", 1280, 720, 240))
    assert type(state.state) is Active
    assert state.state.blocked_reason is BlockReason.STOP_PROOF
    assert events.count("job:terminate") == 1


def test_geometry_stays_unaccepted_until_fourth_successful_resize():
    geometry = Geometry()
    original = geometry.resize
    def resize(*args):
        geometry.converge = len(geometry.resizes) == 3
        original(*args)
    geometry.resize = resize
    assert geometry.run().width == 1431
    assert len(geometry.resizes) == 4
