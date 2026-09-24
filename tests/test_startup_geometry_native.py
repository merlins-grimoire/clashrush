"""Native ABI and production startup ordering; all Win32 calls are synthetic."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from test_startup_continue_integration import Approvals, Runtime, Store
from test_startup_geometry import Geometry, supervisor
from test_win32_lifecycle_host import SyntheticNative, _host, _windows

from clash_rush_rebuild.cycle import StartupDebugCycle
from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult
from clash_rush_rebuild.startup_geometry import (
    GeometryError,
    NativeGeometryApi,
    normalize_window_geometry,
)


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def test_native_restore_return_is_previous_visibility_and_resize_flags_are_exact():
    calls = []

    def rect(root, pointer):
        assert root.value == 101
        value = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        value.left, value.top, value.right, value.bottom = 10, 20, 1805, 865
        return 1

    api = SimpleNamespace(
        ShowWindow=Function(lambda *args: calls.append(args) or 0),
        GetWindowRect=Function(rect),
        SetWindowPos=Function(lambda *args: calls.append(args) or 1),
    )
    port = NativeGeometryApi(api)
    port.restore(101)  # zero is NOT a failed Win32 call
    assert port.bounds(101) == (10, 20, 1805, 865)
    port.resize(101, 10, 20, 1725, 845)
    assert calls[0][0].value == 101 and calls[0][1] == 9
    assert calls[1][0].value == 101
    assert calls[1][1:] == (None, 10, 20, 1725, 845, 0x0014)
    assert api.ShowWindow.argtypes == [wintypes.HWND, ctypes.c_int]
    assert api.GetWindowRect.argtypes == [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    assert api.SetWindowPos.argtypes == [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
    assert all(f.restype is wintypes.BOOL for f in (api.ShowWindow, api.GetWindowRect, api.SetWindowPos))
    api.GetWindowRect.call = lambda *args: 0
    with pytest.raises(GeometryError):
        port.bounds(101)
    api.SetWindowPos.call = lambda *args: 0
    with pytest.raises(GeometryError):
        port.resize(101, 10, 20, 1725, 845)


@pytest.mark.parametrize("fault", ["root", "render", "missing", "tie", "locked", "minimized"])
def test_real_host_rebind_rejects_fault_after_restore(fault):
    native = SyntheticNative()
    native.job_lists *= 4
    native.creation = {100: 9001}
    _windows(native)
    native.windows[11]["size"] = (1501, 805)
    host = _host(native)

    def rebind():
        members = host.stable_job_members("job")
        try:
            current = host.bind_exact(ProcessIdentity(100, 9001), "Exact Slot", members)
            if current is not None:
                host._require_capture_binding(current, "job")
            return current
        finally:
            members.close()

    initial = rebind()

    class Port:
        def restore(self, root):
            if fault == "root":
                native.creation[100] = 9002
            elif fault == "render":
                native.windows[12] = native.windows.pop(11)
            elif fault == "missing":
                del native.windows[11]
            elif fault == "tie":
                native.windows[12] = dict(native.windows[11])
            elif fault == "locked":
                native.unlocked = False
            else:
                native.iconic = True

        def bounds(self, root):
            pytest.fail("must reject before resize")

    with pytest.raises(RuntimeError):
        normalize_window_geometry(initial, Port(), rebind, gate=lambda: True, wait=lambda _: None)
    assert not any(call[0] == "capture" for call in native.calls)
    opens = [call for call in native.calls if call[0] == "open"]
    closes = [call for call in native.calls if call[0] == "close" and isinstance(call[1], tuple)]
    assert len(opens) == len(closes)


@pytest.mark.parametrize("failure", [None, "resize", "restore"])
def test_debug_cycle_consumes_approval_before_geometry_and_detects_after_rebind(failure):
    geometry = Geometry()
    geometry.fail = failure
    subject, host, _state, events = supervisor(geometry)
    geometry.events = events

    def recover(owner, normalized):
        events.append("detect")
        assert normalized.width == 1431
        assert "resize" in events
        assert owner.capture_owned(normalized)[:2] == (1431, 805)
        return StartupContinueResult.HOME

    cycle = StartupDebugCycle(
        Runtime(events),
        load_registry=lambda: tuple(Slot(i, f"Pie64_{i}", f"Example {i}", 1280, 720, 240) for i in range(5)),
        make_state_store=lambda: Store(events),
        observe_player_count=lambda: 0,
        approvals=Approvals(events), candidate_tree=lambda: "b" * 40,
        make_supervisor=lambda *args: subject, recover=recover,
    )
    if failure:
        with pytest.raises(RuntimeError, match="GEOMETRY_" + failure.upper()):
            cycle.visit_once()
        assert "detect" not in events
    else:
        assert cycle.visit_once() is StartupContinueResult.HOME
        assert events.index("resize") < events.index("detect")
    approval = "approval:STARTUP_DEBUG:" + "b" * 40 + ":0"
    assert events.index(approval) < events.index("restore")
    assert events.count("job:terminate") == 1
    assert events[-1] == "mutex:release"
    assert not host.running


def test_composition_uses_geometry_supervisor_only_for_debug(monkeypatch, tmp_path):
    import clash_rush_rebuild.startup_debug_native as module
    monkeypatch.setattr(module, "NativeLifecycleApi", lambda: SimpleNamespace(_user32=object()))
    monkeypatch.setattr(module, "Win32LifecycleHost", lambda *a, **k: object())
    monkeypatch.setattr(module, "Win32Runtime", lambda *a: object())
    monkeypatch.setattr(module, "PrivateApprovalStorage", lambda *a: object())
    monkeypatch.setattr(module, "OneShotApprovalService", lambda *a: object())
    monkeypatch.setattr(module, "NativeGeometryApi", lambda api: "geometry-port")
    captured = {}

    def factory(*args, **kwargs):
        captured.update(kwargs)
        return "normalized-supervisor"

    monkeypatch.setattr(module, "StartupGeometrySupervisor", factory)
    cycle = module.build_cycle(tmp_path, "unused")
    assert cycle._make_supervisor(object(), "Global\\ClashRushRebuildLifecycle-v1") == "normalized-supervisor"
    assert captured["geometry_api"] == "geometry-port"
    assert captured["preserve_ready_cursor"] is True


def test_authorization_revoked_during_bounds_prevents_resize():
    geometry = Geometry()
    allowed = True
    original = geometry.bounds

    def bounds(root):
        nonlocal allowed
        allowed = False
        return original(root)

    geometry.bounds = bounds
    with pytest.raises(GeometryError):
        geometry.run(lambda: allowed)
    assert not geometry.resizes
