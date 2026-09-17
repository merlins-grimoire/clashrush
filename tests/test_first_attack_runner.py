"""Inert regression coverage for the external one-visit wrapper."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.mvp_local_gameplay import LocalBotMode


RUNNER = (
    Path(os.environ["LOCALAPPDATA"])
    / "hermes"
    / "scripts"
    / "clash_rush_first_attack_once.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("first_attack_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_exclusive_writer_marks_ownership_at_creation(tmp_path: Path) -> None:
    runner = _load_runner()
    payload = b"inert-not-an-approval"
    path = tmp_path / "approval.json"
    ownership = runner.ApprovalOwnership(payload)

    runner.write_exclusive(path, payload, ownership)

    assert ownership.created is True
    assert path.read_bytes() == payload


@pytest.mark.parametrize(
    "failure",
    [
        "transition",
        "canonical",
        "exclusive_create",
        "readback_exception",
        "readback_mismatch",
        "native",
        "consumed_native",
        "control_cleanup",
        "normal",
        "foreign_active",
    ],
)
def test_runner_restores_control_and_cleans_only_its_owned_active_approval(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    runner = _load_runner()
    slots = [f"synthetic-{index}" for index in range(5)]
    monkeypatch.setattr(runner, "SLOTS", SimpleNamespace(read_text=lambda **_kw: "slots"))
    monkeypatch.setattr(
        runner, "OLD_CONFIG", SimpleNamespace(read_text=lambda **_kw: "old")
    )
    monkeypatch.setattr(
        runner.json,
        "loads",
        lambda raw: (
            {"display_names": slots}
            if raw == "slots"
            else {"instances": slots, "instance_tag_hashes": ["a" * 64] * 5}
        ),
    )
    monkeypatch.setattr(
        runner,
        "git",
        lambda *args: (
            runner.EXPECTED_COMMIT
            if args == ("rev-parse", "HEAD^{commit}")
            else runner.EXPECTED_TREE
            if args == ("rev-parse", "HEAD^{tree}")
            else ""
        ),
    )
    monkeypatch.setattr(
        runner,
        "build_native_reconciler",
        lambda *_args: SimpleNamespace(
            _make_state_store=lambda: SimpleNamespace(load=lambda: Ready(0)),
            _observe_absence=lambda: (0, 0),
        ),
    )
    monkeypatch.setattr(runner, "CONTROL", SimpleNamespace(exists=lambda: False))

    state = SimpleNamespace(configuration=None, mode=LocalBotMode.UNCONFIGURED)
    events: list[str] = []

    def setup(configuration):
        state.configuration = configuration
        state.mode = LocalBotMode.STOPPED
        events.append("setup")
        return state

    def load():
        events.append(f"load-{state.mode.value}")
        if failure == "control_cleanup" and state.mode is LocalBotMode.STOPPED:
            raise OSError("synthetic STOPPED read-back failure")
        return state

    def transition(mode):
        state.mode = mode
        events.append(f"transition-{mode.value}")
        if failure == "transition" and mode is LocalBotMode.RUNNING:
            raise KeyboardInterrupt("synthetic transition failure after durable change")
        return state

    monkeypatch.setattr(
        runner,
        "LocalControlStore",
        lambda *_args: SimpleNamespace(setup=setup, load=load, transition=transition),
    )
    monkeypatch.setattr(runner, "encode_control_state", lambda *_args: b"control")

    active = {"payload": b"foreign" if failure == "foreign_active" else None}

    def read_bytes():
        return active["payload"]

    def unlink():
        events.append("unlink")
        active["payload"] = None

    monkeypatch.setattr(
        runner,
        "APPROVAL",
        SimpleNamespace(
            exists=lambda: active["payload"] is not None,
            read_bytes=read_bytes,
            unlink=unlink,
        ),
    )

    def canonical(**_kwargs):
        if failure == "canonical":
            raise SystemExit("synthetic canonical payload failure")
        return b"inert-not-an-approval"

    monkeypatch.setattr(runner, "canonical_approval", canonical)

    def write(_path, payload, ownership=None):
        if failure in {"exclusive_create", "foreign_active"}:
            raise FileExistsError("synthetic exclusive-create failure")
        active["payload"] = payload
        if ownership is not None:
            ownership.created = True
        if failure == "readback_exception":
            raise OSError("synthetic read-back exception")
        if failure == "readback_mismatch":
            raise RuntimeError("synthetic read-back mismatch")

    monkeypatch.setattr(runner, "write_exclusive", write)

    def visit(*_args):
        events.append("native")
        if failure == "consumed_native":
            active["payload"] = None
        if failure in {"native", "consumed_native"}:
            raise KeyboardInterrupt("synthetic native failure")
        return SimpleNamespace(
            status="synthetic", reason_code="synthetic", executed=False, confirmed=False
        )

    monkeypatch.setattr(runner, "run_native_mvp_visit", visit)

    if failure == "normal":
        assert runner.main() == 2
    else:
        with pytest.raises(BaseException):
            runner.main()

    assert state.mode is LocalBotMode.STOPPED
    assert "load-STOPPED" in events
    if failure == "foreign_active":
        assert active["payload"] == b"foreign"
        assert "unlink" not in events
    else:
        assert active["payload"] is None
    if failure in {
        "transition",
        "canonical",
        "exclusive_create",
        "readback_exception",
        "readback_mismatch",
        "foreign_active",
    }:
        assert "native" not in events