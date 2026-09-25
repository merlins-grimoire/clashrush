from __future__ import annotations

from subprocess import CompletedProcess

import pytest

import clash_rush_rebuild.cli as cli
from clash_rush_rebuild.diagnostic_child_job import DiagnosticChildOutcome
from clash_rush_rebuild.readiness_calibration import CalibrationResult
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult


def test_public_calibration_requires_explicit_private_frame_authorization() -> None:
    calls: list[object] = []
    status = cli.main([
        "calibrate-readiness", "--project-root", "X", "--slots", "Y",
    ], calibration_child_runner=lambda *a, **k: calls.append((a, k)))
    assert status == 2
    assert calls == []


def test_public_calibration_owns_one_bounded_child_and_accepts_only_exact_scalar(capsys) -> None:
    calls: list[tuple[object, object]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, "CALIBRATED\n", "")

    status = cli.main([
        "calibrate-readiness", "--project-root", "PRIVATE_ROOT", "--slots", "PRIVATE_SLOTS",
        "--allow-private-full-frames", "--timeout-seconds", "180",
    ], calibration_child_runner=run)

    assert status == 0
    assert capsys.readouterr().out == "CALIBRATED\n"
    command, kwargs = calls[0]
    assert command[-6:] == [
        "calibrate-readiness-child", "--project-root", "PRIVATE_ROOT",
        "--slots", "PRIVATE_SLOTS", "--allow-private-full-frames",
    ]
    assert kwargs == {
        "timeout": 180, "check": False, "capture_output": True, "text": True,
    }


@pytest.mark.parametrize(
    "outcome",
    [
        CompletedProcess([], 0, "CALIBRATED\nextra", ""),
        CompletedProcess([], 1, "CALIBRATED\n", ""),
        CompletedProcess([], 0, "CALIBRATED\n", "PRIVATE_DETAIL"),
        DiagnosticChildOutcome(0, "CALIBRATED\n", "", True, False),
        DiagnosticChildOutcome(
            0, "CALIBRATED\n", "", True, True,
            reason="spoofed-parent-reason",
        ),
    ],
)
def test_public_calibration_rejects_nonexact_or_unclean_child(outcome, capsys) -> None:
    status = cli.main([
        "calibrate-readiness", "--project-root", "PRIVATE_ROOT", "--slots", "PRIVATE_SLOTS",
        "--allow-private-full-frames",
    ], calibration_child_runner=lambda *_a, **_k: outcome)

    assert status == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PRIVATE" not in captured.err


def test_private_calibration_child_emits_only_closed_result_after_cycle_cleanup(capsys) -> None:
    events: list[str] = []

    class Cycle:
        def visit_once(self):
            events.append("visit")
            return StartupContinueResult.HOME

    status = cli.main([
        "calibrate-readiness-child", "--project-root", "X", "--slots", "Y",
        "--allow-private-full-frames",
    ], calibration_cycle_builder=lambda root, slots: (
        events.append(f"build:{root}:{slots}") or Cycle()
    ))

    assert status == 0
    assert events == ["build:X:Y", "visit"]
    assert capsys.readouterr().out == "CALIBRATED\n"


def test_private_calibration_child_parser_failure_is_closed(capsys) -> None:
    status = cli.main([
        "calibrate-readiness-child", "--project-root", "PRIVATE_ROOT",
        "--slots", "PRIVATE_SLOTS", "--private-option", "PRIVATE_VALUE",
    ])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "CALIBRATION\n"
    assert "PRIVATE" not in captured.err


def test_calibration_approval_is_separate_and_requires_full_frame_consent(capsys) -> None:
    calls: list[tuple[str, int]] = []
    assert cli.main([
        "issue-readiness-calibration-approval", "--project-root", "X",
        "--lifetime-seconds", "240", "--allow-private-full-frames",
    ], calibration_approval_issuer=lambda root, seconds: calls.append((root, seconds))) == 0
    assert calls == [("X", 240)]
    assert capsys.readouterr().out == "readiness calibration approval issued\n"
