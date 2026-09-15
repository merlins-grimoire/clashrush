"""Durable sanitized audit appends and the input-side fail-closed guard."""

from __future__ import annotations

import ctypes
import hashlib
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock

from .audit_event import (
    AccountRef,
    AuditEvent,
    AuditEventError,
    AuditEventKind,
    AuditOutcome,
    ReasonCode,
    TeamRef,
    decode_audit_event,
    encode_audit_event,
)
from .scheduler_contract import Lane, VisitGeneration
from .scheduler_journal import (
    TERMINAL_ACTION_STATES,
    ActionState,
    JournalAction,
    SchedulerJournalError,
    require_pristine_action,
    transition_action,
)
from .scheduler_store import SchedulerStoreError, SQLiteJobStore

_MAX_GENERATION = 2**63 - 1
_APPEND_LOCK = Lock()
_FILE_BASIC_INFO_CLASS = 0


class _FileBasicInfo(ctypes.Structure):
    _fields_ = (
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
    )


if os.name == "nt":
    _GET_OSFHANDLE = __import__("msvcrt").get_osfhandle
    _WIN_ERROR = ctypes.WinError
    _GET_FILE_INFORMATION_BY_HANDLE_EX = (
        ctypes.windll.kernel32.GetFileInformationByHandleEx
    )
    _GET_FILE_INFORMATION_BY_HANDLE_EX.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _GET_FILE_INFORMATION_BY_HANDLE_EX.restype = wintypes.BOOL
else:
    _GET_OSFHANDLE = None
    _GET_FILE_INFORMATION_BY_HANDLE_EX = None
    _WIN_ERROR = OSError


class AuditStreamError(RuntimeError):
    """The sanitized append-only stream could not prove a durable write."""


class InputResult(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AuditedInputStatus(StrEnum):
    SUPPRESSED = "SUPPRESSED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class AuditedInputReport(tuple[object, ...]):
    """A bounded result carrying the durable scheduler action state."""

    __slots__ = ()

    def __new__(
        cls, *, status: AuditedInputStatus, action: JournalAction
    ) -> AuditedInputReport:
        if type(status) is not AuditedInputStatus:
            raise AuditStreamError("audited input status must be closed")
        try:
            action = require_pristine_action(action)
        except SchedulerJournalError as exc:
            raise AuditStreamError("audited input action is invalid") from exc
        return tuple.__new__(cls, (status, action))

    status = property(lambda self: self[0])
    action = property(lambda self: self[1])


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DurableAuditStream:
    """Append canonical records with flush and byte-equal read-back proof."""

    __slots__ = ("__path", "__monotonic_clock", "__wall_clock", "__poisoned")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("audit stream state is sealed")

    def __init__(
        self,
        path: Path,
        *,
        monotonic_clock: Callable[[], int] = time.monotonic_ns,
        wall_clock: Callable[[], str] = _utc_now,
    ) -> None:
        if not isinstance(path, Path):
            raise AuditStreamError("audit stream path must be a Path")
        try:
            parent = path.parent.resolve(strict=True)
        except OSError as exc:
            raise AuditStreamError("audit stream parent must exist") from exc
        if not parent.is_dir() or parent != path.parent:
            raise AuditStreamError("audit stream path is not canonical")
        if path.exists() and (path.is_symlink() or path.resolve(strict=True) != path):
            raise AuditStreamError("audit stream path must not redirect")
        if not callable(monotonic_clock) or not callable(wall_clock):
            raise AuditStreamError("audit stream clocks must be callable")

        object.__setattr__(self, "_DurableAuditStream__path", path)
        object.__setattr__(
            self, "_DurableAuditStream__monotonic_clock", monotonic_clock
        )
        object.__setattr__(self, "_DurableAuditStream__wall_clock", wall_clock)
        object.__setattr__(self, "_DurableAuditStream__poisoned", False)
        _PRISTINE_LOAD_NEXT_GENERATION(path)

    @property
    def path(self) -> Path:
        return object.__getattribute__(self, "_DurableAuditStream__path")

    @property
    def poisoned(self) -> bool:
        return object.__getattribute__(self, "_DurableAuditStream__poisoned")

    @staticmethod
    def _load_next_generation(path: Path) -> int:
        if not path.exists():
            return 1
        expected = 1
        try:
            with path.open("rb") as source:
                for record in source:
                    event = decode_audit_event(record)
                    if event.event_generation != expected:
                        raise AuditStreamError(
                            "audit stream generations are not contiguous"
                        )
                    expected += 1
                    if expected > _MAX_GENERATION + 1:
                        raise AuditStreamError("audit stream generation is exhausted")
        except AuditStreamError:
            raise
        except (AuditEventError, OSError) as exc:
            raise AuditStreamError("audit stream is invalid") from exc
        if expected > _MAX_GENERATION:
            raise AuditStreamError("audit stream generation is exhausted")
        return expected

    @staticmethod
    def _snapshot(
        stream: DurableAuditStream,
    ) -> tuple[Path, Callable[[], int], Callable[[], str], bool]:
        if type(stream) is not DurableAuditStream:
            raise AuditStreamError("audit stream must be exact")
        try:
            path = object.__getattribute__(stream, "_DurableAuditStream__path")
            monotonic_clock = object.__getattribute__(
                stream, "_DurableAuditStream__monotonic_clock"
            )
            wall_clock = object.__getattribute__(
                stream, "_DurableAuditStream__wall_clock"
            )
            poisoned = object.__getattribute__(
                stream, "_DurableAuditStream__poisoned"
            )
        except AttributeError as exc:
            raise AuditStreamError("audit stream state is invalid") from exc
        if (
            not isinstance(path, Path)
            or not callable(monotonic_clock)
            or not callable(wall_clock)
            or type(poisoned) is not bool
        ):
            raise AuditStreamError("audit stream state is invalid")
        return path, monotonic_clock, wall_clock, poisoned

    def append(
        self,
        *,
        team: TeamRef,
        account_ref: AccountRef,
        visit_generation: VisitGeneration,
        lane: Lane,
        action_nonce: str,
        kind: AuditEventKind,
        reason_code: ReasonCode,
        outcome: AuditOutcome,
    ) -> AuditEvent:
        with _APPEND_LOCK:
            path, monotonic_clock, wall_clock, poisoned = _PRISTINE_STREAM_SNAPSHOT(
                self
            )
            if poisoned:
                raise AuditStreamError("audit stream requires reconciliation")
            try:
                next_generation = _PRISTINE_LOAD_NEXT_GENERATION(path)
                event = AuditEvent(
                    event_generation=next_generation,
                    team=team,
                    account_ref=account_ref,
                    visit_generation=visit_generation,
                    lane=lane,
                    action_nonce=action_nonce,
                    kind=kind,
                    reason_code=reason_code,
                    monotonic_ns=monotonic_clock(),
                    wall_time_utc=wall_clock(),
                    outcome=outcome,
                )
                record = encode_audit_event(event)
            except BaseException as exc:
                if isinstance(exc, AuditStreamError):
                    raise
                raise AuditStreamError("audit event could not be constructed") from exc

            flags = os.O_CREAT | os.O_RDWR | os.O_APPEND
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor: int | None = None
            try:
                descriptor = os.open(path, flags, 0o600)
                written = os.write(descriptor, record)
                if written != len(record):
                    raise OSError("partial audit append")
                os.fsync(descriptor)
                end = os.lseek(descriptor, 0, os.SEEK_END)
                if end < len(record):
                    raise OSError("audit append length is invalid")
                os.lseek(descriptor, end - len(record), os.SEEK_SET)
                if os.read(descriptor, len(record)) != record:
                    raise OSError("audit append read-back failed")
            except BaseException as exc:
                object.__setattr__(
                    self, "_DurableAuditStream__poisoned", True
                )
                raise AuditStreamError("audit append durability was not proved") from exc
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except BaseException:
                        object.__setattr__(
                            self, "_DurableAuditStream__poisoned", True
                        )
            if _PRISTINE_STREAM_SNAPSHOT(self)[3]:
                raise AuditStreamError("audit append close was not proved")
            return event


_PRISTINE_LOAD_NEXT_GENERATION = vars(DurableAuditStream)[
    "_load_next_generation"
].__func__
_PRISTINE_STREAM_SNAPSHOT = vars(DurableAuditStream)["_snapshot"].__func__
_PRISTINE_AUDIT_APPEND = vars(DurableAuditStream)["append"]
_AUDIT_APPEND_DEPENDENCIES = (
    ("class", "append", _PRISTINE_AUDIT_APPEND),
    ("module", "AuditEvent", AuditEvent),
    ("module", "AuditStreamError", AuditStreamError),
    ("module", "_APPEND_LOCK", _APPEND_LOCK),
    ("module", "_PRISTINE_LOAD_NEXT_GENERATION", _PRISTINE_LOAD_NEXT_GENERATION),
    ("module", "_PRISTINE_STREAM_SNAPSHOT", _PRISTINE_STREAM_SNAPSHOT),
    ("module", "decode_audit_event", decode_audit_event),
    ("module", "encode_audit_event", encode_audit_event),
) + tuple(
    ("os", name, getattr(os, name, None))
    for name in (
        "O_APPEND",
        "O_BINARY",
        "O_CREAT",
        "O_RDWR",
        "SEEK_END",
        "SEEK_SET",
        "close",
        "fsync",
        "lseek",
        "open",
        "read",
        "write",
    )
)
_AUDIT_APPEND_BOUNDARY = tuple(
    (
        namespace,
        name,
        dependency,
        getattr(dependency, "__code__", None),
        getattr(dependency, "__defaults__", None),
        getattr(dependency, "__kwdefaults__", None),
    )
    for namespace, name, dependency in _AUDIT_APPEND_DEPENDENCIES
)
_FINALIZATION_BOUNDARY = tuple(
    (
        name,
        descriptor,
        function,
        getattr(function, "__code__", None),
        getattr(function, "__defaults__", None),
        getattr(function, "__kwdefaults__", None),
        tuple(
            (cell, cell.cell_contents)
            for cell in (getattr(function, "__closure__", None) or ())
        ),
    )
    for name in (
        "_require_action",
        "_require_open",
        "_decode_action",
        "_action_binding_seal",
        "mark_input_completed",
        "finalize_action",
    )
    for descriptor in (vars(SQLiteJobStore)[name],)
    for function in (getattr(descriptor, "__func__", descriptor),)
)


def _make_atomic_finalizer(
    require_action: Callable[[object], JournalAction],
    require_open: Callable[[SQLiteJobStore], object],
    decode_action: Callable[[object], JournalAction],
    binding_seal: Callable[[JournalAction], str],
    transition: Callable[..., JournalAction],
    terminal_states: frozenset[ActionState],
) -> Callable[
    [SQLiteJobStore, JournalAction, ActionState, Lane, bool], JournalAction
]:
    def finalize(
        journal: SQLiteJobStore,
        action: JournalAction,
        outcome: ActionState,
        pending_lane: Lane,
        input_completed: bool,
    ) -> JournalAction:
        action = require_action(action)
        if type(outcome) is not ActionState or outcome not in terminal_states:
            raise AuditStreamError("audited action outcome must be terminal")
        if type(pending_lane) is not Lane:
            raise AuditStreamError("audited pending lane is invalid")
        if type(input_completed) is not bool:
            raise AuditStreamError("audited input completion is invalid")
        if action.state not in (ActionState.INPUT_STARTED, ActionState.INPUT_COMPLETED):
            raise AuditStreamError("audited action is not awaiting finalization")
        connection = None
        try:
            persisted_action = action
            if input_completed and action.state is ActionState.INPUT_STARTED:
                action = transition(action, next_state=ActionState.INPUT_COMPLETED)
            finalized = transition(action, next_state=outcome)
            connection = require_open(journal)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM scheduler_actions WHERE action_generation = ?",
                (action.action_generation,),
            ).fetchone()
            if row is None or decode_action(row) != persisted_action:
                raise AuditStreamError("stale audited action finalization")
            visit = connection.execute(
                "SELECT * FROM scheduler_visits WHERE visit_generation = ?",
                (action.visit_generation.value,),
            ).fetchone()
            if visit is None or visit["visit_nonce"] != action.visit_nonce:
                raise AuditStreamError("audited action visit is not open")
            changed_action = connection.execute(
                """UPDATE scheduler_actions
                   SET state = ?, open_singleton = NULL,
                       input_started = ?, input_completed = ?, binding_seal = ?
                   WHERE action_generation = ? AND state = ?""",
                (
                    outcome.value,
                    int(finalized.input_started),
                    int(finalized.input_completed),
                    binding_seal(finalized),
                    action.action_generation,
                    persisted_action.state.value,
                ),
            )
            changed_visit = connection.execute(
                """UPDATE scheduler_visits SET pending_lane = ?
                   WHERE visit_generation = ? AND visit_nonce = ?""",
                (pending_lane.value, action.visit_generation.value, action.visit_nonce),
            )
            changed_job = connection.execute(
                """UPDATE scheduler_jobs SET pending_lane = ?
                   WHERE account_key = ? AND configuration_generation = ?
                     AND job_generation = ? AND state = 'ADMITTED'""",
                (
                    pending_lane.value,
                    visit["account_key"],
                    visit["configuration_generation"],
                    visit["job_generation"],
                ),
            )
            if (
                changed_action.rowcount != 1
                or changed_visit.rowcount != 1
                or changed_job.rowcount != 1
            ):
                raise AuditStreamError("audited action atomic update failed")
            connection.execute("COMMIT")
            return finalized
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, AuditStreamError):
                raise
            raise AuditStreamError("audited input outcome could not be persisted") from exc

    return finalize


_PRISTINE_ATOMIC_FINALIZE = _make_atomic_finalizer(
    vars(SQLiteJobStore)["_require_action"].__func__,
    vars(SQLiteJobStore)["_require_open"],
    vars(SQLiteJobStore)["_decode_action"].__func__,
    vars(SQLiteJobStore)["_action_binding_seal"].__func__,
    transition_action,
    TERMINAL_ACTION_STATES,
)
_ATOMIC_FINALIZER_BOUNDARY = (
    _PRISTINE_ATOMIC_FINALIZE,
    _PRISTINE_ATOMIC_FINALIZE.__code__,
    _PRISTINE_ATOMIC_FINALIZE.__defaults__,
    _PRISTINE_ATOMIC_FINALIZE.__kwdefaults__,
    tuple(
        (cell, cell.cell_contents)
        for cell in (_PRISTINE_ATOMIC_FINALIZE.__closure__ or ())
    ),
    tuple(
        (
            dependency,
            getattr(dependency, "__code__", None),
            getattr(dependency, "__defaults__", None),
            getattr(dependency, "__kwdefaults__", None),
        )
        for cell in (_PRISTINE_ATOMIC_FINALIZE.__closure__ or ())
        for dependency in (cell.cell_contents,)
        if callable(dependency)
    ),
)


def _report(status: AuditedInputStatus, action: JournalAction) -> AuditedInputReport:
    return AuditedInputReport(status=status, action=action)


def _stream_change_time(
    descriptor: int,
    __platform_name: str = os.name,
    __fstat: Callable[[int], os.stat_result] = os.fstat,
    __information_type: type[_FileBasicInfo] = _FileBasicInfo,
    __information_class: int = _FILE_BASIC_INFO_CLASS,
    __get_osfhandle: Callable[[int], int] | None = _GET_OSFHANDLE,
    __query: Callable[..., object] | None = _GET_FILE_INFORMATION_BY_HANDLE_EX,
    __byref: Callable[[object], object] = ctypes.byref,
    __sizeof: Callable[[object], int] = ctypes.sizeof,
    __get_last_error: Callable[[], int] = ctypes.get_last_error,
    __win_error: Callable[[int], OSError] = _WIN_ERROR,
) -> int:
    if __platform_name != "nt":
        return __fstat(descriptor).st_ctime_ns

    information = __information_type()
    if __get_osfhandle is None or __query is None:
        raise OSError("audit stream handle is invalid")
    handle = __get_osfhandle(descriptor)
    if handle == -1:
        raise OSError("audit stream handle is invalid")
    if not __query(
        handle,
        __information_class,
        __byref(information),
        __sizeof(information),
    ):
        raise __win_error(__get_last_error())
    return information.change_time


def _stream_file_seal(
    path: Path,
    __trusted_change_time: Callable[[int], int] = _stream_change_time,
    __stat: Callable[[Path], os.stat_result] = Path.stat,
    __open: Callable[..., object] = Path.open,
    __is_file: Callable[[Path], bool] = Path.is_file,
    __is_symlink: Callable[[Path], bool] = Path.is_symlink,
    __sha256: Callable[[], object] = hashlib.sha256,
) -> tuple[int, int, int, int, int, int, int, int, bytes]:
    if _stream_change_time is not __trusted_change_time:
        raise AuditStreamError("audit stream sealing dispatch changed")
    try:
        metadata = __stat(path)
        parent_metadata = __stat(path.parent)
        digest = __sha256()
        with __open(path, "rb") as source:
            change_time = __trusted_change_time(source.fileno())
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
            verified_change_time = __trusted_change_time(source.fileno())
        verified_metadata = __stat(path)
        verified_parent_metadata = __stat(path.parent)
    except OSError as exc:
        raise AuditStreamError("audit stream seal is unavailable") from exc
    if not __is_file(path) or __is_symlink(path):
        raise AuditStreamError("audit stream seal is invalid")
    file_evidence = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        change_time,
    )
    parent_evidence = (parent_metadata.st_mtime_ns, parent_metadata.st_ctime_ns)
    if file_evidence != (
        verified_metadata.st_dev,
        verified_metadata.st_ino,
        verified_metadata.st_size,
        verified_metadata.st_mtime_ns,
        verified_metadata.st_ctime_ns,
        verified_change_time,
    ) or parent_evidence != (
        verified_parent_metadata.st_mtime_ns,
        verified_parent_metadata.st_ctime_ns,
    ):
        raise AuditStreamError("audit stream changed while sealing")
    return (*file_evidence, *parent_evidence, digest.digest())


_PRISTINE_STREAM_FILE_SEAL = _stream_file_seal


def execute_audited_input(
    stream: DurableAuditStream,
    journal: SQLiteJobStore,
    *,
    team: TeamRef,
    account_ref: AccountRef,
    action: JournalAction,
    pending_lane: Lane,
    input_operation: Callable[[], InputResult],
    __trusted_append: Callable[..., AuditEvent] = _PRISTINE_AUDIT_APPEND,
    __trusted_generation: Callable[[Path], int] = _PRISTINE_LOAD_NEXT_GENERATION,
    __trusted_snapshot: Callable[
        [DurableAuditStream],
        tuple[Path, Callable[[], int], Callable[[], str], bool],
    ] = _PRISTINE_STREAM_SNAPSHOT,
    __trusted_stream_seal: Callable[
        [Path], tuple[int, int, int, int, int, int, int, int, bytes]
    ] = _PRISTINE_STREAM_FILE_SEAL,
    __trusted_change_time: Callable[[int], int] = _stream_change_time,
    __trusted_change_time_code: object = _stream_change_time.__code__,
    __trusted_change_time_defaults: object = _stream_change_time.__defaults__,
    __trusted_stream_seal_code: object = _stream_file_seal.__code__,
    __trusted_stream_seal_defaults: object = _stream_file_seal.__defaults__,
    __trusted_native_query: object = _GET_FILE_INFORMATION_BY_HANDLE_EX,
    __trusted_native_argtypes: object = getattr(
        _GET_FILE_INFORMATION_BY_HANDLE_EX, "argtypes", None
    ),
    __trusted_native_restype: object = getattr(
        _GET_FILE_INFORMATION_BY_HANDLE_EX, "restype", None
    ),
    __trusted_native_errcheck: object = getattr(
        _GET_FILE_INFORMATION_BY_HANDLE_EX, "errcheck", None
    ),
    __trusted_os_module: object = os,
    __trusted_append_boundary: tuple[
        tuple[str, str, object, object, object, object], ...
    ] = (
        _AUDIT_APPEND_BOUNDARY
    ),
    __trusted_finalizer: Callable[
        [SQLiteJobStore, JournalAction, ActionState, Lane, bool], JournalAction
    ] = _PRISTINE_ATOMIC_FINALIZE,
    __trusted_atomic_finalizer_boundary: tuple[object, ...] = (
        _ATOMIC_FINALIZER_BOUNDARY
    ),
    __trusted_finalization_boundary: tuple[tuple[object, ...], ...] = (
        _FINALIZATION_BOUNDARY
    ),
) -> AuditedInputReport:
    """Run one injected input only between durable intent and outcome appends."""

    def closure_cells_unchanged(
        boundary: tuple[tuple[object, object], ...],
    ) -> bool:
        try:
            return all(cell.cell_contents is value for cell, value in boundary)
        except ValueError:
            return False

    sealing_dispatch_unchanged = (
        _stream_change_time is __trusted_change_time
        and _stream_change_time.__code__ is __trusted_change_time_code
        and _stream_change_time.__defaults__ is __trusted_change_time_defaults
        and _stream_file_seal is __trusted_stream_seal
        and _stream_file_seal.__code__ is __trusted_stream_seal_code
        and _stream_file_seal.__defaults__ is __trusted_stream_seal_defaults
        and _GET_FILE_INFORMATION_BY_HANDLE_EX is __trusted_native_query
        and getattr(__trusted_native_query, "argtypes", None)
        is __trusted_native_argtypes
        and getattr(__trusted_native_query, "restype", None)
        is __trusted_native_restype
        and getattr(__trusted_native_query, "errcheck", None)
        is __trusted_native_errcheck
    )
    append_dependencies_unchanged = (
        os is __trusted_os_module
        and _AUDIT_APPEND_BOUNDARY is __trusted_append_boundary
        and all(
            (
                globals().get(name)
                if namespace == "module"
                else vars(DurableAuditStream).get(name)
            )
            is dependency
            and getattr(dependency, "__code__", None) is code
            and getattr(dependency, "__defaults__", None) is defaults
            and getattr(dependency, "__kwdefaults__", None) is kwdefaults
            for namespace, name, dependency, code, defaults, kwdefaults
            in __trusted_append_boundary
            if namespace != "os"
        )
    )
    active_os_append_boundary = tuple(
        (
            name,
            getattr(os, name, None),
            getattr(getattr(os, name, None), "__code__", None),
            getattr(getattr(os, name, None), "__defaults__", None),
            getattr(getattr(os, name, None), "__kwdefaults__", None),
        )
        for namespace, name, _dependency, _code, _defaults, _kwdefaults
        in __trusted_append_boundary
        if namespace == "os"
    )
    finalization_dependencies_unchanged = (
        _FINALIZATION_BOUNDARY is __trusted_finalization_boundary
        and all(
            vars(SQLiteJobStore).get(name) is descriptor
            and getattr(function, "__code__", None) is code
            and getattr(function, "__defaults__", None) is defaults
            and getattr(function, "__kwdefaults__", None) is kwdefaults
            and closure_cells_unchanged(closure)
            for name, descriptor, function, code, defaults, kwdefaults, closure
            in __trusted_finalization_boundary
        )
    )
    (
        finalizer_function,
        finalizer_code,
        finalizer_defaults,
        finalizer_kwdefaults,
        finalizer_closure,
        finalizer_dependencies,
    ) = __trusted_atomic_finalizer_boundary
    atomic_finalizer_unchanged = (
        _ATOMIC_FINALIZER_BOUNDARY is __trusted_atomic_finalizer_boundary
        and _PRISTINE_ATOMIC_FINALIZE is finalizer_function
        and finalizer_function is __trusted_finalizer
        and getattr(finalizer_function, "__code__", None) is finalizer_code
        and getattr(finalizer_function, "__defaults__", None) is finalizer_defaults
        and getattr(finalizer_function, "__kwdefaults__", None)
        is finalizer_kwdefaults
        and closure_cells_unchanged(finalizer_closure)
        and all(
            getattr(dependency, "__code__", None) is code
            and getattr(dependency, "__defaults__", None) is defaults
            and getattr(dependency, "__kwdefaults__", None) is kwdefaults
            for dependency, code, defaults, kwdefaults in finalizer_dependencies
        )
    )
    if type(stream) is not DurableAuditStream or type(journal) is not SQLiteJobStore:
        raise AuditStreamError("audited input requires exact durable stores")
    if (
        _PRISTINE_AUDIT_APPEND is not __trusted_append
        or _PRISTINE_LOAD_NEXT_GENERATION is not __trusted_generation
        or _PRISTINE_STREAM_SNAPSHOT is not __trusted_snapshot
        or _PRISTINE_STREAM_FILE_SEAL is not __trusted_stream_seal
        or _PRISTINE_ATOMIC_FINALIZE is not __trusted_finalizer
        or _FINALIZATION_BOUNDARY is not __trusted_finalization_boundary
        or vars(DurableAuditStream).get("append") is not __trusted_append
        or _stream_file_seal is not __trusted_stream_seal
        or not sealing_dispatch_unchanged
        or not append_dependencies_unchanged
        or not finalization_dependencies_unchanged
        or not atomic_finalizer_unchanged
    ):
        raise AuditStreamError("audited input dispatch is unavailable")
    try:
        current = require_pristine_action(action)
    except SchedulerJournalError as exc:
        raise AuditStreamError("audited input action is invalid") from exc
    if type(pending_lane) is not Lane or not callable(input_operation):
        raise AuditStreamError("audited input request is invalid")

    try:
        if current.state is ActionState.PLANNED:
            current = SQLiteJobStore.record_action_intent(journal, current)
        elif current.state is not ActionState.INTENT_RECORDED:
            raise SchedulerStoreError("action is not awaiting audited input")
    except SchedulerStoreError:
        return _report(AuditedInputStatus.SUPPRESSED, current)

    try:
        __trusted_append(
            stream,
            team=team,
            account_ref=account_ref,
            visit_generation=current.visit_generation,
            lane=current.lane,
            action_nonce=current.action_nonce,
            kind=AuditEventKind.ACTION_INTENT,
            reason_code=ReasonCode.ACTION_AUTHORIZED,
            outcome=AuditOutcome.PENDING,
        )
    except (AuditStreamError, AuditEventError):
        return _report(AuditedInputStatus.SUPPRESSED, current)

    try:
        stream_identity = __trusted_snapshot(stream)
        file_seal = __trusted_stream_seal(stream_identity[0])
        journal_path = object.__getattribute__(journal, "_path")
        if not isinstance(journal_path, Path):
            raise AuditStreamError("scheduler path identity is invalid")
        sealed_outcome_stream = DurableAuditStream(
            stream_identity[0],
            monotonic_clock=stream_identity[1],
            wall_clock=stream_identity[2],
        )
    except AuditStreamError:
        return _report(AuditedInputStatus.SUPPRESSED, current)

    try:
        current = SQLiteJobStore.mark_input_started(journal, current)
    except SchedulerStoreError:
        return _report(AuditedInputStatus.SUPPRESSED, current)

    requested_state = ActionState.UNCERTAIN
    audit_outcome = AuditOutcome.UNCERTAIN
    reason = ReasonCode.ACTION_UNCERTAIN
    status = AuditedInputStatus.UNCERTAIN
    input_completed = False
    try:
        input_result = input_operation()
        if type(input_result) is InputResult:
            input_completed = True
            if input_result is InputResult.SUCCEEDED:
                requested_state = ActionState.CONFIRMED
                audit_outcome = AuditOutcome.SUCCEEDED
                reason = ReasonCode.POSTCONDITION_CONFIRMED
                status = AuditedInputStatus.CONFIRMED
            else:
                requested_state = ActionState.FAILED
                audit_outcome = AuditOutcome.FAILED
                reason = ReasonCode.INPUT_FAILED
                status = AuditedInputStatus.FAILED
    except BaseException:
        pass

    try:
        current_stream_identity = __trusted_snapshot(stream)
        stream_unchanged = (
            current_stream_identity[0] is stream_identity[0]
            and current_stream_identity[1] is stream_identity[1]
            and current_stream_identity[2] is stream_identity[2]
            and current_stream_identity[3] is stream_identity[3]
        )
    except AuditStreamError:
        stream_unchanged = False
    sealing_dispatch_unchanged = (
        _stream_change_time is __trusted_change_time
        and _stream_change_time.__code__ is __trusted_change_time_code
        and _stream_change_time.__defaults__ is __trusted_change_time_defaults
        and _stream_file_seal is __trusted_stream_seal
        and _stream_file_seal.__code__ is __trusted_stream_seal_code
        and _stream_file_seal.__defaults__ is __trusted_stream_seal_defaults
        and _GET_FILE_INFORMATION_BY_HANDLE_EX is __trusted_native_query
        and getattr(__trusted_native_query, "argtypes", None)
        is __trusted_native_argtypes
        and getattr(__trusted_native_query, "restype", None)
        is __trusted_native_restype
        and getattr(__trusted_native_query, "errcheck", None)
        is __trusted_native_errcheck
    )
    if sealing_dispatch_unchanged:
        try:
            stream_file_unchanged = (
                __trusted_stream_seal(stream_identity[0]) == file_seal
            )
        except AuditStreamError:
            stream_file_unchanged = False
    else:
        stream_file_unchanged = False
    append_dispatch_unchanged = (
        _PRISTINE_AUDIT_APPEND is __trusted_append
        and _PRISTINE_LOAD_NEXT_GENERATION is __trusted_generation
        and _PRISTINE_STREAM_SNAPSHOT is __trusted_snapshot
        and vars(DurableAuditStream).get("append") is __trusted_append
        and os is __trusted_os_module
        and _AUDIT_APPEND_BOUNDARY is __trusted_append_boundary
        and all(
            (
                globals().get(name)
                if namespace == "module"
                else vars(DurableAuditStream).get(name)
            )
            is dependency
            and getattr(dependency, "__code__", None) is code
            and getattr(dependency, "__defaults__", None) is defaults
            and getattr(dependency, "__kwdefaults__", None) is kwdefaults
            for namespace, name, dependency, code, defaults, kwdefaults
            in __trusted_append_boundary
            if namespace != "os"
        )
        and all(
            getattr(os, name, None) is dependency
            and getattr(dependency, "__code__", None) is code
            and getattr(dependency, "__defaults__", None) is defaults
            and getattr(dependency, "__kwdefaults__", None) is kwdefaults
            for name, dependency, code, defaults, kwdefaults
            in active_os_append_boundary
        )
    )
    changed_finalization_helpers = tuple(
        entry
        for entry in __trusted_finalization_boundary
        for name, descriptor, function, code, defaults, kwdefaults, closure in (entry,)
        if (
            vars(SQLiteJobStore).get(name) is not descriptor
            or getattr(function, "__code__", None) is not code
            or getattr(function, "__defaults__", None) is not defaults
            or getattr(function, "__kwdefaults__", None) is not kwdefaults
            or not closure_cells_unchanged(closure)
        )
    )
    atomic_finalizer_unchanged = (
        _ATOMIC_FINALIZER_BOUNDARY is __trusted_atomic_finalizer_boundary
        and _PRISTINE_ATOMIC_FINALIZE is finalizer_function
        and finalizer_function is __trusted_finalizer
        and getattr(finalizer_function, "__code__", None) is finalizer_code
        and getattr(finalizer_function, "__defaults__", None) is finalizer_defaults
        and getattr(finalizer_function, "__kwdefaults__", None)
        is finalizer_kwdefaults
        and closure_cells_unchanged(finalizer_closure)
        and all(
            getattr(dependency, "__code__", None) is code
            and getattr(dependency, "__defaults__", None) is defaults
            and getattr(dependency, "__kwdefaults__", None) is kwdefaults
            for dependency, code, defaults, kwdefaults in finalizer_dependencies
        )
    )
    finalization_dispatch_unchanged = (
        not changed_finalization_helpers
        and atomic_finalizer_unchanged
        and _FINALIZATION_BOUNDARY is __trusted_finalization_boundary
    )
    for (
        name,
        descriptor,
        function,
        code,
        defaults,
        kwdefaults,
        closure,
    ) in changed_finalization_helpers:
        type.__setattr__(SQLiteJobStore, name, descriptor)
        function.__code__ = code
        function.__defaults__ = defaults
        function.__kwdefaults__ = kwdefaults
        for cell, value in closure:
            cell.cell_contents = value
    if not atomic_finalizer_unchanged:
        finalizer_function.__code__ = finalizer_code
        finalizer_function.__defaults__ = finalizer_defaults
        finalizer_function.__kwdefaults__ = finalizer_kwdefaults
        for cell, value in finalizer_closure:
            cell.cell_contents = value
        for dependency, code, defaults, kwdefaults in finalizer_dependencies:
            if code is not None:
                dependency.__code__ = code
            if hasattr(dependency, "__defaults__"):
                dependency.__defaults__ = defaults
            if hasattr(dependency, "__kwdefaults__"):
                dependency.__kwdefaults__ = kwdefaults
    if (
        not stream_unchanged
        or not stream_file_unchanged
        or not sealing_dispatch_unchanged
        or not append_dispatch_unchanged
        or not finalization_dispatch_unchanged
    ):
        requested_state = ActionState.UNCERTAIN
        audit_outcome = AuditOutcome.UNCERTAIN
        reason = ReasonCode.ACTION_UNCERTAIN
        status = AuditedInputStatus.UNCERTAIN
    outcome_stream = stream if stream_unchanged else sealed_outcome_stream

    if append_dispatch_unchanged:
        try:
            __trusted_append(
                outcome_stream,
                team=team,
                account_ref=account_ref,
                visit_generation=current.visit_generation,
                lane=current.lane,
                action_nonce=current.action_nonce,
                kind=AuditEventKind.ACTION_OUTCOME,
                reason_code=reason,
                outcome=audit_outcome,
            )
        except (AuditStreamError, AuditEventError):
            requested_state = ActionState.UNCERTAIN
            status = AuditedInputStatus.UNCERTAIN

    try:
        current = __trusted_finalizer(
            journal,
            current,
            requested_state,
            pending_lane,
            input_completed,
        )
    except (AuditStreamError, SchedulerStoreError):
        try:
            with SQLiteJobStore(journal_path) as recovery_store:
                current = __trusted_finalizer(
                    recovery_store,
                    current,
                    ActionState.UNCERTAIN,
                    pending_lane,
                    input_completed,
                )
        except BaseException as recovery_error:
            raise AuditStreamError(
                "audited input outcome could not be persisted"
            ) from recovery_error
        status = AuditedInputStatus.UNCERTAIN
    return _report(status, current)
