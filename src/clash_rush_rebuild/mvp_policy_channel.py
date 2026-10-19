"""One-shot, OS-authenticated P/W channel; never admission or SUCCESS authority.

Bootstrap (no inherited handles): P creates WindowsPolicyListener BEFORE spawning
W; its random name and P's PID/creation time go in W's constrained launch argv.
After topology.prepare, P accepts using its RETAINED W handle and sends Context.
W opens/retains P query+wait, checks named-pipe server PID and creation time, then
checks Context's P/W identities. P checks the pipe client against retained W.
No secret file, ambient inheritance, stdout parsing, service or broker is needed.
Same-user arbitrary process injection/handle theft is outside this boundary.

P sends Context; W consumes the durable reservation under the lifecycle mutex,
then sends HandoffAck; P accepts it under that mutex. IPC waits MUST be outside
that mutex. P may send Revoke at any point after Context, W alone releases input
and freezes spawning before ReleaseStageReceipt. OutcomeNotice is only a request
to the sole durable finalizer, never proof of success or eligibility.

Call no_more_gameplay immediately at every worker action gate (in addition to
all existing evidence/deadline gates); it probes the retained peer and pipe and
latches on any pending P control message. Receive that message before release.
There is no background monitor: the caller must bound its gate/poll interval.
Closing or losing the channel permanently blocks gameplay; never resume/retry.

Donor boundary: same lane's BasePilot -> CoC_Bot -> ClashAutomation trace
(IMPLEMENTATION_PREFLIGHT_BLOCKER.md) found no compatible authenticated retained-
identity IPC. Local boundary reuses NativeProcessApi and Win32 SDDL patterns.
No production command activates this module.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import re
import secrets
import struct
import threading
import time

from .lifecycle import ProcessIdentity
from .mvp_process_topology import PlayerHandoff, HandoffAck, ReleaseStageReceipt
from .mvp_session_authority import RunBindingV3
from .mvp_calibration import CalibrationCapability, CalibrationScope, CalibrationObservation, ParentPackageFact

MAX_FRAME = 3_000_000
MAX_MANIFEST = 2_000_000
_NAME = re.compile(r'\\\\\.\\pipe\\ClashRush-policy-[0-9a-f]{32}')
_HEX32 = re.compile('[0-9a-f]{32}')


class PolicyChannelError(RuntimeError):
    """Sanitized denial; payloads, paths and native errors are never echoed."""


def _need(value):
    if not value:
        raise PolicyChannelError('POLICY_CHANNEL_DENIED')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names.split()))
    return value


def _identity(value):
    return ProcessIdentity(**_keys(value, 'pid creation_time_100ns'))


def _offer(value):
    value = dict(_keys(value, 'run_nonce generation challenge parent worker player '
                      'remote_player_handle remote_player_job_query'))
    for key in ('parent', 'worker', 'player'):
        value[key] = _identity(value[key])
    return PlayerHandoff(**value)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    run: RunBindingV3
    producer_epoch: int
    offer: PlayerHandoff
    runner_manifest_bytes: bytes
    calibration: CalibrationCapability | None = None

    def __post_init__(self):
        _need(type(self.run) is RunBindingV3 and type(self.offer) is PlayerHandoff
              and type(self.producer_epoch) is int and 0 < self.producer_epoch < 2**63
              and self.offer.run_nonce == self.run.run_nonce
              and self.offer.generation == self.run.generation
              and type(self.runner_manifest_bytes) is bytes
              and 0 < len(self.runner_manifest_bytes) <= MAX_MANIFEST
              and hashlib.sha256(self.runner_manifest_bytes).hexdigest() == self.run.runner_manifest_digest)
        if self.calibration is not None:
            _need(type(self.calibration) is CalibrationCapability)
            self.calibration.scope.require_run(self.run)


@dataclass(frozen=True, slots=True)
class PlayerReady:
    """P has completed exact ownership/handoff checks and resumed E once."""
    offer: PlayerHandoff
    package_fact: ParentPackageFact | None = None


@dataclass(frozen=True, slots=True)
class Revoke:
    token: str

    def __post_init__(self):
        _need(type(self.token) is str and _HEX32.fullmatch(self.token) is not None)


@dataclass(frozen=True, slots=True)
class OutcomeNotice:
    """Untrusted-as-evidence finalization request, not a durable OutcomeV3."""
    outcome: str
    reason: str

    def __post_init__(self):
        _need(type(self.outcome) is str and self.outcome in
              ('SUCCESS', 'FAILED', 'EXPIRED', 'BLOCKED', 'UNCERTAIN', 'CANCELED')
              and type(self.reason) is str and self.reason in
              ('PRE_D_COMPLETE', 'GAMEPLAY_FAILED', 'DEADLINE_INCOMPLETE', 'PRODUCER_LOST',
               'EVIDENCE_INCOMPLETE', 'INPUT_UNCERTAIN', 'EMERGENCY_RELEASE', 'CANCELED'))


def _payload(message):
    if type(message) is PolicyContext:
        payload = {'run': asdict(message.run), 'producer_epoch': message.producer_epoch,
                'offer': asdict(message.offer), 'runner_manifest_bytes':
                base64.b64encode(message.runner_manifest_bytes).decode('ascii')}
        if message.calibration is not None:
            payload['calibration'] = asdict(message.calibration)
        return payload
    _need(type(message) in (HandoffAck, PlayerReady, Revoke, ReleaseStageReceipt, OutcomeNotice, CalibrationObservation))
    return asdict(message)


def _message(kind, payload):
    if kind == 'PolicyContext':
        extra = ' calibration' if type(payload) is dict and 'calibration' in payload else ''
        p = _keys(payload, 'run producer_epoch offer runner_manifest_bytes' + extra)
        capability = None
        if extra:
            c = _keys(p['calibration'], 'scope nonce issuer operations')
            _need(type(c['operations']) is list)
            capability = CalibrationCapability(CalibrationScope(**c['scope']), c['nonce'],
                                                c['issuer'], tuple(c['operations']))
        _need(type(p['runner_manifest_bytes']) is str)
        return PolicyContext(RunBindingV3(**p['run']), p['producer_epoch'], _offer(p['offer']),
                             base64.b64decode(p['runner_manifest_bytes'], validate=True), capability)
    if kind == 'CalibrationObservation':
        return CalibrationObservation(**_keys(payload, 'scope_digest offer_digest status observed_ns evidence_digest'))
    if kind == 'HandoffAck':
        p = _keys(payload, 'offer accepted noninheritable')
        _need(p['accepted'] is True and p['noninheritable'] is True)
        return HandoffAck(_offer(p['offer']), True, True)
    if kind == 'Revoke':
        return Revoke(**_keys(payload, 'token'))
    if kind == 'PlayerReady':
        p = _keys(payload, 'offer package_fact')
        fact = None
        if p['package_fact'] is not None:
            f = dict(_keys(p['package_fact'], 'scope_digest player instance_digest package running endpoint_digest executable_digest proof_digest observed_ns command_kinds'))
            f['player'] = _identity(f['player'])
            _need(type(f['command_kinds']) is list)
            f['command_kinds'] = tuple(f['command_kinds'])
            fact = ParentPackageFact(**f)
        return PlayerReady(_offer(p['offer']), fact)
    if kind == 'OutcomeNotice':
        return OutcomeNotice(**_keys(payload, 'outcome reason'))
    _need(kind == 'ReleaseStageReceipt')
    p = _keys(payload, 'offer revoke_token stage worker_members spawning_stopped')
    _need(type(p['worker_members']) is list and 1 <= len(p['worker_members']) <= 4096
          and p['spawning_stopped'] is True and type(p['stage']) is str
          and p['stage'] in ('NOT_REQUIRED', 'ATTEMPTED', 'NO_AUTHORIZED_RELEASE'))
    members = tuple(_identity(i) for i in p['worker_members'])
    _need(len(set(members)) == len(members))
    Revoke(p['revoke_token'])
    return ReleaseStageReceipt(_offer(p['offer']), p['revoke_token'], p['stage'], members, True)


class PolicyCodec:
    """Pure closed-schema sequencing; NOT authentication without Windows transport.

    Per-direction sequence numbers; context/ack/revoke/release/outcome at most once.
    Revoke may cross an in-flight ack or outcome; it can never clear a latch.
    """
    def __init__(self, role):
        _need(role in ('P', 'W'))
        self.role = role
        self.context = None
        self.context_digest = None
        self.sent = self.received = 0
        self.acked = self.ready = self.released = self.outcome = False
        self.revocation = None
        self.failed = False

    def _advance(self, message, sender):
        kind = type(message)
        if kind is PolicyContext:
            _need(sender == 'P' and self.context is None)
            self.context = message
            self.context_digest = hashlib.sha256(_json(_payload(message))).hexdigest()
            return
        _need(self.context is not None)
        if kind is Revoke:
            _need(sender == 'P' and self.revocation is None)
            self.revocation = message.token
            return
        if kind is PlayerReady:
            _need(sender == 'P' and self.acked and not self.ready
                  and self.revocation is None and message.offer == self.context.offer)
            if message.package_fact is not None:
                _need(self.context.calibration is not None)
                message.package_fact.require_context(self.context)
            self.ready = True
            return
        _need(sender == 'W')
        if kind is HandoffAck:
            _need(not self.acked and not self.released and not self.outcome
                  and (self.role == 'P' or self.revocation is None)
                  and message.offer == self.context.offer
                  and message.accepted is True and message.noninheritable is True)
            self.acked = True
        elif kind is ReleaseStageReceipt:
            _need(self.revocation is not None and not self.released
                  and message.revoke_token == self.revocation
                  and message.offer == self.context.offer
                  and self.context.offer.worker in message.worker_members)
            self.released = True
        else:
            expected = CalibrationObservation if self.context.calibration is not None else OutcomeNotice
            _need(kind is expected and not self.outcome and (self.ready or self.revocation is not None))
            if kind is CalibrationObservation:
                message.require_context(self.context)
            self.outcome = True

    def encode(self, message):
        try:
            _need(not self.failed)
            # Same strict validation for local producers and incoming wire objects.
            payload = json.loads(_json(_payload(message)))
            checked = _message(type(message).__name__, payload)
            self._advance(checked, self.role)
            self.sent += 1
            raw = _json({'schema': 1, 'sender': self.role, 'sequence': self.sent,
                         'run_digest': self.context.run.digest, 'challenge': self.context.offer.challenge,
                         'context_digest': self.context_digest, 'kind': type(message).__name__, 'payload': payload})
            _need(0 < len(raw) <= MAX_FRAME)
            return raw
        except Exception:
            self.failed = True
            raise PolicyChannelError('POLICY_CHANNEL_PROTOCOL') from None

    def decode(self, raw):
        try:
            _need(not self.failed and type(raw) is bytes and 0 < len(raw) <= MAX_FRAME)
            p = _keys(json.loads(raw), 'schema sender sequence run_digest challenge context_digest kind payload')
            _need(_json(p) == raw and type(p['schema']) is int and p['schema'] == 1
                  and p['sender'] == ('W' if self.role == 'P' else 'P')
                  and type(p['sequence']) is int and p['sequence'] == self.received + 1
                  and type(p['kind']) is str)
            message = _message(p['kind'], p['payload'])
            context = message if type(message) is PolicyContext else self.context
            _need(context is not None and p['run_digest'] == context.run.digest
                  and p['challenge'] == context.offer.challenge
                  and p['context_digest'] == hashlib.sha256(_json(_payload(context))).hexdigest())
            self._advance(message, p['sender'])
            self.received += 1
            return message
        except Exception:
            self.failed = True
            raise PolicyChannelError('POLICY_CHANNEL_PROTOCOL') from None


def _deadline(timeout):
    _need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 30)
    return time.monotonic() + timeout


class _Pipe:
    """Byte-mode PIPE_NOWAIT bounds every kernel read/write (no pending I/O).

    ponytail: one owner thread, one connection, 32KiB chunks; use overlapped I/O
    only if measured throughput warrants a more complex cancellation lifetime.
    """
    def __init__(self):
        _need(os.name == 'nt')
        from .win32_lifecycle_host import NativeProcessApi
        self.api = NativeProcessApi()
        self.k = self.api._kernel32
        h, d, p = wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p
        signatures = {
            'CreateNamedPipeW': ([wintypes.LPCWSTR, d, d, d, d, d, d, p], h),
            'ConnectNamedPipe': ([h, p], wintypes.BOOL),
            'SetNamedPipeHandleState': ([h, p, p, p], wintypes.BOOL),
            'GetNamedPipeClientProcessId': ([h, p], wintypes.BOOL),
            'GetNamedPipeServerProcessId': ([h, p], wintypes.BOOL),
            'PeekNamedPipe': ([h, p, d, p, p, p], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        self.handle = self.peer = None
        self.peer_identity = None
        self.owner = (os.getpid(), threading.get_ident())
        self.identity = self.identify(self.api.current_process_handle())
        self.server = False

    def identify(self, handle):
        return ProcessIdentity(self.api.get_process_id(handle), self.api.process_creation_time(handle))

    def own_thread(self):
        _need(self.owner == (os.getpid(), threading.get_ident()))

    def retain(self, handle, identity):
        _need(type(identity) is ProcessIdentity and self.identify(handle) == identity)
        duplicate = wintypes.HANDLE()
        current = self.api.current_process_handle()
        _need(self.k.DuplicateHandle(current, handle, current, ctypes.byref(duplicate),
                                     0x101000, False, 0))
        self.peer = duplicate.value
        self.peer_identity = identity
        _need(not self.api.handle_is_inheritable(self.peer))
        self.peer_live()

    def peer_live(self):
        self.own_thread()
        _need(self.peer is not None and self.api.wait_for_single_object(self.peer, 0) == 258
              and self.identify(self.peer) == self.peer_identity)

    def authenticate(self):
        self.peer_live()
        pid = wintypes.DWORD()
        function = self.k.GetNamedPipeClientProcessId if self.server else self.k.GetNamedPipeServerProcessId
        _need(function(self.handle, ctypes.byref(pid)) and pid.value == self.peer_identity.pid
              and not self.api.handle_is_inheritable(self.handle))

    def available(self):
        self.authenticate()
        count = wintypes.DWORD()
        _need(self.k.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(count), None))
        return count.value

    def pause(self, deadline):
        self.peer_live()
        remaining = deadline - time.monotonic()
        _need(remaining > 0)
        time.sleep(min(.002, remaining))

    def transfer(self, data, size, deadline):
        # Writes pass bytes, reads pass None. Every operation has ONE deadline.
        output = bytearray()
        offset = 0
        while offset < size:
            self.authenticate()
            _need(time.monotonic() < deadline)
            amount = min(32768, size - offset)
            buffer = ctypes.create_string_buffer(data[offset:offset + amount]) if data is not None else ctypes.create_string_buffer(amount)
            count = wintypes.DWORD()
            function = self.k.WriteFile if data is not None else self.k.ReadFile
            ok = function(self.handle, buffer, amount, ctypes.byref(count), None)
            if not ok:
                _need(ctypes.get_last_error() in (232, 536))  # no data / listening
                # Peek distinguishes an empty live pipe from a disconnected pipe.
                self.available()
            _need(count.value <= amount)
            if count.value:
                if data is None:
                    output.extend(buffer.raw[:count.value])
                offset += count.value
            else:
                self.pause(deadline)
        self.authenticate()
        return bytes(output)

    def send(self, raw, deadline):
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME)
        framed = struct.pack('!I', len(raw)) + raw
        self.transfer(framed, len(framed), deadline)

    def receive(self, deadline):
        size, = struct.unpack('!I', self.transfer(None, 4, deadline))
        _need(0 < size <= MAX_FRAME)
        return self.transfer(None, size, deadline)

    def close(self):
        self.own_thread()
        failed = False
        for name in ('handle', 'peer'):
            handle = getattr(self, name)
            if handle is not None:
                if self.api.close_handle(handle):
                    setattr(self, name, None)
                else:
                    failed = True
        _need(not failed)


class WindowsPolicyListener:
    """P's one-shot private local pipe; create before topology.prepare()."""
    def __init__(self):
        self._pipe = _Pipe()
        self.name = r'\\.\pipe\ClashRush-policy-' + secrets.token_hex(16)
        self.parent = self._pipe.identity
        self._used = False
        pipe = self._pipe
        descriptor = ctypes.c_void_p()
        try:
            from .win32_runtime import SECURITY_ATTRIBUTES
            sid = pipe.api.current_operator_sid()
            sddl = f'D:P(A;;GA;;;{sid})'
            _need(pipe.api._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None))
            attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
            # FIRST_PIPE_INSTANCE, duplex; byte mode, NOWAIT, REJECT_REMOTE_CLIENTS.
            handle = pipe.k.CreateNamedPipeW(self.name, 0x80003, 0x9, 1, 65536, 65536, 0,
                                              ctypes.byref(attributes))
            _need(handle not in (None, 0, ctypes.c_void_p(-1).value))
            pipe.handle, pipe.server = handle, True
        except Exception:
            pipe.close()
            raise PolicyChannelError('POLICY_CHANNEL_CREATE') from None
        finally:
            if descriptor.value:
                pipe.k.LocalFree(descriptor)

    def accept(self, *, worker_handle, context, timeout=5):
        try:
            _need(not self._used and type(context) is PolicyContext and context.offer.parent == self.parent)
            self._used = True
            pipe = self._pipe
            deadline = _deadline(timeout)
            pipe.retain(worker_handle, context.offer.worker)
            while True:
                pipe.peer_live()
                if pipe.k.ConnectNamedPipe(pipe.handle, None):
                    # NOWAIT first call may only transition to listening.
                    pass
                else:
                    error = ctypes.get_last_error()
                    if error == 535:  # ERROR_PIPE_CONNECTED
                        break
                    _need(error == 536)
                pipe.pause(deadline)
            pipe.authenticate()
            channel = PolicyChannel(pipe, 'P')
            channel._send(context, deadline)
            return channel
        except Exception:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_ACCEPT') from None

    def close(self):
        self._used = True
        self._pipe.close()


class PolicyChannel:
    """Only public transport factories establish OS peer authentication."""
    def __init__(self, pipe, role):
        _need(type(pipe) is _Pipe)
        pipe.authenticate()
        self._pipe, self._codec = pipe, PolicyCodec(role)
        self._closed = self._blocked = False

    @classmethod
    def connect(cls, name, *, parent, timeout=5):
        pipe = _Pipe()
        try:
            deadline = _deadline(timeout)
            _need(type(name) is str and _NAME.fullmatch(name) is not None and type(parent) is ProcessIdentity)
            handle = pipe.api.open_process_for_identity(parent.pid)
            _need(handle not in (None, 0))
            try:
                pipe.retain(handle, parent)
            finally:
                pipe.api.close_handle(handle)
            while True:
                pipe.peer_live()
                # SQOS identification prevents a server from impersonating W.
                handle = pipe.k.CreateFileW(name, 0xC0000000, 0, None, 3, 0x110000, None)
                if handle not in (None, 0, ctypes.c_void_p(-1).value):
                    pipe.handle = handle
                    break
                _need(ctypes.get_last_error() in (2, 231))
                pipe.pause(deadline)
            mode = wintypes.DWORD(1)  # byte read mode + NOWAIT, no inheritance
            _need(pipe.k.SetNamedPipeHandleState(pipe.handle, ctypes.byref(mode), None, None))
            channel = cls(pipe, 'W')
            context = channel._receive(deadline)
            _need(type(context) is PolicyContext)
            return channel
        except Exception:
            pipe.close()
            raise PolicyChannelError('POLICY_CHANNEL_CONNECT') from None

    @property
    def context(self):
        return self._codec.context

    @property
    def no_more_gameplay(self):
        if not self._closed:
            try:
                pending = self._pipe.available()
                if self._codec.role == 'W' and self._codec.ready and pending:
                    self._blocked = True  # After PlayerReady, P may only revoke.
            except Exception:
                self.close()
        return (self._blocked or self._closed or self._codec.revocation is not None
                or (self._codec.role == 'W' and not self._codec.ready))

    def pending(self):
        """Nonblocking metadata poll, not a receive timeout or gameplay permit."""
        try:
            _need(not self._closed)
            return self._pipe.available() > 0
        except BaseException:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_LOST') from None

    def _send(self, message, deadline):
        try:
            _need(not self._closed)
            if type(message) is PolicyContext:
                _need(message.offer.parent == self._pipe.identity
                      and message.offer.worker == self._pipe.peer_identity)
            if type(message) is Revoke:
                self._blocked = True
            self._pipe.send(self._codec.encode(message), deadline)
        except Exception:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_SEND') from None

    def send(self, message, *, timeout=5):
        try:
            self._send(message, _deadline(timeout))
        except Exception:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_SEND') from None

    def _receive(self, deadline):
        try:
            _need(not self._closed)
            message = self._codec.decode(self._pipe.receive(deadline))
            if type(message) is PolicyContext:
                _need(message.offer.worker == self._pipe.identity
                      and message.offer.parent == self._pipe.peer_identity)
            if type(message) is Revoke:
                self._blocked = True
            return message
        except Exception:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_RECEIVE') from None

    def receive(self, *, timeout=5):
        try:
            return self._receive(_deadline(timeout))
        except Exception:
            self.close()
            raise PolicyChannelError('POLICY_CHANNEL_RECEIVE') from None

    def close(self):
        self._blocked = self._closed = True
        try:
            self._pipe.close()
        except Exception:
            raise PolicyChannelError('POLICY_CHANNEL_CLOSE') from None
