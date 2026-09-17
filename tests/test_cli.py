from __future__ import annotations

from pathlib import Path

from clash_rush_rebuild.cli import build_native_state_store, main
from clash_rush_rebuild.mvp_local_gameplay import VisitResult


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


def test_local_mvp_controls_persist_across_cli_processes_and_redact_output(
    tmp_path: Path, capsys,
) -> None:
    common = ["--project-root", str(tmp_path)]
    assert main([
        "mvp-setup", *common,
        "--team-ref", "team-secret",
        "--account-ref", "account-secret",
        "--instance-ref", "instance-secret",
    ]) == 0
    assert main(["mvp-run", *common]) == 0
    assert main(["mvp-status", *common]) == 0
    assert main(["mvp-pause", *common]) == 0
    assert main(["mvp-status", *common]) == 0
    assert main(["mvp-stop", *common]) == 0

    output = capsys.readouterr().out
    assert "RUNNING" in output
    assert "PAUSED" in output
    assert "team-secret" not in output
    assert "account-secret" not in output
    assert "instance-secret" not in output


def test_local_mvp_visit_requires_live_approval_before_composition() -> None:
    events: list[str] = []

    status = main(
        [
            "mvp-visit-one",
            "--project-root", "X",
            "--slots", "Y",
            "--transaction-ref", "tx-one",
        ],
        mvp_visit_runner=lambda *_args: events.append("visit"),
    )

    assert status == 2
    assert events == []


def test_local_mvp_cli_runs_one_bounded_attack_composition(capsys) -> None:
    events: list[str] = []

    def run(project_root: str, slots: str, transaction_ref: str) -> VisitResult:
        events.append(f"visit:{project_root}:{slots}:{transaction_ref}")
        return VisitResult("completed", "RETURNED_HOME", True, True)

    status = main(
        [
            "mvp-visit-one",
            "--project-root", "X",
            "--slots", "Y",
            "--transaction-ref", "tx-one",
            "--owner-approved-live",
        ],
        mvp_visit_runner=run,
    )

    assert status == 0
    assert events == ["visit:X:Y:tx-one"]
    output = capsys.readouterr().out
    assert output.strip() == "visit status=completed reason=RETURNED_HOME"
