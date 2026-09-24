"""Lifecycle and geometry fault matrix through the real production cycle."""
from types import SimpleNamespace
import pytest
from test_lifecycle import FakeHost, FakeStateStore
from test_startup_geometry import Geometry, supervisor
from test_startup_continue_integration import _cycle
from clash_rush_rebuild.cycle import StartupDebugCycle
from clash_rush_rebuild.lifecycle_state import BlockReason, Ready
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.startup_failure import StartupFailure, StartupReason


def fail(*a, **k):
    raise OSError("private-marker")


@pytest.mark.parametrize("reason,method", [
    (BlockReason.JOB_SETUP, "create_job"), (BlockReason.PROCESS_CREATE, "create_suspended"),
    (BlockReason.IDENTITY, "identity_from_handle"), (BlockReason.ASSIGNMENT, "assign_to_job"),
    (BlockReason.RESUME, "resume_thread"), (BlockReason.THREAD_CLOSE, "close_thread"),
    (BlockReason.PLAYER_ENUMERATION, "complete_player_snapshot"),
    (BlockReason.MEMBERSHIP, "stable_job_members"), (BlockReason.WINDOW_BINDING, "bind_exact"),
    (BlockReason.CAPTURE_READINESS, "capture_ready"),
])
def test_lifecycle_stage_propagates_through_cycle_and_releases_mutex(reason, method):
    geometry = Geometry()
    subject, host, state, events = supervisor(geometry)
    subject._readiness_attempts = 1
    original = getattr(host, method)
    calls = 0
    def fault(*a, **k):
        nonlocal calls
        calls += 1
        # Complete snapshot first proves no overlap; fail the post-launch read.
        if calls == (2 if method == "complete_player_snapshot" else 1):
            fail()
        return original(*a, **k)
    setattr(host, method, fault)
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle
    cycle._make_supervisor = lambda *a: subject
    cycle._recover = lambda *a: pytest.fail("must not reach evidence or input")
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason.value == reason.value
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "private-marker" not in str(caught.value)
    assert events[-1] == "mutex:release"
    assert not host.running
    assert state.state.blocked_reason is reason


@pytest.mark.parametrize("stage", ["restore", "bounds", "resize", "binding", "verify", "capture"])
def test_geometry_faults_keep_exact_reason_and_always_stop(stage):
    geometry = Geometry()
    subject, host, state, events = supervisor(geometry)
    if stage in {"restore", "bounds", "resize"}:
        setattr(geometry, stage, fail)
    elif stage == "binding":
        host._require_capture_binding = fail
    elif stage == "verify":
        geometry.converge = False
    else:
        original = host.capture_ready
        calls = 0
        def capture(*a):
            nonlocal calls
            calls += 1
            return fail() if calls > 1 else original(*a)
        host.capture_ready = capture
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle
    cycle._make_supervisor = lambda *a: subject
    cycle._recover = lambda *a: pytest.fail("no evidence/input")
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason.value == "GEOMETRY_" + stage.upper()
    assert caught.value.__context__ is None
    assert events.count("job:terminate") == 1
    assert events[-1] == "mutex:release"
    assert not host.running


@pytest.mark.parametrize("stage,reason", [
    ("_load_registry", "REGISTRY"), ("_make_state_store", "STATE"),
    ("_observe_player_count", "PLAYER_ENUMERATION"), ("_candidate_tree", "AUTHORIZATION"),
    ("_make_supervisor", "JOB_SETUP"),
])
def test_cycle_prelaunch_failures_do_not_reach_start(stage, reason):
    events = []
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle
    setattr(cycle, stage, fail)
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason.value == reason
    assert not any(e.startswith("start:") for e in events)
    assert "recover" not in events and events[-1] == "mutex:release"


def test_rollback_failure_retains_original_stage():
    subject, host, state, events = supervisor(Geometry())
    host.resume_thread = fail
    host.fail_job_termination = True
    with pytest.raises(StartupFailure) as caught:
        subject.start(Slot(0, "Pie64", "Example A", 1280, 720, 240))
    assert caught.value.reason is StartupReason.ROLLBACK_UNPROVED
    assert caught.value.prior == (StartupReason.RESUME,)
    assert state.state.blocked_reason is BlockReason.ROLLBACK_UNPROVED
    assert caught.value.__context__ is None


def test_all_lifecycle_block_reasons_have_closed_protocol_values():
    assert all(StartupReason(r.value).value == r.value for r in BlockReason)


@pytest.mark.parametrize("fault", ["query", "close"])
def test_prelaunch_lifecycle_enumeration_is_not_collapsed_to_state(fault):
    from clash_rush_rebuild.lifecycle import PlayerSnapshot
    subject, host, state, events = supervisor(Geometry())
    host.complete_player_snapshot = fail if fault == "query" else lambda: PlayerSnapshot((), fail)
    cycle = _cycle(events)
    cycle.__class__ = StartupDebugCycle
    cycle._make_supervisor = lambda *a: subject
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason is StartupReason.PLAYER_ENUMERATION
    assert caught.value.__context__ is None
    assert "job:create" not in events and "recover" not in events
    assert events[-1] == "mutex:release"


def test_mutex_release_failure_disables_cycle_and_retains_prior():
    events = []
    cycle = _cycle(events, approval_fails=True)
    cycle.__class__ = StartupDebugCycle
    cycle._runtime.acquire_mutex = lambda: SimpleNamespace(
        abandoned=False, require_usable=lambda: None, release=fail)
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason is StartupReason.CLEANUP
    assert caught.value.prior == (StartupReason.AUTHORIZATION,)
    assert caught.value.__context__ is None
    assert cycle._disabled is True


def test_native_evidence_init_fault_precedes_native_input(monkeypatch, tmp_path):
    import clash_rush_rebuild.startup_debug_native as mod
    monkeypatch.setattr(mod, "NativeLifecycleApi", lambda: SimpleNamespace(_user32=object()))
    monkeypatch.setattr(mod, "Win32LifecycleHost", lambda *a, **k: object())
    monkeypatch.setattr(mod, "Win32Runtime", lambda *a: object())
    monkeypatch.setattr(mod, "PrivateApprovalStorage", lambda *a: object())
    monkeypatch.setattr(mod, "OneShotApprovalService", lambda *a: object())
    monkeypatch.setattr(mod, "WindowService", lambda *a: object())
    monkeypatch.setattr(mod, "NoInputHomeDiagnosticController", lambda *a: object())
    monkeypatch.setattr(mod, "DebugEvidence", fail)
    monkeypatch.setattr(mod, "StartupNativeInput", lambda *a: pytest.fail("input construction"))
    cycle = mod.build_cycle(tmp_path, "synthetic")
    with pytest.raises(StartupFailure) as caught:
        cycle._recover(SimpleNamespace(capture_owned=lambda *a: None), object())
    assert caught.value.reason is StartupReason.EVIDENCE_INIT
    assert caught.value.__context__ is None
