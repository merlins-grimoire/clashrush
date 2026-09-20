"""Thin inert boundaries around the sealed CoC_Bot donor spine.

The donor's mutable generated configuration, broad launch helpers, and public
logging are intentionally not imported.  This adapter composes the existing
private configuration/control and Job-owned no-input lifecycle seams, then
records only a sanitized verified-stop event.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from .approval_reconciliation import _seal_private_path
from .lifecycle import StopRecord
from .mvp_local_gameplay import LocalBotMode
from .mvp_local_runtime import PersistentControl

_NONCE = re.compile(r"[0-9a-f]{32}")


class SpineBoundaryError(RuntimeError):
    """A donor-spine boundary was absent, malformed, or unproved."""


class ExplicitRunAuthorization:
    """One-use authorization for one exact sanitized slot."""

    __slots__ = ("_consumed", "_nonce", "_selected_slot")

    def __init__(self, nonce: str, *, selected_slot: int) -> None:
        if (
            type(nonce) is not str
            or _NONCE.fullmatch(nonce) is None
            or type(selected_slot) is not int
            or not 0 <= selected_slot < 5
        ):
            raise SpineBoundaryError("explicit run authorization is malformed")
        self._nonce = nonce
        self._selected_slot = selected_slot
        self._consumed = False

    @property
    def consumed(self) -> bool:
        return self._consumed

    def consume(self) -> int:
        if self._consumed:
            raise SpineBoundaryError("explicit run authorization was already consumed")
        self._consumed = True
        return self._selected_slot


class ControlPort(Protocol):
    def load(self) -> PersistentControl: ...


class InertCyclePort(Protocol):
    def visit_once(self) -> StopRecord: ...


class ProtectedLifecycleLog:
    """Append closed lifecycle events beneath a protected private-log directory."""

    def __init__(
        self,
        path: Path,
        *,
        permission_sealer=_seal_private_path,
    ) -> None:
        candidate = Path(path)
        if (
            candidate.name != "lifecycle.jsonl"
            or candidate.parent.name != "private-logs"
            or not callable(permission_sealer)
        ):
            raise SpineBoundaryError("protected lifecycle log path is invalid")
        self._path = candidate
        self._seal = permission_sealer

    def append_stopped(self, slot: int) -> None:
        if type(slot) is not int or not 0 <= slot < 5:
            raise SpineBoundaryError("sanitized lifecycle slot is invalid")
        parent = self._path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or parent.resolve(strict=True) != parent:
            raise SpineBoundaryError("protected lifecycle log path must not redirect")
        self._seal(parent, True)
        if self._path.exists() and (
            self._path.is_symlink() or self._path.resolve(strict=True) != self._path
        ):
            raise SpineBoundaryError("protected lifecycle log path must not redirect")
        record = (
            json.dumps(
                {"event": "NO_INPUT_STOPPED", "schema": 1, "slot": slot},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        flags = os.O_CREAT | os.O_RDWR | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(self._path, flags, 0o600)
        try:
            self._seal(self._path, False)
            if os.write(descriptor, record) != len(record):
                raise OSError("partial lifecycle append")
            os.fsync(descriptor)
            end = os.lseek(descriptor, 0, os.SEEK_END)
            os.lseek(descriptor, end - len(record), os.SEEK_SET)
            if os.read(descriptor, len(record)) != record:
                raise OSError("lifecycle append read-back mismatch")
        except SpineBoundaryError:
            raise
        except BaseException as exc:
            raise SpineBoundaryError("protected lifecycle log append failed") from exc
        finally:
            os.close(descriptor)


class DonorSpineLifecycleAdapter:
    """Authorize, bind local control, run one inert visit, and log verified stop."""

    def __init__(
        self,
        control: ControlPort,
        cycle: InertCyclePort,
        lifecycle_log: ProtectedLifecycleLog,
    ) -> None:
        self._control = control
        self._cycle = cycle
        self._log = lifecycle_log

    def run_once(self, authorization: ExplicitRunAuthorization) -> StopRecord:
        if type(authorization) is not ExplicitRunAuthorization:
            raise SpineBoundaryError("exact explicit run authorization is required")
        selected_slot = authorization.consume()
        state = self._control.load()
        if type(state) is not PersistentControl:
            raise SpineBoundaryError("local control state is malformed")
        if state.mode is not LocalBotMode.RUNNING:
            raise SpineBoundaryError("local control must be explicitly RUNNING")
        if state.configuration.instance_ref != f"slot-{selected_slot}":
            raise SpineBoundaryError("authorization does not match selected slot")
        record = self._cycle.visit_once()
        if type(record) is not StopRecord or record.slot != selected_slot:
            raise SpineBoundaryError("verified stop does not match selected slot")
        self._log.append_stopped(record.slot)
        return record


__all__ = [
    "DonorSpineLifecycleAdapter",
    "ExplicitRunAuthorization",
    "ProtectedLifecycleLog",
    "SpineBoundaryError",
]
