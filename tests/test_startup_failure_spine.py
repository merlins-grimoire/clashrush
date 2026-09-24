"""Adapted complete diagnose-home protocol and BasePilot two-layer envelope."""
from types import SimpleNamespace
import subprocess

import pytest

from clash_rush_rebuild import cli
from clash_rush_rebuild.diagnostic_child_job import DiagnosticChildOutcome
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult as Result
from clash_rush_rebuild.startup_failure import StartupReason, StartupFailure
from clash_rush_rebuild.diagnostic_child_job import DiagnosticParentReason


def args(command="startup-debug-one"):
    return [command, "--project-root", "synthetic", "--slots", "synthetic", "--allow-private-full-frames"]


@pytest.mark.parametrize("status,text", [(0, "HOME\n"), (1, "BUILDER\n"), (1, "UNKNOWN\n")])
def test_parent_uses_owned_child_and_never_builds_cycle(monkeypatch, capsys, status, text):
    calls = []
    def runner(command, *, timeout):
        calls.append((command, timeout))
        return DiagnosticChildOutcome(status, text, "", True, True)
    monkeypatch.setattr(cli, "run_owned_diagnostic_child", runner)
    assert cli.main(args()) == status
    assert capsys.readouterr() == (text, "")
    assert calls[0][0][3] == "startup-debug-child"
    assert "--allow-private-full-frames" in calls[0][0]


@pytest.mark.parametrize("status,out,err", [
    (True, "BUILDER\n", ""), (0, "HOME\nextra\n", ""),
    (0, "HOME\n", "private-marker\n"), (3, "HOME\n", ""),
    (2, "", "INITIAL_CAPTURE\nextra\n"), (2, "HOME\n", "INITIAL_CAPTURE\n"),
    (0, b"\xff", ""), (0, " HOME\n", ""),
])
def test_parent_rejects_noncanonical_child_output(monkeypatch, capsys, status, out, err):
    monkeypatch.setattr(cli, "run_owned_diagnostic_child", lambda *a, **k: DiagnosticChildOutcome(status, out, err, True, True))
    assert cli.main(args()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private-marker" not in captured.err
    cli.parse_diagnostic_failure_record(captured.err)


def test_child_preserves_closed_stage_and_never_raw_exception(capsys):
    from clash_rush_rebuild.startup_failure import StartupFailure, StartupReason
    def build(*a):
        raise StartupFailure(StartupReason.EVIDENCE_INIT)
    assert cli.main(args("startup-debug-child"), startup_debug_cycle_builder=build) == 2
    assert capsys.readouterr() == ("", "EVIDENCE_INIT\n")


@pytest.mark.parametrize("result,status", [(Result.HOME, 0), (Result.BUILDER, 1), (Result.UNKNOWN, 1)])
def test_hidden_child_complete_result_protocol(capsys, result, status):
    build = lambda *a: SimpleNamespace(visit_once=lambda: result)
    assert cli.main(args("startup-debug-child"), startup_debug_cycle_builder=build) == status
    assert capsys.readouterr() == (result.value + "\n", "")


def test_geometry_reason_survives_stop_and_has_no_raw_context():
    from test_startup_geometry import Geometry, supervisor
    from clash_rush_rebuild.registry import Slot
    from clash_rush_rebuild.startup_failure import StartupFailure, StartupReason
    geometry = Geometry()
    def fail_resize(*a):
        raise OSError("private-marker")
    geometry.resize = fail_resize
    subject, host, store, events = supervisor(geometry)
    with pytest.raises(StartupFailure) as caught:
        subject.start(Slot(0, "Pie64", "Example A", 1280, 720, 240))
    assert caught.value.reason is StartupReason.GEOMETRY_RESIZE
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "job:terminate" in events and not host.running


def test_cycle_cleanup_precedence_retains_prior_reason():
    from test_startup_continue_integration import _cycle
    from clash_rush_rebuild.cycle import StartupDebugCycle
    from clash_rush_rebuild.startup_failure import StartupFailure, StartupReason
    events = []
    cycle = _cycle(events, stop_fails=True)
    cycle.__class__ = StartupDebugCycle
    def recover(*a):
        raise StartupFailure(StartupReason.ICON_VERIFY, input_completed=True)
    cycle._recover = recover
    with pytest.raises(StartupFailure) as caught:
        cycle.visit_once()
    assert caught.value.reason is StartupReason.CLEANUP
    assert caught.value.prior == (StartupReason.ICON_VERIFY,)
    assert caught.value.input_completed is True
    assert caught.value.__context__ is None
    assert events[-1] == "mutex:release"


@pytest.mark.parametrize("field,value", [("operational_failure", 1), ("child_wait_completed", 1), ("cleanup_succeeded", 1), ("reason", "PARENT_TIMEOUT")])
def test_malformed_parent_outcome_cannot_be_success(monkeypatch, capsys, field, value):
    from dataclasses import replace
    outcome = replace(DiagnosticChildOutcome(0, "HOME\n", "", True, True), **{field: value})
    monkeypatch.setattr(cli, "run_owned_diagnostic_child", lambda *a, **k: outcome)
    assert cli.main(args()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    cli.parse_diagnostic_failure_record(captured.err)


def test_failure_record_parse_drops_exception_context():
    with pytest.raises(ValueError) as caught:
        cli.parse_diagnostic_failure_record("private-marker")
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_hidden_child_bad_arguments_still_emit_only_closed_token(capsys):
    assert cli.main(["startup-debug-child", "--private-marker"]) == 2
    assert capsys.readouterr() == ("", "AUTHORIZATION\n")


@pytest.mark.parametrize("reason", list(StartupReason))
def test_complete_child_and_parent_failure_vocabulary(reason, monkeypatch, capsys):
    def build(*a):
        raise StartupFailure(reason)
    assert cli.main(args("startup-debug-child"), startup_debug_cycle_builder=build) == 2
    assert capsys.readouterr() == ("", reason.value + "\n")
    monkeypatch.setattr(cli, "run_owned_diagnostic_child", lambda *a, **k:
        DiagnosticChildOutcome(2, "", reason.value + "\n", True, True))
    assert cli.main(args()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert cli.parse_diagnostic_failure_record(captured.err) == (reason.value, 2, True)


@pytest.mark.parametrize("reason", list(DiagnosticParentReason))
def test_parent_owned_reason_presentation(reason, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_owned_diagnostic_child", lambda *a, **k:
        DiagnosticChildOutcome(None, None, None, False, False, True, reason))
    assert cli.main(args()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert cli.parse_diagnostic_failure_record(captured.err) == (reason.value, None, False)


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt, SystemExit])
def test_child_suppresses_production_output_and_raw_exception(error, capsys):
    def build(*a):
        print("private-marker")
        raise error("private-marker")
    assert cli.main(args("startup-debug-child"), startup_debug_cycle_builder=build) == 2
    assert capsys.readouterr() == ("", "COMPOSITION\n")
