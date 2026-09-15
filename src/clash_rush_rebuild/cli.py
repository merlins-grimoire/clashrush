"""Explicitly gated composition root for one inert lifecycle visit."""

from __future__ import annotations

import argparse
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .config import load_private_registry
from .cycle import InertCycle
from .guided_setup import export_synthetic_installation, run_guided_setup
from .lifecycle import AcquiredMutexLease, LifecycleSupervisor
from .lifecycle_state import LifecycleStateStore
from .win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from .win32_runtime import Win32Runtime
from .win32_state_io import NativeWin32StateApi, Win32StateFilePort

_BLUESTACKS_CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")


class CycleCommand(Protocol):
    def initialize(self) -> object: ...
    def visit_once(self) -> object: ...


def build_native_state_store(project_root: Path) -> LifecycleStateStore:
    project = Path(project_root).resolve(strict=True)
    var = project / "var"
    var.mkdir(exist_ok=True)
    state_api = NativeWin32StateApi()
    return LifecycleStateStore(
        project,
        Win32StateFilePort(project, state_api),
    )


def build_inert_cycle(project_root: str, slots_path: str) -> InertCycle:
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
    export = subcommands.add_parser(
        "export-setup-example",
        help="write a public-safe synthetic installation configuration",
    )
    export.add_argument("--output", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    cycle_builder: Callable[[str, str], CycleCommand] = build_inert_cycle,
    setup_runner: Callable[[Path], object] = run_guided_setup,
    synthetic_exporter: Callable[[], str] = export_synthetic_installation,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "visit-one" and args.owner_approved is not True:
        return 2
    try:
        if args.command == "export-setup-example":
            with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(synthetic_exporter())
            return 0
        if args.command == "setup":
            setup_runner(Path(args.project_root))
            return 0
        cycle = cycle_builder(args.project_root, args.slots)
        if args.command == "initialize":
            cycle.initialize()
        else:
            cycle.visit_once()
    except BaseException:  # noqa: BLE001 - CLI emits no private native/config details
        sys.stderr.write("inert lifecycle visit failed\n")
        return 1
    return 0
