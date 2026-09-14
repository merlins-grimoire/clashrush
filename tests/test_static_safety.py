from __future__ import annotations

from pathlib import Path


def test_production_source_exposes_no_prohibited_transport_or_frame_writer() -> None:
    source_root = Path(__file__).parents[1] / "src" / "clash_rush_rebuild"
    prohibited = (
        "adb",
        "postmessage",
        "mouse_event",
        "keybd_event",
        "setcursorpos",
        ".save(",
        "imwrite",
        "screenshot",
    )
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        for token in prohibited:
            assert token not in text, (
                f"prohibited production token {token!r} in {path.name}"
            )
