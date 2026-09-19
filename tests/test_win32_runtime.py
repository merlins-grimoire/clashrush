from __future__ import annotations

import ctypes
import os
import uuid

import pytest

from clash_rush_rebuild.win32_runtime import (
    CREATE_SUSPENDED,
    DEFAULT_MUTEX_NAME,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    WAIT_ABANDONED,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    CreatedProcess,
    MutexAbandonedError,
    MutexTimeoutError,
    NativeWin32Api,
    Win32Runtime,
    Win32RuntimeError,
)


class FakeRuntimeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.sid = "S-1-5-21-1234"
        self.wait_result = WAIT_OBJECT_0
        self.wait_error: Exception | None = None
        self.last_error = 5
        self.already_exists = False
        self.dacl_matches = True
        self.verify_error: Exception | None = None
        self.release_ok = True
        self.close_ok = True
        self.job_create_result: object | None = "job"
        self.configure_ok = True
        self.process_result = CreatedProcess("process", "thread", 321)
        self.assign_ok = True
        self.in_job_result = True
        self.resume_result = 1
        self.terminate_ok = True
        self.process_terminate_ok = True
        self.active_count_result = 0
        self.duplicate_result: object | None = "retained-process"
        self.creation_time_result = 123456

    def current_operator_sid(self) -> str:
        self.calls.append(("sid",))
        return self.sid

    def create_mutex_with_sddl(
        self, name: str, sddl: str
    ) -> tuple[object | None, bool]:
        self.calls.append(("mutex:create", name, sddl))
        return "mutex", self.already_exists

    def mutex_dacl_matches_sddl(self, handle: object, sddl: str) -> bool:
        self.calls.append(("mutex:verify-dacl", handle, sddl))
        if self.verify_error is not None:
            raise self.verify_error
        return self.dacl_matches

    def wait_for_single_object(self, handle: object, milliseconds: int) -> int:
        self.calls.append(("wait", handle, milliseconds))
        if self.wait_error is not None:
            raise self.wait_error
        return self.wait_result

    def get_last_error(self) -> int:
        return self.last_error

    def release_mutex(self, handle: object) -> bool:
        self.calls.append(("mutex:release", handle))
        return self.release_ok

    def close_handle(self, handle: object) -> bool:
        self.calls.append(("handle:close", handle))
        return self.close_ok

    def create_job_object(self) -> object | None:
        self.calls.append(("job:create", None))
        return self.job_create_result

    def set_job_limit_flags(self, job: object, flags: int) -> bool:
        self.calls.append(("job:configure", job, flags))
        return self.configure_ok

    def create_process_w(
        self,
        executable: str,
        command_line: ctypes.Array[ctypes.c_wchar],
        inherit_handles: bool,
        creation_flags: int,
    ) -> CreatedProcess:
        self.calls.append(
            (
                "process:create",
                executable,
                command_line.value,
                inherit_handles,
                creation_flags,
                isinstance(command_line, ctypes.Array),
            )
        )
        return self.process_result

    def assign_process_to_job(self, job: object, process: object) -> bool:
        self.calls.append(("job:assign", job, process))
        return self.assign_ok

    def is_process_in_job(self, process: object, job: object) -> bool:
        self.calls.append(("job:is-member", process, job))
        return self.in_job_result

    def resume_thread(self, thread: object) -> int:
        self.calls.append(("thread:resume", thread))
        return self.resume_result

    def terminate_job_object(self, job: object, exit_code: int) -> bool:
        self.calls.append(("job:terminate", job, exit_code))
        return self.terminate_ok

    def terminate_process(self, process: object, exit_code: int) -> bool:
        self.calls.append(("process:terminate", process, exit_code))
        return self.process_terminate_ok

    def duplicate_process_handle(self, process: object) -> object | None:
        self.calls.append(("process:duplicate", process))
        return self.duplicate_result

    def process_creation_time(self, process: object) -> int:
        self.calls.append(("process:creation-time", process))
        return self.creation_time_result

    def job_active_process_count(self, job: object) -> int:
        self.calls.append(("job:active-count", job))
        return self.active_count_result


def test_runtime_exposes_retained_process_identity_and_termination() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)

    retained = runtime.duplicate_process_handle("raw-process")
    creation_time = runtime.process_creation_time(retained)
    runtime.terminate_process(retained)

    assert retained == "retained-process"
    assert creation_time == 123456
    assert api.calls == [
        ("process:duplicate", "raw-process"),
        ("process:creation-time", "retained-process"),
        ("process:terminate", "retained-process", 1),
    ]


def test_new_mutex_uses_protected_system_and_operator_dacl_before_waiting() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)

    lease = runtime.acquire_mutex(
        name="Global\\ClashRushRebuildLifecycle-test", timeout_ms=0
    )

    assert lease.abandoned is False
    assert api.calls == [
        ("sid",),
        (
            "mutex:create",
            "Global\\ClashRushRebuildLifecycle-test",
            "D:P(A;;GA;;;SY)(A;;GA;;;S-1-5-21-1234)",
        ),
        ("wait", "mutex", 0),
    ]


def test_existing_mutex_dacl_is_verified_before_acquisition() -> None:
    api = FakeRuntimeApi()
    api.already_exists = True

    Win32Runtime(api).acquire_mutex(name=DEFAULT_MUTEX_NAME, timeout_ms=77)

    assert api.calls[2:] == [
        (
            "mutex:verify-dacl",
            "mutex",
            "D:P(A;;GA;;;SY)(A;;GA;;;S-1-5-21-1234)",
        ),
        ("wait", "mutex", 77),
    ]


def test_invalid_operator_sid_cannot_change_the_protected_dacl() -> None:
    api = FakeRuntimeApi()
    api.sid = "SY)(A;;GA;;;WD"

    with pytest.raises(Win32RuntimeError, match="operator SID"):
        Win32Runtime(api).acquire_mutex()

    assert api.calls == [("sid",)]


def test_existing_mutex_with_wrong_dacl_is_closed_without_waiting() -> None:
    api = FakeRuntimeApi()
    api.already_exists = True
    api.dacl_matches = False

    with pytest.raises(Win32RuntimeError, match="DACL mismatch"):
        Win32Runtime(api).acquire_mutex()

    assert api.calls[-2:] == [
        (
            "mutex:verify-dacl",
            "mutex",
            "D:P(A;;GA;;;SY)(A;;GA;;;S-1-5-21-1234)",
        ),
        ("handle:close", "mutex"),
    ]
    assert not any(call[0] == "wait" for call in api.calls)


def test_mutex_wait_classifies_abandoned_without_treating_it_as_usable() -> None:
    api = FakeRuntimeApi()
    api.wait_result = WAIT_ABANDONED

    lease = Win32Runtime(api).acquire_mutex()

    assert lease.abandoned is True
    with pytest.raises(MutexAbandonedError, match="reconciliation"):
        lease.require_usable()


@pytest.mark.parametrize(
    ("wait_result", "exception_type", "message"),
    [
        (WAIT_TIMEOUT, MutexTimeoutError, "timed out"),
        (WAIT_FAILED, Win32RuntimeError, "error 5"),
        (42, Win32RuntimeError, "unexpected mutex wait result 42"),
    ],
)
def test_non_acquired_mutex_wait_results_close_the_handle_and_fail_exactly(
    wait_result: int, exception_type: type[Win32RuntimeError], message: str
) -> None:
    api = FakeRuntimeApi()
    api.wait_result = wait_result

    with pytest.raises(exception_type, match=message):
        Win32Runtime(api).acquire_mutex()

    assert api.calls[-1] == ("handle:close", "mutex")


def test_mutex_release_is_exactly_once_and_closes_after_release() -> None:
    api = FakeRuntimeApi()
    lease = Win32Runtime(api).acquire_mutex()

    lease.release()

    assert api.calls[-2:] == [
        ("mutex:release", "mutex"),
        ("handle:close", "mutex"),
    ]
    with pytest.raises(Win32RuntimeError, match="already closed"):
        lease.release()


def test_existing_mutex_verification_exception_still_closes_handle() -> None:
    api = FakeRuntimeApi()
    api.already_exists = True
    api.verify_error = Win32RuntimeError("verification failed")

    with pytest.raises(Win32RuntimeError, match="verification failed"):
        Win32Runtime(api).acquire_mutex()

    assert api.calls[-1] == ("handle:close", "mutex")


def test_mutex_wait_cleanup_failure_is_not_hidden_as_a_timeout() -> None:
    api = FakeRuntimeApi()
    api.wait_result = WAIT_TIMEOUT
    api.close_ok = False

    with pytest.raises(Win32RuntimeError, match="mutex handle close failed"):
        Win32Runtime(api).acquire_mutex()


def test_mutex_close_retry_does_not_release_twice() -> None:
    api = FakeRuntimeApi()
    lease = Win32Runtime(api).acquire_mutex()
    api.close_ok = False

    with pytest.raises(Win32RuntimeError, match="mutex handle close failed"):
        lease.release()
    api.close_ok = True
    lease.release()

    assert api.calls.count(("mutex:release", "mutex")) == 1
    assert api.calls.count(("handle:close", "mutex")) == 2


def test_mutex_wait_exception_still_closes_unacquired_handle() -> None:
    api = FakeRuntimeApi()
    api.wait_error = Win32RuntimeError("wait adapter failed")

    with pytest.raises(Win32RuntimeError, match="wait adapter failed"):
        Win32Runtime(api).acquire_mutex()

    assert api.calls[-1] == ("handle:close", "mutex")


def test_private_job_is_unnamed_and_has_only_kill_on_close_limit() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)

    job = runtime.create_job()
    runtime.set_kill_on_close(job)

    assert job == "job"
    assert api.calls == [
        ("job:create", None),
        ("job:configure", "job", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
    ]
    assert JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000


def test_job_creation_and_configuration_fail_closed() -> None:
    for invalid_handle in (None, 0):
        api = FakeRuntimeApi()
        api.job_create_result = invalid_handle
        with pytest.raises(Win32RuntimeError, match="CreateJobObjectW"):
            Win32Runtime(api).create_job()

    api = FakeRuntimeApi()
    api.configure_ok = False
    with pytest.raises(Win32RuntimeError, match="SetInformationJobObject"):
        Win32Runtime(api).set_kill_on_close("job")


def test_create_process_uses_absolute_executable_mutable_exact_command_line() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)
    executable = r"C:\Program Files\Harmless\helper.exe"
    command_line = '"C:\\Program Files\\Harmless\\helper.exe" --quiet exact-value'

    created = runtime.create_suspended(executable, command_line)

    assert created == CreatedProcess("process", "thread", 321)
    assert api.calls == [
        (
            "process:create",
            executable,
            command_line,
            False,
            CREATE_SUSPENDED,
            True,
        )
    ]
    assert CREATE_SUSPENDED == 0x00000004


def test_invalid_created_process_record_closes_any_returned_handles() -> None:
    api = FakeRuntimeApi()
    api.process_result = CreatedProcess("process", 0, 321)

    with pytest.raises(Win32RuntimeError, match="invalid process record"):
        Win32Runtime(api).create_suspended(r"C:\helper.exe", r"C:\helper.exe")

    assert api.calls[-1] == ("handle:close", "process")


@pytest.mark.parametrize(
    ("executable", "command_line"),
    [
        ("relative.exe", "relative.exe"),
        (r"C:\absolute.exe", ""),
    ],
)
def test_create_process_rejects_nonexact_inputs_before_native_call(
    executable: str, command_line: str
) -> None:
    api = FakeRuntimeApi()

    with pytest.raises(
        Win32RuntimeError, match="absolute executable and exact command line"
    ):
        Win32Runtime(api).create_suspended(executable, command_line)

    assert api.calls == []


def test_assignment_membership_resume_and_close_preserve_exact_handles_and_return() -> (
    None
):
    api = FakeRuntimeApi()
    api.resume_result = 2
    runtime = Win32Runtime(api)

    runtime.assign_to_job("job", "process")
    assert runtime.is_process_in_job("process", "job") is True
    assert runtime.resume_thread("thread") == 2
    runtime.close_thread("thread")
    runtime.close_handle("process")

    assert api.calls == [
        ("job:assign", "job", "process"),
        ("job:is-member", "process", "job"),
        ("thread:resume", "thread"),
        ("handle:close", "thread"),
        ("handle:close", "process"),
    ]


def test_job_termination_wait_and_active_count_are_exact() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)

    runtime.terminate_job("job")
    assert runtime.wait_process("process", 30_000) is True
    assert runtime.job_active_count("job") == 0

    assert api.calls == [
        ("job:terminate", "job", 1),
        ("wait", "process", 30_000),
        ("job:active-count", "job"),
    ]


def test_process_wait_classifies_timeout_failed_and_unexpected_exactly() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)
    api.wait_result = WAIT_TIMEOUT
    assert runtime.wait_process("process", 1) is False

    api.wait_result = WAIT_FAILED
    with pytest.raises(Win32RuntimeError, match="error 5"):
        runtime.wait_process("process", 2)

    api.wait_result = WAIT_ABANDONED
    with pytest.raises(Win32RuntimeError, match="unexpected process wait result 128"):
        runtime.wait_process("process", 3)


def test_runtime_operations_fail_closed_on_native_false_or_invalid_results() -> None:
    api = FakeRuntimeApi()
    runtime = Win32Runtime(api)

    api.assign_ok = False
    with pytest.raises(Win32RuntimeError, match="AssignProcessToJobObject"):
        runtime.assign_to_job("job", "process")

    api.terminate_ok = False
    with pytest.raises(Win32RuntimeError, match="TerminateJobObject"):
        runtime.terminate_job("job")

    api.process_terminate_ok = False
    with pytest.raises(Win32RuntimeError, match="TerminateProcess"):
        runtime.terminate_process("process")

    api.duplicate_result = None
    with pytest.raises(Win32RuntimeError, match="DuplicateHandle"):
        runtime.duplicate_process_handle("process")

    api.creation_time_result = True
    with pytest.raises(Win32RuntimeError, match="creation time"):
        runtime.process_creation_time("process")

    api.close_ok = False
    with pytest.raises(Win32RuntimeError, match="CloseHandle"):
        runtime.close_handle("process")

    api.active_count_result = -1
    with pytest.raises(Win32RuntimeError, match="active-process count"):
        runtime.job_active_count("job")


@pytest.mark.skipif(os.name != "nt", reason="native Win32 smoke test")
def test_native_unique_mutex_and_harmless_suspended_job_process_smoke() -> None:
    api = NativeWin32Api()
    runtime = Win32Runtime(api)
    name = f"Global\\ClashRushRebuildLifecycle-test-{uuid.uuid4().hex}"

    lease = runtime.acquire_mutex(name=name, timeout_ms=0)
    lease.require_usable()
    sid = api.current_operator_sid()
    duplicate, already_exists = api.create_mutex_with_sddl(
        name, f"D:P(A;;GA;;;SY)(A;;GA;;;{sid})"
    )
    assert duplicate is not None
    assert already_exists is True
    assert api.mutex_dacl_matches_sddl(duplicate, f"D:P(A;;GA;;;SY)(A;;GA;;;{sid})")
    assert api.close_handle(duplicate) is True
    lease.release()

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = os.path.join(system_root, "System32", "cmd.exe")
    command_line = f'"{executable}" /d /c exit 0'
    job = runtime.create_job()
    process: object | None = None
    thread: object | None = None
    retained: object | None = None
    try:
        runtime.set_kill_on_close(job)
        created = runtime.create_suspended(executable, command_line)
        process = created.process_handle
        thread = created.thread_handle
        retained = runtime.duplicate_process_handle(process)
        assert runtime.process_creation_time(retained) > 0
        runtime.assign_to_job(job, process)
        assert runtime.is_process_in_job(process, job) is True
        assert runtime.resume_thread(thread) == 1
        runtime.close_thread(thread)
        thread = None
        runtime.terminate_job(job)
        assert runtime.wait_process(process, 10_000) is True
        assert runtime.job_active_count(job) == 0
    finally:
        if thread is not None:
            api.close_handle(thread)
        if process is not None:
            api.close_handle(process)
        if retained is not None:
            api.close_handle(retained)
        api.close_handle(job)
