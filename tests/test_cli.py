from __future__ import annotations

from pathlib import Path

from clash_rush_rebuild.cli import build_native_state_store, main


class FakeCycle:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def visit_once(self) -> object:
        self.events.append("visit")
        return object()

    def initialize(self) -> object:
        self.events.append("initialize")
        return object()


def test_cli_refuses_inert_visit_without_explicit_owner_approval() -> None:
    events: list[str] = []

    status = main(
        ["visit-one", "--project-root", "X", "--slots", "Y"],
        cycle_builder=lambda *_args: events.append("build"),
    )

    assert status == 2
    assert events == []


def test_cli_composes_exactly_one_visit_after_explicit_owner_approval() -> None:
    events: list[str] = []

    def build(project_root: str, slots_path: str) -> FakeCycle:
        events.append(f"build:{project_root}:{slots_path}")
        return FakeCycle(events)

    status = main(
        [
            "visit-one",
            "--project-root",
            "X",
            "--slots",
            "Y",
            "--owner-approved",
        ],
        cycle_builder=build,
    )

    assert status == 0
    assert events == ["build:X:Y", "visit"]


def test_cli_initializes_state_without_live_approval_or_visit() -> None:
    events: list[str] = []

    def build(project_root: str, slots_path: str) -> FakeCycle:
        events.append(f"build:{project_root}:{slots_path}")
        return FakeCycle(events)

    status = main(
        ["initialize", "--project-root", "X", "--slots", "Y"],
        cycle_builder=build,
    )

    assert status == 0
    assert events == ["build:X:Y", "initialize"]


def test_native_state_composition_creates_validated_private_var_before_port(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    store = build_native_state_store(project)

    assert (project / "var").is_dir()
    assert store.__class__.__name__ == "LifecycleStateStore"


def test_cli_runs_guided_setup_without_composing_lifecycle() -> None:
    events: list[str] = []

    status = main(
        ["setup", "--project-root", "X"],
        cycle_builder=lambda *_args: events.append("cycle"),
        setup_runner=lambda root: events.append(f"setup:{root}"),
    )

    assert status == 0
    assert events == [f"setup:{Path('X')}"]


def test_cli_exports_synthetic_installation_to_selected_file(tmp_path: Path) -> None:
    output = tmp_path / "example.toml"

    status = main(
        ["export-setup-example", "--output", str(output)],
        synthetic_exporter=lambda: "schema = 2\n",
    )

    assert status == 0
    assert output.read_text(encoding="utf-8") == "schema = 2\n"


def test_cli_does_not_overwrite_an_existing_setup_example(tmp_path: Path) -> None:
    output = tmp_path / "example.toml"
    output.write_text("keep\n", encoding="utf-8")

    status = main(
        ["export-setup-example", "--output", str(output)],
        synthetic_exporter=lambda: "schema = 2\n",
    )

    assert status == 1
    assert output.read_text(encoding="utf-8") == "keep\n"
