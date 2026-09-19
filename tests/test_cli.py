from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import cv2
import pytest

import clash_rush_rebuild.cli as cli_module
import clash_rush_rebuild.cycle as cycle_module
from clash_rush_rebuild.cli import build_native_state_store, main
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity, StopRecord
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.mvp_local_gameplay import VisitResult
from clash_rush_rebuild.no_input_home_diagnostic import HomeDiagnosticResult
from clash_rush_rebuild.registry import Slot


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
        "--instance-ref", "slot-0",
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
    assert "slot-0" not in output


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


class FakeDiagnosticCycle:
    def __init__(self, events: list[str], result: HomeDiagnosticResult) -> None:
        self.events = events
        self.result = result

    def visit_once(self) -> HomeDiagnosticResult:
        self.events.append("diagnose:visit")
        return self.result


def test_private_diagnostic_child_emits_only_closed_home_scalar(capsys) -> None:
    events: list[str] = []

    status = main(
        ["diagnose-home-child", "--project-root", "X", "--slots", "Y"],
        diagnostic_cycle_builder=lambda root, slots: (
            events.append(f"diagnose:build:{root}:{slots}")
            or FakeDiagnosticCycle(events, HomeDiagnosticResult.HOME)
        ),
    )

    captured = capsys.readouterr()
    assert status == 0
    assert events == ["diagnose:build:X:Y", "diagnose:visit"]
    assert captured.out == "HOME\n"
    assert captured.err == ""


def test_private_diagnostic_child_emits_only_closed_stage_failure(capsys) -> None:
    class FailingCycle:
        def visit_once(self) -> HomeDiagnosticResult:
            raise cycle_module.DiagnosticStageError(
                cycle_module.DiagnosticFailureStage.CAPTURE
            )

    status = main(
        ["diagnose-home-child", "--project-root", "X", "--slots", "Y"],
        diagnostic_cycle_builder=lambda _root, _slots: FailingCycle(),
    )

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "CAPTURE\n"


def test_private_diagnostic_child_maps_composition_failure_to_launch(capsys) -> None:
    status = main(
        ["diagnose-home-child", "--project-root", "X", "--slots", "Y"],
        diagnostic_cycle_builder=lambda _root, _slots: (_ for _ in ()).throw(
            RuntimeError("private composition detail")
        ),
    )

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "LAUNCH\n"


def test_public_diagnostic_bounds_installed_child_and_forwards_exact_scalar(capsys) -> None:
    calls: list[tuple[object, ...]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, "HOME\n", "")

    status = main(
        [
            "diagnose-home",
            "--project-root",
            "X",
            "--slots",
            "Y",
            "--timeout-seconds",
            "45",
        ],
        diagnostic_child_runner=run,
    )

    assert status == 0
    assert capsys.readouterr().out == "HOME\n"
    command, kwargs = calls[0]
    assert command[-5:] == [
        "diagnose-home-child",
        "--project-root",
        "X",
        "--slots",
        "Y",
    ]
    assert kwargs == {
        "timeout": 45,
        "check": False,
        "capture_output": True,
        "text": True,
    }


def test_public_diagnostic_rejects_partial_output(capsys) -> None:
    def extra_output(command, **_kwargs):
        return CompletedProcess(command, 0, "HOME\nextra\n", "")

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=extra_output,
    ) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, "BUILDER\n"), (1, "UNKNOWN\n")],
)
def test_public_diagnostic_forwards_each_closed_nonhome_scalar(
    returncode: int,
    stdout: str,
    capsys,
) -> None:
    completed = CompletedProcess([], returncode, stdout, "")

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=lambda *_args, **_kwargs: completed,
    ) == 1
    assert capsys.readouterr().out == stdout


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [
        (0, "BUILDER\n", ""),
        (1, "HOME\n", ""),
        (1, "UNKNOWN\n", "private detail"),
        (2, "", ""),
    ],
)
def test_public_diagnostic_rejects_invalid_status_output_combinations(
    returncode: int,
    stdout: str,
    stderr: str,
    capsys,
) -> None:
    completed = CompletedProcess([], returncode, stdout, stderr)

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=lambda *_args, **_kwargs: completed,
    ) == 1
    assert capsys.readouterr().out == ""


def _failure_record(
    classification: str,
    child_status: int | None,
    child_wait_completed: bool,
) -> str:
    return json.dumps(
        {
            "classification": classification,
            "child_status": child_status,
            "child_wait_completed": child_wait_completed,
        },
        separators=(",", ":"),
    ) + "\n"


def test_diagnostic_harness_accepts_only_exact_closed_failure_record() -> None:
    record = _failure_record("CAPTURE", 2, True)

    evidence = cli_module.parse_diagnostic_failure_record(record)

    assert evidence == ("CAPTURE", 2, True)


@pytest.mark.parametrize(
    "record",
    [
        "",
        "not-json\n",
        _failure_record("CAPTURE", 2, True) + "extra\n",
        '{"classification":"CAPTURE","child_status":2,"child_wait_completed":true,"extra":1}\n',
        '{"classification":"PRIVATE","child_status":2,"child_wait_completed":true}\n',
        '{"classification":"CAPTURE","child_status":true,"child_wait_completed":true}\n',
        '{"classification":"CAPTURE","child_status":2,"child_wait_completed":1}\n',
        '{"child_status":2,"classification":"CAPTURE","child_wait_completed":true}\n',
        _failure_record("CAPTURE", None, False),
        _failure_record("LAUNCH", 2, False),
        _failure_record("TIMEOUT", None, True),
        _failure_record("UNEXPECTED_STDERR", 1, False),
    ],
)
def test_diagnostic_harness_rejects_missing_malformed_or_noncanonical_record(
    record: str,
) -> None:
    with pytest.raises(ValueError, match="malformed"):
        cli_module.parse_diagnostic_failure_record(record)


@pytest.mark.parametrize("stage", ["LAUNCH", "CAPTURE", "RECOGNITION", "CLEANUP"])
def test_public_diagnostic_forwards_exact_child_stage_as_sanitized_json(
    stage: str,
    capsys,
) -> None:
    completed = CompletedProcess([], 2, "", f"{stage}\n")

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=lambda *_args, **_kwargs: completed,
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _failure_record(stage, 2, True)


@pytest.mark.parametrize(
    ("completed", "classification", "child_status"),
    [
        (CompletedProcess([], 0, "HOME\n", "private detail"), "UNEXPECTED_STDERR", 0),
        (CompletedProcess([], 1, "UNKNOWN\n", "CAPTURE\n"), "UNEXPECTED_STDERR", 1),
        (CompletedProcess([], 2, "extra", "CAPTURE\n"), "UNEXPECTED_STDERR", 2),
        (CompletedProcess([], 3, "", ""), "SCALAR_PARSE", 3),
        (CompletedProcess([], 0, "HOME\nextra\n", ""), "SCALAR_PARSE", 0),
        (CompletedProcess([], True, "", ""), "SCALAR_PARSE", None),
    ],
)
def test_public_diagnostic_classification_precedence_is_closed(
    completed: CompletedProcess[str],
    classification: str,
    child_status: int | None,
    capsys,
) -> None:
    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=lambda *_args, **_kwargs: completed,
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _failure_record(classification, child_status, True)
    assert "private detail" not in captured.err


@pytest.mark.parametrize(
    "error",
    [
        OSError("private spawn detail C:/private/account-one"),
        TimeoutExpired(["private", "command", "account-one"], 30),
        RuntimeError("private wait detail account-one"),
        KeyboardInterrupt("private arbitrary detail account-one"),
    ],
)
def test_public_diagnostic_maps_every_injected_runner_exception_to_cleanup(
    error: BaseException,
    capsys,
) -> None:
    def fail(*_args, **_kwargs):
        raise error

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
        diagnostic_child_runner=fail,
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _failure_record("CLEANUP", None, False)
    parsed = json.loads(captured.err)
    assert type(parsed["classification"]) is str
    assert parsed["child_status"] is None
    assert type(parsed["child_wait_completed"]) is bool
    assert "private" not in captured.err
    assert "account-one" not in captured.err


class _InertDiagnosticPopen:
    def __init__(
        self,
        command,
        *,
        failure: str | None = None,
        **_kwargs,
    ) -> None:
        self.args = command
        self.returncode = 0
        self._failure = failure
        self._communicate_calls = 0
        self.stdout = _InertDiagnosticStream(self, "stdout")
        self.stderr = _InertDiagnosticStream(self, "stderr")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.stdout.close()
        self.stderr.close()
        if self._failure == "context-exit":
            raise TimeoutExpired(["private", "context-exit"], 30)
        self.wait()
        return None

    def communicate(self, _input=None, timeout=None):
        self._communicate_calls += 1
        if self._failure in {
            "initial-communicate",
            "kill",
            "second-communicate",
        }:
            if self._communicate_calls == 1:
                raise TimeoutExpired(self.args, timeout)
            if self._failure == "second-communicate":
                raise TimeoutExpired(["private", "second-communicate"], 30)
        return "HOME\n", ""

    def kill(self) -> None:
        if self._failure == "kill":
            raise TimeoutExpired(["private", "kill"], 30)

    def wait(self) -> int:
        if self._failure == "wait":
            raise TimeoutExpired(["private", "wait"], 30)
        return self.returncode

    def poll(self) -> int:
        return self.returncode


class _InertDiagnosticStream:
    def __init__(self, process: _InertDiagnosticPopen, name: str) -> None:
        self._process = process
        self._name = name

    def close(self) -> None:
        if self._process._failure == "stream-close" and self._name == "stdout":
            raise TimeoutExpired(["private", "stream-close"], 30)


def _run_with_inert_popen(monkeypatch, failure: str | None, capsys) -> tuple[int, str, str]:
    def popen(command, **kwargs):
        return _InertDiagnosticPopen(command, failure=failure, **kwargs)

    monkeypatch.setattr(cli_module.subprocess, "Popen", popen)
    status = main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
    )
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_standard_runner_maps_pre_createprocess_constructor_failure_to_cleanup(
    monkeypatch,
    capsys,
) -> None:
    class FailConstructor:
        def __init__(self, *_args, **_kwargs) -> None:
            self._child_created = False
            raise OSError("private constructor detail")

    monkeypatch.setattr(cli_module.subprocess, "Popen", FailConstructor)

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _failure_record("CLEANUP", None, False)
    assert "private" not in captured.err


def test_standard_runner_maps_createprocess_then_pipe_cleanup_failure_to_cleanup(
    monkeypatch,
    capsys,
) -> None:
    class FailAfterCreate:
        def __init__(self, *_args, **_kwargs) -> None:
            self._child_created = False
            self.create_process_succeeded = True
            raise OSError("private post-create pipe cleanup detail")

    monkeypatch.setattr(cli_module.subprocess, "Popen", FailAfterCreate)

    assert main(
        ["diagnose-home", "--project-root", "X", "--slots", "Y"],
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _failure_record("CLEANUP", None, False)
    assert "private" not in captured.err


@pytest.mark.parametrize(
    "failure",
    [
        "initial-communicate",
        "kill",
        "second-communicate",
        "stream-close",
        "context-exit",
        "wait",
    ],
)
def test_standard_runner_maps_every_timeout_path_exception_to_cleanup(
    failure: str,
    monkeypatch,
    capsys,
) -> None:
    status, stdout, stderr = _run_with_inert_popen(monkeypatch, failure, capsys)

    assert status == 1
    assert stdout == ""
    assert stderr == _failure_record("CLEANUP", None, False)
    assert "private" not in stderr


def test_standard_runner_preserves_ordinary_scalar_with_inert_popen(
    monkeypatch,
    capsys,
) -> None:
    status, stdout, stderr = _run_with_inert_popen(monkeypatch, None, capsys)

    assert status == 0
    assert stdout == "HOME\n"
    assert stderr == ""


def test_real_diagnostic_composition_wires_owned_capture_to_donor_controller(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    fixture = cv2.imread(
        str(Path(__file__).parent / "fixtures" / "donor" / "home_screen.png"),
        cv2.IMREAD_COLOR,
    )
    assert fixture is not None
    bgra = cv2.cvtColor(fixture, cv2.COLOR_BGR2BGRA).tobytes()
    slots = tuple(
        Slot(index, f"Instance {index}", f"Slot {index}", 1728, 1080, 240)
        for index in range(5)
    )
    identity = ProcessIdentity(100, 9001)
    binding = PlayerBinding(identity, identity, 101, 102, 1728, 1080, "a" * 32)

    class Lease:
        abandoned = False

        def require_usable(self) -> None:
            events.append("mutex:usable")

        def release(self) -> None:
            events.append("mutex:release")

    class Runtime:
        def acquire_mutex(self) -> Lease:
            return Lease()

    class Snapshot:
        identities = ()

        def close(self) -> None:
            events.append("snapshot:close")

    class Host:
        def complete_player_snapshot(self) -> Snapshot:
            return Snapshot()

    class Store:
        def load_with_bytes(self):
            return Ready(0), b'{"next_slot":0,"schema":1,"state":"READY"}\n'

    class Approvals:
        def validate_then_consume(self, *_args):
            events.append("approval")
            return object()

    class Supervisor:
        def __init__(self, host, *_args, **_kwargs):
            self.host = host

        def start(self, slot):
            events.append(f"start:{slot.index}")
            return binding

        def capture_owned(self, selected):
            assert selected is binding
            events.append("capture")
            return 1728, 1080, bgra

        def stop(self, selected, proof):
            assert selected is binding
            assert proof is None
            events.append("stop")
            return StopRecord(0, "0" * 32, 1, 2, True)

    monkeypatch.setattr(cli_module, "NativeLifecycleApi", lambda: object())
    monkeypatch.setattr(cli_module, "Win32Runtime", lambda _native: Runtime())
    monkeypatch.setattr(cli_module, "Win32LifecycleHost", lambda *_args, **_kwargs: Host())
    monkeypatch.setattr(cli_module, "PrivateApprovalStorage", lambda _root: object())
    monkeypatch.setattr(cli_module, "OneShotApprovalService", lambda _storage: Approvals())
    monkeypatch.setattr(cli_module, "build_native_state_store", lambda _root: Store())
    monkeypatch.setattr(cli_module, "load_private_registry", lambda *_args: slots)
    monkeypatch.setattr(cli_module, "LifecycleSupervisor", Supervisor)
    monkeypatch.setattr(cli_module, "AcquiredMutexLease", lambda _name: object())
    monkeypatch.setattr(cli_module, "_candidate_tree", lambda _root: "b" * 40)

    cycle = cli_module.build_no_input_diagnostic_cycle(str(tmp_path), "slots.toml")

    assert cycle.visit_once() is HomeDiagnosticResult.HOME
    assert events == [
        "mutex:usable",
        "snapshot:close",
        "approval",
        "start:0",
        "capture",
        "stop",
        "mutex:release",
    ]
