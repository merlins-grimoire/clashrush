"""Installed CLI for the fail-closed control-plane-only MVP."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .control_plane_mvp import ControlPlane, ControlPlaneError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clash-rush-rebuild")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="create opaque stopped control-plane state")
    setup.add_argument("--project-root", required=True)
    setup.add_argument("--generation", required=True)
    setup.add_argument("--account-key", action="append", required=True)
    for name in ("status", "schedule"):
        command = commands.add_parser(name)
        command.add_argument("--project-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        service = ControlPlane(Path(args.project_root).resolve(strict=True))
        if args.command == "setup":
            configured = service.setup(tuple(args.account_key), generation=args.generation)
            print(
                "control-plane configured "
                f"mode={configured.mode.value} accounts={configured.account_count}"
            )
            return 0
        if args.command == "status":
            status = service.status()
            capabilities = {key.value: value for key, value in status.capabilities}
            print(
                json.dumps(
                    {
                        "account_count": status.account_count,
                        "gameplay": capabilities["gameplay_action"],
                        "lifecycle": status.lifecycle,
                        "mode": status.mode.value,
                        "next_slot": status.next_slot,
                        "readiness": capabilities["readiness"],
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        selection = service.schedule_next()
        print(
            f"schedule slot={selection.slot_index} dispatch=DENIED "
            "reason=GAMEPLAY_UNAVAILABLE"
        )
        return 0
    except (ControlPlaneError, OSError, ValueError):
        sys.stderr.write("control-plane request failed\n")
        return 1


__all__ = ["main"]
