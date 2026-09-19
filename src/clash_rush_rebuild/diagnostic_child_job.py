"""Atomic Windows Job ownership for the bounded diagnostic child.

The permanent, thread-gated ``CreateProcess`` wrapper is adapted from
NousResearch/hermes-agent PR #69076 at commit
c10c89f74637cc945e9840705e0209df29aa08c8.  Unlike the donor's generic,
process-wide Job, this module admits one explicit per-diagnostic owner and
fails closed whenever atomic ownership cannot be proved.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import threading
import time
import types
from dataclasses import dataclass
from typing import Callable, Sequence

CREATE_SUSPENDED = 0x00000004
CLEANUP_WAIT_MILLISECONDS = 5_000
STREAM_CLOSE_TIMEOUT_SECONDS = 1.0
SUPPORTED_PYTHON_MINORS = frozenset({(3, 11), (3, 13)})
_CREATION_FLAGS_INDEX = 5
_CREATE_PROCESS_ARGC = 9
_original_create_process = None
_create_process_install_lock = threading.Lock()
_spawn_owner = threading.local()


class DiagnosticChildJobError(RuntimeError):
    """The diagnostic child could not be owned or retired conclusively."""


@dataclass(frozen=True, slots=True)
class DiagnosticChildOutcome:
    returncode: int | None
    stdout: str | None
    stderr: str | None
    child_wait_completed: bool
    cleanup_succeeded: bool
    operational_failure: bool = False


@dataclass(slots=True)
class DiagnosticJobOwner:
    runtime: object
    job: object
    token: object
    retained_process: object | None = None
    raw_process: object | None = None
    raw_thread: object | None = None
    pid: int | None = None
    creation_time: int | None = None
    assignment_attempted: bool = False
    assigned: bool = False
    resumed: bool = False
    child_wait_completed: bool = False
    cleanup_succeeded: bool = False
    cleaned: bool = False
    spawn_claimed: bool = False

    def cleanup_created(self, process: object, thread: object) -> None:
        """Retire a raw CreateProcess result through every owned authority."""
        if self.cleaned:
            return
        clean = True

        def attempt(operation) -> object | None:
            nonlocal clean
            try:
                return operation()
            except BaseException:
                clean = False
                return None

        job_terminated = True
        try:
            self.runtime.terminate_job(self.job)
        except BaseException:
            clean = False
            job_terminated = False
        if not self.assigned:
            attempt(lambda: self.runtime.terminate_process(process))
        elif not job_terminated and self.retained_process is not None:
            attempt(lambda: self.runtime.terminate_process(self.retained_process))
        waited = (
            attempt(
                lambda: self.runtime.wait_process(
                    self.retained_process, CLEANUP_WAIT_MILLISECONDS
                )
            )
            if self.retained_process is not None
            else None
        )
        self.child_wait_completed = waited is True
        if not self.child_wait_completed:
            clean = False
        active = attempt(lambda: self.runtime.job_active_count(self.job))
        if type(active) is not int or active != 0:
            clean = False
        for handle in (thread, process, self.retained_process, self.job):
            if handle is not None:
                attempt(lambda handle=handle: self.runtime.close_handle(handle))
        self.cleaned = True
        self.cleanup_succeeded = clean

    def cleanup_published(self) -> None:
        """Terminate the private Job and prove its published child retired."""
        if self.cleaned:
            return
        clean = True

        def attempt(operation) -> object | None:
            nonlocal clean
            try:
                return operation()
            except BaseException:
                clean = False
                return None

        job_terminated = True
        try:
            self.runtime.terminate_job(self.job)
        except BaseException:
            clean = False
            job_terminated = False
        if not job_terminated and self.retained_process is not None:
            attempt(lambda: self.runtime.terminate_process(self.retained_process))
        waited = (
            attempt(
                lambda: self.runtime.wait_process(
                    self.retained_process, CLEANUP_WAIT_MILLISECONDS
                )
            )
            if self.retained_process is not None
            else None
        )
        self.child_wait_completed = waited is True
        if not self.child_wait_completed:
            clean = False
        active = attempt(lambda: self.runtime.job_active_count(self.job))
        if type(active) is not int or active != 0:
            clean = False
        for handle in (self.retained_process, self.job):
            if handle is not None:
                attempt(lambda handle=handle: self.runtime.close_handle(handle))
        self.cleaned = True
        self.cleanup_succeeded = clean


def _job_owned_create_process(*args, **kwargs):
    """Pass through unless this exact thread owns a diagnostic spawn."""
    owner = getattr(_spawn_owner, "owner", None)
    if owner is None:
        if _original_create_process is None:
            raise RuntimeError("CreateProcess wrapper is not installed")
        return _original_create_process(*args, **kwargs)
    if type(owner) is not DiagnosticJobOwner or owner.token is None:
        raise DiagnosticChildJobError("invalid diagnostic spawn owner")
    if owner.spawn_claimed:
        raise DiagnosticChildJobError("diagnostic spawn owner already used")
    owner.spawn_claimed = True
    if len(args) != _CREATE_PROCESS_ARGC or kwargs:
        raise DiagnosticChildJobError("unsupported CreateProcess signature")
    flags = args[_CREATION_FLAGS_INDEX]
    if type(flags) is not int:
        raise DiagnosticChildJobError("invalid CreateProcess flags")
    patched = list(args)
    patched[_CREATION_FLAGS_INDEX] = flags | CREATE_SUSPENDED
    if _original_create_process is None:
        raise DiagnosticChildJobError("CreateProcess wrapper is not installed")
    created = _original_create_process(*patched)
    if (
        type(created) is not tuple
        or len(created) != 4
        or any(type(value) is not int or value <= 0 for value in created)
    ):
        raise DiagnosticChildJobError("invalid CreateProcess result")
    process, thread, pid, _thread_id = created
    owner.raw_process = process
    owner.raw_thread = thread
    runtime = owner.runtime
    try:
        owner.retained_process = runtime.duplicate_process_handle(process)
        owner.pid = pid
        owner.creation_time = runtime.process_creation_time(owner.retained_process)
        owner.assignment_attempted = True
        runtime.assign_to_job(owner.job, process)
        if runtime.is_process_in_job(process, owner.job) is not True:
            raise DiagnosticChildJobError("diagnostic Job membership unproved")
        owner.assigned = True
        resume_result = runtime.resume_thread(thread)
        if type(resume_result) is not int or resume_result != 1:
            raise DiagnosticChildJobError("diagnostic child resume failed")
        owner.resumed = True
        return created
    except BaseException:
        owner.cleanup_created(process, thread)
        raise DiagnosticChildJobError("diagnostic child ownership failed") from None


def _install_job_owned_create_process_once() -> None:
    """Install the donor-derived permanent thread-gated wrapper exactly once."""
    global _original_create_process
    with _create_process_install_lock:
        if _python_minor() not in SUPPORTED_PYTHON_MINORS:
            raise DiagnosticChildJobError("unsupported Python minor")
        winapi = getattr(subprocess, "_winapi", None)
        current = getattr(winapi, "CreateProcess", None)
        if _original_create_process is not None:
            if current is not _job_owned_create_process:
                raise DiagnosticChildJobError("unknown CreateProcess wrapper")
            return
        if getattr(current, "_clash_rush_diagnostic_job_owned", False) is True:
            original = getattr(current, "_clash_rush_true_original", None)
            if original is None:
                raise DiagnosticChildJobError("unknown CreateProcess wrapper")
            _original_create_process = original
            _job_owned_create_process._clash_rush_true_original = original  # type: ignore[attr-defined]
            _job_owned_create_process._clash_rush_diagnostic_job_owned = True  # type: ignore[attr-defined]
            winapi.CreateProcess = _job_owned_create_process
            return
        if (
            not isinstance(current, types.BuiltinFunctionType)
            or getattr(current, "__module__", None) != "_winapi"
            or getattr(current, "__name__", None) != "CreateProcess"
        ):
            raise DiagnosticChildJobError("unknown CreateProcess wrapper")
        try:
            parameters = tuple(inspect.signature(current).parameters.values())
        except BaseException:
            raise DiagnosticChildJobError("unsupported CreateProcess signature") from None
        if len(parameters) != _CREATE_PROCESS_ARGC or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
            for parameter in parameters
        ):
            raise DiagnosticChildJobError("unsupported CreateProcess signature")
        _original_create_process = current
        _job_owned_create_process._clash_rush_true_original = current  # type: ignore[attr-defined]
        _job_owned_create_process._clash_rush_diagnostic_job_owned = True  # type: ignore[attr-defined]
        winapi.CreateProcess = _job_owned_create_process


def _python_minor() -> tuple[int, int]:
    return sys.version_info.major, sys.version_info.minor


def _close_popen_resources(process: object) -> bool:
    """Close Popen resources without allowing a pipe lock to block cleanup."""
    clean = True
    closers: list[threading.Thread] = []

    def close_stream(stream: object) -> None:
        nonlocal clean
        try:
            stream.close()
        except BaseException:
            clean = False

    for stream_name in ("stdout", "stderr"):
        stream = getattr(process, stream_name, None)
        if stream is not None:
            try:
                closer = threading.Thread(
                    target=close_stream, args=(stream,), daemon=True
                )
            except BaseException:
                clean = False
                continue
            try:
                closer.start()
            except BaseException:
                clean = False
                continue
            closers.append(closer)
    deadline = time.monotonic() + STREAM_CLOSE_TIMEOUT_SECONDS
    for closer in closers:
        try:
            closer.join(max(0.0, deadline - time.monotonic()))
            if closer.is_alive():
                clean = False
        except BaseException:
            clean = False
    handle = getattr(process, "_handle", None)
    if handle is not None:
        try:
            handle.Close()
            setattr(process, "_handle", None)
        except BaseException:
            clean = False
    return clean


def run_owned_diagnostic_child(
    command: Sequence[str],
    *,
    timeout: int,
    runtime_factory: Callable[[], object] | None = None,
    popen_factory: Callable[..., object] = subprocess.Popen,
    installer: Callable[[], None] = _install_job_owned_create_process_once,
    monotonic: Callable[[], float] = time.monotonic,
) -> DiagnosticChildOutcome:
    """Run one child under a private Job with finite communication and cleanup."""
    if (
        type(timeout) is not int
        or timeout <= 0
        or type(command) not in (list, tuple)
        or not command
        or any(type(part) is not str or not part for part in command)
        or getattr(_spawn_owner, "owner", None) is not None
    ):
        return DiagnosticChildOutcome(None, None, None, False, False)
    if runtime_factory is None:
        from .win32_runtime import NativeWin32Api, Win32Runtime

        runtime_factory = lambda: Win32Runtime(NativeWin32Api())
    runtime = None
    owner = None
    process = None
    try:
        runtime = runtime_factory()
        job = runtime.create_job()
        owner = DiagnosticJobOwner(runtime, job, object())
        runtime.set_kill_on_close(job)
        installer()
        deadline = monotonic() + timeout
        _spawn_owner.owner = owner
        try:
            process = popen_factory(
                list(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            owner.raw_process = None
            owner.raw_thread = None
        finally:
            _spawn_owner.owner = None
        remaining = max(0.0, deadline - monotonic())
        stdout, stderr = process.communicate(timeout=remaining)
        returncode = process.returncode
        if type(returncode) is not int or type(stdout) is not str or type(stderr) is not str:
            raise DiagnosticChildJobError("malformed diagnostic child result")
        owner.cleanup_published()
        resources_closed = _close_popen_resources(process)
        if not owner.cleanup_succeeded or not resources_closed:
            return DiagnosticChildOutcome(None, None, None, owner.child_wait_completed, False)
        return DiagnosticChildOutcome(returncode, stdout, stderr, True, True)
    except BaseException:
        if owner is not None and not owner.cleaned:
            if owner.raw_process is not None and owner.raw_thread is not None:
                owner.cleanup_created(owner.raw_process, owner.raw_thread)
            else:
                owner.cleanup_published()
        if process is not None:
            drain_succeeded = True
            try:
                process.communicate(timeout=1.0)
            except BaseException:
                drain_succeeded = False
            resources_closed = _close_popen_resources(process)
        else:
            drain_succeeded = True
            resources_closed = True
        if owner is not None:
            return DiagnosticChildOutcome(
                None,
                None,
                None,
                owner.child_wait_completed,
                owner.cleanup_succeeded and drain_succeeded and resources_closed,
                operational_failure=True,
            )
        return DiagnosticChildOutcome(
            None, None, None, False, False, operational_failure=True
        )
    finally:
        _spawn_owner.owner = None
