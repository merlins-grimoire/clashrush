from __future__ import annotations

import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DONOR = ROOT / "donors" / "coc_bot"
SEAL = DONOR / "donor-files.sha256"


def _sealed_files() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in SEAL.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        result[relative] = digest
    return result


def test_complete_donor_runtime_snapshot_matches_byte_seal() -> None:
    sealed = _sealed_files()
    copied = {
        path.relative_to(DONOR).as_posix()
        for path in DONOR.rglob("*")
        if path.is_file()
        and path.name not in {"README.md", "donor-files.sha256"}
    }

    assert len(sealed) == 37
    assert copied == set(sealed)
    for relative, expected in sealed.items():
        actual = hashlib.sha256((DONOR / relative).read_bytes()).hexdigest()
        assert actual == expected, relative


def test_copied_python_sources_are_syntactically_complete() -> None:
    sources = sorted((DONOR / "src").rglob("*.py"))
    assert {path.relative_to(DONOR).as_posix() for path in sources} == {
        "src/attacker.py",
        "src/coc_bot.py",
        "src/configs.template.py",
        "src/gui.py",
        "src/gui_server/__init__.py",
        "src/gui_server/gui_server.py",
        "src/launch.py",
        "src/log.py",
        "src/main.py",
        "src/test.py",
        "src/upgrader.py",
        "src/utils.py",
    }
    for source in sources:
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def test_operational_spine_and_both_villages_are_present() -> None:
    main = (DONOR / "src" / "main.py").read_text(encoding="utf-8")
    launch = (DONOR / "src" / "launch.py").read_text(encoding="utf-8")
    bot = (DONOR / "src" / "coc_bot.py").read_text(encoding="utf-8")
    utils = (DONOR / "src" / "utils.py").read_text(encoding="utf-8")

    assert "from launch import launch" in main
    assert "bot = CoC_Bot()" in launch and "bot.run()" in launch
    expected_calls = (
        "start_coc(detailed=True)",
        "run_home_base",
        "run_builder_base",
        "to_home_base()",
        "stop_coc(sleep=True)",
    )
    positions = [bot.index(call) for call in expected_calls]
    assert positions == sorted(positions)
    assert "class Frame_Handler" in utils
    assert "class Input_Handler" in utils
    assert "def start_coc(" in utils
    assert "def stop_coc(" in utils


def test_snapshot_is_inert_and_not_project_packaged() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_sources = list((ROOT / "src" / "clash_rush_rebuild").rglob("*.py"))

    assert not (DONOR / "src" / "configs.py").exists()
    assert not (DONOR / "assets" / "fonts" / "CCBackBeat.ttf").exists()
    assert not (DONOR / "assets" / "fonts" / "SupercellMagic.ttf").exists()
    assert "donors/coc_bot" not in pyproject
    assert all("donors.coc_bot" not in path.read_text(encoding="utf-8") for path in project_sources)
