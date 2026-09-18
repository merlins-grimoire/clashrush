from __future__ import annotations

import json
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from clash_rush_rebuild.control_plane_mvp import (
    CapabilityUnavailable,
    ControlPlane,
    ControlPlaneCapability,
    ControlPlaneMode,
)
from clash_rush_rebuild.control_plane_cli import main as control_plane_main


def test_setup_status_and_schedule_are_control_plane_only(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    service = ControlPlane(root)

    configured = service.setup(("account-a", "account-b"), generation="a" * 32)
    status = service.status()
    selection = service.schedule_next()

    assert configured.mode is ControlPlaneMode.STOPPED
    assert status.mode is ControlPlaneMode.STOPPED
    assert status.lifecycle == "READY"
    assert status.next_slot == 0
    assert status.account_count == 2
    assert status.capabilities == tuple(
        (capability, "UNAVAILABLE") for capability in ControlPlaneCapability
    )
    assert selection.slot_index == 0
    assert selection.account_key == "account-a"
    assert selection.dispatch_allowed is False

    with pytest.raises(CapabilityUnavailable, match="GAMEPLAY_UNAVAILABLE"):
        service.dispatch(selection)

    assert set(path.name for path in (root / "var").iterdir()) == {
        "control-plane-config.json",
        "control-plane-state.json",
    }
    assert json.loads((root / "var" / "control-plane-state.json").read_text()) == {
        "configuration_generation": "a" * 32,
        "schema": 2,
        "state": "READY",
        "tie_after_account_key": None,
    }


def test_cli_exposes_only_inert_control_plane_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    forbidden_modules = {
        "clash_rush_rebuild.capture",
        "clash_rush_rebuild.mvp_local_native",
        "clash_rush_rebuild.mvp_local_runtime",
        "clash_rush_rebuild.mvp_local_gameplay",
        "clash_rush_rebuild.discord_control_protocol",
    }
    loaded_before = forbidden_modules.intersection(sys.modules)
    root = str(tmp_path.resolve())
    assert control_plane_main(
        [
            "setup",
            "--project-root",
            root,
            "--generation",
            "b" * 32,
            "--account-key",
            "account-a",
            "--account-key",
            "account-b",
        ]
    ) == 0
    assert control_plane_main(["status", "--project-root", root]) == 0
    assert control_plane_main(["schedule", "--project-root", root]) == 0

    output = capsys.readouterr().out.splitlines()
    assert output[0] == "control-plane configured mode=STOPPED accounts=2"
    status = json.loads(output[1])
    assert status["mode"] == "STOPPED"
    assert status["lifecycle"] == "READY"
    assert status["gameplay"] == "UNAVAILABLE"
    assert status["readiness"] == "UNAVAILABLE"
    assert output[2] == "schedule slot=0 dispatch=DENIED reason=GAMEPLAY_UNAVAILABLE"
    assert "account-a" not in "\n".join(output)
    assert forbidden_modules.intersection(sys.modules) == loaded_before


def test_installed_command_targets_only_the_control_plane_cli() -> None:
    matching = tuple(
        entry
        for entry in entry_points(group="console_scripts")
        if entry.name == "clash-rush-rebuild"
    )

    assert len(matching) == 1
    assert matching[0].value == "clash_rush_rebuild.control_plane_cli:main"
