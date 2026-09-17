from __future__ import annotations

from pathlib import Path

import clash_rush_rebuild.cli as cli_module
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


class FakeApprovalIssuer:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def issue(self, *, lifetime_seconds: int) -> object:
        self.events.append(f"issue:{lifetime_seconds}")
        return object()


class FakeReconciler:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def reconcile(self) -> object:
        self.events.append("reconcile")
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


def test_native_state_composition_injects_state_adapter_without_constructing_native(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    class FakeStateApi:
        @staticmethod
        def get_file_attributes(_path: str) -> int:
            return 0

    fake_state_api = FakeStateApi()
    store = build_native_state_store(project, state_api_factory=lambda: fake_state_api)

    assert (project / "var").is_dir()
    assert store.__class__.__name__ == "LifecycleStateStore"


def test_inert_diagnostic_composition_explicitly_preserves_ready_cursor(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingCycle:
        def __init__(self, runtime: object, **kwargs: object) -> None:
            captured["runtime"] = runtime
            captured.update(kwargs)

    def capture_supervisor(*args: object, **kwargs: object) -> object:
        captured["supervisor_args"] = args
        captured["supervisor_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(cli_module, "NativeLifecycleApi", lambda: "native")
    monkeypatch.setattr(cli_module, "Win32Runtime", lambda native: ("runtime", native))
    monkeypatch.setattr(
        cli_module,
        "Win32LifecycleHost",
        lambda native, nonce_factory: ("host", native, nonce_factory),
    )
    monkeypatch.setattr(cli_module, "LifecycleSupervisor", capture_supervisor)
    monkeypatch.setattr(cli_module, "InertCycle", CapturingCycle)

    cli_module.build_inert_cycle(
        "project", "slots.toml", preserve_ready_cursor=True
    )
    make_supervisor = captured["make_supervisor"]
    assert callable(make_supervisor)
    make_supervisor(object(), "Global\\ClashRushRebuildLifecycle-v1")

    supervisor_kwargs = captured["supervisor_kwargs"]
    assert isinstance(supervisor_kwargs, dict)
    assert supervisor_kwargs["preserve_ready_cursor"] is True
    assert callable(supervisor_kwargs["nonce_factory"])
    assert set(supervisor_kwargs) == {"nonce_factory", "preserve_ready_cursor"}


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
        "--player-tag-sha256", "a" * 64,
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


def test_local_mvp_visit_cannot_use_a_callback_to_bypass_approval(monkeypatch) -> None:
    events: list[str] = []

    def reject(*_args):
        events.append("native-entry")
        raise RuntimeError("approval unavailable")

    monkeypatch.setattr(cli_module, "run_native_mvp_visit", reject)

    status = main(
        [
            "mvp-visit-one",
            "--project-root", "X",
            "--slots", "Y",
            "--transaction-ref", "tx-one",
        ],
    )

    assert status == 1
    assert events == ["native-entry"]


def test_local_mvp_cli_runs_only_the_sealed_native_entry(monkeypatch, capsys) -> None:
    events: list[str] = []

    def run(project_root: str, slots: str, transaction_ref: str) -> VisitResult:
        events.append(f"visit:{project_root}:{slots}:{transaction_ref}")
        return VisitResult("completed", "RETURNED_HOME", True, True)

    monkeypatch.setattr(cli_module, "run_native_mvp_visit", run)

    status = main(
        [
            "mvp-visit-one",
            "--project-root", "X",
            "--slots", "Y",
            "--transaction-ref", "tx-one",
        ],
    )

    assert status == 0
    assert events == ["visit:X:Y:tx-one"]
    output = capsys.readouterr().out
    assert output.strip() == "visit status=completed reason=RETURNED_HOME"


def test_cli_issues_separate_short_lived_reconciliation_approval(capsys) -> None:
    events: list[str] = []

    status = main(
        [
            "issue-reconciliation-approval",
            "--project-root",
            "X",
            "--lifetime-seconds",
            "240",
        ],
        approval_issuer_builder=lambda root: (
            events.append(f"build-issuer:{root}") or FakeApprovalIssuer(events)
        ),
    )

    assert status == 0
    assert events == ["build-issuer:X", "issue:240"]
    assert capsys.readouterr().out.strip() == "reconciliation approval issued"


def test_cli_consumes_reconciliation_without_launch_or_input_callbacks(capsys) -> None:
    events: list[str] = []

    status = main(
        [
            "reconcile-window-binding",
            "--project-root",
            "X",
            "--slots",
            "Y",
        ],
        reconciler_builder=lambda root, slots: (
            events.append(f"build-reconciler:{root}:{slots}")
            or FakeReconciler(events)
        ),
    )

    assert status == 0
    assert events == ["build-reconciler:X:Y", "reconcile"]
    assert capsys.readouterr().out.strip() == "lifecycle reconciliation completed"
