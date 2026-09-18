from __future__ import annotations

import builtins
import json
import os
import socket
import subprocess
import sys
import urllib.request
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from clash_rush_rebuild.control_plane_mvp import (
    CapabilityUnavailable,
    ControlPlane,
    ControlPlaneCapability,
    ControlPlaneError,
    ControlPlaneMode,
)
from clash_rush_rebuild.control_plane_cli import main as control_plane_main
from clash_rush_rebuild.configuration_v2 import AccountKey, ConfigurationGeneration
from clash_rush_rebuild.lifecycle_state import BlockReason
from clash_rush_rebuild.lifecycle_state_v2 import ActiveV2, ReadyV2, encode_state_v2


class MemoryStatePort:
    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.redirected = False

    def _check(self) -> None:
        if self.redirected:
            raise OSError("synthetic redirected path")

    def create_new_temp_write_through(self, path: Path) -> object:
        self._check()
        if path in self.files:
            raise FileExistsError(path)
        self.files[path] = b""
        return path

    def write(self, file: object, data: memoryview) -> int:
        self._check()
        path = Path(file)
        self.files[path] += bytes(data)
        return len(data)

    def flush(self, file: object) -> None:
        self._check()

    def close(self, file: object) -> None:
        pass

    def replace_write_through(self, source: Path, target: Path) -> None:
        self._check()
        self.files[target] = self.files.pop(source)

    def open_read_exclusive_no_reparse(self, path: Path) -> object:
        self._check()
        if path not in self.files:
            raise FileNotFoundError(path)
        return path

    def read_bounded(self, file: object, limit: int) -> bytes:
        self._check()
        payload = self.files[Path(file)]
        if len(payload) > limit:
            raise OSError("synthetic oversized file")
        return payload

    def delete_file_no_reparse(self, path: Path) -> None:
        self._check()
        del self.files[path]


def test_setup_status_and_schedule_are_control_plane_only(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    port = MemoryStatePort()
    service = ControlPlane(root, file_port=port)

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

    assert {path.name for path in port.files} == {
        "control-plane-config.json",
        f"lifecycle-state-v2.{'a' * 32}.json",
    }
    state_path = root / "var" / f"lifecycle-state-v2.{'a' * 32}.json"
    assert json.loads(port.files[state_path]) == {
        "configuration_generation": "a" * 32,
        "schema": 2,
        "state": "READY",
        "tie_after_account_key": None,
    }


def test_setup_failure_after_configuration_replace_leaves_guard_and_blocks_reads(
    tmp_path: Path,
) -> None:
    class CorruptingPort(MemoryStatePort):
        def replace_write_through(self, source: Path, target: Path) -> None:
            super().replace_write_through(source, target)
            if target.name == "control-plane-config.json":
                self.files[target] += b"corrupt"

    root = tmp_path.resolve()
    port = CorruptingPort()
    service = ControlPlane(root, file_port=port)

    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.setup(("account-a",), generation="a" * 32)

    assert root / "var" / ".control-plane-setup.transition" in port.files
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.status()


@pytest.mark.parametrize("failure", ["partial", "flush"])
def test_partial_or_unflushed_configuration_write_preserves_setup_guard(
    tmp_path: Path,
    failure: str,
) -> None:
    class FailingPort(MemoryStatePort):
        def write(self, file: object, data: memoryview) -> int:
            if (
                Path(file).name.startswith(".control-plane-config.json.")
                and failure == "partial"
            ):
                return 0
            return super().write(file, data)

        def flush(self, file: object) -> None:
            if (
                Path(file).name.startswith(".control-plane-config.json.")
                and failure == "flush"
            ):
                raise OSError("synthetic flush failure")
            super().flush(file)

    root = tmp_path.resolve()
    port = FailingPort()
    service = ControlPlane(root, file_port=port)

    with pytest.raises(ControlPlaneError):
        service.setup(("account-a",), generation="a" * 32)

    assert root / "var" / ".control-plane-setup.transition" in port.files
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.schedule_next()


def test_guard_active_malformed_redirected_and_mismatched_state_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    generation = ConfigurationGeneration("a" * 32)
    cases = (
        b"not-json\n",
        encode_state_v2(
            ActiveV2(
                generation,
                AccountKey("account-a"),
                0,
                "b" * 32,
                BlockReason.WINDOW_BINDING,
            )
        ),
        encode_state_v2(ReadyV2(ConfigurationGeneration("b" * 32), None)),
        encode_state_v2(ReadyV2(generation, AccountKey("account-z"))),
    )
    for payload in cases:
        port = MemoryStatePort()
        service = ControlPlane(root, file_port=port)
        service.setup(("account-a",), generation=generation.value)
        state_path = root / "var" / f"lifecycle-state-v2.{generation.value}.json"
        port.files[state_path] = payload
        with pytest.raises(ControlPlaneError, match="control-plane"):
            service.schedule_next()

    port = MemoryStatePort()
    service = ControlPlane(root, file_port=port)
    service.setup(("account-a",), generation=generation.value)
    setup_guard = root / "var" / ".control-plane-setup.transition"
    port.files[setup_guard] = b"setup\n"
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.status()

    port.files.pop(setup_guard)
    port.redirected = True
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.status()


def test_oversized_configuration_and_lifecycle_transition_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    generation = "a" * 32
    port = MemoryStatePort()
    service = ControlPlane(root, file_port=port)
    service.setup(("account-a",), generation=generation)

    configuration_path = root / "var" / "control-plane-config.json"
    original_configuration = port.files[configuration_path]
    port.files[configuration_path] = b"x" * 4098
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.status()

    port.files[configuration_path] = original_configuration
    lifecycle_guard = root / "var" / f".lifecycle-state-v2.{generation}.transition"
    port.files[lifecycle_guard] = (
        b'{"configuration_generation":"' + generation.encode() + b'","schema":2}\n'
    )
    with pytest.raises(ControlPlaneError, match="control-plane"):
        service.schedule_next()


def test_repeated_setup_is_rejected_without_stranding_guard(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    port = MemoryStatePort()
    ControlPlane(root, file_port=port).setup(("account-a",), generation="a" * 32)

    with pytest.raises(ControlPlaneError, match="already configured"):
        ControlPlane(root, file_port=port).setup(
            ("account-b",), generation="b" * 32
        )

    assert root / "var" / ".control-plane-setup.transition" not in port.files


def test_cli_exposes_only_inert_control_plane_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden_modules = {
        "clash_rush_rebuild.capture",
        "clash_rush_rebuild.cli",
        "clash_rush_rebuild.mvp_local_native",
        "clash_rush_rebuild.mvp_local_runtime",
        "clash_rush_rebuild.mvp_local_gameplay",
        "clash_rush_rebuild.discord_control_protocol",
        "clash_rush_rebuild.win32_lifecycle_host",
        "clash_rush_rebuild.win32_runtime",
    }
    loaded_before = forbidden_modules.intersection(sys.modules)
    calls: list[str] = []

    def forbidden(name: str):
        def fail(*args: object, **kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"forbidden boundary called: {name}")

        return fail

    monkeypatch.setattr(subprocess, "Popen", forbidden("process:Popen"))
    monkeypatch.setattr(subprocess, "run", forbidden("process:run"))
    monkeypatch.setattr(os, "system", forbidden("process:system"))
    monkeypatch.setattr(socket, "socket", forbidden("network:socket"))
    monkeypatch.setattr(socket, "create_connection", forbidden("network:connect"))
    monkeypatch.setattr(urllib.request, "urlopen", forbidden("network:urlopen"))
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", forbidden("process:startfile"))
    original_import = builtins.__import__
    forbidden_imports = forbidden_modules | {
        "cv2",
        "discord",
        "httpx",
        "keyring",
        "PIL",
        "requests",
        "win32cred",
    }

    def guarded_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in forbidden_imports
        ):
            return forbidden(f"import:{name}")()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
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
    assert calls == []
    assert not tuple(
        path
        for path in tmp_path.rglob("*")
        if path.suffix.lower() in {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
    )

    service = ControlPlane(tmp_path.resolve())
    selection = service.schedule_next()
    with pytest.raises(CapabilityUnavailable, match="GAMEPLAY_UNAVAILABLE"):
        service.dispatch(object())  # type: ignore[arg-type]
    with pytest.raises(CapabilityUnavailable, match="GAMEPLAY_UNAVAILABLE"):
        service.dispatch(selection)
    assert calls == []

    state_path = (
        tmp_path
        / "var"
        / f"lifecycle-state-v2.{'b' * 32}.json"
    )
    state_path.write_bytes(b"malformed\n")
    assert control_plane_main(["status", "--project-root", root]) == 1
    assert control_plane_main(["schedule", "--project-root", root]) == 1
    assert calls == []

    with pytest.raises(SystemExit):
        control_plane_main(["run", "--project-root", root])
    assert calls == []


def test_installed_command_targets_only_the_control_plane_cli() -> None:
    matching = tuple(
        entry
        for entry in entry_points(group="console_scripts")
        if entry.name == "clash-rush-rebuild"
    )

    assert len(matching) == 1
    assert matching[0].value == "clash_rush_rebuild.control_plane_cli:main"
