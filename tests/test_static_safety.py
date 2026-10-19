from __future__ import annotations

from pathlib import Path


def test_production_source_confines_attack_input_and_exposes_no_prohibited_transport() -> None:
    source_root = Path(__file__).parents[1] / "src" / "clash_rush_rebuild"
    prohibited = (
        "adb",
        "postmessage",
        ".save(",
        "imwrite",
    )
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        for token in prohibited:
            # Attachment 535 allows exactly the dormant read-only build source.
            # Its command/default-deny matrix lives in test_policy_installed_closure.
            if token == 'adb' and path.name == 'mvp_installed_build.py':
                continue
            # SQLite readback evidence is not an Android transport reference.
            checked = text.replace('readback','read_back') if token == 'adb' else text
            assert token not in checked, (
                f"prohibited production token {token!r} in {path.name}"
            )

    memory_capture_carriers = []
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        if "screenshot" in text:
            memory_capture_carriers.append(path.name)
    assert sorted(memory_capture_carriers) == [
        "basepilot_window.py",
        "no_input_home_diagnostic.py",
        "startup_continue_recovery.py",
        "startup_debug_native.py",
    ]

    native_input = ("mouse_event", "keybd_event", "setcursorpos")
    carriers = []
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        if any(token in text for token in native_input):
            carriers.append(path.name)
    assert sorted(carriers) == [
        "mvp_deployment_input.py",
        "mvp_local_native.py",
        "startup_debug_native.py",
    ]
