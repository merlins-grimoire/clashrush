from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clash_rush_rebuild.input_authorization import InputAction

from clash_rush_rebuild.mvp_local_approval import (
    LIVE_ACTION,
    ApprovalError,
    LiveApprovalStore,
    canonical_approval,
)
from clash_rush_rebuild.mvp_local_world_export import (
    EXPORT_BUTTON,
    MORE_SETTINGS_BUTTON,
    SETTINGS_BUTTON,
    ExportCaptureError,
    capture_world_export,
    summarize_world_export,
)


TREE = "a" * 40
LIFECYCLE = b'{"next_slot":0,"schema":1,"state":"READY"}\n'
CONTROL = b'{"configuration":{"account_ref":"account","instance_ref":"instance","player_tag_sha256":"' + b"b" * 64 + b'","team_ref":"team"},"mode":"RUNNING","schema":2}\n'


def _approval(*, expires_at: int = 200, tree: str = TREE) -> bytes:
    return canonical_approval(
        action=LIVE_ACTION,
        candidate_tree=tree,
        issued_at=100,
        expires_at=expires_at,
        lifecycle_sha256=hashlib.sha256(LIFECYCLE).hexdigest(),
        control_sha256=hashlib.sha256(CONTROL).hexdigest(),
        nonce="c" * 32,
    )


def test_live_approval_is_bound_consumed_once_and_mismatch_is_not_consumed(tmp_path: Path) -> None:
    store = LiveApprovalStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(_approval(tree="d" * 40))

    with pytest.raises(ApprovalError, match="candidate tree"):
        store.validate_and_consume(
            candidate_tree=TREE,
            lifecycle_bytes=LIFECYCLE,
            control_bytes=CONTROL,
            now=150,
        )
    assert store.path.exists()

    store.path.write_bytes(_approval())
    store.validate_and_consume(
        candidate_tree=TREE,
        lifecycle_bytes=LIFECYCLE,
        control_bytes=CONTROL,
        now=150,
    )
    assert not store.path.exists()
    assert (store.consumed_dir / ("c" * 32 + ".json")).read_bytes() == _approval()
    with pytest.raises(ApprovalError, match="unavailable"):
        store.validate_and_consume(
            candidate_tree=TREE,
            lifecycle_bytes=LIFECYCLE,
            control_bytes=CONTROL,
            now=150,
        )


@pytest.mark.parametrize("field", ("issued_at", "expires_at"))
def test_live_approval_rejects_boolean_times(tmp_path: Path, field: str) -> None:
    raw = json.loads(_approval())
    raw[field] = True
    path = LiveApprovalStore(tmp_path).path
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n")

    with pytest.raises(ApprovalError, match="malformed"):
        LiveApprovalStore(tmp_path).validate_and_consume(
            candidate_tree=TREE,
            lifecycle_bytes=LIFECYCLE,
            control_bytes=CONTROL,
            now=150,
        )


def _export(tag: str = "#SYNTHETIC") -> str:
    return json.dumps({"tag": tag, "timestamp": 1_788_894_243, "buildings": []})


def test_world_export_matches_hash_without_exposing_tag() -> None:
    expected = hashlib.sha256(b"#SYNTHETIC").hexdigest()
    summary = summarize_world_export(
        _export(), expected_tag_sha256=expected,
        now=datetime.fromtimestamp(1_788_894_243, UTC),
    )
    assert summary.account_matches is True
    assert "SYNTHETIC" not in repr(summary)

    with pytest.raises(ExportCaptureError, match="account binding"):
        summarize_world_export(
            _export(), expected_tag_sha256="0" * 64,
            now=datetime.fromtimestamp(1_788_894_243, UTC),
        )


class FakeInput:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def click(
        self,
        _binding: object,
        x: float,
        y: float,
        *,
        action: InputAction,
    ) -> bool:
        self.events.append(("click", x, y, action))
        return True

    def drag(
        self,
        _binding: object,
        *points: float,
        action: InputAction,
    ) -> bool:
        self.events.append(("drag", *points, action))
        return True


def test_world_export_navigation_is_live_gated_and_clipboard_is_always_cleared() -> None:
    inputs = FakeInput()
    reads = iter(["old", _export()])
    clears: list[bool] = []
    gates = iter([True, True, True, True, True, True])

    summary = capture_world_export(
        object(), input_port=inputs,
        expected_tag_sha256=hashlib.sha256(b"#SYNTHETIC").hexdigest(),
        live_gate=lambda: next(gates),
        clipboard_read=lambda: next(reads),
        clipboard_clear=lambda: clears.append(True),
        sleep=lambda _seconds: None,
        now=datetime.fromtimestamp(1_788_894_243, UTC),
    )

    assert summary.account_matches is True
    assert inputs.events == [
        ("click", *SETTINGS_BUTTON, InputAction.ACCOUNT_EXPORT_NAVIGATION),
        ("click", *MORE_SETTINGS_BUTTON, InputAction.ACCOUNT_EXPORT_NAVIGATION),
        ("drag", 0.50, 0.74, 0.50, 0.24, InputAction.ACCOUNT_EXPORT_NAVIGATION),
        ("drag", 0.50, 0.74, 0.50, 0.24, InputAction.ACCOUNT_EXPORT_NAVIGATION),
        ("drag", 0.50, 0.74, 0.50, 0.24, InputAction.ACCOUNT_EXPORT_NAVIGATION),
        ("click", *EXPORT_BUTTON, InputAction.ACCOUNT_EXPORT_NAVIGATION),
    ]
    assert len(clears) == 2


def test_world_export_gate_failure_emits_no_later_input_and_clears_clipboard() -> None:
    inputs = FakeInput()
    clears: list[bool] = []
    gates = iter([True, False])

    with pytest.raises(ExportCaptureError, match="live gate"):
        capture_world_export(
            object(), input_port=inputs, expected_tag_sha256="0" * 64,
            live_gate=lambda: next(gates), clipboard_read=lambda: "old",
            clipboard_clear=lambda: clears.append(True), sleep=lambda _seconds: None,
            now=datetime.now(UTC),
        )
    assert inputs.events == [
        ("click", *SETTINGS_BUTTON, InputAction.ACCOUNT_EXPORT_NAVIGATION)
    ]
    assert len(clears) == 2


def test_world_export_hash_mismatch_clears_clipboard_and_returns_no_evidence() -> None:
    inputs = FakeInput()
    reads = iter(["old", _export("#OTHER")])
    clears: list[bool] = []

    with pytest.raises(ExportCaptureError, match="account binding"):
        capture_world_export(
            object(), input_port=inputs,
            expected_tag_sha256=hashlib.sha256(b"#EXPECTED").hexdigest(),
            live_gate=lambda: True, clipboard_read=lambda: next(reads),
            clipboard_clear=lambda: clears.append(True), sleep=lambda _seconds: None,
            now=datetime.fromtimestamp(1_788_894_243, UTC),
        )
    assert len(clears) == 2
