"""Exact crash-durable lifecycle state values and codec."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class LifecycleStateError(RuntimeError):
    """Persisted lifecycle state is absent, malformed, or unprovable."""


class BlockReason(str, Enum):
    STATE = "STATE"
    JOB_SETUP = "JOB_SETUP"
    PROCESS_CREATE = "PROCESS_CREATE"
    IDENTITY = "IDENTITY"
    ASSIGNMENT = "ASSIGNMENT"
    RESUME = "RESUME"
    THREAD_CLOSE = "THREAD_CLOSE"
    PLAYER_ENUMERATION = "PLAYER_ENUMERATION"
    MEMBERSHIP = "MEMBERSHIP"
    WINDOW_BINDING = "WINDOW_BINDING"
    CAPTURE_READINESS = "CAPTURE_READINESS"
    STOP_PROOF = "STOP_PROOF"
    ROLLBACK_UNPROVED = "ROLLBACK_UNPROVED"


@dataclass(frozen=True, slots=True)
class Ready:
    next_slot: int

    def __post_init__(self) -> None:
        if type(self.next_slot) is not int or not 0 <= self.next_slot < 5:
            raise LifecycleStateError("READY slot must be an integer from zero to four")

    @property
    def state_name(self) -> str:
        return "READY"


@dataclass(frozen=True, slots=True)
class Active:
    slot: int
    run_nonce: str
    blocked_reason: BlockReason | None

    def __post_init__(self) -> None:
        if type(self.slot) is not int or not 0 <= self.slot < 5:
            raise LifecycleStateError(
                "ACTIVE slot must be an integer from zero to four"
            )
        if (
            type(self.run_nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.run_nonce) is None
        ):
            raise LifecycleStateError(
                "ACTIVE nonce must be 32 lowercase hex characters"
            )
        if (
            self.blocked_reason is not None
            and type(self.blocked_reason) is not BlockReason
        ):
            raise LifecycleStateError("ACTIVE blocked reason is invalid")

    @property
    def state_name(self) -> str:
        return "ACTIVE"


class WriteThroughFilePort(Protocol):
    def create_new_temp_write_through(self, path: Path) -> object: ...
    def write(self, file: object, data: memoryview) -> int: ...
    def flush(self, file: object) -> None: ...
    def close(self, file: object) -> None: ...
    def replace_write_through(self, source: Path, target: Path) -> None: ...
    def open_read_exclusive_no_reparse(self, path: Path) -> object: ...
    def read_bounded(self, file: object, limit: int) -> bytes: ...
    def delete_file_no_reparse(self, path: Path) -> None: ...


class LifecycleStateStore:
    """Write-through state store whose return includes exact read-back."""

    def __init__(self, project_root: Path, file_port: WriteThroughFilePort) -> None:
        try:
            project = Path(project_root).resolve(strict=True)
        except OSError as exc:
            raise LifecycleStateError("existing project root required") from exc
        if not project.is_dir():
            raise LifecycleStateError("project root must be a directory")
        candidate = project / "var"
        try:
            candidate.mkdir(exist_ok=True)
            root = candidate.resolve(strict=True)
            root.relative_to(project)
        except (OSError, ValueError) as exc:
            raise LifecycleStateError(
                "lifecycle state path escaped project var"
            ) from exc
        if root != candidate:
            raise LifecycleStateError("lifecycle state path escaped project var")
        self._port = file_port
        self._path = root / "lifecycle-state.json"
        self._transition = root / ".lifecycle-state.transition"

    def _write_new_file(self, path: Path, payload: bytes) -> None:
        writer = self._port.create_new_temp_write_through(path)
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = self._port.write(writer, view[offset:])
                if (
                    type(written) is not int
                    or written <= 0
                    or written > len(view) - offset
                ):
                    raise LifecycleStateError("lifecycle state write was incomplete")
                offset += written
            self._port.flush(writer)
        finally:
            self._port.close(writer)

    def _require_no_transition(self) -> None:
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._transition)
        except FileNotFoundError:
            return
        except BaseException as exc:
            raise LifecycleStateError(
                "lifecycle transition guard is unprovable"
            ) from exc
        try:
            self._port.close(reader)
        except BaseException as exc:
            raise LifecycleStateError(
                "lifecycle transition guard could not close"
            ) from exc
        raise LifecycleStateError(
            "incomplete lifecycle transition requires reconciliation"
        )

    def initialize_ready(self) -> Ready:
        self._require_no_transition()
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._path)
        except FileNotFoundError:
            return self.commit(Ready(0))
        except BaseException as exc:
            raise LifecycleStateError("existing lifecycle state is unprovable") from exc
        try:
            self._port.close(reader)
        except BaseException as exc:
            raise LifecycleStateError("lifecycle state handle could not close") from exc
        raise LifecycleStateError("lifecycle state already exists")

    def load(self) -> Ready | Active:
        self._require_no_transition()
        try:
            reader = self._port.open_read_exclusive_no_reparse(self._path)
        except FileNotFoundError as exc:
            raise LifecycleStateError(
                "lifecycle state is missing; explicit initialization required"
            ) from exc
        except BaseException as exc:
            raise LifecycleStateError("lifecycle state could not be opened") from exc
        try:
            payload = self._port.read_bounded(reader, 513)
        except BaseException as exc:
            raise LifecycleStateError("lifecycle state could not be read") from exc
        finally:
            try:
                self._port.close(reader)
            except BaseException as exc:
                raise LifecycleStateError(
                    "lifecycle state handle could not close"
                ) from exc
        return decode_state(payload)

    def commit(self, state: Ready | Active) -> Ready | Active:
        payload = encode_state(state)
        temporary = self._path.with_name(
            f".{self._path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            self._write_new_file(self._transition, b"transition\n")
            self._write_new_file(temporary, payload)
            self._port.replace_write_through(temporary, self._path)
            reader = self._port.open_read_exclusive_no_reparse(self._path)
            try:
                read_back = self._port.read_bounded(reader, 513)
            finally:
                self._port.close(reader)
            if type(read_back) is not bytes or read_back != payload:
                raise LifecycleStateError("lifecycle state read-back mismatch")
            parsed = decode_state(read_back)
            if type(parsed) is not type(state) or parsed != state:
                raise LifecycleStateError(
                    "lifecycle state read-back changed type or value"
                )
            self._port.delete_file_no_reparse(self._transition)
            return parsed
        except BaseException as exc:
            if isinstance(exc, LifecycleStateError):
                raise
            raise LifecycleStateError("lifecycle state commit failed") from exc


def encode_state(state: Ready | Active) -> bytes:
    if type(state) is Ready:
        raw = {"next_slot": state.next_slot, "schema": 1, "state": "READY"}
    elif type(state) is Active:
        raw = {
            "blocked_reason": (
                None if state.blocked_reason is None else state.blocked_reason.value
            ),
            "run_nonce": state.run_nonce,
            "schema": 1,
            "slot": state.slot,
            "state": "ACTIVE",
        }
    else:
        raise LifecycleStateError("unsupported lifecycle state")
    return (
        json.dumps(
            raw,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def decode_state(payload: bytes) -> Ready | Active:
    if type(payload) is not bytes or len(payload) > 512:
        raise LifecycleStateError("lifecycle state payload is invalid")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleStateError("lifecycle state payload is unreadable") from exc
    if type(raw) is not dict:
        raise LifecycleStateError("lifecycle state object is invalid")
    if type(raw.get("schema")) is not int or raw.get("schema") != 1:
        raise LifecycleStateError("lifecycle state schema is invalid")
    if raw.get("state") == "READY":
        if set(raw) != {"next_slot", "schema", "state"}:
            raise LifecycleStateError("READY fields are invalid")
        state: Ready | Active = Ready(raw["next_slot"])
        if encode_state(state) != payload:
            raise LifecycleStateError("lifecycle state encoding is not canonical")
        return state
    if raw.get("state") == "ACTIVE":
        if set(raw) != {"blocked_reason", "run_nonce", "schema", "slot", "state"}:
            raise LifecycleStateError("ACTIVE fields are invalid")
        reason = raw["blocked_reason"]
        try:
            parsed_reason = None if reason is None else BlockReason(reason)
        except (TypeError, ValueError) as exc:
            raise LifecycleStateError("ACTIVE blocked reason is invalid") from exc
        state = Active(raw["slot"], raw["run_nonce"], parsed_reason)
        if encode_state(state) != payload:
            raise LifecycleStateError("lifecycle state encoding is not canonical")
        return state
    raise LifecycleStateError("lifecycle state kind is invalid")
