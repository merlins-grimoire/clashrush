from __future__ import annotations

from pathlib import Path


def test_production_source_confines_attack_input_and_exposes_no_prohibited_transport() -> None:
    source_root = Path(__file__).parents[1] / "src" / "clash_rush_rebuild"
    prohibited = (
        "adb",
        "postmessage",
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

    native_input = ("mouse_event", "keybd_event", "setcursorpos")
    carriers = []
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        if any(token in text for token in native_input):
            carriers.append(path.name)
    assert carriers == ["mvp_local_native.py"]
