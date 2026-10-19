"""Inert installed-build specification; no native/ADB calls in this file."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib

import pytest

from clash_rush_rebuild.lifecycle import ProcessIdentity


DUMP = """Packages:
  Package [com.supercell.clashofclans] (123abc):
    userId=10001
    codePath=/data/app/synthetic/base
    versionCode=180600008 minSdk=23 targetSdk=35
    versionName=18.600.7
    firstInstallTime=2026-08-31 01:56:16
    lastUpdateTime=2026-10-06 16:43:48
    signatures=PackageSignatures{123 version:3, signatures:[cae5b92e], past signatures:[]}
    apkSigningVersion=3
    User 0: installed=true hidden=false suspended=false stopped=false notLaunched=false enabled=0

Queries:
  system apps queryable: false
"""
CONF = b'bst.instance.Pie64_test.adb_port="5555"\nbst.instance.Pie64_test.status.adb_port="5555"\n'


def module():
    try:
        return importlib.import_module("clash_rush_rebuild.mvp_installed_build")
    except ModuleNotFoundError:
        pytest.fail("installed-build provider cannot enforce the behavioral contract: missing implementation")


class Leaves:
    """Only low-level process/config/TCP/command responses are faked."""
    def __init__(self, m):
        self.m = m
        self.player = ProcessIdentity(100, 200)
        self.daemon = ProcessIdentity(300, 400)
        self.conf = CONF
        self.argv = (m.PLAYER, "--instance", "Pie64_test")
        self.pid_change = False
        self.running = False
        self.cleaned = []
        self.closed = []
        self.commands = []
        self.dumps = [DUMP, DUMP]
        self.devices = ["List of devices attached\n\n", "List of devices attached\n127.0.0.1:5555 device product:test\n"]
        self.serial = "127.0.0.1:5555\n"
        self.connected = False
        self.cleanup_ok = True
        self.wrong_owner = False
        self.wrong_peer = False
        self.failure = None
        self.t = 10.0
        self.preexisting = m.TcpRow(999, "LISTEN", "127.0.0.1", 5037, "0.0.0.0", 0)

    def monotonic(self):
        self.t += 0.001
        return self.t

    def open_player(self, pid):
        assert pid == self.player.pid
        return "player-handle"

    def observe(self, handle):
        if handle == "daemon-handle":
            return self.m.ProcessObservation(self.daemon, self.m.ADB, (self.m.ADB, "-P", "5038", "nodaemon", "server"))
        identity = replace(self.player, creation_time_100ns=201) if self.pid_change else self.player
        return self.m.ProcessObservation(identity, self.m.PLAYER, self.argv)

    def read_configuration(self):
        return self.conf, 1234

    def tcp_rows(self):
        owner = 101 if self.wrong_owner else self.player.pid
        rows = [self.preexisting, self.m.TcpRow(owner, "LISTEN", "127.0.0.1", 5555, "0.0.0.0", 0)]
        if self.running:
            rows.append(self.m.TcpRow(self.daemon.pid, "LISTEN", "127.0.0.1", 5038, "0.0.0.0", 0))
            if self.connected:
                rows.append(self.m.TcpRow(self.daemon.pid, "ESTABLISHED", "127.0.0.1", 61000, "127.0.0.1", 5556 if self.wrong_peer else 5555))
        return tuple(rows)

    def start_daemon(self, argv):
        assert argv == (self.m.ADB, "-P", "5038", "nodaemon", "server")
        self.running = True
        return "daemon-handle"

    def alive(self, handle):
        return self.running if handle == "daemon-handle" else True

    def stop_daemon(self, handle):
        assert handle == "daemon-handle"  # Never a PID, player, or preexisting server.
        self.cleaned.append(handle)
        if self.cleanup_ok:
            self.running = False
        return self.cleanup_ok

    def close_player(self, handle):
        self.closed.append(handle)

    def command(self, argv, timeout, limit):
        assert timeout > 0 and limit > 0
        self.commands.append(argv)
        if self.failure:
            return self.failure
        tail = argv[3:]
        if tail == ("devices", "-l"):
            output = self.devices.pop(0) if len(self.devices) > 1 else self.devices[0]
        elif tail == ("connect", "127.0.0.1:5555"):
            self.connected = True
            output = "connected to 127.0.0.1:5555\n"
        elif tail == ("-s", "127.0.0.1:5555", "get-serialno"):
            output = self.serial
        elif tail == ("-s", "127.0.0.1:5555", "shell", "dumpsys", "package", self.m.PACKAGE):
            output = self.dumps.pop(0) if len(self.dumps) > 1 else self.dumps[0]
        else:
            raise AssertionError(f"non-allowlisted operation: {tail}")
        return self.m.CommandResult(output, "", 0, True)


def setup():
    m = module()
    leaves = Leaves(m)
    binding = m.BuildBinding("a" * 32, 1, leaves.player, hashlib.sha256(b"Pie64_test").hexdigest(), hashlib.sha256(CONF).hexdigest())
    provider = m.InstalledBuildProvider(transport=m.InstalledBuildTransport(leaves=leaves))
    return m, leaves, binding, provider


def acquire(provider, binding):
    return provider.acquire(binding, expected_version_name="18.600.7", expected_version_code=180600008)


def test_exact_observed_build_cleanup_then_one_use():
    m, low, binding, provider = setup()
    evidence = acquire(provider, binding)
    assert evidence.binding == binding
    assert evidence.package.version_name == "18.600.7"
    assert evidence.package.version_code == 180600008
    assert evidence.package.apk_sha256 is None
    assert evidence.package.certificate_sha256 is None
    assert evidence.package.apk_hash_status == evidence.package.certificate_status == "UNAVAILABLE"
    assert evidence.transport.serial == "127.0.0.1:5555"
    assert evidence.transport.listener_port == 5555
    assert evidence.transport.daemon_identity == low.daemon
    assert evidence.start_monotonic < evidence.end_monotonic
    assert low.cleaned == ["daemon-handle"] and not low.running
    assert low.preexisting in low.tcp_rows()
    with pytest.raises(FrozenInstanceError):
        evidence.binding = binding
    assert provider.check_current(binding) is True
    assert provider.consume(binding) is evidence
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)
    # Same run may require another admission, but it must perform two new reads.
    count = len(low.commands)
    fresh = acquire(provider, binding)
    assert len(low.commands) > count
    assert fresh.transport.generation != evidence.transport.generation


@pytest.mark.parametrize("bad", [
    DUMP.replace("com.supercell.clashofclans", "com.wrong.package"),
    DUMP + DUMP,
    DUMP.replace("180600008", "180600008x"),
    DUMP.replace("versionName=18.600.7", "versionName="),
    DUMP.replace("    codePath=/data/app/synthetic/base\n", ""),
    DUMP.replace("    lastUpdateTime=2026-10-06 16:43:48\n", ""),
    DUMP.replace("installed=true", "installed=false"),
    DUMP.replace("    versionName=18.600.7\n", "    versionName=18.600.7\n    versionName=18.600.7\n"),
    "error: closed\n", DUMP[:150],
])
def test_malformed_wrong_duplicate_partial_package_denied(bad):
    m, low, binding, provider = setup()
    low.dumps = [bad]
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert low.cleaned == ["daemon-handle"]
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)


@pytest.mark.parametrize("field,value", [
    ("pid_change", True), ("wrong_owner", True), ("wrong_peer", True),
    ("argv", ("wrong.exe", "--instance", "Pie64_other")),
    ("argv", ("wrong.exe", "--instance", "Pie64_test", "--instance", "Pie64_test")),
    ("conf", CONF.replace(b'port="5555"', b'port="5556"', 1)),
    ("serial", "emulator-5554\n"), ("cleanup_ok", False),
    ("devices", ["List of devices attached\n\n"]),
    ("devices", ["List of devices attached\n127.0.0.1:5555 offline\n"]),
    ("devices", ["List of devices attached\n127.0.0.1:5555 device\nemulator-5554 device\n"]),
])
def test_identity_transport_ambiguity_denied(field, value):
    m, low, binding, provider = setup()
    setattr(low, field, value)
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert all(handle == "daemon-handle" for handle in low.cleaned)
    assert low.preexisting in low.tcp_rows()


@pytest.mark.parametrize("change", [
    ("18.600.7", "18.600.8"), ("180600008", "180600009"),
    ("/data/app/synthetic/base", "/data/app/updated/base"),
    ("2026-10-06", "2026-10-07"), ("cae5b92e", "bbbbbbbb"),
])
def test_package_update_inside_bracket_denied(change):
    m, low, binding, provider = setup()
    low.dumps[1] = DUMP.replace(*change)
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert low.cleaned == ["daemon-handle"]


@pytest.mark.parametrize("field,value", [("run_nonce", "b" * 32), ("generation", 2), ("process_identity", ProcessIdentity(100, 201)), ("instance_digest", "b" * 64), ("configuration_digest", "c" * 64)])
def test_evidence_cannot_rebind(field, value):
    m, low, binding, provider = setup()
    acquire(provider, binding)
    with pytest.raises(m.InstalledBuildError):
        provider.consume(replace(binding, **{field: value}))
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)  # A failed check revokes rather than leaving a reusable token.


def test_restart_after_acquisition_revokes_without_ttl():
    m, low, binding, provider = setup()
    acquire(provider, binding)
    low.pid_change = True
    with pytest.raises(m.InstalledBuildError):
        provider.check_current(binding)
    low.pid_change = False
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)


@pytest.mark.parametrize("kind", ["exit", "partial", "stderr", "timeout"])
def test_low_level_failure_denies(kind):
    m, low, binding, provider = setup()
    low.failure = m.CommandResult(DUMP, "error: closed" if kind == "stderr" else "", 1 if kind == "exit" else 0, kind != "partial")
    if kind == "timeout":
        def timeout(*args):
            raise TimeoutError("synthetic")
        low.command = timeout
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert low.cleaned == ["daemon-handle"]


def test_production_default_is_unavailable_without_any_native_action():
    m = module()
    provider = m.InstalledBuildProvider()
    binding = m.BuildBinding("a" * 32, 1, ProcessIdentity(100, 200), "a" * 64, "b" * 64)
    with pytest.raises(m.InstalledBuildError, match="activation"):
        acquire(provider, binding)


@pytest.mark.parametrize("name,code", [("18.600.8", 180600008), ("18.600.7", 180600009), ("18.600.7", True)])
def test_wrong_expected_version_denied(name, code):
    m, low, binding, provider = setup()
    with pytest.raises(m.InstalledBuildError):
        provider.acquire(binding, name, code)
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)


@pytest.mark.parametrize("mutation", ["config", "mtime", "package", "cleanup", "daemon", "listener", "preexisting", "serial"])
def test_changes_after_or_during_read_fail_closed(mutation):
    m, low, binding, provider = setup()
    if mutation == "package":
        acquire(provider, binding)
        low.dumps = [DUMP.replace("18.600.7", "18.600.8")]
        with pytest.raises(m.InstalledBuildError):
            provider.check_current(binding)
        return
    original = low.command
    calls = 0
    def command(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 5:
            if mutation == "config":
                low.conf += b'bst.adb_enabled="1"\n'
            elif mutation == "mtime":
                low.read_configuration = lambda: (CONF, 1235)
            elif mutation == "daemon":
                low.daemon = replace(low.daemon, creation_time_100ns=401)
            elif mutation == "listener":
                low.wrong_owner = True
            elif mutation == "serial":
                low.serial = "emulator-5554\n"
        return result
    low.command = command
    if mutation in ("cleanup", "preexisting"):
        stop = low.stop_daemon
        def cleanup(handle):
            result = stop(handle)
            if mutation == "cleanup":
                low.pid_change = True
            else:
                low.preexisting = replace(low.preexisting, pid=998)
            return result
        low.stop_daemon = cleanup
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert low.cleaned == ["daemon-handle"]


@pytest.mark.parametrize("times", [[10.0, 9.0], [10.0, float("nan")], [10.0, 100.0], [float("inf")]])
def test_nonmonotonic_future_or_timeout_clock_denied(times):
    m, low, binding, provider = setup()
    values = iter(times)
    last = times[-1]
    low.monotonic = lambda: next(values, last)
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)


def test_revocation_and_cleanup_failure_are_not_reusable():
    m, low, binding, provider = setup()
    acquire(provider, binding)
    provider.invalidate()
    with pytest.raises(m.InstalledBuildError):
        provider.consume(binding)
    low.cleanup_ok = False
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    count = len(low.commands)
    low.cleanup_ok = True
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert len(low.commands) == count


@pytest.mark.parametrize("text", [
    CONF + b'bst.instance.Pie64_test.adb_port="5555"\n',
    CONF + b'bst.instance.Pie64_other.adb_port="5555"\n',
    CONF.replace(b'"5555"', b'"05555"'),
    CONF.replace(b'"5555"', b'"65536"'),
])
def test_config_parser_does_not_trust_even_matching_digest(text):
    m, low, binding, provider = setup()
    low.conf = text
    binding = replace(binding, configuration_digest=hashlib.sha256(text).hexdigest())
    with pytest.raises(m.InstalledBuildError):
        acquire(provider, binding)
    assert not low.commands and not low.cleaned
    assert low.closed == ["player-handle"]


@pytest.mark.parametrize('fault', ['timeout','wait','cleanup','partial','oversized','wrong_type'])
def test_bundled_client_rejects_incomplete_process_result(monkeypatch,fault):
    from clash_rush_rebuild import diagnostic_child_job as child
    m=module()
    result=child.DiagnosticChildOutcome(0,DUMP,'',True,True)
    if fault=='timeout': result=replace(result,operational_failure=True)
    if fault=='wait': result=replace(result,child_wait_completed=False)
    if fault=='cleanup': result=replace(result,cleanup_succeeded=False)
    if fault=='partial': result=replace(result,stdout=None)
    if fault=='oversized': result=replace(result,stdout='x'*(m.MAX_OUTPUT+1))
    if fault=='wrong_type': result=object()
    monkeypatch.setattr(child,'run_owned_diagnostic_child',lambda *a,**k:result)
    argv=(m.ADB,'-P','5038','-s','127.0.0.1:5555','shell','dumpsys','package',m.PACKAGE)
    with pytest.raises(m.InstalledBuildError):
        m._NativeLeaves.command(argv,1.0,m.MAX_OUTPUT)


@pytest.mark.parametrize("tail", [("kill-server",), ("start-server",), ("-s", "127.0.0.1:5555", "shell", "input", "tap", "1", "2"), ("install", "file.apk")])
def test_no_arbitrary_shell_or_default_server_api(monkeypatch, tail):
    from clash_rush_rebuild import diagnostic_child_job as child
    m = module()
    def forbidden(*args, **kwargs):
        pytest.fail("a rejected command must never open a socket or process")
    monkeypatch.setattr(m.socket, "create_connection", forbidden)
    monkeypatch.setattr(child,'run_owned_diagnostic_child',forbidden)
    with pytest.raises(m.InstalledBuildError):
        m._NativeLeaves.command((m.ADB, "-P", "5038") + tail, 1.0, m.MAX_OUTPUT)


def test_native_constructor_is_gated_before_win32_or_spawn(monkeypatch):
    m = module()
    def forbidden(*args, **kwargs):
        pytest.fail("native activation must remain denied")
    monkeypatch.setattr(m, "NativeWin32Api", forbidden)
    monkeypatch.setattr(m.subprocess, "Popen", forbidden)
    with pytest.raises(m.InstalledBuildError, match="activation"):
        m.InstalledBuildTransport()


@pytest.mark.parametrize("tail,expected", [
    (("devices", "-l"), "List of devices attached\n127.0.0.1:5555\tdevice product:test\n"),
    (("connect", "127.0.0.1:5555"), "connected to 127.0.0.1:5555"),
    (("-s", "127.0.0.1:5555", "get-serialno"), "127.0.0.1:5555"),
])
def test_bundled_host_commands_preserve_exact_output(monkeypatch, tail, expected):
    from clash_rush_rebuild import diagnostic_child_job as child
    m = module()
    monkeypatch.setattr(child,'run_owned_diagnostic_child',lambda *a,**k:
                        child.DiagnosticChildOutcome(0,expected,'',True,True))
    result = m._NativeLeaves.command((m.ADB, "-P", "5038") + tail, 1.0, m.MAX_OUTPUT)
    assert result.stdout == expected and result.complete


def test_bundled_shell_preserves_failure_status_and_stderr(monkeypatch):
    from clash_rush_rebuild import diagnostic_child_job as child
    m = module()
    monkeypatch.setattr(child,'run_owned_diagnostic_child',lambda *a,**k:
                        child.DiagnosticChildOutcome(1,'','error: closed',True,True))
    result = m._NativeLeaves.command((m.ADB, "-P", "5038", "-s", "127.0.0.1:5555", "shell", "dumpsys", "package", m.PACKAGE), 1.0, m.MAX_OUTPUT)
    assert result.returncode == 1 and result.stderr == "error: closed"


def test_native_daemon_only_terminates_retained_owned_process(monkeypatch):
    m = module()
    actions = []
    class Handle:
        def Close(self):
            actions.append("close-retained-handle")
    class Process:
        def __init__(self, argv, **kwargs):
            actions.append((argv, kwargs))
            self._handle = Handle()
            self.returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            actions.append("terminate-retained-process")
            self.returncode = 1
        def wait(self, timeout):
            actions.append(("wait", timeout))
    monkeypatch.setattr(m.subprocess, "Popen", Process)
    monkeypatch.setattr(m.Path, "is_file", lambda self: str(self) == m.ADB)
    argv = (m.ADB, "-P", "5038", "nodaemon", "server")
    process = m._NativeLeaves.start_daemon(argv)
    assert m._NativeLeaves.stop_daemon(process)
    assert actions[0][0] == argv
    assert actions[0][1]["shell"] is False and actions[0][1]["close_fds"] is True
    assert actions[1:] == ["terminate-retained-process", ("wait", 2.0), "close-retained-handle"]
    with pytest.raises(m.InstalledBuildError):
        m._NativeLeaves.stop_daemon(999)


def test_native_tcp_parser_includes_ipv6_conflicting_listeners(monkeypatch):
    import ctypes
    import struct
    from types import SimpleNamespace
    m = module()
    ipv4 = struct.pack("<I6I", 1, 2, 0x0100007f, m.socket.htons(5555), 0, 0, 100)
    ipv6 = struct.pack("<I", 1) + bytes(20) + struct.pack("<I", m.socket.htons(5038)) + bytes(20) + struct.pack("<III", 0, 2, 999)
    def get_table(buffer, size, ordered, family, kind, reserved):
        payload = ipv4 if family == 2 else ipv6
        ctypes.cast(size, ctypes.POINTER(m.wintypes.DWORD))[0] = len(payload)
        if buffer is None:
            return 122
        ctypes.memmove(buffer, payload, len(payload))
        return 0
    monkeypatch.setattr(m.ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(GetExtendedTcpTable=get_table))
    rows = m._NativeLeaves.tcp_rows()
    assert rows == (m.TcpRow(100, "LISTEN", "127.0.0.1", 5555, "0.0.0.0", 0),
                    m.TcpRow(999, "LISTEN", "::", 5038, "::", 0))


def test_native_process_identity_uses_filetime_and_windows_argv_parser(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    m = module()
    raw_command = f'"{m.PLAYER}" --instance "Pie64_test"'
    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", m.wintypes.USHORT), ("MaximumLength", m.wintypes.USHORT), ("Buffer", ctypes.c_void_p)]
    def process_query(handle, kind, buffer, size, needed):
        assert handle == 123 and kind == 60
        required = ctypes.sizeof(UnicodeString) + (len(raw_command) + 1) * ctypes.sizeof(ctypes.c_wchar)
        ctypes.cast(needed, ctypes.POINTER(m.wintypes.ULONG))[0] = required
        if buffer is None:
            return -1
        address = ctypes.addressof(buffer) + ctypes.sizeof(UnicodeString)
        text = ctypes.create_unicode_buffer(raw_command)
        ctypes.memmove(address, text, ctypes.sizeof(text))
        value = UnicodeString.from_buffer(buffer)
        value.Length = len(raw_command) * 2
        value.MaximumLength = value.Length + 2
        value.Buffer = address
        return 0
    parsed = (ctypes.c_wchar_p * 3)(m.PLAYER, "--instance", "Pie64_test")
    observed_command = []
    def argv_parse(text, count):
        observed_command.append(text)
        ctypes.cast(count, ctypes.POINTER(ctypes.c_int))[0] = 3
        return parsed
    def image(handle, flags, output, count):
        assert handle == 123
        output.value = m.PLAYER
        return True
    freed = []
    api = SimpleNamespace(
        _kernel32=SimpleNamespace(GetProcessId=lambda handle: 100,
                                  QueryFullProcessImageNameW=image,
                                  LocalFree=lambda pointer: freed.append(pointer)),
        process_creation_time=lambda handle: 200,
    )
    monkeypatch.setattr(m._NativeLeaves, "_api", staticmethod(lambda: api))
    monkeypatch.setattr(m.ctypes, "WinDLL", lambda name, **kwargs: SimpleNamespace(
        **({"NtQueryInformationProcess": process_query} if name == "ntdll" else {"CommandLineToArgvW": argv_parse})))
    # Bypass only the deliberately unavailable constructor; every OS leaf is fake.
    low = object.__new__(m._NativeLeaves)
    result = low.observe(123)
    assert result == m.ProcessObservation(ProcessIdentity(100, 200), m.PLAYER,
                                          (m.PLAYER, "--instance", "Pie64_test"))
    assert observed_command == [raw_command] and len(freed) == 1


def test_short_signature_is_metadata_not_certificate_hash():
    m = module()
    record = m.parse_package_record(DUMP)
    assert any("cae5b92e" in value for value in record.signature_metadata)
    assert record.certificate_sha256 is None
    with pytest.raises(m.InstalledBuildError):
        record.require_crypto(apk_sha256="39dbe457850f706bd830490c1d0602f699699d18aea0c598cd510b81af4f71f3", certificate_sha256="cae5b92e")
    with pytest.raises(m.InstalledBuildError, match="unavailable"):
        record.require_crypto(apk_sha256="a" * 64, certificate_sha256="b" * 64)


def test_selected_source_uses_bundled_client_not_new_shell_v2(monkeypatch):
    """Low-level process result is synthetic; the source controller is real."""
    from clash_rush_rebuild import diagnostic_child_job as child
    m = module()
    calls = []
    def command(argv, *, timeout, environment):
        assert type(environment) is dict and 'ADB_SERVER_SOCKET' not in environment
        calls.append((tuple(argv), timeout))
        return child.DiagnosticChildOutcome(0, DUMP, '', True, True)
    monkeypatch.setattr(child, 'run_owned_diagnostic_child', command)
    monkeypatch.setattr(m.socket, 'create_connection', lambda *a, **k: pytest.fail('unreviewed shell-v2 transport'))
    argv=(m.ADB,'-P','5038','-s','127.0.0.1:5555','shell','dumpsys','package',m.PACKAGE)
    assert m._NativeLeaves.command(argv,1.0,m.MAX_OUTPUT)==m.CommandResult(DUMP,'',0,True)
    assert calls==[(argv,1)]


def test_client_cannot_inherit_remote_server_override(monkeypatch):
    from clash_rush_rebuild import diagnostic_child_job as child
    m=module(); calls=[]
    monkeypatch.setenv('ADB_SERVER_SOCKET','tcp:remote.invalid:9999')
    def command(argv,**kwargs):
        calls.append(kwargs)
        return child.DiagnosticChildOutcome(0,DUMP,'',True,True)
    monkeypatch.setattr(child,'run_owned_diagnostic_child',command)
    m._NativeLeaves.command((m.ADB,'-P','5038','devices','-l'),1.0,m.MAX_OUTPUT)
    assert type(calls[0].get('environment')) is dict
    assert 'ADB_SERVER_SOCKET' not in calls[0]['environment']
