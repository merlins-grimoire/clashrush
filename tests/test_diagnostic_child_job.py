from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import clash_rush_rebuild.diagnostic_child_job as child_job


def test_donor_wrapper_leaves_unowned_create_process_untouched(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return (11, 12, 13, 14)

    monkeypatch.setattr(child_job, "_original_create_process", original)

    result = child_job._job_owned_create_process("app", "command", flag=7)

    assert result == (11, 12, 13, 14)
    assert calls == [(("app", "command"), {"flag": 7})]


class FakeRuntime:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def create_job(self) -> object:
        self.events.append(("create-job",))
        return "job"

    def set_kill_on_close(self, job: object) -> None:
        self.events.append(("kill-on-close", job))

    def duplicate_process_handle(self, process: object) -> object:
        self.events.append(("duplicate", process))
        return 21

    def process_creation_time(self, process: object) -> int:
        self.events.append(("creation-time", process))
        return 99

    def assign_to_job(self, job: object, process: object) -> None:
        self.events.append(("assign", job, process))

    def is_process_in_job(self, process: object, job: object) -> bool:
        self.events.append(("membership", process, job))
        return True

    def resume_thread(self, thread: object) -> int:
        self.events.append(("resume", thread))
        return 1

    def terminate_job(self, job: object) -> None:
        self.events.append(("terminate-job", job))

    def terminate_process(self, process: object) -> None:
        self.events.append(("terminate-process", process))

    def wait_process(self, process: object, milliseconds: int) -> bool:
        self.events.append(("wait", process, milliseconds))
        return True

    def job_active_count(self, job: object) -> int:
        self.events.append(("active-count", job))
        return 0

    def close_handle(self, handle: object) -> None:
        self.events.append(("close", handle))


def test_donor_wrapper_creates_suspended_then_retains_assigns_and_resumes(
    monkeypatch,
) -> None:
    runtime = FakeRuntime()
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())

    def original(*args):
        runtime.events.append(("create", args[5]))
        return (11, 12, 13, 14)

    monkeypatch.setattr(child_job, "_original_create_process", original)
    child_job._spawn_owner.owner = owner
    try:
        result = child_job._job_owned_create_process(
            "app", "command", None, None, False, 0, {}, None, "startup"
        )
    finally:
        child_job._spawn_owner.owner = None

    assert result == (11, 12, 13, 14)
    assert runtime.events == [
        ("create", child_job.CREATE_SUSPENDED),
        ("duplicate", 11),
        ("creation-time", 21),
        ("assign", "job", 11),
        ("membership", 11, "job"),
        ("resume", 12),
    ]
    assert owner.retained_process == 21
    assert owner.pid == 13
    assert owner.creation_time == 99
    assert owner.resumed is True


def test_assignment_uncertainty_uses_both_kill_authorities_and_closes_every_handle(
    monkeypatch,
) -> None:
    runtime = FakeRuntime()

    def fail_assignment(job: object, process: object) -> None:
        runtime.events.append(("assign", job, process))
        raise OSError("private assignment detail")

    runtime.assign_to_job = fail_assignment  # type: ignore[method-assign]
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())
    monkeypatch.setattr(
        child_job,
        "_original_create_process",
        lambda *_args: (11, 12, 13, 14),
    )
    child_job._spawn_owner.owner = owner
    try:
        with pytest.raises(child_job.DiagnosticChildJobError) as caught:
            child_job._job_owned_create_process(
                "app", "command", None, None, False, 0, {}, None, "startup"
            )
    finally:
        child_job._spawn_owner.owner = None

    assert caught.value.__cause__ is None
    assert "private" not in str(caught.value)
    assert runtime.events == [
        ("duplicate", 11),
        ("creation-time", 21),
        ("assign", "job", 11),
        ("terminate-job", "job"),
        ("terminate-process", 11),
        ("wait", 21, child_job.CLEANUP_WAIT_MILLISECONDS),
        ("active-count", "job"),
        ("close", 12),
        ("close", 11),
        ("close", 21),
        ("close", "job"),
    ]
    assert owner.child_wait_completed is True
    assert owner.cleanup_succeeded is True


@pytest.mark.parametrize("resume_result", [0, 2, 0xFFFFFFFF, True, 1.0])
def test_nonexact_resume_result_fails_closed_and_retires_child(
    resume_result: object,
    monkeypatch,
) -> None:
    runtime = FakeRuntime()
    runtime.resume_thread = lambda thread: (  # type: ignore[method-assign]
        runtime.events.append(("resume", thread)) or resume_result
    )
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())
    monkeypatch.setattr(
        child_job,
        "_original_create_process",
        lambda *_args: (11, 12, 13, 14),
    )
    child_job._spawn_owner.owner = owner
    try:
        with pytest.raises(child_job.DiagnosticChildJobError):
            child_job._job_owned_create_process(
                "app", "command", None, None, False, 0, {}, None, "startup"
            )
    finally:
        child_job._spawn_owner.owner = None

    assert ("terminate-job", "job") in runtime.events
    assert ("terminate-process", 11) not in runtime.events
    assert owner.child_wait_completed is True


@pytest.mark.parametrize(
    "failure_stage",
    ["duplicate", "creation-time", "assign", "membership", "resume"],
)
def test_every_post_create_failure_retires_and_closes_all_owned_handles(
    failure_stage: str,
    monkeypatch,
) -> None:
    runtime = FakeRuntime()

    def fail(*args):
        runtime.events.append((failure_stage, *args))
        raise OSError("private post-create detail")

    if failure_stage == "duplicate":
        runtime.duplicate_process_handle = fail  # type: ignore[method-assign]
    elif failure_stage == "creation-time":
        runtime.process_creation_time = fail  # type: ignore[method-assign]
    elif failure_stage == "assign":
        runtime.assign_to_job = fail  # type: ignore[method-assign]
    elif failure_stage == "membership":
        runtime.is_process_in_job = fail  # type: ignore[method-assign]
    else:
        runtime.resume_thread = fail  # type: ignore[method-assign]
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())
    monkeypatch.setattr(
        child_job, "_original_create_process", lambda *_args: (11, 12, 13, 14)
    )
    child_job._spawn_owner.owner = owner
    try:
        with pytest.raises(child_job.DiagnosticChildJobError):
            child_job._job_owned_create_process(
                "app", "command", None, None, False, 0, {}, None, "startup"
            )
    finally:
        child_job._spawn_owner.owner = None

    assert ("terminate-job", "job") in runtime.events
    if failure_stage == "resume":
        assert ("terminate-process", 11) not in runtime.events
    else:
        assert ("terminate-process", 11) in runtime.events
    assert ("close", 12) in runtime.events
    assert ("close", 11) in runtime.events
    assert ("close", "job") in runtime.events
    if failure_stage != "duplicate":
        assert ("close", 21) in runtime.events


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        (("app",), {}),
        (("app", "command", None, None, False, 0, {}, None, "startup"), {"x": 1}),
    ],
)
def test_owned_wrapper_rejects_wrong_arity_or_kwargs_before_create(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    monkeypatch,
) -> None:
    creates: list[object] = []
    owner = child_job.DiagnosticJobOwner(FakeRuntime(), "job", object())
    monkeypatch.setattr(
        child_job, "_original_create_process", lambda *_args: creates.append(object())
    )
    child_job._spawn_owner.owner = owner
    try:
        with pytest.raises(child_job.DiagnosticChildJobError, match="signature"):
            child_job._job_owned_create_process(*args, **kwargs)
    finally:
        child_job._spawn_owner.owner = None

    assert creates == []


def test_owner_token_admits_exactly_one_createprocess_call(monkeypatch) -> None:
    runtime = FakeRuntime()
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())
    creates: list[int] = []

    def original(*_args):
        creates.append(1)
        return (11, 12, 13, 14)

    monkeypatch.setattr(child_job, "_original_create_process", original)
    child_job._spawn_owner.owner = owner
    try:
        child_job._job_owned_create_process(
            "app", "command", None, None, False, 0, {}, None, "startup"
        )
        with pytest.raises(child_job.DiagnosticChildJobError, match="already used"):
            child_job._job_owned_create_process(
                "app", "second", None, None, False, 0, {}, None, "startup"
            )
    finally:
        child_job._spawn_owner.owner = None
        owner.cleanup_created(11, 12)

    assert creates == [1]


def test_timeout_terminates_job_before_any_bounded_pipe_drain() -> None:
    runtime = FakeRuntime()

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            runtime.events.append(("stream-close", self.name))

    class ProcessHandle:
        def Close(self) -> None:
            runtime.events.append(("popen-handle-close",))

    class Process:
        returncode = None
        stdout = Stream("stdout")
        stderr = Stream("stderr")
        _handle = ProcessHandle()

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, *, timeout: float):
            self.calls += 1
            runtime.events.append(("communicate", self.calls, timeout))
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["private"], timeout)
            return "", ""

    def popen(_command, **_kwargs):
        owner = child_job._spawn_owner.owner
        owner.retained_process = 21
        owner.pid = 13
        owner.creation_time = 99
        owner.assigned = True
        owner.resumed = True
        return Process()

    outcome = child_job.run_owned_diagnostic_child(
        ["python", "-c", "private"],
        timeout=1,
        runtime_factory=lambda: runtime,
        popen_factory=popen,
        installer=lambda: None,
        monotonic=iter((10.0, 10.1)).__next__,
    )

    first_drain = runtime.events.index(next(e for e in runtime.events if e[:2] == ("communicate", 2)))
    termination = runtime.events.index(("terminate-job", "job"))
    assert termination < first_drain
    assert outcome.returncode is None
    assert outcome.child_wait_completed is True
    assert outcome.cleanup_succeeded is True


def test_failed_cleanup_drain_and_blocking_stream_close_is_bounded_and_unsuccessful() -> None:
    runtime = FakeRuntime()
    close_started = threading.Event()
    release_close = threading.Event()

    class BlockingStream:
        def close(self) -> None:
            close_started.set()
            release_close.wait()

    class ProcessHandle:
        def Close(self) -> None:
            runtime.events.append(("popen-handle-close",))

    class Process:
        returncode = None
        stdout = BlockingStream()
        stderr = None
        _handle = ProcessHandle()

        def communicate(self, *, timeout: float):
            raise subprocess.TimeoutExpired(["private"], timeout)

    def popen(_command, **_kwargs):
        owner = child_job._spawn_owner.owner
        owner.retained_process = 21
        owner.assigned = True
        owner.resumed = True
        return Process()

    result: list[child_job.DiagnosticChildOutcome] = []
    worker = threading.Thread(
        target=lambda: result.append(
            child_job.run_owned_diagnostic_child(
                ["python", "-c", "private"],
                timeout=1,
                runtime_factory=lambda: runtime,
                popen_factory=popen,
                installer=lambda: None,
                monotonic=iter((10.0, 10.1)).__next__,
            )
        )
    )
    worker.start()
    assert close_started.wait(timeout=1)
    worker.join(timeout=2)
    try:
        assert not worker.is_alive()
        assert result == [
            child_job.DiagnosticChildOutcome(
                None, None, None, True, False, operational_failure=True
            )
        ]
        assert ("popen-handle-close",) in runtime.events
    finally:
        release_close.set()
        worker.join(timeout=1)


def test_popen_cleanup_thread_start_failure_continues_other_stream_and_handle_cleanup(
    monkeypatch,
) -> None:
    events: list[tuple[str, ...]] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(("stream-close", self.name))

    class ProcessHandle:
        def Close(self) -> None:
            events.append(("popen-handle-close",))

    class Process:
        stdout = Stream("stdout")
        stderr = Stream("stderr")
        _handle = ProcessHandle()

    class RetirementThread:
        starts = 0

        def __init__(self, *, target, args, daemon: bool) -> None:
            assert daemon is True
            self.target = target
            self.args = args
            events.append(("thread-constructed", args[0].name))

        def start(self) -> None:
            type(self).starts += 1
            events.append(("thread-start", self.args[0].name))
            if type(self).starts == 1:
                raise RuntimeError("private thread start detail")
            self.target(*self.args)

        def join(self, _timeout: float) -> None:
            events.append(("thread-join", self.args[0].name))

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(child_job.threading, "Thread", RetirementThread)

    process = Process()
    assert child_job._close_popen_resources(process) is False
    assert events == [
        ("thread-constructed", "stdout"),
        ("thread-start", "stdout"),
        ("thread-constructed", "stderr"),
        ("thread-start", "stderr"),
        ("stream-close", "stderr"),
        ("thread-join", "stderr"),
        ("popen-handle-close",),
    ]
    assert process._handle is None


def test_popen_cleanup_join_failure_still_checks_liveness_and_remaining_resources(
    monkeypatch,
) -> None:
    events: list[tuple[str, ...]] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(("stream-close", self.name))

    class ProcessHandle:
        def Close(self) -> None:
            events.append(("popen-handle-close",))

    class Process:
        stdout = Stream("stdout")
        stderr = Stream("stderr")
        _handle = ProcessHandle()

    class RetirementThread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            assert daemon is True
            self.target = target
            self.args = args

        def start(self) -> None:
            events.append(("thread-start", self.args[0].name))
            self.target(*self.args)

        def join(self, _timeout: float) -> None:
            events.append(("thread-join", self.args[0].name))
            if self.args[0].name == "stdout":
                raise RuntimeError("private thread join detail")

        def is_alive(self) -> bool:
            events.append(("thread-is-alive", self.args[0].name))
            return False

    monkeypatch.setattr(child_job.threading, "Thread", RetirementThread)

    process = Process()
    assert child_job._close_popen_resources(process) is False
    assert events == [
        ("thread-start", "stdout"),
        ("stream-close", "stdout"),
        ("thread-start", "stderr"),
        ("stream-close", "stderr"),
        ("thread-join", "stdout"),
        ("thread-is-alive", "stdout"),
        ("thread-join", "stderr"),
        ("thread-is-alive", "stderr"),
        ("popen-handle-close",),
    ]
    assert process._handle is None


def test_owned_child_thread_start_failure_returns_unsuccessful_cleanup_and_closes_handle(
    monkeypatch,
) -> None:
    runtime = FakeRuntime()
    events: list[tuple[str, ...]] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(("stream-close", self.name))

    class ProcessHandle:
        def Close(self) -> None:
            events.append(("popen-handle-close",))

    class Process:
        returncode = 0
        stdout = Stream("stdout")
        stderr = Stream("stderr")
        _handle = ProcessHandle()

        def communicate(self, *, timeout: float):
            return "HOME\n", ""

    class RetirementThread:
        starts = 0

        def __init__(self, *, target, args, daemon: bool) -> None:
            assert daemon is True
            self.target = target
            self.args = args

        def start(self) -> None:
            type(self).starts += 1
            if type(self).starts == 1:
                raise RuntimeError("private thread start detail")
            self.target(*self.args)

        def join(self, _timeout: float) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    process = Process()

    def popen(_command, **_kwargs):
        owner = child_job._spawn_owner.owner
        owner.retained_process = 21
        owner.assigned = True
        owner.resumed = True
        return process

    monkeypatch.setattr(child_job.threading, "Thread", RetirementThread)

    outcome = child_job.run_owned_diagnostic_child(
        ["python", "-c", "private"],
        timeout=1,
        runtime_factory=lambda: runtime,
        popen_factory=popen,
        installer=lambda: None,
        monotonic=iter((10.0, 10.1)).__next__,
    )

    assert outcome == child_job.DiagnosticChildOutcome(
        None, None, None, True, False
    )
    assert events == [("stream-close", "stderr"), ("popen-handle-close",)]
    assert process._handle is None


def test_constructor_failure_after_create_closes_raw_and_retained_handles(
    monkeypatch,
) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(
        child_job,
        "_original_create_process",
        lambda *_args: (11, 12, 13, 14),
    )

    def popen(_command, **_kwargs):
        child_job._job_owned_create_process(
            "app", "command", None, None, False, 0, {}, None, "startup"
        )
        raise OSError("private pipe cleanup detail")

    outcome = child_job.run_owned_diagnostic_child(
        ["python", "-c", "private"],
        timeout=1,
        runtime_factory=lambda: runtime,
        popen_factory=popen,
        installer=lambda: None,
        monotonic=lambda: 10.0,
    )

    assert outcome == child_job.DiagnosticChildOutcome(
        None, None, None, True, True, operational_failure=True
    )
    assert ("terminate-job", "job") in runtime.events
    assert ("terminate-process", 11) not in runtime.events
    assert ("close", 12) in runtime.events
    assert ("close", 11) in runtime.events
    assert ("close", 21) in runtime.events


@pytest.mark.parametrize("stage", ["create-job", "kill-on-close", "installer"])
def test_pre_spawn_setup_failure_never_calls_popen_and_returns_closed_failure(
    stage: str,
) -> None:
    runtime = FakeRuntime()
    popen_calls: list[object] = []

    if stage == "create-job":
        runtime.create_job = lambda: (_ for _ in ()).throw(OSError("private"))  # type: ignore[method-assign]
    elif stage == "kill-on-close":
        runtime.set_kill_on_close = lambda _job: (_ for _ in ()).throw(  # type: ignore[method-assign]
            OSError("private")
        )

    def installer() -> None:
        if stage == "installer":
            raise child_job.DiagnosticChildJobError("private")

    outcome = child_job.run_owned_diagnostic_child(
        ["python", "-c", "private"],
        timeout=1,
        runtime_factory=lambda: runtime,
        popen_factory=lambda *_args, **_kwargs: popen_calls.append(object()),
        installer=installer,
        monotonic=lambda: 10.0,
    )

    assert popen_calls == []
    assert outcome.operational_failure is True
    assert outcome.cleanup_succeeded is False


def test_published_cleanup_uses_retained_process_if_job_termination_fails() -> None:
    runtime = FakeRuntime()

    def fail_job(job: object) -> None:
        runtime.events.append(("terminate-job", job))
        raise OSError("private termination detail")

    runtime.terminate_job = fail_job  # type: ignore[method-assign]
    owner = child_job.DiagnosticJobOwner(
        runtime,
        "job",
        object(),
        retained_process=21,
        pid=13,
        creation_time=99,
        assigned=True,
        resumed=True,
    )

    owner.cleanup_published()

    assert runtime.events[:2] == [
        ("terminate-job", "job"),
        ("terminate-process", 21),
    ]
    assert owner.child_wait_completed is True
    assert owner.cleanup_succeeded is False
    assert ("close", 21) in runtime.events
    assert ("close", "job") in runtime.events


def test_installer_rejects_an_unknown_python_createprocess_wrapper(monkeypatch) -> None:
    monkeypatch.setattr(child_job, "_original_create_process", None)
    monkeypatch.setattr(
        child_job.subprocess,
        "_winapi",
        SimpleNamespace(CreateProcess=lambda *_args: None),
    )

    with pytest.raises(child_job.DiagnosticChildJobError, match="unknown"):
        child_job._install_job_owned_create_process_once()


def test_installer_rejects_post_install_wrapper_replacement(monkeypatch) -> None:
    monkeypatch.setattr(child_job, "_original_create_process", object())
    monkeypatch.setattr(
        child_job.subprocess,
        "_winapi",
        SimpleNamespace(CreateProcess=lambda *_args: None),
    )

    with pytest.raises(child_job.DiagnosticChildJobError, match="unknown"):
        child_job._install_job_owned_create_process_once()


def test_installer_rebinds_current_wrapper_after_reload_and_stays_idempotent(
    monkeypatch,
) -> None:
    original = object()

    def old_wrapper(*_args):
        return None

    old_wrapper._clash_rush_true_original = original
    old_wrapper._clash_rush_diagnostic_job_owned = True
    winapi = SimpleNamespace(CreateProcess=old_wrapper)
    monkeypatch.setattr(child_job, "_original_create_process", None)
    monkeypatch.setattr(child_job.subprocess, "_winapi", winapi)

    child_job._install_job_owned_create_process_once()
    child_job._install_job_owned_create_process_once()

    assert child_job._original_create_process is original
    assert winapi.CreateProcess is child_job._job_owned_create_process


def test_installer_rejects_unsupported_python_minor_before_install(monkeypatch) -> None:
    monkeypatch.setattr(child_job, "_python_minor", lambda: (3, 12), raising=False)
    monkeypatch.setattr(child_job, "_original_create_process", None)
    monkeypatch.setattr(
        child_job.subprocess,
        "_winapi",
        SimpleNamespace(CreateProcess=object()),
    )

    with pytest.raises(child_job.DiagnosticChildJobError, match="Python minor"):
        child_job._install_job_owned_create_process_once()


@pytest.mark.parametrize(
    ("operation", "expected_event"),
    [
        ("terminate-job", ("terminate-process", 21)),
        ("terminate-process", ("active-count", "job")),
        ("wait", ("active-count", "job")),
        ("active-count", ("close", 21)),
        ("close", ("close", "job")),
    ],
)
def test_published_cleanup_failure_never_skips_later_authorities(
    operation: str,
    expected_event: tuple[object, ...],
) -> None:
    runtime = FakeRuntime()

    def fail(*args):
        runtime.events.append((operation, *args))
        raise OSError("private cleanup detail")

    if operation == "terminate-job":
        runtime.terminate_job = fail  # type: ignore[method-assign]
    elif operation == "terminate-process":
        runtime.terminate_job = fail  # type: ignore[method-assign]
        runtime.terminate_process = fail  # type: ignore[method-assign]
    elif operation == "wait":
        runtime.wait_process = fail  # type: ignore[method-assign]
    elif operation == "active-count":
        runtime.job_active_count = fail  # type: ignore[method-assign]
    else:
        runtime.close_handle = fail  # type: ignore[method-assign]
    owner = child_job.DiagnosticJobOwner(runtime, "job", object(), retained_process=21)

    owner.cleanup_published()

    assert owner.cleanup_succeeded is False
    assert expected_event in runtime.events


def test_nested_run_is_rejected_before_runtime_or_child_creation() -> None:
    child_job._spawn_owner.owner = object()
    try:
        outcome = child_job.run_owned_diagnostic_child(
            ["python", "-c", "private"],
            timeout=1,
            runtime_factory=lambda: (_ for _ in ()).throw(AssertionError("runtime")),
            popen_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("popen")
            ),
        )
    finally:
        child_job._spawn_owner.owner = None

    assert outcome == child_job.DiagnosticChildOutcome(None, None, None, False, False)


def test_owned_spawn_does_not_capture_concurrent_unrelated_thread(monkeypatch) -> None:
    runtime = FakeRuntime()
    owner = child_job.DiagnosticJobOwner(runtime, "job", object())
    owned_entered = threading.Event()
    release_owned = threading.Event()
    calls: list[str] = []

    def original(*args):
        calls.append(args[1])
        if args[1] == "owned":
            owned_entered.set()
            assert release_owned.wait(timeout=2)
        return (11, 12, 13, 14)

    monkeypatch.setattr(child_job, "_original_create_process", original)

    def owned_spawn() -> None:
        child_job._spawn_owner.owner = owner
        try:
            child_job._job_owned_create_process(
                "app", "owned", None, None, False, 0, {}, None, "startup"
            )
        finally:
            child_job._spawn_owner.owner = None

    worker = threading.Thread(target=owned_spawn)
    worker.start()
    assert owned_entered.wait(timeout=1)
    assert child_job._job_owned_create_process(
        "app", "unrelated", None, None, False, 0, {}, None, "startup"
    ) == (11, 12, 13, 14)
    release_owned.set()
    worker.join(timeout=2)
    try:
        assert not worker.is_alive()
        assert calls == ["owned", "unrelated"]
        assert runtime.events.count(("assign", "job", 11)) == 1
    finally:
        owner.cleanup_created(11, 12)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess contract")
def test_real_installer_is_concurrent_idempotent_and_unowned_calls_survive(
    monkeypatch,
) -> None:
    winapi = subprocess._winapi
    original = winapi.CreateProcess
    monkeypatch.setattr(child_job, "_original_create_process", None)
    try:
        threads = [
            threading.Thread(target=child_job._install_job_owned_create_process_once)
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert winapi.CreateProcess is child_job._job_owned_create_process
        assert child_job._original_create_process is original
        completed = subprocess.run(
            [sys.executable, "-c", "print('UNOWNED')"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert completed.stdout == "UNOWNED\n"
    finally:
        winapi.CreateProcess = original
        child_job._original_create_process = None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job ownership contract")
def test_real_constructor_pipe_cleanup_failure_retires_created_child(
    monkeypatch,
) -> None:
    original_close = subprocess.Popen._close_pipe_fds

    def close_then_fail(self, *args):
        original_close(self, *args)
        raise OSError("private simulated CPython pipe cleanup detail")

    monkeypatch.setattr(subprocess.Popen, "_close_pipe_fds", close_then_fail)
    started = time.monotonic()

    outcome = child_job.run_owned_diagnostic_child(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=5,
    )

    assert time.monotonic() - started < 8
    assert outcome == child_job.DiagnosticChildOutcome(
        None, None, None, True, True, operational_failure=True
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job ownership contract")
def test_real_inherited_pipe_descendant_is_retired_within_finite_bound() -> None:
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "time.sleep(30)"
    )
    started = time.monotonic()

    outcome = child_job.run_owned_diagnostic_child(
        [sys.executable, "-c", script],
        timeout=1,
    )

    assert time.monotonic() - started < 8
    assert outcome.returncode is None
    assert outcome.child_wait_completed is True
    assert outcome.cleanup_succeeded is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job ownership contract")
def test_real_normal_completion_is_waited_job_empty_and_closed() -> None:
    outcome = child_job.run_owned_diagnostic_child(
        [sys.executable, "-c", "print('HOME')"],
        timeout=5,
    )

    assert outcome == child_job.DiagnosticChildOutcome(
        0, "HOME\n", "", True, True
    )
