"""One-use current-guest PackageManager evidence, never gameplay authority.

Source: ARCHITECTURE_BLOCKER_RESOLUTION/BUILD_SOURCE, not AppCache or a wheel.
Donor trace: BasePilot 4ede1efd has window identity, not guest build identity;
CoC_Bot a5c943af has instance/port lookup but cached/global ADB and kill-server
are incompatible; ClashAutomation c41fe12 has name-based process control, not
this proof. Reuse lifecycle.ProcessIdentity / Win32 FILETIME primitives. The
strict port extension is local: config.py's registry deliberately ignores ADB.

The historical InstalledBuildTransport/Provider remain default-denied. The closed
ParentPackageObserver below is the separate owner-authorized calibration-only
exception, using the existing issuer and never activating the historical provider.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager, ExitStack
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import ntpath
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import threading
import time

from .lifecycle import ProcessIdentity
from .win32_runtime import NativeWin32Api, Win32Runtime

ADB = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"
PLAYER = r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
CONFIG = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")
PACKAGE = "com.supercell.clashofclans"
MAX_OUTPUT = 256 * 1024
READ_BUDGET = 15.0
_HEX = re.compile(r"[0-9a-fA-F]{64}")


def _client_environment() -> dict[str, str]:
    # No ADB_SERVER_SOCKET/ANDROID_* redirection, executable or key overrides.
    return {key:os.environ[key] for key in ('SystemRoot','SYSTEMROOT','WINDIR',
            'TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA') if key in os.environ}


class InstalledBuildError(RuntimeError):
    """Unavailable, ambiguous, stale, or unclosed evidence; deny admission."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InstalledBuildError(message)


def _digest(value: str) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class BuildBinding:
    run_nonce: str
    generation: int
    process_identity: ProcessIdentity
    instance_digest: str
    configuration_digest: str

    def __post_init__(self) -> None:
        _require(type(self.run_nonce) is str and re.fullmatch(r"[0-9a-f]{32}", self.run_nonce) is not None,
                 "exact run nonce required")
        _require(type(self.generation) is int and self.generation >= 0, "exact generation required")
        _require(type(self.process_identity) is ProcessIdentity, "process creation identity required")
        _require(_digest(self.instance_digest) and _digest(self.configuration_digest), "exact digests required")


@dataclass(frozen=True, slots=True)
class PackageRecord:
    package_name: str
    version_name: str
    version_code: int
    code_path: str
    first_install_time: str
    last_update_time: str
    signing_scheme: int | None
    signature_metadata: tuple[str, ...]
    user_metadata: tuple[str, ...]
    package_status: str = "OBSERVED"
    version_status: str = "OBSERVED"
    apk_hash_status: str = "UNAVAILABLE"
    certificate_status: str = "UNAVAILABLE"
    apk_sha256: None = None
    certificate_sha256: None = None

    @property
    def signing_scheme_status(self) -> str:
        return "OBSERVED" if self.signing_scheme is not None else "UNAVAILABLE"

    def require_crypto(self, *, apk_sha256: str, certificate_sha256: str) -> None:
        _require(_digest(apk_sha256) and _digest(certificate_sha256), "invalid expected SHA-256 literal")
        # No successful byte/certificate source exists in the approved source tier.
        raise InstalledBuildError("installed cryptographic evidence unavailable")


def parse_package_record(text: str) -> PackageRecord:
    """Parse one complete current package section, not unrelated transient dumps."""
    _require(type(text) is str and 0 < len(text.encode("utf-8")) <= MAX_OUTPUT
             and "\x00" not in text and text.endswith("\n"), "partial or oversized package output")
    lines = text.splitlines()
    headers = [(i, re.fullmatch(r"  Package \[([^\]]+)\] \([^\r\n]+\):", line))
               for i, line in enumerate(lines) if re.match(r"\s*Package \[", line)]
    _require(len(headers) == 1 and headers[0][1] is not None, "ambiguous package section")
    index, header = headers[0]
    _require(header.group(1) == PACKAGE and "Packages:" in lines[:index], "wrong PackageManager package")
    body = []
    for line in lines[index + 1:]:
        if line and not line.startswith("    "):
            break
        if line.strip():
            body.append(line.strip())

    def field(name: str, *, optional: bool = False) -> str | None:
        found = [line[len(name) + 1:] for line in body if line.startswith(name + "=")]
        _require(len(found) == 1 or (optional and not found), "missing or duplicate package field")
        return found[0] if found else None

    version = field("versionName")
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}", version) is not None, "invalid versionName")
    code = field("versionCode")
    match = re.fullmatch(r"(0|[1-9][0-9]{0,18})(?: minSdk=[0-9]+)?(?: targetSdk=[0-9]+)?", code)
    _require(match is not None, "invalid versionCode")
    path = field("codePath")
    _require(path.startswith("/data/app/") and not any(c.isspace() for c in path)
             and "/../" not in path and not path.endswith("/.."), "invalid installed codePath")
    first, last = field("firstInstallTime"), field("lastUpdateTime")
    for value in (first, last):
        _require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value) is not None,
                 "invalid package update metadata")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise InstalledBuildError("invalid package timestamp") from None
    scheme = field("apkSigningVersion", optional=True)
    _require(scheme is None or re.fullmatch(r"[1-9][0-9]*", scheme) is not None, "invalid signing scheme")
    signatures = tuple(line for line in body if re.match(r"(?:signatures|pastSigningCertificates|signingInfo)=", line))
    _require(len({line.split("=", 1)[0] for line in signatures}) == len(signatures), "duplicate signature metadata")
    users = tuple(line for line in body if line.startswith("User "))
    _require(len(users) == 1 and re.match(r"User 0: ", users[0]) is not None
             and re.search(r"(?:^| )installed=true(?: |$)", users[0]) is not None,
             "missing or ambiguous installed user state")
    return PackageRecord(PACKAGE, version, int(match.group(1)), path, first, last,
                         int(scheme) if scheme is not None else None, signatures, users)


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int
    complete: bool


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    identity: ProcessIdentity
    image: str
    argv: tuple[str, ...]  # Native CommandLineToArgvW, never shlex or quote regex.


@dataclass(frozen=True, slots=True)
class TcpRow:
    pid: int
    state: str
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    process: ProcessObservation
    configuration_digest: str
    configuration_mtime_ns: int
    instance_digest: str
    listener: TcpRow


@dataclass(frozen=True, slots=True)
class TransportIdentity:
    generation: str
    daemon_identity: ProcessIdentity
    server_port: int
    listener_port: int
    serial: str
    connection: TcpRow


@dataclass(frozen=True, slots=True)
class InstalledBuildEvidence:
    binding: BuildBinding
    package: PackageRecord
    source: SourceIdentity
    transport: TransportIdentity
    start_monotonic: float
    end_monotonic: float
    start_utc: datetime
    end_utc: datetime
    # One-use state belongs to the issuing provider, never to a caller-set bool.


def _source(leaves, handle: object, binding: BuildBinding) -> SourceIdentity:
    observed = leaves.observe(handle)
    _require(type(observed) is ProcessObservation and observed.identity == binding.process_identity
             and ntpath.normcase(observed.image) == ntpath.normcase(PLAYER)
             and leaves.alive(handle) is True, "lost player creation identity")
    argv = observed.argv
    _require(type(argv) is tuple and len(argv) >= 3 and argv.count("--instance") == 1
             and ntpath.normcase(argv[0]) == ntpath.normcase(PLAYER), "invalid parsed player command line")
    i = argv.index("--instance")
    _require(i + 1 < len(argv) and re.fullmatch(r"[A-Za-z0-9_]+", argv[i + 1]) is not None,
             "missing exact instance")
    instance = argv[i + 1]
    instance_digest = hashlib.sha256(instance.encode("utf-8")).hexdigest()
    raw, mtime = leaves.read_configuration()
    _require(type(raw) is bytes and 0 < len(raw) <= 4 * 1024 * 1024
             and type(mtime) is int and mtime > 0, "configuration unavailable")
    digest = hashlib.sha256(raw).hexdigest()
    _require(instance_digest == binding.instance_digest and digest == binding.configuration_digest,
             "instance/configuration binding changed")
    text = raw.decode("utf-8-sig", errors="strict")
    ports: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        if not re.match(r"\s*bst\.instance\..*\.(?:status\.)?adb_port", line):
            continue
        match = re.fullmatch(r'bst\.instance\.([A-Za-z0-9_]+)\.(adb_port|status\.adb_port)="([1-9][0-9]{0,4})"', line.strip())
        _require(match is not None, "malformed instance port mapping")
        name, key, value = match.groups()
        fields = ports.setdefault(name, {})
        _require(key not in fields and 0 < int(value) < 65536, "duplicate/invalid port mapping")
        fields[key] = int(value)
    selected = ports.get(instance, {})
    _require(set(selected) == {"adb_port", "status.adb_port"}
             and selected["adb_port"] == selected["status.adb_port"], "instance/status ports disagree")
    port = selected["adb_port"]
    _require(not any(port in fields.values() for name, fields in ports.items() if name != instance),
             "listener port maps to multiple instances")
    listeners = [row for row in leaves.tcp_rows() if row.state == "LISTEN" and row.local_port == port]
    _require(len(listeners) == 1 and listeners[0].pid == binding.process_identity.pid
             and listeners[0].local_address == "127.0.0.1", "wrong or ambiguous player listener")
    return SourceIdentity(observed, digest, mtime, instance_digest, listeners[0])


def _devices(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    _require(bool(lines) and lines[0] == "List of devices attached", "invalid device enumeration")
    found = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        _require(len(fields) >= 2 and fields[1] == "device"
                 and re.fullmatch(r"[A-Za-z0-9_.:\-]+", fields[0]) is not None,
                 "offline or malformed device")
        found.append(fields[0])
    _require(len(found) <= 1, "multiple devices")
    return tuple(found)


class InstalledBuildTransport:
    """Closed, bounded controller; only process/config/socket/command leaves vary."""
    def __init__(self, *, leaves=None) -> None:
        self._leaves = leaves if leaves is not None else _NativeLeaves()
        self._lock = threading.Lock()
        self._poisoned = False

    def read(self, binding: BuildBinding) -> InstalledBuildEvidence:
        with self._lock:
            _require(not self._poisoned, "transport cleanup previously failed")
            try:
                return self._read(binding)
            except InstalledBuildError:
                raise
            except Exception:
                raise InstalledBuildError("installed source unavailable") from None

    def _read(self, binding: BuildBinding) -> InstalledBuildEvidence:
        _require(type(binding) is BuildBinding, "exact build binding required")
        low = self._leaves
        start, start_utc = low.monotonic(), datetime.now(timezone.utc)
        previous = start
        _require(type(start) in (float, int) and math.isfinite(start) and start >= 0, "invalid monotonic start")

        def remaining() -> float:
            nonlocal previous
            now = low.monotonic()
            _require(type(now) in (float, int) and math.isfinite(now)
                     and previous <= now < start + READ_BUDGET, "read timeout or nonmonotonic clock")
            previous = now
            return start + READ_BUDGET - now

        player = low.open_player(binding.process_identity.pid)
        daemon = None
        baseline = ()
        server_port = None
        try:
            source = _source(low, player, binding)
            baseline = tuple(row for row in low.tcp_rows() if row.state == "LISTEN")
            used = {row.local_port for row in low.tcp_rows()}
            server_port = next((p for p in range(5038, 5100) if p not in used), None)
            _require(server_port is not None, "no isolated server port available")
            prefix = (ADB, "-P", str(server_port))
            # Only this retained foreground handle is cleanup authority.
            daemon = low.start_daemon(prefix + ("nodaemon", "server"))
            daemon_observation = low.observe(daemon)
            _require(type(daemon_observation) is ProcessObservation
                     and ntpath.normcase(daemon_observation.image) == ntpath.normcase(ADB)
                     and daemon_observation.argv == prefix + ("nodaemon", "server")
                     and daemon_observation.identity.pid not in {r.pid for r in baseline},
                     "daemon ownership unavailable")
            ready_deadline = low.monotonic() + 2.0

            def daemon_current() -> None:
                _require(low.alive(daemon) is True and low.observe(daemon) == daemon_observation,
                         "lost retained daemon identity")
                rows = [r for r in low.tcp_rows() if r.state == "LISTEN" and r.local_port == server_port]
                _require(len(rows) == 1 and rows[0].pid == daemon_observation.identity.pid
                         and rows[0].local_address == "127.0.0.1", "wrong daemon server listener")

            while not any(r.state == "LISTEN" and r.local_port == server_port for r in low.tcp_rows()):
                remaining()
                _require(low.alive(daemon) is True and low.monotonic() < ready_deadline, "daemon startup timeout")
                time.sleep(0.01)
            daemon_current()

            def command(tail: tuple[str, ...]) -> str:
                daemon_current()
                result = low.command(prefix + tail, remaining(), MAX_OUTPUT)
                remaining()
                daemon_current()
                _require(type(result) is CommandResult and type(result.returncode) is int
                         and result.returncode == 0 and result.complete is True
                         and result.stderr == "" and type(result.stdout) is str
                         and len(result.stdout.encode("utf-8")) <= MAX_OUTPUT,
                         "command failed, truncated, or unavailable")
                return result.stdout

            endpoint = f"127.0.0.1:{source.listener.local_port}"
            initial = _devices(command(("devices", "-l")))
            _require(not initial or initial == (endpoint,), "wrong initial serial")
            connected = command(("connect", endpoint)).strip()
            _require(connected in (f"connected to {endpoint}", f"already connected to {endpoint}"), "connect failed")
            devices = _devices(command(("devices", "-l")))
            _require(len(devices) == 1, "no discovered online device")
            serial = devices[0]  # Discovered, then compared; never synthesized as evidence.
            _require(serial == endpoint and command(("-s", serial, "get-serialno")).strip() == serial,
                     "wrong discovered serial")

            def connection() -> TcpRow:
                rows = [r for r in low.tcp_rows() if r.state == "ESTABLISHED"
                        and r.pid == daemon_observation.identity.pid and r.remote_port == source.listener.local_port]
                _require(len(rows) == 1 and rows[0].local_address == rows[0].remote_address == "127.0.0.1",
                         "daemon not connected to exact player listener")
                return rows[0]

            peer = connection()
            _require(_source(low, player, binding) == source, "identity changed before package read")
            query = ("-s", serial, "shell", "dumpsys", "package", PACKAGE)
            first = parse_package_record(command(query))
            second = parse_package_record(command(query))
            _require(first == second, "package changed inside read bracket")
            _require(_devices(command(("devices", "-l"))) == (serial,)
                     and command(("-s", serial, "get-serialno")).strip() == serial
                     and connection() == peer and _source(low, player, binding) == source,
                     "source/transport changed inside read bracket")
            identity = TransportIdentity(secrets.token_hex(16), daemon_observation.identity,
                                         server_port, source.listener.local_port, serial, peer)
            remaining()
        finally:
            try:
                if daemon is not None:
                    cleanup = low.stop_daemon(daemon)
                    rows = low.tcp_rows()
                    _require(cleanup is True and low.alive(daemon) is False
                             and not any(r.state == "LISTEN" and r.local_port == server_port for r in rows)
                             and all(r in rows for r in baseline), "daemon cleanup or preexisting-server preservation failed")
                    _require(_source(low, player, binding) == source, "source changed during cleanup")
            except BaseException:
                self._poisoned = True
                raise InstalledBuildError("daemon cleanup failed; transport revoked") from None
            finally:
                low.close_player(player)
        remaining()  # Cleanup is part of the bounded observation, not post-admission work.
        end = low.monotonic()
        _require(previous <= end < start + READ_BUDGET, "invalid monotonic completion")
        return InstalledBuildEvidence(binding, first, source, identity, start, end,
                                      start_utc, datetime.now(timezone.utc))


class InstalledBuildProvider:
    """Serialized admission evidence. acquire/consume have no reusable cache TTL.

    check_current does a fresh two-read bracket (including package comparison),
    not merely a PID check. It proves a new snapshot, never future immutability.
    Any failed check, invalidate, or attempted wrong-binding consume revokes the
    pending record. Callers must invalidate on admission revocation/observed change.
    """
    def __init__(self, *, transport: InstalledBuildTransport | None = None) -> None:
        _require(transport is None or type(transport) is InstalledBuildTransport, "closed transport required")
        self._transport = transport
        self._pending: InstalledBuildEvidence | None = None
        self._lock = threading.RLock()

    def acquire(self, binding: BuildBinding, expected_version_name: str,
                expected_version_code: int) -> InstalledBuildEvidence:
        with self._lock:
            self._pending = None
            _require(self._transport is not None, "production transport activation unavailable")
            _require(type(expected_version_name) is str and bool(expected_version_name)
                     and type(expected_version_code) is int and expected_version_code >= 0,
                     "exact expected package version required")
            record = self._transport.read(binding)
            _require(record.package.version_name == expected_version_name
                     and record.package.version_code == expected_version_code, "installed version mismatch")
            self._pending = record
            return record

    def check_current(self, binding: BuildBinding) -> bool:
        with self._lock:
            pending = self._pending
            try:
                _require(pending is not None and pending.binding == binding, "missing, used, or wrong-binding evidence")
                fresh = self._transport.read(binding)
                _require(fresh.source == pending.source and fresh.package == pending.package
                         and fresh.start_monotonic >= pending.end_monotonic,
                         "installed evidence changed or stale")
                return True
            except BaseException:
                self._pending = None
                raise

    def consume(self, binding: BuildBinding) -> InstalledBuildEvidence:
        with self._lock:
            self.check_current(binding)
            pending, self._pending = self._pending, None
            return pending

    def invalidate(self) -> None:
        with self._lock:
            self._pending = None


class _NativeLeaves:
    """Actual Win32/bundled-client leaves, behind an unavailable capability gate.

    The four closed commands use the observed bundled executable, not a second
    implementation of its wire protocol. Every client is suspended/assigned to
    a dedicated automation-only Job before it runs. A lost-server auto-start
    descendant stays in that client Job and is retired with it, never adopted as
    evidence. The retained foreground daemon remains the only accepted server.
    """
    def __init__(self) -> None:
        raise InstalledBuildError("production transport activation unavailable: no bound capability issuer")

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def _api():
        api = NativeWin32Api()
        k = api._kernel32
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetProcessId.argtypes = [wintypes.HANDLE]
        k.GetProcessId.restype = wintypes.DWORD
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        return api

    def open_player(self, pid: int):
        handle = self._api()._kernel32.OpenProcess(0x1000 | 0x100000, False, pid)
        _require(bool(handle), "player identity handle unavailable")
        return handle

    def close_player(self, handle) -> None:
        Win32Runtime(self._api()).close_handle(handle)

    def observe(self, handle) -> ProcessObservation:
        raw_handle = int(handle._handle) if isinstance(handle, subprocess.Popen) else handle
        api = self._api()
        k = api._kernel32
        identity = ProcessIdentity(int(k.GetProcessId(raw_handle)), Win32Runtime(api).process_creation_time(raw_handle))
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        _require(bool(k.QueryFullProcessImageNameW(raw_handle, 0, image, ctypes.byref(size))), "native image unavailable")
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        query = ntdll.NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = ctypes.c_long
        required = wintypes.ULONG()
        query(raw_handle, 60, None, 0, ctypes.byref(required))
        _require(0 < required.value <= 131072, "command line size unavailable")
        buffer = ctypes.create_string_buffer(required.value)
        _require(query(raw_handle, 60, buffer, len(buffer), ctypes.byref(required)) == 0, "native command line unavailable")

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

        value = UnicodeString.from_buffer(buffer)
        _require(value.Length % 2 == 0 and value.Length > 0 and value.Buffer is not None
                 and ctypes.addressof(buffer) <= value.Buffer
                 and value.Buffer + value.Length <= ctypes.addressof(buffer) + len(buffer), "invalid command line buffer")
        text = ctypes.wstring_at(value.Buffer, value.Length // 2)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        count = ctypes.c_int()
        argv = shell.CommandLineToArgvW(text, ctypes.byref(count))
        _require(bool(argv), "native command line parse failed")
        try:
            args = tuple(argv[i] for i in range(count.value))
        finally:
            k.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
        return ProcessObservation(identity, image.value, args)

    @staticmethod
    def read_configuration() -> tuple[bytes, int]:
        with CONFIG.open("rb") as stream:
            import os
            before = os.fstat(stream.fileno())
            raw = stream.read(4 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        current = CONFIG.stat()
        _require((before.st_ino, before.st_size, before.st_mtime_ns) ==
                 (after.st_ino, after.st_size, after.st_mtime_ns) ==
                 (current.st_ino, current.st_size, current.st_mtime_ns), "configuration read raced")
        return raw, after.st_mtime_ns

    @staticmethod
    def tcp_rows() -> tuple[TcpRow, ...]:
        iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
        query = iphlp.GetExtendedTcpTable
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                          wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
        query.restype = wintypes.DWORD
        rows = []
        # Include IPv6 to detect wildcard/conflicting server listeners as well.
        for family, width in ((2, 24), (23, 56)):
            size = wintypes.DWORD()
            _require(query(None, ctypes.byref(size), False, family, 5, 0) in (0, 122), "TCP size unavailable")
            _require(4 <= size.value <= 16 * 1024 * 1024, "TCP table unbounded")
            buf = ctypes.create_string_buffer(size.value)
            _require(query(buf, ctypes.byref(size), False, family, 5, 0) == 0, "TCP table raced")
            count = struct.unpack_from("<I", buf, 0)[0]
            _require(4 + count * width <= len(buf), "TCP table truncated")
            for i in range(count):
                offset = 4 + i * width
                if family == 2:
                    state, local, lp, remote, rp, pid = struct.unpack_from("<6I", buf, offset)
                    local = socket.inet_ntoa(struct.pack("<I", local))
                    remote = socket.inet_ntoa(struct.pack("<I", remote))
                else:
                    raw = buf.raw[offset:offset + width]
                    local = socket.inet_ntop(socket.AF_INET6, raw[:16])
                    remote = socket.inet_ntop(socket.AF_INET6, raw[24:40])
                    lp, rp, state, pid = (struct.unpack_from("<I", raw, n)[0] for n in (20, 44, 48, 52))
                rows.append(TcpRow(pid, {2: "LISTEN", 5: "ESTABLISHED"}.get(state, str(state)),
                                   local, socket.ntohs(lp & 0xffff), remote, socket.ntohs(rp & 0xffff)))
        return tuple(rows)

    @staticmethod
    def start_daemon(argv: tuple[str, ...]):
        _require(len(argv) == 5 and argv[0] == ADB and argv[1] == "-P"
                 and argv[2].isdecimal() and 5038 <= int(argv[2]) < 5100
                 and argv[3:] == ("nodaemon", "server"), "non-allowlisted daemon command")
        _require(Path(ADB).is_file(), "bundled HD-Adb unavailable")
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                                env=_client_environment())

    def alive(self, handle) -> bool:
        if isinstance(handle, subprocess.Popen):
            return handle.poll() is None
        return not Win32Runtime(self._api()).wait_process(handle, 0)

    @staticmethod
    def stop_daemon(handle) -> bool:
        _require(isinstance(handle, subprocess.Popen), "retained foreground process required")
        if handle.poll() is None:
            handle.terminate()  # Popen uses its retained process handle, not a PID lookup.
        handle.wait(timeout=2.0)
        exited = handle.poll() is not None
        if exited:
            handle._handle.Close()
        return exited

    @staticmethod
    def command(argv: tuple[str, ...], timeout: float, limit: int) -> CommandResult:
        _require(type(argv) is tuple and len(argv) >= 5 and argv[0] == ADB and argv[1] == "-P"
                 and argv[2].isdecimal() and 5038 <= int(argv[2]) < 5100
                 and type(timeout) in (int,float) and math.isfinite(timeout)
                 and 0 < timeout <= READ_BUDGET and type(limit) is int
                 and 0 < limit <= MAX_OUTPUT, "invalid closed command")
        tail = argv[3:]
        allowed = False
        if tail == ("devices", "-l"):
            allowed = True
        elif len(tail) == 2 and tail[0] == "connect" and re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", tail[1]):
            allowed = int(tail[1].rsplit(':',1)[1]) < 65536
        elif len(tail) >= 3 and tail[0] == "-s" and re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", tail[1]):
            allowed = (int(tail[1].rsplit(':',1)[1]) < 65536
                       and tail[2:] in (("get-serialno",),("shell","dumpsys","package",PACKAGE)))
        _require(allowed, "non-allowlisted command")
        from .diagnostic_child_job import DiagnosticChildOutcome, run_owned_diagnostic_child
        result = run_owned_diagnostic_child(argv, timeout=max(1,math.ceil(timeout)),
                                            environment=_client_environment())
        _require(type(result) is DiagnosticChildOutcome and result.operational_failure is False
                 and result.child_wait_completed is True and result.cleanup_succeeded is True
                 and type(result.returncode) is int and type(result.stdout) is str
                 and type(result.stderr) is str, "bundled client unavailable or cleanup unproved")
        _require(len(result.stdout.encode('utf-8'))+len(result.stderr.encode('utf-8'))<=limit,
                 "bundled client output oversized")
        return CommandResult(result.stdout,result.stderr,result.returncode,True)


# Narrow owner exception; the old transport/provider above remain default-denied.
from enum import Enum
from .mvp_calibration import CalibrationIssuer, ParentPackageFact, OPERATIONS as CALIBRATION_OPERATIONS
from .mvp_session_authority import canonical_v3, digest_v3


class PackageCommand(Enum):
    SERIAL = ('get-serialno',)
    PACKAGE = ('shell', 'pm', 'list', 'packages', PACKAGE)
    RUNNING = ('shell', 'pidof', 'com.supercell.clashofclans')


@dataclass(frozen=True, slots=True)
class BundledExecutable:
    path: str
    sha256: str
    file_version: str
    product_version: str
    path_class: str = 'SELECTED_BLUESTACKS_INSTALLATION'


def resolve_bundled_executable(player, metadata):
    """Metadata snapshot only. Invocation must retain _pin_bundled_executable."""
    with _pin_bundled_executable(player, metadata) as executable:
        return executable


_auxiliary_pin = threading.local()


def auxiliary_executable_is_pinned(path):
    """Narrow auxiliary-owner join; grants no command/package/game authority."""
    return getattr(_auxiliary_pin, 'path', None) == os.path.normcase(os.path.abspath(path))


@contextmanager
def _pin_bundled_executable(player, metadata):
    """Hold no-write/no-delete file and ancestor handles through the caller.

    Reuses the private reader's OPEN_REPARSE_POINT/ancestor retention pattern
    and the lifecycle file API. A second hash sample is not the pin. Existing
    write/delete handles prevent acquiring this lease, not just future opens.
    """
    from .mvp_deployment_profile import _no_link
    from .win32_state_io import NativeWin32StateApi, BY_HANDLE_FILE_INFORMATION
    path = Path(player).absolute()
    _require(path.name.lower() == 'hd-player.exe' and path.is_file()
             and path.resolve(strict=True) == path, 'BUNDLED_EXECUTABLE_UNAVAILABLE')
    api, handles = NativeWin32StateApi(), []
    def retain(candidate, directory):
        _no_link(candidate)
        handle = api.create_file(str(candidate), 0x80000000, 1, 3,
                                 0x00200000 | (0x02000000 if directory else 0))
        _require(handle not in (None, 0), 'BUNDLED_EXECUTABLE_UNAVAILABLE')
        handles.append(handle)
        attributes = api.handle_attributes(handle)
        _require(not attributes & 0x400 and bool(attributes & 0x10) == directory
                 and Path(api.final_path(handle)) == candidate, 'BUNDLED_EXECUTABLE_CHANGED')
        return handle
    try:
        for ancestor in reversed(path.parents):
            retain(ancestor, True)
        retain(path, False)
        candidates = [p for p in path.parent.iterdir() if p.name.lower() in ('hd-adb.exe', 'adb.exe')]
        _require(len(candidates) == 1 and candidates[0].name.lower() == 'hd-adb.exe',
                 'BUNDLED_EXECUTABLE_UNAVAILABLE_OR_AMBIGUOUS')
        executable = candidates[0]
        handle = retain(executable, False)
        info = BY_HANDLE_FILE_INFORMATION()
        _require(api._kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)),
                 'BUNDLED_EXECUTABLE_UNAVAILABLE')
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        _require(info.nNumberOfLinks == 1 and 0 < size <= 64 * 1024 * 1024,
                 'BUNDLED_EXECUTABLE_UNAVAILABLE')
        digest, count = hashlib.sha256(), 0
        while count < size:
            chunk = api.read_file(handle, min(65536, size - count))
            _require(chunk, 'BUNDLED_EXECUTABLE_CHANGED')
            count += len(chunk)
            digest.update(chunk)
        _require(count == size and api.read_file(handle, 1) == b'', 'BUNDLED_EXECUTABLE_CHANGED')
        versions = metadata(str(executable))
        _require(type(versions) is tuple and len(versions) == 2
                 and all(type(v) is str and re.fullmatch(r'(?:[0-9]{1,5}\.){3}[0-9]{1,5}|UNAVAILABLE', v)
                         for v in versions), 'BUNDLED_METADATA_UNAVAILABLE')
        previous = getattr(_auxiliary_pin, 'path', None)
        _auxiliary_pin.path = os.path.normcase(str(executable))
        try:
            yield BundledExecutable(str(executable), digest.hexdigest(), *versions)
        finally:
            _auxiliary_pin.path = previous
    finally:
        closed = True
        for handle in reversed(handles):
            try:
                if api.close_handle(handle) is not True: closed = False
            except BaseException:
                closed = False
        _require(closed, 'BUNDLED_EXECUTABLE_CLOSE_UNPROVED')


class _ParentPackageLeaves:
    """Only read-only host leaves and the existing owned-child boundary."""
    open_player = _NativeLeaves.open_player
    close_player = _NativeLeaves.close_player
    observe = _NativeLeaves.observe
    alive = _NativeLeaves.alive
    read_configuration = staticmethod(_NativeLeaves.read_configuration)
    tcp_rows = staticmethod(_NativeLeaves.tcp_rows)
    _api = staticmethod(_NativeLeaves._api)

    @staticmethod
    def executable_metadata(path):
        api = ctypes.WinDLL('version', use_last_error=True)
        size = api.GetFileVersionInfoSizeW
        size.argtypes, size.restype = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD
        get = api.GetFileVersionInfoW
        get.argtypes, get.restype = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL
        query = api.VerQueryValueW
        query.argtypes, query.restype = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)], wintypes.BOOL
        ignored = wintypes.DWORD()
        length = size(path, ctypes.byref(ignored))
        if length == 0:
            return ('UNAVAILABLE', 'UNAVAILABLE')
        _require(length <= 1024 * 1024, 'BUNDLED_METADATA_UNAVAILABLE')
        buffer = ctypes.create_string_buffer(length)
        _require(get(path, 0, length, buffer), 'BUNDLED_METADATA_UNAVAILABLE')
        address, count = ctypes.c_void_p(), wintypes.UINT()
        _require(query(buffer, '\\', ctypes.byref(address), ctypes.byref(count))
                 and count.value >= 52 and address.value is not None
                 and ctypes.addressof(buffer) <= address.value
                 and address.value + count.value <= ctypes.addressof(buffer) + length,
                 'BUNDLED_METADATA_UNAVAILABLE')
        words = struct.unpack('<13I', ctypes.string_at(address, 52))
        _require(words[0] == 0xFEEF04BD, 'BUNDLED_METADATA_UNAVAILABLE')
        return tuple('.'.join(str(v) for v in (words[i] >> 16, words[i] & 65535,
                     words[i+1] >> 16, words[i+1] & 65535)) for i in (2, 4))

    def server_current(self, executable, port):
        listeners = [r for r in self.tcp_rows() if r.state == 'LISTEN' and r.local_port == 5037]
        _require(len(listeners) == 1 and listeners[0].local_address == '127.0.0.1',
                 'READ_ONLY_PACKAGE_TRANSPORT_FAILED')
        handle = self.open_player(listeners[0].pid)
        try:
            observed = self.observe(handle)
            _require(self.alive(handle) is True and type(observed) is ProcessObservation
                     and ntpath.normcase(observed.image) == ntpath.normcase(executable.path)
                     and observed.identity.pid == listeners[0].pid, 'ADB_INSTANCE_BINDING_UNCERTAIN')
            peers = [r for r in self.tcp_rows() if r.state == 'ESTABLISHED'
                     and r.pid == observed.identity.pid and r.remote_port == port]
            _require(len(peers) == 1 and peers[0].remote_address == peers[0].local_address == '127.0.0.1',
                     'ADB_INSTANCE_BINDING_UNCERTAIN')
            return observed, listeners[0], peers[0]
        finally:
            self.close_player(handle)

    @staticmethod
    def fixed_command(argv, timeout, limit, *, before_spawn):
        _require(type(argv) is list and len(argv) >= 4
                 and all(type(v) is str for v in argv)
                 and ntpath.normcase(argv[0]) == ntpath.normcase(str(Path(PLAYER).parent / 'HD-Adb.exe'))
                 and argv[1] == '-s' and re.fullmatch(r'127\.0\.0\.1:[1-9][0-9]{0,4}', argv[2]) is not None
                 and int(argv[2].rsplit(':',1)[1]) < 65536
                 and tuple(argv[3:]) in tuple(k.value for k in PackageCommand)
                 and type(timeout) is int and timeout == 5 and type(limit) is int and limit == 4096
                 and callable(before_spawn), 'READ_ONLY_PACKAGE_COMMAND_DENIED')
        # C1: ordinary clients may start/replace a server before issuing these
        # read-only commands. No qualified fail-without-server-control mode for
        # the exact bundled binary is available. A sampled live server, pinned
        # executable, Job containment or discarded output does not prove this.
        # Do not activate this leaf, add server commands or a bypass boolean.
        raise InstalledBuildError('READ_ONLY_PACKAGE_TRANSPORT_FAILED')


class ParentPackageObserver:
    """P-only, one-shot exact observation; no arbitrary command API or retries."""
    def __init__(self, issuer, binding, *, leaves=None):
        _require(type(issuer) is CalibrationIssuer and issuer._used is True
                 and type(binding) is BuildBinding, 'CALIBRATION_AUTHORITY_DENIED')
        scope = issuer.scope
        _require((binding.run_nonce, binding.generation, binding.instance_digest) ==
                 (scope.run_nonce, scope.generation, scope.instance_digest), 'ADB_INSTANCE_BINDING_UNCERTAIN')
        issuer.check()
        self._issuer, self._binding = issuer, binding
        self._scope = scope
        self._owner = (os.getpid(), threading.get_ident())
        self._leaves = _ParentPackageLeaves() if leaves is None else leaves
        self._used = False
        self.executable = resolve_bundled_executable(PLAYER, self._leaves.executable_metadata)
        issuer.check()

    def observe(self):
        low, issuer, binding = self._leaves, self._issuer, self._binding
        handle = None
        pins = ExitStack()
        try:
            _require(not self._used and self._owner == (os.getpid(), threading.get_ident())
                     and issuer.scope == self._scope, 'CALIBRATION_AUTHORITY_DENIED')
            self._used = True
            issuer.check()
            pinned = pins.enter_context(_pin_bundled_executable(PLAYER, low.executable_metadata))
            _require(pinned == self.executable, 'BUNDLED_EXECUTABLE_CHANGED')
            handle = low.open_player(binding.process_identity.pid)
            def bound_source():
                try:
                    return _source(low, handle, binding)
                except Exception:
                    raise InstalledBuildError('ADB_INSTANCE_BINDING_UNCERTAIN') from None
            raw, _ = low.read_configuration()
            _require(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024
                     and digest_v3(raw) == binding.configuration_digest, 'ADB_INSTANCE_BINDING_UNCERTAIN')
            enabled = [line for line in raw.decode('utf-8-sig').splitlines()
                       if re.match(r'\s*bst\.enable_adb_access', line)]
            _require(enabled == ['bst.enable_adb_access="1"'],
                     'ADB_NOT_ENABLED_OWNER_SETTING_CHANGE_REQUIRED')
            source = bound_source()
            endpoint = f'127.0.0.1:{source.listener.local_port}'
            server = low.server_current(self.executable, source.listener.local_port)

            def check():
                issuer.check()
                _require(issuer.scope == self._scope
                         and bound_source() == source
                         and resolve_bundled_executable(PLAYER, low.executable_metadata) == self.executable
                         and low.server_current(self.executable, source.listener.local_port) == server,
                         'ADB_INSTANCE_BINDING_UNCERTAIN')
                issuer.check()  # final callback-revocation latch before spawn/parse

            outputs = []
            for kind in PackageCommand:
                check()
                argv = [self.executable.path, '-s', endpoint, *kind.value]
                result = low.fixed_command(argv, 5, 4096, before_spawn=check)
                check()
                _require(type(result) is CommandResult and result.complete is True
                         and type(result.returncode) is int and result.returncode == 0
                         and type(result.stdout) is str and type(result.stderr) is str
                         and result.stderr == '' and len(result.stdout.encode('utf-8')) <= 4096,
                         'READ_ONLY_PACKAGE_TRANSPORT_FAILED')
                text = result.stdout
                if kind is PackageCommand.SERIAL:
                    _require(text == endpoint + '\n', 'ADB_INSTANCE_BINDING_UNCERTAIN')
                elif kind is PackageCommand.PACKAGE:
                    _require(text == 'package:' + PACKAGE + '\n', 'PACKAGE_OBSERVATION_UNPROVED')
                else:
                    _require(re.fullmatch(r'[1-9][0-9]{0,9}\n', text) is not None,
                             'RUNNING_PACKAGE_OBSERVATION_UNPROVED')
                outputs.append(text)
            check()
            fact = ParentPackageFact(self._scope.digest, binding.process_identity,
                binding.instance_digest, PACKAGE, True, digest_v3(endpoint.encode()),
                self.executable.sha256, digest_v3(canonical_v3(outputs)), issuer._last)
            closing, handle = handle, None
            low.close_player(closing)
            issuer.check()
            return fact
        except BaseException as exc:
            issuer.revoke()
            reason = (str(exc) if type(exc) is InstalledBuildError and str(exc) in
                ('ADB_NOT_ENABLED_OWNER_SETTING_CHANGE_REQUIRED', 'ADB_INSTANCE_BINDING_UNCERTAIN')
                else 'READ_ONLY_PACKAGE_TRANSPORT_FAILED')
            raise InstalledBuildError(reason) from None
        finally:
            try:
                if handle is not None:
                    low.close_player(handle)
            finally:
                pins.close()
