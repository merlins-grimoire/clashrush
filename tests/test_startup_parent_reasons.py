"""Complete existing Job runner harness reused for startup reason distinctions."""
import subprocess
import sys
from types import SimpleNamespace
import pytest
from test_diagnostic_child_job import FakeRuntime
from clash_rush_rebuild import diagnostic_child_job as job


@pytest.mark.parametrize("stage,expected", [
    ("setup", "PARENT_OWNERSHIP"), ("spawn", "PARENT_SPAWN"),
    ("timeout", "PARENT_TIMEOUT"), ("communication", "PARENT_COMMUNICATION"),
    ("decode", "PARENT_COMMUNICATION"), ("empty", "PARENT_CLEANUP"),
])
def test_owned_runner_distinct_failure_reasons(stage, expected):
    runtime = FakeRuntime()
    if stage == "setup":
        runtime.create_job = lambda: (_ for _ in ()).throw(OSError("private-marker"))
    if stage == "empty":
        runtime.job_active_count = lambda j: 1
    class Process:
        returncode = 0
        stdout = stderr = _handle = None
        calls = 0
        def communicate(self, *, timeout):
            self.calls += 1
            if self.calls == 1:
                if stage == "timeout":
                    raise subprocess.TimeoutExpired("private-marker", 1)
                if stage == "communication":
                    raise OSError("private-marker")
                if stage == "decode":
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private-marker")
            return "HOME\n", ""
    def popen(*a, **k):
        if stage == "spawn":
            raise OSError("private-marker")
        owner = job._spawn_owner.owner
        owner.retained_process = 21
        owner.assigned = owner.resumed = True
        return Process()
    result = job.run_owned_diagnostic_child(["generic"], timeout=1,
        runtime_factory=lambda: runtime, popen_factory=popen, installer=lambda: None)
    assert result.reason.value == expected
    assert "private-marker" not in repr(result)
    if stage not in {"setup", "spawn"}:
        assert ("terminate-job", "job") in runtime.events
        assert result.child_wait_completed is True


def test_cleanup_reason_structurally_preserves_timeout():
    runtime = FakeRuntime()
    runtime.job_active_count = lambda j: 1
    class Process:
        stdout = stderr = _handle = None
        def communicate(self, *, timeout):
            raise subprocess.TimeoutExpired("private-marker", timeout)
    def popen(*a, **k):
        job._spawn_owner.owner.retained_process = 21
        return Process()
    result = job.run_owned_diagnostic_child(["generic"], timeout=1,
        runtime_factory=lambda: runtime, popen_factory=popen, installer=lambda: None)
    assert result.reason.value == "PARENT_CLEANUP"
    assert result.prior_reason.value == "PARENT_TIMEOUT"


def test_parent_cleanup_keeps_exact_child_failure_structurally():
    from clash_rush_rebuild.startup_failure import StartupReason
    runtime = FakeRuntime()
    runtime.job_active_count = lambda j: 1
    class Process:
        returncode = 2
        stdout = stderr = _handle = None
        def communicate(self, *, timeout):
            return "", "GEOMETRY_BINDING\n"
    def popen(*a, **k):
        job._spawn_owner.owner.retained_process = 21
        return Process()
    result = job.run_owned_diagnostic_child(["generic"], timeout=1,
        runtime_factory=lambda: runtime, popen_factory=popen, installer=lambda: None)
    assert result.reason is job.DiagnosticParentReason.CLEANUP
    assert result.prior_reason is StartupReason.GEOMETRY_BINDING


@pytest.mark.skipif(sys.platform != "win32", reason="owned Windows child")
@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_real_malformed_utf8_never_leaks_reader_thread_traceback(capfd):
    # Isolate the permanent CreateProcess hook from installer mutation tests.
    script = (
        "import sys; from clash_rush_rebuild.diagnostic_child_job import run_owned_diagnostic_child;"
        "r=run_owned_diagnostic_child([sys.executable,'-c','import sys;sys.stdout.buffer.write(bytes([255]))'],timeout=5);"
        "print(r.reason.value,r.child_wait_completed,r.cleanup_succeeded)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout == "PARENT_COMMUNICATION True True\n"
    assert result.stderr == ""
    assert capfd.readouterr() == ("", "")
