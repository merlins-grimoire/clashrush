"""Protected one-shot approval and stale lifecycle reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
import ctypes
from ctypes import wintypes
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .lifecycle_state import Active, BlockReason, Ready, encode_state

_MAX_APPROVAL_BYTES = 2048
_MAX_LIFETIME_SECONDS = 600
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_APPROVAL_FIELDS = {
    "action",
    "approval_id",
    "candidate_tree",
    "consumed_at",
    "expires_at",
    "issued_at",
    "schema",
    "state_sha256",
}


class ApprovalError(RuntimeError):
    """An approval is absent, malformed, stale, mismatched, or consumed."""


class ReconciliationError(RuntimeError):
    """Stale state could not be reconciled with every proof intact."""


class ApprovalAction(StrEnum):
    RECONCILE_WINDOW_BINDING = "RECONCILE_WINDOW_BINDING"
    LAUNCH_WINDOW_BINDING_DIAGNOSTIC = "LAUNCH_WINDOW_BINDING_DIAGNOSTIC"
    STARTUP_CONTINUE_ONLY = "STARTUP_CONTINUE_ONLY"
    STARTUP_DEBUG = "STARTUP_DEBUG"


@dataclass(frozen=True, slots=True)
class OneShotApproval:
    action: ApprovalAction
    approval_id: str
    candidate_tree: str
    issued_at: int
    expires_at: int
    state_sha256: str
    consumed_at: int | None

    def __post_init__(self) -> None:
        if (
            type(self.action) is not ApprovalAction
            or type(self.approval_id) is not str
            or _HEX_32.fullmatch(self.approval_id) is None
            or type(self.candidate_tree) is not str
            or _HEX_40.fullmatch(self.candidate_tree) is None
            or type(self.issued_at) is not int
            or type(self.expires_at) is not int
            or not 0 <= self.issued_at < self.expires_at
            or self.expires_at - self.issued_at > _MAX_LIFETIME_SECONDS
            or type(self.state_sha256) is not str
            or _HEX_64.fullmatch(self.state_sha256) is None
            or (
                self.consumed_at is not None
                and (
                    type(self.consumed_at) is not int
                    or self.consumed_at < self.issued_at
                )
            )
        ):
            raise ApprovalError("approval is malformed")


class ApprovalStoragePort(Protocol):
    def replace_artifact(self, action: ApprovalAction, payload: bytes) -> bytes: ...

    def read_artifact(self, action: ApprovalAction) -> bytes: ...

    def consume_artifact(
        self, action: ApprovalAction, expected: bytes, consumed: bytes
    ) -> bytes: ...


class MutexLeasePort(Protocol):
    abandoned: bool

    def require_usable(self) -> None: ...

    def release(self) -> None: ...


class MutexRuntimePort(Protocol):
    def acquire_mutex(self) -> MutexLeasePort: ...


class StateStorePort(Protocol):
    def load_with_bytes(self) -> tuple[Ready | Active, bytes]: ...

    def commit(self, state: Ready | Active) -> Ready | Active: ...

    def commit_with_postcondition(
        self, state: Ready | Active, postcondition: Callable[[], None]
    ) -> Ready | Active: ...


class ReconciliationAuditPort(Protocol):
    def append_intent(self) -> None: ...

    def append_outcome(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WindowsPrivatePathSecurity:
    """Injectable Windows SID/DACL boundary for private approval paths."""

    current_operator_sid: Callable[[], str]
    set_and_verify_dacl: Callable[[Path, str], None]

    def seal(self, path: Path, directory: bool) -> None:
        sid = self.current_operator_sid()
        flags = "OICI" if directory else ""
        expected_sddl = f"D:P(A;{flags};GA;;;SY)(A;{flags};GA;;;{sid})"
        self.set_and_verify_dacl(path, expected_sddl)


def _native_current_operator_sid() -> str:
    from .win32_runtime import NativeWin32Api

    return NativeWin32Api().current_operator_sid()


def _seal_private_path(path: Path, directory: bool) -> None:
    """Restrict a private path to the current operator and SYSTEM."""
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ApprovalError("private path permissions are not restrictive")
        return

    WindowsPrivatePathSecurity(
        current_operator_sid=_native_current_operator_sid,
        set_and_verify_dacl=_set_and_verify_windows_dacl,
    ).seal(path, directory)


def _set_and_verify_windows_dacl(path: Path, expected_sddl: str) -> None:
    """Replace, protect, and read back an exact file-object DACL."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer_pointer = ctypes.POINTER(ctypes.c_void_p)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        pointer_pointer,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        pointer_pointer,
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        pointer_pointer,
        pointer_pointer,
        pointer_pointer,
        pointer_pointer,
        pointer_pointer,
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        pointer_pointer,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    def descriptor_sddl(value: ctypes.c_void_p) -> str:
        text = ctypes.c_void_p()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            value,
            1,
            0x00000004,
            ctypes.byref(text),
            None,
        ):
            raise ApprovalError("private path DACL read-back failed")
        try:
            return ctypes.wstring_at(text)
        finally:
            kernel32.LocalFree(text)

    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        expected_sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ApprovalError("private path DACL could not be constructed")
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ) or present.value != 1:
            raise ApprovalError("private path DACL could not be inspected")
        result = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x00000004 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if result != 0:
            raise ApprovalError("private path permissions could not be restricted")
        canonical_expected_sddl = descriptor_sddl(descriptor)
    finally:
        kernel32.LocalFree(descriptor)

    actual_descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x00000004,
        None,
        None,
        None,
        None,
        ctypes.byref(actual_descriptor),
    )
    if result != 0:
        raise ApprovalError("private path DACL read-back failed")
    try:
        actual_sddl = descriptor_sddl(actual_descriptor)
    finally:
        kernel32.LocalFree(actual_descriptor)
    actual_prefix = actual_sddl.split("(", 1)[0]
    principals = tuple(
        re.findall(r"\(A;[^;]*;[^;]*;;;([^)]+)\)", canonical_expected_sddl)
    )
    if "OICI" in expected_sddl:
        expected_aces = tuple(
            sorted(
                ace
                for principal in principals
                for ace in (
                    f"(A;;FA;;;{principal})",
                    f"(A;OICIIO;GA;;;{principal})",
                )
            )
        )
    else:
        expected_aces = tuple(
            sorted(f"(A;;FA;;;{principal})" for principal in principals)
        )
    actual_aces = tuple(sorted(re.findall(r"\([^)]*\)", actual_sddl)))
    if actual_prefix not in {"D:P", "D:PAI"} or actual_aces != expected_aces:
        raise ApprovalError("private path DACL does not match the required principals")


def _replace_write_through(source: Path, target: Path) -> None:
    if os.name == "nt":
        from .win32_state_io import (
            MOVEFILE_REPLACE_EXISTING,
            MOVEFILE_WRITE_THROUGH,
            NativeWin32StateApi,
        )

        if NativeWin32StateApi().move_file_ex(
            str(source),
            str(target),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        ) is not True:
            raise OSError("write-through replacement failed")
        return
    os.replace(source, target)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class PrivateApprovalStorage:
    """Atomic private artifact storage beneath one canonical project ``var``."""

    def __init__(
        self,
        project_root: Path,
        *,
        permission_sealer: Callable[[Path, bool], None] = _seal_private_path,
    ) -> None:
        project = Path(project_root).resolve(strict=True)
        var = (project / "var").resolve(strict=True)
        if not var.is_dir() or var.parent != project:
            raise ApprovalError("exact existing project var directory required")
        approvals = var / "approvals"
        approvals.mkdir(mode=0o700, exist_ok=True)
        if approvals.is_symlink() or approvals.resolve(strict=True) != approvals:
            raise ApprovalError("approval path must not redirect")
        self._root = approvals
        self._seal = permission_sealer
        self._seal(approvals, True)

    def _path(self, action: ApprovalAction) -> Path:
        if type(action) is not ApprovalAction:
            raise ApprovalError("approval action is invalid")
        return self._root / f"{action.value.lower()}.json"

    @staticmethod
    def _read(path: Path) -> bytes:
        with path.open("rb") as stream:
            payload = stream.read(_MAX_APPROVAL_BYTES + 1)
        if len(payload) > _MAX_APPROVAL_BYTES:
            raise ApprovalError("approval is malformed")
        return payload

    def _replace(self, path: Path, payload: bytes) -> bytes:
        temporary = self._root / f".{path.name}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        replaced = False
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(temporary, flags, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if type(written) is not int or written <= 0:
                    raise OSError("partial approval write")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            self._seal(temporary, False)
            if not hmac.compare_digest(self._read(temporary), payload):
                raise OSError("approval temporary read-back mismatch")
            _replace_write_through(temporary, path)
            replaced = True
            self._seal(path, False)
            read_back = self._read(path)
            if not hmac.compare_digest(read_back, payload):
                raise OSError("approval destination read-back mismatch")
            _decode_approval(read_back)
            return read_back
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not replaced:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def replace_artifact(self, action: ApprovalAction, payload: bytes) -> bytes:
        return self._replace(self._path(action), payload)

    def read_artifact(self, action: ApprovalAction) -> bytes:
        path = self._path(action)
        self._seal(path, False)
        return self._read(path)

    def consume_artifact(
        self, action: ApprovalAction, expected: bytes, consumed: bytes
    ) -> bytes:
        path = self._path(action)
        current = self.read_artifact(action)
        if not hmac.compare_digest(current, expected):
            raise OSError("approval artifact changed")
        return self._replace(path, consumed)


class DurableReconciliationAudit:
    """Append only closed, sanitized reconciliation intent/outcome records."""

    def __init__(
        self,
        path: Path,
        *,
        utc_now: Callable[[], int] = lambda: int(time.time()),
        permission_sealer: Callable[[Path, bool], None] = _seal_private_path,
    ) -> None:
        candidate = Path(path)
        parent = candidate.parent.resolve(strict=True)
        if candidate.parent != parent or not parent.is_dir():
            raise ReconciliationError("audit path must be canonical")
        if candidate.exists() and (
            candidate.is_symlink() or candidate.resolve(strict=True) != candidate
        ):
            raise ReconciliationError("audit path must not redirect")
        self._path = candidate
        self._utc_now = utc_now
        self._seal = permission_sealer

    def _append(self, event: str) -> None:
        now = self._utc_now()
        if type(now) is not int or now < 0:
            raise ReconciliationError("audit clock is invalid")
        record = (
            json.dumps(
                {"event": event, "schema": 1, "timestamp": now},
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
                raise OSError("partial audit append")
            os.fsync(descriptor)
            end = os.lseek(descriptor, 0, os.SEEK_END)
            os.lseek(descriptor, end - len(record), os.SEEK_SET)
            if os.read(descriptor, len(record)) != record:
                raise OSError("audit read-back mismatch")
        finally:
            os.close(descriptor)

    def append_intent(self) -> None:
        self._append("RECONCILIATION_INTENT")

    def append_outcome(self) -> None:
        self._append("RECONCILIATION_OUTCOME_READY")


def _encode_approval(approval: OneShotApproval) -> bytes:
    if type(approval) is not OneShotApproval:
        raise ApprovalError("approval is malformed")
    return (
        json.dumps(
            {
                "action": approval.action.value,
                "approval_id": approval.approval_id,
                "candidate_tree": approval.candidate_tree,
                "consumed_at": approval.consumed_at,
                "expires_at": approval.expires_at,
                "issued_at": approval.issued_at,
                "schema": 1,
                "state_sha256": approval.state_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def _decode_approval(payload: bytes) -> OneShotApproval:
    if type(payload) is not bytes or len(payload) > _MAX_APPROVAL_BYTES:
        raise ApprovalError("approval is malformed")
    try:
        raw = json.loads(payload.decode("ascii"))
        if (
            type(raw) is not dict
            or set(raw) != _APPROVAL_FIELDS
            or type(raw["schema"]) is not int
            or raw["schema"] != 1
        ):
            raise ApprovalError("approval is malformed")
        approval = OneShotApproval(
            ApprovalAction(raw["action"]),
            raw["approval_id"],
            raw["candidate_tree"],
            raw["issued_at"],
            raw["expires_at"],
            raw["state_sha256"],
            raw["consumed_at"],
        )
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ApprovalError("approval is malformed") from exc
    if _encode_approval(approval) != payload:
        raise ApprovalError("approval is malformed")
    return approval


def _require_state_binding(state: Ready | Active, state_bytes: bytes) -> None:
    if (
        type(state) not in (Ready, Active)
        or type(state_bytes) is not bytes
        or encode_state(state) != state_bytes
    ):
        raise ApprovalError("canonical lifecycle state is required")


class OneShotApprovalService:
    """Issue, validate, and durably consume exact-bound approval artifacts."""

    def __init__(
        self,
        storage: ApprovalStoragePort,
        *,
        utc_now: Callable[[], int] = lambda: int(time.time()),
        approval_id_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._storage = storage
        self._utc_now = utc_now
        self._approval_id_factory = approval_id_factory

    def grant(
        self,
        action: ApprovalAction,
        candidate_tree: str,
        state: Ready | Active,
        state_bytes: bytes,
        *,
        lifetime_seconds: int,
    ) -> OneShotApproval:
        if type(action) is not ApprovalAction:
            raise ApprovalError("approval action is invalid")
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 600:
            raise ApprovalError("approval lifetime must be from one to ten minutes")
        _require_state_binding(state, state_bytes)
        now = self._utc_now()
        approval = OneShotApproval(
            action,
            self._approval_id_factory(),
            candidate_tree,
            now,
            now + lifetime_seconds,
            hashlib.sha256(state_bytes).hexdigest(),
            None,
        )
        payload = _encode_approval(approval)
        try:
            read_back = self._storage.replace_artifact(action, payload)
        except BaseException as exc:
            raise ApprovalError("approval issuance was not durable") from exc
        if type(read_back) is not bytes or not hmac.compare_digest(read_back, payload):
            raise ApprovalError("approval issuance read-back mismatch")
        return approval

    def validate(
        self,
        action: ApprovalAction,
        candidate_tree: str,
        state: Ready | Active,
        state_bytes: bytes,
    ) -> OneShotApproval:
        if type(action) is not ApprovalAction:
            raise ApprovalError("approval action is invalid")
        _require_state_binding(state, state_bytes)
        try:
            payload = self._storage.read_artifact(action)
        except FileNotFoundError as exc:
            raise ApprovalError("approval is missing") from exc
        except BaseException as exc:
            raise ApprovalError("approval is unavailable") from exc
        approval = _decode_approval(payload)
        now = self._utc_now()
        if approval.action is not action:
            raise ApprovalError("approval action mismatch")
        if approval.consumed_at is not None:
            raise ApprovalError("approval was already consumed")
        if not approval.issued_at <= now < approval.expires_at:
            raise ApprovalError("approval is expired or not yet valid")
        if not hmac.compare_digest(approval.candidate_tree, candidate_tree):
            raise ApprovalError("approval candidate tree mismatch")
        state_digest = hashlib.sha256(state_bytes).hexdigest()
        if not hmac.compare_digest(approval.state_sha256, state_digest):
            raise ApprovalError("approval lifecycle state mismatch")
        return approval

    def validate_then_consume(
        self,
        action: ApprovalAction,
        candidate_tree: str,
        state: Ready | Active,
        state_bytes: bytes,
    ) -> OneShotApproval:
        approval = self.validate(action, candidate_tree, state, state_bytes)
        payload = _encode_approval(approval)
        consumed_at = self._utc_now()
        if (
            type(consumed_at) is not int
            or not approval.issued_at <= consumed_at < approval.expires_at
        ):
            raise ApprovalError("approval is expired or not yet valid at consumption")
        consumed = OneShotApproval(
            approval.action,
            approval.approval_id,
            approval.candidate_tree,
            approval.issued_at,
            approval.expires_at,
            approval.state_sha256,
            consumed_at,
        )
        consumed_payload = _encode_approval(consumed)
        try:
            read_back = self._storage.consume_artifact(
                action, payload, consumed_payload
            )
        except BaseException as exc:
            raise ApprovalError("approval consumption was not durable") from exc
        if type(read_back) is not bytes or not hmac.compare_digest(
            read_back, consumed_payload
        ):
            raise ApprovalError("approval consumption read-back mismatch")
        return consumed


class StaleStateApprovalIssuer:
    """Issue one reconciliation approval while holding the lifecycle mutex."""

    def __init__(
        self,
        runtime: MutexRuntimePort,
        *,
        make_state_store: Callable[[], StateStorePort],
        approvals: OneShotApprovalService,
        candidate_tree: Callable[[], str],
    ) -> None:
        self._runtime = runtime
        self._make_state_store = make_state_store
        self._approvals = approvals
        self._candidate_tree = candidate_tree

    def issue(self, *, lifetime_seconds: int) -> OneShotApproval:
        lease = self._runtime.acquire_mutex()
        try:
            if type(lease.abandoned) is not bool or lease.abandoned:
                raise ReconciliationError("abandoned mutex forbids approval issuance")
            lease.require_usable()
            state, payload = self._make_state_store().load_with_bytes()
            blocked = StaleStateReconciler._require_exact_blocked(state, payload)
            return self._approvals.grant(
                ApprovalAction.RECONCILE_WINDOW_BINDING,
                self._candidate_tree(),
                blocked,
                payload,
                lifetime_seconds=lifetime_seconds,
            )
        finally:
            try:
                lease.release()
            except BaseException as exc:
                raise ReconciliationError(
                    "mutex release is unresolved after approval issuance"
                ) from exc


class StaleStateReconciler:
    """Change one exact blocked v1 state to READY only after complete proofs."""

    def __init__(
        self,
        runtime: MutexRuntimePort,
        *,
        make_state_store: Callable[[], StateStorePort],
        approvals: OneShotApprovalService,
        candidate_tree: Callable[[], str],
        observe_absence: Callable[[], tuple[int, int]],
        audit: ReconciliationAuditPort,
    ) -> None:
        self._runtime = runtime
        self._make_state_store = make_state_store
        self._approvals = approvals
        self._candidate_tree = candidate_tree
        self._observe_absence = observe_absence
        self._audit = audit

    @staticmethod
    def _require_exact_blocked(state: object, payload: bytes) -> Active:
        if (
            type(state) is not Active
            or state.slot != 0
            or state.blocked_reason is not BlockReason.WINDOW_BINDING
            or encode_state(state) != payload
        ):
            raise ReconciliationError("exact blocked state is required")
        return state

    def _require_absence(self) -> None:
        try:
            observation = self._observe_absence()
        except BaseException as exc:
            raise ReconciliationError("complete process/window absence is unproved") from exc
        if (
            type(observation) is not tuple
            or len(observation) != 2
            or any(type(count) is not int or count != 0 for count in observation)
        ):
            raise ReconciliationError("complete process/window absence is unproved")

    def reconcile(self) -> Ready:
        lease = self._runtime.acquire_mutex()
        try:
            if type(lease.abandoned) is not bool or lease.abandoned:
                raise ReconciliationError("abandoned mutex forbids reconciliation")
            lease.require_usable()
            store = self._make_state_store()
            state, payload = store.load_with_bytes()
            blocked = self._require_exact_blocked(state, payload)
            tree = self._candidate_tree()
            self._approvals.validate(
                ApprovalAction.RECONCILE_WINDOW_BINDING, tree, blocked, payload
            )
            self._require_absence()

            current, current_payload = store.load_with_bytes()
            if (
                type(current) is not Active
                or current != blocked
                or current_payload != payload
                or self._candidate_tree() != tree
            ):
                raise ReconciliationError("lifecycle state or candidate tree changed")
            self._approvals.validate_then_consume(
                ApprovalAction.RECONCILE_WINDOW_BINDING,
                tree,
                current,
                current_payload,
            )
            self._require_absence()
            try:
                self._audit.append_intent()
            except BaseException as exc:
                raise ReconciliationError("reconciliation intent audit failed") from exc
            self._require_absence()
            outcome_failed = False

            def append_outcome() -> None:
                nonlocal outcome_failed
                try:
                    self._audit.append_outcome()
                except BaseException:
                    outcome_failed = True
                    raise

            try:
                committed = store.commit_with_postcondition(Ready(0), append_outcome)
            except BaseException as exc:
                if outcome_failed:
                    raise ReconciliationError(
                        "reconciliation outcome audit is unresolved after READY"
                    ) from exc
                raise ReconciliationError(
                    "lifecycle write/read-back is unresolved"
                ) from exc
            if type(committed) is not Ready or committed != Ready(0):
                raise ReconciliationError("lifecycle write/read-back is unresolved")
            return committed
        finally:
            try:
                lease.release()
            except BaseException as exc:
                raise ReconciliationError(
                    "mutex release is unresolved after reconciliation"
                ) from exc


__all__ = [
    "ApprovalAction",
    "ApprovalError",
    "DurableReconciliationAudit",
    "OneShotApproval",
    "OneShotApprovalService",
    "PrivateApprovalStorage",
    "ReconciliationError",
    "StaleStateApprovalIssuer",
    "StaleStateReconciler",
    "WindowsPrivatePathSecurity",
]
