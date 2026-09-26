"""Explicitly gated composition root for one inert lifecycle visit."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .approval_reconciliation import (
    ApprovalAction,
    DurableReconciliationAudit,
    OneShotApprovalService,
    PrivateApprovalStorage,
    ReconciliationError,
    StaleStateApprovalIssuer,
    StaleStateReconciler,
)
from .config import load_private_registry
from .basepilot_window import WindowService
from .cycle import (
    DiagnosticFailureStage,
    DiagnosticStageError,
    InertCycle,
    NoInputDiagnosticCycle,
    StartupContinueCycle,
)
from .diagnostic_child_job import (
    DiagnosticParentReason,
    DiagnosticChildOutcome,
    run_owned_diagnostic_child,
)
from .donor_spine_lifecycle_adapter import (
    DonorSpineLifecycleAdapter,
    ExplicitRunAuthorization,
    ProtectedLifecycleLog,
    SpineBoundaryError,
)
from .guided_setup import (
    export_synthetic_installation,
    install_private_startup_font,
    run_guided_setup,
)
from .input_authorization import InputAuthorization
from .lifecycle import AcquiredMutexLease, LifecycleSupervisor, StopRecord
from .lifecycle_state import LifecycleStateStore, Ready, encode_state
from .manual_readiness_profile import build_manual_readiness_profile
from .mvp_account_ready import AccountReady
from .mvp_local_gameplay import LocalBotMode, MvpConfiguration, VisitResult
from .mvp_local_runtime import LocalControlStore, RuntimeSafetyError
from .mvp_session_authority import ControlMode, DurableSessionAuthority
from .no_input_home_diagnostic import (
    HomeDiagnosticResult,
    NoInputHomeDiagnosticController,
)
from .startup_continue_recovery import (
    StartupContinueController,
    StartupContinueResult,
)
from .startup_failure import StartupReason, StartupFailure, fault_from
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import NativeWin32Api, Win32Runtime
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort

_BLUESTACKS_CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")
_INERT_VISIT_TIMEOUT_SECONDS = 120
_DIAGNOSTIC_STAGE_STDERR = {
    f"{stage.value}\n": stage.value for stage in DiagnosticFailureStage
}
_DIAGNOSTIC_FAILURE_CLASSES = {
    *(stage.value for stage in DiagnosticFailureStage),
    *(stage.value for stage in StartupReason),
    *(stage.value for stage in DiagnosticParentReason),
    "UNEXPECTED_STDERR",
    "SCALAR_PARSE",
}


def _diagnostic_failure_record(
    classification: str,
    child_status: int | None,
    child_wait_completed: bool,
) -> str:
    record = {
        "classification": classification,
        "child_status": child_status,
        "child_wait_completed": child_wait_completed,
    }
    return json.dumps(record, separators=(",", ":")) + "\n"


def _diagnostic_failure_semantics_are_valid(
    classification: str,
    child_status: int | None,
    child_wait_completed: bool,
) -> bool:
    if classification in {r.value for r in DiagnosticParentReason}:
        return child_status is None
    if classification in {r.value for r in StartupReason} - {"CAPTURE", "RECOGNITION", "CLEANUP"}:
        return child_status == 2 and child_wait_completed is True
    if classification in {"CAPTURE", "RECOGNITION"}:
        return child_status == 2 and child_wait_completed is True
    if classification in {"LAUNCH", "CLEANUP"}:
        return (child_status, child_wait_completed) in {
            (2, True),
            (None, False),
            (None, True),
        }
    return child_wait_completed is True


def parse_diagnostic_failure_record(record: str) -> tuple[str, int | None, bool]:
    """Strict invoking-harness boundary for one sanitized failure record."""
    invalid = False
    try:
        if type(record) is not str:
            raise ValueError
        parsed = json.loads(record)
        if type(parsed) is not dict or set(parsed) != {
            "classification",
            "child_status",
            "child_wait_completed",
        }:
            raise ValueError
        classification = parsed["classification"]
        child_status = parsed["child_status"]
        child_wait_completed = parsed["child_wait_completed"]
        if (
            type(classification) is not str
            or classification not in _DIAGNOSTIC_FAILURE_CLASSES
            or (child_status is not None and type(child_status) is not int)
            or type(child_wait_completed) is not bool
            or not _diagnostic_failure_semantics_are_valid(
                classification, child_status, child_wait_completed
            )
            or record
            != _diagnostic_failure_record(
                classification, child_status, child_wait_completed
            )
        ):
            raise ValueError
    except BaseException:
        invalid = True
    if invalid:
        raise ValueError("diagnostic evidence is malformed") from None
    return classification, child_status, child_wait_completed


def _emit_diagnostic_failure(
    classification: str,
    child_status: int | None,
    child_wait_completed: bool,
) -> int:
    sys.stderr.write(
        _diagnostic_failure_record(
            classification, child_status, child_wait_completed
        )
    )
    return 1


class CycleCommand(Protocol):
    def initialize(self) -> object: ...
    def visit_once(self) -> object: ...


class ApprovalIssuerCommand(Protocol):
    def issue(self, *, lifetime_seconds: int) -> object: ...


class ReconcilerCommand(Protocol):
    def reconcile(self) -> object: ...


def build_native_state_store(
    project_root: Path,
    *,
    state_api_factory: Callable[[], object] = NativeWin32StateApi,
) -> LifecycleStateStore:
    project = Path(project_root).resolve(strict=True)
    var = project / "var"
    var.mkdir(exist_ok=True)
    state_api = state_api_factory()
    return LifecycleStateStore(
        project,
        Win32StateFilePort(project, state_api),
    )


def build_inert_cycle(
    project_root: str,
    slots_path: str,
    *,
    preserve_ready_cursor: bool = False,
) -> InertCycle:
    """Compose native Slice 1 without launching or inspecting until visit_once."""
    project = Path(project_root)
    slots = Path(slots_path)
    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))

    def make_state_store() -> LifecycleStateStore:
        return build_native_state_store(project)

    def observe_player_count() -> int:
        snapshot = host.complete_player_snapshot()
        try:
            return len(snapshot.identities)
        finally:
            snapshot.close()

    def make_supervisor(
        store: LifecycleStateStore,
        mutex_name: str,
    ) -> LifecycleSupervisor:
        return LifecycleSupervisor(
            host,
            store,
            AcquiredMutexLease(mutex_name),
            nonce_factory=lambda: secrets.token_hex(16),
            preserve_ready_cursor=preserve_ready_cursor,
        )

    return InertCycle(
        runtime,
        load_registry=lambda: load_private_registry(
            project,
            slots,
            _BLUESTACKS_CONF,
        ),
        make_state_store=make_state_store,
        observe_player_count=observe_player_count,
        make_supervisor=make_supervisor,
    )


def build_no_input_diagnostic_cycle(
    project_root: str,
    slots_path: str,
) -> NoInputDiagnosticCycle:
    """Compose the donor recognizer inside the reviewed lifecycle boundary."""
    project = Path(project_root).resolve(strict=True)
    slots = Path(slots_path)
    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))
    approvals = OneShotApprovalService(PrivateApprovalStorage(project))

    def make_state_store() -> LifecycleStateStore:
        return build_native_state_store(project)

    def observe_player_count() -> int:
        snapshot = host.complete_player_snapshot()
        try:
            return len(snapshot.identities)
        finally:
            snapshot.close()

    def make_supervisor(
        store: LifecycleStateStore,
        mutex_name: str,
    ) -> LifecycleSupervisor:
        return LifecycleSupervisor(
            host,
            store,
            AcquiredMutexLease(mutex_name),
            nonce_factory=lambda: secrets.token_hex(16),
            preserve_ready_cursor=True,
        )

    def observe(
        supervisor: LifecycleSupervisor,
        binding,
    ) -> HomeDiagnosticResult:
        window = WindowService(binding, supervisor.capture_owned)
        return NoInputHomeDiagnosticController(window).wait_for_readiness(
            timeout_seconds=30,
            poll_interval_seconds=0.5,
        )

    return NoInputDiagnosticCycle(
        runtime,
        load_registry=lambda: load_private_registry(
            project,
            slots,
            _BLUESTACKS_CONF,
        ),
        make_state_store=make_state_store,
        observe_player_count=observe_player_count,
        approvals=approvals,
        candidate_tree=lambda: _candidate_tree(project),
        make_supervisor=make_supervisor,
        observe=observe,
    )


def build_startup_continue_cycle(
    project_root: str,
    slots_path: str,
) -> StartupContinueCycle:
    """Compose the approval-bound donor Continue-only recovery slice."""
    from .mvp_local_native import Win32StartupContinueInput

    project = Path(project_root).resolve(strict=True)
    slots = Path(slots_path)
    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))
    approvals = OneShotApprovalService(PrivateApprovalStorage(project))

    def make_state_store() -> LifecycleStateStore:
        return build_native_state_store(project)

    def observe_player_count() -> int:
        snapshot = host.complete_player_snapshot()
        try:
            return len(snapshot.identities)
        finally:
            snapshot.close()

    def make_supervisor(
        store: LifecycleStateStore,
        mutex_name: str,
    ) -> LifecycleSupervisor:
        return LifecycleSupervisor(
            host,
            store,
            AcquiredMutexLease(mutex_name),
            nonce_factory=lambda: secrets.token_hex(16),
            preserve_ready_cursor=True,
        )

    def recover(supervisor: LifecycleSupervisor, binding) -> StartupContinueResult:
        window = WindowService(binding, supervisor.capture_owned)
        village = NoInputHomeDiagnosticController(window)
        live = True

        def classify(frame):
            observed = village._detect_village_type(
                frame, expected_size=(binding.width, binding.height)
            )
            return StartupContinueResult(observed.value)

        authorization = InputAuthorization.startup_continue_only(lambda: live)
        input_port = Win32StartupContinueInput(
            binding, supervisor.capture_owned, authorization
        )
        try:
            return StartupContinueController(
                binding=binding,
                window=window,
                input_port=input_port,
                font_path=project / "private" / "assets" / "CCBackBeat.ttf",
                classify_village=classify,
            ).run(
                timeout_seconds=60,
                poll_interval_seconds=0.5,
                settle_seconds=1.0,
            )
        finally:
            live = False

    return StartupContinueCycle(
        runtime,
        load_registry=lambda: load_private_registry(
            project, slots, _BLUESTACKS_CONF
        ),
        make_state_store=make_state_store,
        observe_player_count=observe_player_count,
        approvals=approvals,
        candidate_tree=lambda: _candidate_tree(project),
        make_supervisor=make_supervisor,
        recover=recover,
    )


def issue_startup_continue_approval(
    project_root: str, lifetime_seconds: int
) -> object:
    """Issue one exact-tree/state approval without launching BlueStacks."""
    project = Path(project_root).resolve(strict=True)
    runtime = Win32Runtime(NativeWin32Api())
    lease = runtime.acquire_mutex()
    try:
        if lease.abandoned:
            raise ReconciliationError("abandoned mutex forbids approval issuance")
        lease.require_usable()
        state, state_bytes = build_native_state_store(project).load_with_bytes()
        if type(state) is not Ready:
            raise ReconciliationError("READY lifecycle is required")
        return OneShotApprovalService(PrivateApprovalStorage(project)).grant(
            ApprovalAction.STARTUP_CONTINUE_ONLY,
            _candidate_tree(project),
            state,
            state_bytes,
            lifetime_seconds=lifetime_seconds,
        )
    finally:
        lease.release()


def _candidate_tree(project: Path) -> str:
    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status.stdout:
            raise ReconciliationError("candidate tree is not immutable")
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReconciliationError("candidate tree is unavailable") from exc
    tree = result.stdout.strip()
    if len(tree) != 40 or any(character not in "0123456789abcdef" for character in tree):
        raise ReconciliationError("candidate tree is malformed")
    return tree


def build_native_reconciliation_issuer(project_root: str) -> StaleStateApprovalIssuer:
    project = Path(project_root).resolve(strict=True)
    runtime = Win32Runtime(NativeWin32Api())
    build_native_state_store(project)
    approvals = OneShotApprovalService(PrivateApprovalStorage(project))
    return StaleStateApprovalIssuer(
        runtime,
        make_state_store=lambda: build_native_state_store(project),
        approvals=approvals,
        candidate_tree=lambda: _candidate_tree(project),
    )


def build_native_reconciler(
    project_root: str, slots_path: str
) -> StaleStateReconciler:
    project = Path(project_root).resolve(strict=True)
    slots = Path(slots_path)
    native = NativeLifecycleApi()
    runtime = Win32Runtime(native)
    host = Win32LifecycleHost(native, nonce_factory=lambda: secrets.token_hex(16))
    build_native_state_store(project)
    approvals = OneShotApprovalService(PrivateApprovalStorage(project))

    def observe_absence() -> tuple[int, int]:
        registry = load_private_registry(project, slots, _BLUESTACKS_CONF)
        if (
            type(registry) is not tuple
            or len(registry) != 5
            or tuple(slot.index for slot in registry) != (0, 1, 2, 3, 4)
        ):
            raise ReconciliationError("exact ordered five-slot registry required")
        def player_count() -> int:
            snapshot = host.complete_player_snapshot()
            try:
                return len(snapshot.identities)
            finally:
                snapshot.close()

        process_count_before = player_count()
        window_count = host.complete_relevant_root_window_count(
            registry[0].display_name
        )
        process_count_after = player_count()
        return max(process_count_before, process_count_after), window_count

    return StaleStateReconciler(
        runtime,
        make_state_store=lambda: build_native_state_store(project),
        approvals=approvals,
        candidate_tree=lambda: _candidate_tree(project),
        observe_absence=observe_absence,
        audit=DurableReconciliationAudit(
            project / "var" / "reconciliation-audit.jsonl"
        ),
    )


def _local_control_store(project_root: str | Path) -> LocalControlStore:
    return LocalControlStore(Path(project_root) / "var" / "mvp-local-control.json")


def _mvp_session_store(project_root: str | Path) -> DurableSessionAuthority:
    return DurableSessionAuthority(Path(project_root) / "var" / "mvp-session.sqlite3")


def _start_mvp_session(
    project_root: str | Path, *, resume: bool, purpose: str
):
    if (
        type(resume) is not bool
        or type(purpose) is not str
        or purpose not in {"MVP_SINGLE_ACCOUNT_ATTACK", "MVP_ACCOUNT_READINESS"}
        or (resume and purpose != "MVP_SINGLE_ACCOUNT_ATTACK")
    ):
        raise RuntimeSafetyError("MVP run purpose is malformed")
    project = Path(project_root).resolve(strict=True)
    lifecycle = build_native_state_store(project).load()
    if type(lifecycle) is not Ready:
        raise RuntimeSafetyError("MVP run requires exact READY lifecycle state")
    store = _mvp_session_store(project)
    operation = store.resume if resume else store.run
    now = int(time.time())
    return operation(
        purpose=purpose,
        candidate_tree=_candidate_tree(project),
        ready_bytes=encode_state(lifecycle),
        deadline=now + 300,
        now=now,
    )


def run_authorized_donor_visit(project_root: str, slots_path: str) -> StopRecord:
    """Run the donor's inert launch/stop seam through all local boundaries."""
    project = Path(project_root).resolve(strict=True)
    control = _local_control_store(project)
    state = control.load()
    instance_ref = state.configuration.instance_ref
    if (
        type(instance_ref) is not str
        or not instance_ref.startswith("slot-")
        or not instance_ref[5:].isdigit()
    ):
        raise SpineBoundaryError("selected local instance is malformed")
    selected_slot = int(instance_ref[5:])
    authorization = ExplicitRunAuthorization(
        secrets.token_hex(16), selected_slot=selected_slot
    )
    adapter = DonorSpineLifecycleAdapter(
        control,
        build_inert_cycle(project_root, slots_path),
        ProtectedLifecycleLog(project / "var" / "private-logs" / "lifecycle.jsonl"),
    )
    return adapter.run_once(authorization)


def run_native_mvp_visit(
    project_root: str, slots_path: str, transaction_ref: str
) -> VisitResult:
    """Late-bind the native implementation so ordinary controls stay inert."""
    from .mvp_local_native import run_native_mvp_visit as run

    return run(project_root, slots_path, transaction_ref)


def run_native_mvp_account_ready(
    project_root: str, slots_path: str, transaction_ref: str
) -> AccountReady:
    """Late-bind the readiness-only owned visit."""
    from .mvp_local_native import run_native_mvp_account_ready as run

    return run(project_root, slots_path, transaction_ref)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clash-rush-rebuild")
    subcommands = parser.add_subparsers(dest="command", required=True)
    visit = subcommands.add_parser(
        "visit-one",
        help="run one inert launch/bind/capture/stop visit",
    )
    visit.add_argument("--project-root", required=True)
    visit.add_argument("--slots", required=True)
    visit.add_argument(
        "--owner-approved",
        action="store_true",
        help="confirm fresh owner approval for this single inert visit",
    )
    visit.add_argument(
        "--diagnostic-preserve-cursor",
        action="store_true",
        help="preserve the admitted READY cursor for an explicit diagnostic visit",
    )
    visit_child = subcommands.add_parser("visit-one-child", help=argparse.SUPPRESS)
    visit_child.add_argument("--project-root", required=True)
    visit_child.add_argument("--slots", required=True)
    visit_child.add_argument("--owner-approved", action="store_true")
    visit_child.add_argument("--diagnostic-preserve-cursor", action="store_true")
    donor_visit = subcommands.add_parser(
        "donor-visit-one",
        help="run one authorized donor-spine no-input launch/stop visit",
    )
    donor_visit.add_argument("--project-root", required=True)
    donor_visit.add_argument("--slots", required=True)
    donor_visit.add_argument(
        "--owner-approved",
        action="store_true",
        help="confirm fresh owner approval for this single no-input visit",
    )
    initialize = subcommands.add_parser(
        "initialize",
        help="create READY(0) only when state is absent and no player is running",
    )
    initialize.add_argument("--project-root", required=True)
    initialize.add_argument("--slots", required=True)
    setup = subcommands.add_parser(
        "setup",
        help="interactively create the private installation configuration",
    )
    setup.add_argument("--project-root", required=True)
    setup_font = subcommands.add_parser(
        "setup-startup-font",
        help="copy an operator-provided startup font into private assets",
    )
    setup_font.add_argument("--project-root", required=True)
    setup_font.add_argument("--font", required=True)
    issue_continue = subcommands.add_parser(
        "issue-startup-continue-approval",
        help="issue one short-lived Continue-only startup approval",
    )
    issue_continue.add_argument("--project-root", required=True)
    issue_continue.add_argument("--lifetime-seconds", type=int, default=300)
    continue_once = subcommands.add_parser(
        "startup-continue-one",
        help="consume approval for one bounded Continue-only startup recovery",
    )
    continue_once.add_argument("--project-root", required=True)
    continue_once.add_argument("--slots", required=True)
    debug_issue = subcommands.add_parser('issue-startup-debug-approval')
    debug_issue.add_argument('--project-root', required=True)
    debug_issue.add_argument('--lifetime-seconds', type=int, default=300)
    debug_issue.add_argument('--allow-private-full-frames', action='store_true')
    debug = subcommands.add_parser('startup-debug-one')
    debug.add_argument('--project-root', required=True)
    debug.add_argument('--slots', required=True)
    debug.add_argument('--allow-private-full-frames', action='store_true')
    debug.add_argument('--timeout-seconds', type=int, default=240)
    debug_child = subcommands.add_parser('startup-debug-child', help=argparse.SUPPRESS)
    debug_child.add_argument('--project-root', required=True)
    debug_child.add_argument('--slots', required=True)
    debug_child.add_argument('--allow-private-full-frames', action='store_true')
    export = subcommands.add_parser(
        "export-setup-example",
        help="write a public-safe synthetic installation configuration",
    )
    export.add_argument("--output", required=True)
    mvp_setup = subcommands.add_parser(
        "mvp-setup", help="configure exactly one local Team/account/instance"
    )
    mvp_setup.add_argument("--project-root", required=True)
    mvp_setup.add_argument("--team-ref", required=True)
    mvp_setup.add_argument("--account-ref", required=True)
    mvp_setup.add_argument("--instance-ref", required=True)
    mvp_setup.add_argument("--player-tag-sha256", required=True)
    mvp_import = subcommands.add_parser(
        "mvp-import-stopped",
        help="explicitly import the preserved legacy STOPPED control file",
    )
    mvp_import.add_argument("--project-root", required=True)
    mvp_rebind = subcommands.add_parser(
        "mvp-rebind-ready-instance",
        help="rebind stopped MVP configuration to the lifecycle READY slot",
    )
    mvp_rebind.add_argument("--project-root", required=True)
    mvp_rebind.add_argument("--expected-instance-ref", required=True)
    for name in (
        "mvp-run",
        "mvp-run-readiness",
        "mvp-pause",
        "mvp-resume",
        "mvp-stop",
        "mvp-status",
    ):
        control = subcommands.add_parser(name)
        control.add_argument("--project-root", required=True)
    mvp_visit = subcommands.add_parser(
        "mvp-visit-one", help="run one owner-approved attack-only local visit"
    )
    mvp_visit.add_argument("--project-root", required=True)
    mvp_visit.add_argument("--slots", required=True)
    mvp_visit.add_argument("--transaction-ref", required=True)
    mvp_ready = subcommands.add_parser(
        "mvp-account-ready-one",
        help="prove one account ready without requesting an attack or deployment",
    )
    mvp_ready.add_argument("--project-root", required=True)
    mvp_ready.add_argument("--slots", required=True)
    mvp_ready.add_argument("--transaction-ref", required=True)
    manual_profile = subcommands.add_parser(
        "build-readiness-profile",
        help="build the private runtime profile from reviewed narrow crops",
    )
    manual_profile.add_argument("--project-root", required=True)
    manual_profile.add_argument("--review-manifest", required=True)
    issue_reconciliation = subcommands.add_parser(
        "issue-reconciliation-approval",
        help="issue one exact-state-bound stale-state reconciliation approval",
    )
    issue_reconciliation.add_argument("--project-root", required=True)
    issue_reconciliation.add_argument(
        "--lifetime-seconds", type=int, default=300
    )
    reconcile = subcommands.add_parser(
        "reconcile-window-binding",
        help="consume one approval and reconcile the exact stale blocked state",
    )
    reconcile.add_argument("--project-root", required=True)
    reconcile.add_argument("--slots", required=True)
    diagnose = subcommands.add_parser(
        "diagnose-home",
        help="run one bounded installed no-input Home diagnostic",
    )
    diagnose.add_argument("--project-root", required=True)
    diagnose.add_argument("--slots", required=True)
    diagnose.add_argument("--timeout-seconds", type=int, default=120)
    diagnose_child = subcommands.add_parser(
        "diagnose-home-child",
        help=argparse.SUPPRESS,
    )
    diagnose_child.add_argument("--project-root", required=True)
    diagnose_child.add_argument("--slots", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    cycle_builder: Callable[[str, str], CycleCommand] | None = None,
    setup_runner: Callable[[Path], object] = run_guided_setup,
    synthetic_exporter: Callable[[], str] = export_synthetic_installation,
    approval_issuer_builder: Callable[
        [str], ApprovalIssuerCommand
    ] = build_native_reconciliation_issuer,
    reconciler_builder: Callable[
        [str, str], ReconcilerCommand
    ] = build_native_reconciler,
    diagnostic_cycle_builder: Callable[[str, str], CycleCommand] | None = None,
    diagnostic_child_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    inert_child_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    authorized_visit_runner: Callable[[str, str], object] | None = None,
    startup_font_installer: Callable[[Path, Path], object] = install_private_startup_font,
    startup_approval_issuer: Callable[[str, int], object] = issue_startup_continue_approval,
    startup_continue_runner: Callable[[str, str], StartupContinueResult] | None = None,
    startup_debug_cycle_builder: Callable[[str, str], CycleCommand] | None = None,
) -> int:
    diagnostic_argv = sys.argv[1:] if argv is None else argv
    sanitized_commands = {
        "build-readiness-profile",
        "diagnose-home",
        "startup-debug-one",
        "startup-debug-child",
        "visit-one",
        "visit-one-child",
    }
    if tuple(diagnostic_argv[:1]) in tuple((name,) for name in sanitized_commands):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                args = _parser().parse_args(diagnostic_argv)
        except SystemExit as exc:
            if exc.code != 2:
                raise
            if tuple(diagnostic_argv[:1]) == ("startup-debug-child",):
                sys.stderr.write("AUTHORIZATION\n")
                return 2
            if tuple(diagnostic_argv[:1]) == ("visit-one-child",):
                sys.stderr.write("LIFECYCLE\n")
                return 2
            if tuple(diagnostic_argv[:1]) == ("visit-one",):
                print("inert lifecycle arguments invalid", file=sys.stderr)
                raise SystemExit(2) from None
            if tuple(diagnostic_argv[:1]) == ("build-readiness-profile",):
                print("private readiness profile arguments invalid", file=sys.stderr)
                raise SystemExit(2) from None
            print("diagnostic arguments invalid", file=sys.stderr)
            raise SystemExit(2) from None
    else:
        args = _parser().parse_args(argv)
    if (
        args.command in {"visit-one", "visit-one-child", "donor-visit-one"}
        and args.owner_approved is not True
    ):
        return 2

    try:
        if args.command == "visit-one":
            command = [
                sys.executable,
                "-m",
                "clash_rush_rebuild",
                "visit-one-child",
                "--project-root",
                args.project_root,
                "--slots",
                args.slots,
                "--owner-approved",
            ]
            if args.diagnostic_preserve_cursor is True:
                command.append("--diagnostic-preserve-cursor")
            try:
                if inert_child_runner is None:
                    completed = run_owned_diagnostic_child(
                        command,
                        timeout=_INERT_VISIT_TIMEOUT_SECONDS,
                    )
                else:
                    completed = inert_child_runner(
                        command,
                        timeout=_INERT_VISIT_TIMEOUT_SECONDS,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
            except BaseException:
                sys.stderr.write("inert lifecycle visit failed\n")
                return 1
            if type(completed) is DiagnosticChildOutcome:
                success = (
                    type(completed.returncode) is int
                    and completed.returncode == 0
                    and type(completed.stdout) is str
                    and completed.stdout == "STOPPED\n"
                    and type(completed.stderr) is str
                    and completed.stderr == ""
                    and completed.child_wait_completed is True
                    and completed.cleanup_succeeded is True
                    and completed.operational_failure is False
                    and completed.reason is None
                    and completed.prior_reason is None
                )
            elif type(completed) is subprocess.CompletedProcess and inert_child_runner is not None:
                success = (
                    type(completed.returncode) is int
                    and completed.returncode == 0
                    and type(completed.stdout) is str
                    and completed.stdout == "STOPPED\n"
                    and type(completed.stderr) is str
                    and completed.stderr == ""
                )
            else:
                success = False
            if not success:
                sys.stderr.write("inert lifecycle visit failed\n")
                return 1
            print("inert lifecycle visit completed")
            return 0
        if args.command == "visit-one-child":
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if cycle_builder is None:
                        cycle = build_inert_cycle(
                            args.project_root,
                            args.slots,
                            preserve_ready_cursor=args.diagnostic_preserve_cursor is True,
                        )
                    else:
                        cycle = cycle_builder(args.project_root, args.slots)
                    cycle.visit_once()
            except BaseException:
                sys.stderr.write("LIFECYCLE\n")
                return 2
            print("STOPPED")
            return 0
        if args.command == "export-setup-example":
            with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(synthetic_exporter())
            return 0
        if args.command == "setup":
            setup_runner(Path(args.project_root))
            return 0
        if args.command == 'issue-startup-debug-approval':
            if args.allow_private_full_frames is not True:
                return 2
            from .startup_debug_native import issue_approval
            issue_approval(args.project_root, args.lifetime_seconds)
            print('startup diagnostic approval issued')
            return 0
        if args.command == 'startup-debug-child':
            reason = None
            if args.allow_private_full_frames is not True:
                reason = StartupReason.AUTHORIZATION
            else:
                try:
                    # Emit only after the production cycle has cleaned up.
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        if startup_debug_cycle_builder is None:
                            from .startup_debug_native import build_cycle
                            builder = build_cycle
                        else:
                            builder = startup_debug_cycle_builder
                        result = builder(args.project_root, args.slots).visit_once()
                        if type(result) is not StartupContinueResult:
                            raise StartupFailure(StartupReason.RECOGNITION)
                except BaseException as exc:
                    reason = fault_from(exc, StartupReason.COMPOSITION).reason
            if reason is not None:
                sys.stderr.write(reason.value + '\n')
                return 2
            print(result.value)
            return 0 if result is StartupContinueResult.HOME else 1
        if args.command == "setup-startup-font":
            startup_font_installer(Path(args.project_root), Path(args.font))
            return 0
        if args.command == "issue-startup-continue-approval":
            startup_approval_issuer(args.project_root, args.lifetime_seconds)
            print("startup Continue approval issued")
            return 0
        if args.command == "startup-continue-one":
            runner = startup_continue_runner or (
                lambda project_root, slots_path: build_startup_continue_cycle(
                    project_root, slots_path
                ).visit_once()
            )
            result = runner(args.project_root, args.slots)
            if type(result) is not StartupContinueResult:
                raise RuntimeError("startup recovery result is malformed")
            print(result.value)
            return 0 if result in {
                StartupContinueResult.HOME,
                StartupContinueResult.BUILDER,
            } else 1
        if args.command == "mvp-setup":
            _mvp_session_store(args.project_root).setup(
                MvpConfiguration(
                    args.team_ref,
                    args.account_ref,
                    args.instance_ref,
                    args.player_tag_sha256,
                )
            )
            print("local MVP configured mode=STOPPED")
            return 0
        if args.command == "mvp-rebind-ready-instance":
            project = Path(args.project_root).resolve(strict=True)
            lifecycle = build_native_state_store(project).load()
            if type(lifecycle) is not Ready:
                raise RuntimeError("lifecycle READY state required")
            authority = _mvp_session_store(args.project_root)
            authority.rebind_stopped_instance(
                expected_instance_ref=args.expected_instance_ref,
                instance_ref=f"slot-{lifecycle.next_slot}",
            )
            print("local MVP instance rebound mode=STOPPED")
            return 0
        if args.command == "mvp-import-stopped":
            project = Path(args.project_root).resolve(strict=True)
            _mvp_session_store(project).import_stopped_legacy(
                project / "var" / "mvp-local-control.json"
            )
            print("legacy local MVP imported mode=STOPPED")
            return 0
        if args.command in {"mvp-run", "mvp-run-readiness", "mvp-resume"}:
            _start_mvp_session(
                args.project_root,
                resume=args.command == "mvp-resume",
                purpose=(
                    "MVP_ACCOUNT_READINESS"
                    if args.command == "mvp-run-readiness"
                    else "MVP_SINGLE_ACCOUNT_ATTACK"
                ),
            )
            print("local MVP mode=RUNNING")
            return 0
        transitions = {
            "mvp-pause": ControlMode.PAUSED,
            "mvp-stop": ControlMode.STOPPED,
        }
        if args.command in transitions:
            state = _mvp_session_store(args.project_root).command(
                transitions[args.command], command_id=secrets.token_hex(16)
            )
            print(f"local MVP mode={state.mode.value}")
            return 0
        if args.command == "mvp-status":
            state = _mvp_session_store(args.project_root).status()
            receipt = "complete" if state.final_acknowledged else "pending"
            game = state.game_outcome or "PENDING"
            retirement = state.retirement_outcome or "PENDING"
            print(
                f"local MVP mode={state.mode.value} configured=yes "
                f"game={game} retirement={retirement} receipt={receipt}"
            )
            return 0
        if args.command == "mvp-visit-one":
            result = run_native_mvp_visit(
                args.project_root, args.slots, args.transaction_ref
            )
            if type(result) is not VisitResult:
                raise RuntimeSafetyError("visit result is malformed")
            print(f"visit status={result.status} reason={result.reason_code}")
            return 0 if result.confirmed else 1
        if args.command == "mvp-account-ready-one":
            result = run_native_mvp_account_ready(
                args.project_root, args.slots, args.transaction_ref
            )
            if type(result) is not AccountReady:
                raise RuntimeSafetyError("AccountReady result is malformed")
            print("account ready")
            return 0
        if args.command == "build-readiness-profile":
            build_manual_readiness_profile(args.project_root, args.review_manifest)
            print("private readiness profile built")
            return 0
        if args.command == "issue-reconciliation-approval":
            approval_issuer_builder(args.project_root).issue(
                lifetime_seconds=args.lifetime_seconds
            )
            print("reconciliation approval issued")
            return 0
        if args.command == "reconcile-window-binding":
            reconciler_builder(args.project_root, args.slots).reconcile()
            print("lifecycle reconciliation completed")
            return 0
        if args.command == "diagnose-home-child":
            try:
                builder = diagnostic_cycle_builder or build_no_input_diagnostic_cycle
                result = builder(args.project_root, args.slots).visit_once()
                if type(result) is not HomeDiagnosticResult:
                    raise DiagnosticStageError(DiagnosticFailureStage.RECOGNITION)
            except DiagnosticStageError as exc:
                sys.stderr.write(f"{exc.stage.value}\n")
                return 2
            except BaseException:
                sys.stderr.write("LAUNCH\n")
                return 2
            print(result.value)
            return 0 if result is HomeDiagnosticResult.HOME else 1
        if args.command in {"diagnose-home", "startup-debug-one"}:
            startup = args.command == "startup-debug-one"
            if startup and args.allow_private_full_frames is not True:
                return 2
            if (
                type(args.timeout_seconds) is not int
                or not 1 <= args.timeout_seconds <= 600
            ):
                return 2
            command = [
                sys.executable,
                "-m",
                "clash_rush_rebuild",
                "startup-debug-child" if startup else "diagnose-home-child",
                "--project-root",
                args.project_root,
                "--slots",
                args.slots,
            ]
            if startup:
                command.append("--allow-private-full-frames")
            try:
                if diagnostic_child_runner is None:
                    completed = run_owned_diagnostic_child(
                        command,
                        timeout=args.timeout_seconds,
                    )
                else:
                    completed = diagnostic_child_runner(
                        command,
                        timeout=args.timeout_seconds,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
            except BaseException:
                return _emit_diagnostic_failure("CLEANUP", None, False)
            if type(completed) is DiagnosticChildOutcome:
                if (
                    type(completed.operational_failure) is not bool
                    or type(completed.cleanup_succeeded) is not bool
                    or type(completed.child_wait_completed) is not bool
                    or (completed.reason is not None and type(completed.reason) is not DiagnosticParentReason)
                    or (completed.prior_reason is not None and type(completed.prior_reason) not in (DiagnosticParentReason, StartupReason))
                ):
                    return _emit_diagnostic_failure("PARENT_COMMUNICATION", None, False)
                if startup and type(completed.reason) is DiagnosticParentReason:
                    return _emit_diagnostic_failure(completed.reason.value, None, completed.child_wait_completed is True)
                if (
                    completed.operational_failure is True
                    or completed.cleanup_succeeded is not True
                    or completed.child_wait_completed is not True
                ):
                    return _emit_diagnostic_failure(
                        "CLEANUP", None, completed.child_wait_completed is True
                    )
                child_status = completed.returncode if type(completed.returncode) is int else None
                stdout = completed.stdout
                stderr = completed.stderr
            elif type(completed) is subprocess.CompletedProcess and diagnostic_child_runner is not None:
                child_status = (
                    completed.returncode
                    if type(completed.returncode) is int
                    else None
                )
                stdout = completed.stdout
                stderr = completed.stderr
            else:
                return _emit_diagnostic_failure("PARENT_COMMUNICATION", None, False)
            valid = {
                (0, "HOME\n"): HomeDiagnosticResult.HOME,
                (1, "BUILDER\n"): HomeDiagnosticResult.BUILDER,
                (1, "UNKNOWN\n"): HomeDiagnosticResult.UNKNOWN,
            }
            result = (
                valid.get((child_status, stdout))
                if child_status is not None and type(stdout) is str
                else None
            )
            stage = (
                ({r.value + "\n": r.value for r in StartupReason} if startup else _DIAGNOSTIC_STAGE_STDERR).get(stderr)
                if child_status == 2 and stdout == "" and type(stderr) is str
                else None
            )
            if stage is not None:
                return _emit_diagnostic_failure(stage, child_status, True)
            if type(stderr) is not str or stderr != "":
                return _emit_diagnostic_failure(
                    "UNEXPECTED_STDERR", child_status, True
                )
            if result is None:
                return _emit_diagnostic_failure("SCALAR_PARSE", child_status, True)
            print(result.value)
            return 0 if result is HomeDiagnosticResult.HOME else 1
        if args.command == "donor-visit-one":
            runner = authorized_visit_runner or run_authorized_donor_visit
            runner(args.project_root, args.slots)
            return 0
        if cycle_builder is None:
            cycle = build_inert_cycle(
                args.project_root,
                args.slots,
                preserve_ready_cursor=(
                    args.command == "visit-one"
                    and args.diagnostic_preserve_cursor is True
                ),
            )
        else:
            cycle = cycle_builder(args.project_root, args.slots)
        if args.command == "initialize":
            cycle.initialize()
        else:
            cycle.visit_once()
    except BaseException:  # noqa: BLE001 - CLI emits no private native/config details
        if args.command in {"diagnose-home", "diagnose-home-child", "startup-debug-one", "startup-debug-child"}:
            return 1
        if args.command == "build-readiness-profile":
            sys.stderr.write("private readiness profile build failed\n")
            return 1
        sys.stderr.write("inert lifecycle visit failed\n")
        return 1
    return 0
