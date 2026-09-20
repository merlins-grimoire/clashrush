from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from clash_rush_rebuild.lifecycle import StopRecord
from clash_rush_rebuild.mvp_local_gameplay import LocalBotMode, MvpConfiguration
from clash_rush_rebuild.mvp_local_runtime import PersistentControl
from clash_rush_rebuild.donor_spine_lifecycle_adapter import (
    DonorSpineLifecycleAdapter,
    ExplicitRunAuthorization,
    ProtectedLifecycleLog,
    SpineBoundaryError,
)


CONFIG = MvpConfiguration(
    "team-synthetic-one",
    "account-synthetic-one",
    "slot-3",
    "a" * 64,
)


@dataclass
class FakeControl:
    events: list[str]
    state: PersistentControl = PersistentControl(CONFIG, LocalBotMode.RUNNING)

    def load(self) -> PersistentControl:
        self.events.append("control:load")
        return self.state


@dataclass
class FakeCycle:
    events: list[str]
    result: StopRecord = StopRecord(3, "b" * 32, 1, 2, True)

    def visit_once(self) -> StopRecord:
        self.events.append("cycle:visit")
        return self.result


def test_missing_explicit_authorization_reaches_no_configuration_or_lifecycle(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    adapter = DonorSpineLifecycleAdapter(
        FakeControl(events),
        FakeCycle(events),
        ProtectedLifecycleLog(tmp_path / "private-logs" / "lifecycle.jsonl"),
    )

    with pytest.raises(SpineBoundaryError, match="authorization"):
        adapter.run_once(None)  # type: ignore[arg-type]

    assert events == []
    assert not (tmp_path / "private-logs").exists()


def test_one_shot_authorization_selects_exact_slot_and_logs_only_sanitized_stop(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    sealed: list[tuple[Path, bool]] = []
    log = ProtectedLifecycleLog(
        tmp_path / "private-logs" / "lifecycle.jsonl",
        permission_sealer=lambda path, directory: sealed.append((path, directory)),
    )
    adapter = DonorSpineLifecycleAdapter(FakeControl(events), FakeCycle(events), log)
    approval = ExplicitRunAuthorization("c" * 32, selected_slot=3)

    record = adapter.run_once(approval)

    assert record.slot == 3
    assert events == ["control:load", "cycle:visit"]
    assert approval.consumed is True
    payload = (tmp_path / "private-logs" / "lifecycle.jsonl").read_text(
        encoding="ascii"
    )
    assert json.loads(payload) == {
        "event": "NO_INPUT_STOPPED",
        "schema": 1,
        "slot": 3,
    }
    assert "account-synthetic-one" not in payload
    assert "team-synthetic-one" not in payload
    assert sealed == [
        (tmp_path / "private-logs", True),
        (tmp_path / "private-logs" / "lifecycle.jsonl", False),
    ]

    with pytest.raises(SpineBoundaryError, match="consumed"):
        adapter.run_once(approval)
    assert events == ["control:load", "cycle:visit"]


def test_selected_instance_must_match_the_verified_stop_record(tmp_path: Path) -> None:
    events: list[str] = []
    cycle = FakeCycle(events, StopRecord(2, "b" * 32, 1, 2, True))
    adapter = DonorSpineLifecycleAdapter(
        FakeControl(events),
        cycle,
        ProtectedLifecycleLog(tmp_path / "private-logs" / "lifecycle.jsonl"),
    )

    with pytest.raises(SpineBoundaryError, match="selected slot"):
        adapter.run_once(ExplicitRunAuthorization("c" * 32, selected_slot=3))

    assert not (tmp_path / "private-logs").exists()


@pytest.mark.parametrize("mode", [LocalBotMode.STOPPED, LocalBotMode.PAUSED])
def test_non_running_control_never_reaches_lifecycle(
    tmp_path: Path, mode: LocalBotMode
) -> None:
    events: list[str] = []
    control = FakeControl(events, PersistentControl(CONFIG, mode))
    adapter = DonorSpineLifecycleAdapter(
        control,
        FakeCycle(events),
        ProtectedLifecycleLog(tmp_path / "private-logs" / "lifecycle.jsonl"),
    )

    with pytest.raises(SpineBoundaryError, match="RUNNING"):
        adapter.run_once(ExplicitRunAuthorization("c" * 32, selected_slot=3))

    assert events == ["control:load"]
