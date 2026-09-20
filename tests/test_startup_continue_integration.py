from __future__ import annotations

from pathlib import Path

import pytest

import clash_rush_rebuild.mvp_local_native as native
from clash_rush_rebuild.cli import main
from clash_rush_rebuild.approval_reconciliation import ApprovalAction
from clash_rush_rebuild.cycle import StartupContinueCycle
from clash_rush_rebuild.guided_setup import (
    InstallationValidationError,
    install_private_startup_font,
)
from clash_rush_rebuild.input_authorization import InputAuthorization
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity, StopRecord
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.registry import Slot
from clash_rush_rebuild.startup_continue_recovery import StartupContinueResult


BINDING = PlayerBinding(
    ProcessIdentity(100, 9001),
    ProcessIdentity(100, 9001),
    101,
    102,
    1280,
    720,
    "a" * 32,
)


class Lease:
    abandoned = False

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def require_usable(self) -> None:
        self.events.append("mutex:usable")

    def release(self) -> None:
        self.events.append("mutex:release")


class Runtime:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def acquire_mutex(self) -> Lease:
        self.events.append("mutex:acquire")
        return Lease(self.events)


class Store:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def load_with_bytes(self):
        self.events.append("state:load")
        return Ready(0), b'{"next_slot":0,"schema":1,"state":"READY"}\n'


class Approvals:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def validate_then_consume(self, action, tree, state, state_bytes):
        self.events.append(f"approval:{action.value}:{tree}:{state.next_slot}")
        if self.fail:
            raise RuntimeError("expired or replayed")
        return object()


class Supervisor:
    def __init__(self, events: list[str], *, stop_fails: bool = False) -> None:
        self.events = events
        self.stop_fails = stop_fails

    def start(self, slot: Slot) -> PlayerBinding:
        self.events.append(f"start:{slot.index}")
        return BINDING

    def stop(self, binding: PlayerBinding, proof: None) -> StopRecord:
        self.events.append("stop")
        if self.stop_fails:
            raise RuntimeError("cleanup failed")
        return StopRecord(0, "0" * 32, 1, 2, True)


def _cycle(events: list[str], *, approval_fails=False, stop_fails=False):
    slots = tuple(
        Slot(index, f"Pie64_{index}", f"Generic {index}", 1280, 720, 240)
        for index in range(5)
    )
    return StartupContinueCycle(
        Runtime(events),
        load_registry=lambda: events.append("registry") or slots,
        make_state_store=lambda: Store(events),
        observe_player_count=lambda: events.append("players") or 0,
        approvals=Approvals(events, fail=approval_fails),
        candidate_tree=lambda: events.append("tree") or "b" * 40,
        make_supervisor=lambda _store, _mutex: Supervisor(
            events, stop_fails=stop_fails
        ),
        recover=lambda _supervisor, _binding: events.append("recover")
        or StartupContinueResult.HOME,
    )


def test_action_cycle_consumes_exact_approval_before_launch_and_always_stops() -> None:
    events: list[str] = []
    assert _cycle(events).visit_once() is StartupContinueResult.HOME
    assert events == [
        "mutex:acquire",
        "mutex:usable",
        "registry",
        "state:load",
        "players",
        "tree",
        "approval:STARTUP_CONTINUE_ONLY:" + "b" * 40 + ":0",
        "start:0",
        "recover",
        "stop",
        "mutex:release",
    ]


def test_expired_or_replayed_approval_prevents_launch_and_input() -> None:
    events: list[str] = []
    with pytest.raises(RuntimeError, match="LAUNCH"):
        _cycle(events, approval_fails=True).visit_once()
    assert "recover" not in events
    assert not any(event.startswith("start:") for event in events)


def test_action_cycle_reports_cleanup_over_recovery_result() -> None:
    events: list[str] = []
    with pytest.raises(RuntimeError, match="CLEANUP"):
        _cycle(events, stop_fails=True).visit_once()
    assert events[-1] == "mutex:release"


def test_native_continue_port_revalidates_authority_and_is_one_shot(monkeypatch) -> None:
    calls: list[object] = []

    class BoundInput:
        def __init__(self, binding, safety_check, authorization):
            calls.append((binding, safety_check, authorization.purpose))

        def click(self, binding, x, y, *, action):
            calls.append((binding, x, y, action))
            return True

    monkeypatch.setattr(native, "Win32BoundInput", BoundInput)
    subject = native.Win32StartupContinueInput(
        BINDING,
        lambda _binding: None,
        InputAuthorization.startup_continue_only(lambda: True),
    )
    assert subject.click_continue(BINDING, 0.25, 0.75) is True
    assert subject.click_continue(BINDING, 0.25, 0.75) is False


def test_setup_copies_operator_font_only_under_ignored_private_assets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "operator-font.ttf"
    source.write_bytes(b"synthetic-font-bytes")

    result = install_private_startup_font(project, source)

    assert result == project / "private" / "assets" / "CCBackBeat.ttf"
    assert result.read_bytes() == b"synthetic-font-bytes"
    assert source.exists()


def test_setup_rejects_missing_or_existing_private_font(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(InstallationValidationError, match="font"):
        install_private_startup_font(project, tmp_path / "missing.ttf")
    source = tmp_path / "font.ttf"
    source.write_bytes(b"first")
    install_private_startup_font(project, source)
    with pytest.raises(InstallationValidationError, match="already exists"):
        install_private_startup_font(project, source)


def test_approval_vocabulary_has_exact_separate_continue_action() -> None:
    assert ApprovalAction.STARTUP_CONTINUE_ONLY.value == "STARTUP_CONTINUE_ONLY"


def test_cli_installs_private_font_through_explicit_setup_command(tmp_path: Path) -> None:
    calls: list[tuple[Path, Path]] = []
    assert main(
        [
            "setup-startup-font",
            "--project-root",
            str(tmp_path),
            "--font",
            str(tmp_path / "font.ttf"),
        ],
        startup_font_installer=lambda project, font: calls.append((project, font)),
    ) == 0
    assert calls == [(tmp_path, tmp_path / "font.ttf")]


def test_cli_issues_and_runs_continue_as_two_separate_explicit_steps(tmp_path: Path) -> None:
    issued: list[tuple[str, int]] = []
    ran: list[tuple[str, str]] = []
    assert main(
        [
            "issue-startup-continue-approval",
            "--project-root",
            str(tmp_path),
            "--lifetime-seconds",
            "120",
        ],
        startup_approval_issuer=lambda project, lifetime: issued.append(
            (project, lifetime)
        ),
    ) == 0
    assert main(
        [
            "startup-continue-one",
            "--project-root",
            str(tmp_path),
            "--slots",
            "private/slots.json",
        ],
        startup_continue_runner=lambda project, slots: ran.append((project, slots))
        or StartupContinueResult.HOME,
    ) == 0
    assert issued == [(str(tmp_path), 120)]
    assert ran == [(str(tmp_path), "private/slots.json")]
