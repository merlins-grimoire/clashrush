from __future__ import annotations

from pathlib import Path


def test_ordinary_tests_do_not_import_personal_profile_scripts() -> None:
    tests = Path(__file__).parent
    forbidden = ("AppData", "LOCALAPPDATA", "/hermes/scripts", "\\hermes\\scripts")
    offenders: list[str] = []
    for path in sorted(tests.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in forbidden):
            offenders.append(path.name)
    assert offenders == []


def test_source_bound_harness_does_not_copy_the_ambient_working_tree() -> None:
    tests = Path(__file__).parent
    offenders: list[str] = []
    for path in sorted(tests.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if "copytree(" in text:
            offenders.append(path.name)
    assert offenders == []
