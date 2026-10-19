"""Closed observer-only calibration authority; workers hold parsed facts only.

The specification digest is not a completed gameplay profile. P owns the issuer,
reservation and durable evidence. Inert providers exercise this boundary offline;
no HWND, installed package metadata or capability fabricates running-app proof.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import re
import secrets
import time

from .mvp_session_authority import canonical_v3, digest_v3

PURPOSE = 'CALIBRATION_READ_ONLY'
OPERATIONS = ('PROCESS_IDENTITY', 'JOB_MEMBERSHIP',
              'WINDOW_GEOMETRY', 'GAME_WINDOW_CAPTURE', 'PRIVATE_EVIDENCE')


def need(value):
    if not value:
        raise RuntimeError('CALIBRATION_AUTHORITY_DENIED')


def hex_value(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


@dataclass(frozen=True, slots=True)
class CalibrationScope:
    candidate_commit: str
    candidate_tree: str
    profile_digest: str
    slot: int
    instance_digest: str
    package: str
    private_root: str
    run_nonce: str
    generation: int
    issued_ns: int
    expires_ns: int
    purpose: str = PURPOSE

    def __post_init__(self):
        need(type(self.purpose) is str and self.purpose == PURPOSE
             and hex_value(self.candidate_commit, 40) and hex_value(self.candidate_tree, 40)
             and hex_value(self.profile_digest, 64) and hex_value(self.instance_digest, 64)
             and hex_value(self.run_nonce, 32) and type(self.slot) is int and 0 <= self.slot < 5
             and type(self.generation) is int and self.generation > 0
             and type(self.issued_ns) is int and type(self.expires_ns) is int
             and 0 <= self.issued_ns < self.expires_ns
             and self.expires_ns - self.issued_ns <= 300_000_000_000
             and type(self.package) is str and self.package == 'com.supercell.clashofclans'
             and type(self.private_root) is str
             and re.fullmatch(r'private/[a-z][a-z0-9_-]{0,63}', self.private_root) is not None)

    @property
    def digest(self):
        return digest_v3(canonical_v3(asdict(self)))

    def require_run(self, run):
        CalibrationScope(**asdict(self))
        need((run.source_tree, run.profile_digest, run.run_nonce, run.generation,
              run.started_ns, run.deadline_ns, run.binding_digest, run.provider_digest) ==
             (self.candidate_tree, self.profile_digest, self.run_nonce, self.generation,
              self.issued_ns, self.expires_ns, self.digest, self.digest))

    @classmethod
    def decode(cls, raw):
        need(type(raw) is bytes and 0 < len(raw) <= 8192)
        try:
            value = json.loads(raw)
            need(canonical_v3(value) == raw)
            return cls(**value)
        except (ValueError, TypeError):
            raise RuntimeError('CALIBRATION_DESCRIPTOR_DENIED') from None


@dataclass(frozen=True, slots=True)
class CalibrationCapability:
    scope: CalibrationScope
    nonce: str
    issuer: str = 'P_CALIBRATION_V1'
    operations: tuple[str, ...] = OPERATIONS

    def __post_init__(self):
        need(type(self.scope) is CalibrationScope and hex_value(self.nonce, 32)
             and type(self.issuer) is str and self.issuer == 'P_CALIBRATION_V1'
             and type(self.operations) is tuple and self.operations == OPERATIONS)


class CalibrationIssuer:
    """P's one-shot lease; run consumption supplies durable restart/replay fencing."""
    def __init__(self, scope, *, current, clock_ns=time.monotonic_ns):
        need(type(scope) is CalibrationScope and callable(current) and callable(clock_ns))
        CalibrationScope(**asdict(scope))
        self.scope, self._current, self._clock = scope, current, clock_ns
        self._scope_bytes = canonical_v3(asdict(scope))
        self._last = scope.issued_ns
        self._capability = None
        self._used = self._revoked = False

    def check(self):
        need(not self._revoked)
        expected = CalibrationScope.decode(self._scope_bytes)
        need(type(self.scope) is CalibrationScope and self.scope == expected)
        now = self._clock()
        need(not self._revoked and type(now) is int
             and self._last <= now < expected.expires_ns and self._current() is True)
        after = self._clock()
        need(type(after) is int and now <= after < expected.expires_ns)
        # No callback after this latch. Compare detached original authority, not
        # an aliased frozen dataclass a callback could replace or mutate.
        need(not self._revoked and type(self.scope) is CalibrationScope
             and self.scope == expected and canonical_v3(asdict(self.scope)) == self._scope_bytes)
        self._last = after
        return True

    def issue(self):
        self.check()
        need(self._capability is None)
        self._capability = CalibrationCapability(self.scope, secrets.token_hex(16))
        return self._capability

    def consume(self, capability, *, scope, operations):
        self.check()
        need(not self._used and type(capability) is CalibrationCapability
             and type(scope) is CalibrationScope and scope == self.scope
             and capability == self._capability and type(operations) is tuple
             and operations == OPERATIONS)
        CalibrationCapability(**asdict_capability(capability))
        self._used = True
        return self

    def revoke(self):
        self._revoked = True


def asdict_capability(value):
    return dict(scope=value.scope, nonce=value.nonce, issuer=value.issuer, operations=value.operations)


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    scope_digest: str
    offer_digest: str
    status: str
    observed_ns: int
    evidence_digest: str | None

    def __post_init__(self):
        need(hex_value(self.scope_digest, 64) and hex_value(self.offer_digest, 64)
             and type(self.status) is str and self.status in
             ('CAPTURED', 'NEEDS_NAVIGATION_INPUT', 'SOURCE_UNAVAILABLE')
             and type(self.observed_ns) is int and self.observed_ns >= 0
             and (hex_value(self.evidence_digest, 64) if self.status == 'CAPTURED'
                  else self.evidence_digest is None))

    def require_context(self, context):
        CalibrationObservation(**asdict(self))
        scope = context.calibration.scope
        need(self.scope_digest == scope.digest
             and self.offer_digest == digest_v3(canonical_v3(asdict(context.offer)))
             and scope.issued_ns <= self.observed_ns < scope.expires_ns)


@dataclass(frozen=True, slots=True)
class CalibrationProcessQueries:
    """Exact handoff observation/handle-close surface, not a Python sandbox."""
    current_process_handle: object
    current_process_id: object
    identity_from_handle: object
    handle_is_inheritable: object
    wait_process: object
    job_limit_flags: object
    is_process_in_job: object
    close_handle: object

    @classmethod
    def from_host(cls, host):
        return cls(**{f.name: getattr(host, f.name) for f in fields(cls)})


def execute_calibration_worker(channel, *, authority, host, sources, mutex_factory, clock, wait):
    """Same authenticated handshake, no gameplay imports or input-release path."""
    from .mvp_process_topology import acknowledge_player_handoff, ReleaseStageReceipt
    from .mvp_policy_channel import PlayerReady, Revoke
    from .mvp_policy_run import WorkerLease
    from .mvp_runner_manifest import verify_runner_manifest
    from pathlib import Path
    need(type(host) is CalibrationProcessQueries)
    context = channel.context
    scope = context.calibration.scope
    lease = WorkerLease(channel, clock=clock)
    result = None
    try:
        with mutex_factory() as mutex:
            need(mutex.abandoned is False)
            mutex.require_usable()
            sources.require_context(context)
            need(authority.calibration_scope_v3(context.run) == scope)
            verify_runner_manifest(context.runner_manifest_bytes,
                approved_sha256=context.run.runner_manifest_digest,
                approved_tree=context.run.source_tree, package_root=Path(__file__).parent)
            ack = acknowledge_player_handoff(host, context.offer)
            authority.consume_v3(context.run, producer_epoch=context.producer_epoch,
                                 now_ns=int(clock() * 1e9))
        channel.send(ack)
        message = channel.receive(timeout=min(30., max(.001, context.run.deadline_ns / 1e9 - clock())))
        need(type(message) is PlayerReady and message.offer == context.offer)
        need(lease.active())
        sources.require_context(context)
        if message.package_fact is not None:
            need(type(sources) is CalibrationSources)
            sources.accept_parent_fact(message.package_fact, context)
            need(lease.active())
        result = sources.observe(context, lease)
        need(type(result) is CalibrationObservation and lease.active())
        result.require_context(context)
        sources.require_context(context)
        channel.send(result)
        revoke = channel.receive(timeout=5)
        need(type(revoke) is Revoke)
        lease.revoke()
        channel.send(ReleaseStageReceipt(context.offer, revoke.token, 'NOT_REQUIRED',
                                         (context.offer.worker,), True))
        until = clock() + 5.
        while not channel._closed and clock() < until:
            channel.no_more_gameplay
            wait(.01)
        return result
    finally:
        lease.revoke()
        try:
            try:
                if type(sources) is CalibrationSources:
                    sources.close()
            finally:
                host.close_handle(context.offer.remote_player_handle)
        finally:
            try: host.close_handle(context.offer.remote_player_job_query)
            finally: channel.close()


@dataclass(frozen=True, slots=True)
class PackageObservation:
    instance_digest: str
    package: str
    player: object
    running: bool


@dataclass(frozen=True, slots=True)
class ParentPackageFact:
    """Parsed P observation only: no raw output, path or transport capability."""
    scope_digest: str
    player: object
    instance_digest: str
    package: str
    running: bool
    endpoint_digest: str
    executable_digest: str
    proof_digest: str
    observed_ns: int
    command_kinds: tuple[str, ...] = ('SERIAL', 'PACKAGE', 'RUNNING')

    def __post_init__(self):
        from .lifecycle import ProcessIdentity
        need(type(self.player) is ProcessIdentity and self.running is True
             and type(self.package) is str and self.package == 'com.supercell.clashofclans'
             and all(hex_value(v, 64) for v in (self.scope_digest, self.instance_digest,
                 self.endpoint_digest, self.executable_digest, self.proof_digest))
             and type(self.observed_ns) is int and self.observed_ns >= 0
             and type(self.command_kinds) is tuple
             and self.command_kinds == ('SERIAL', 'PACKAGE', 'RUNNING'))

    def require_context(self, context):
        ParentPackageFact(**{f.name: getattr(self, f.name) for f in fields(self)})
        scope = context.calibration.scope
        scope.require_run(context.run)
        need(self.scope_digest == scope.digest and self.player == context.offer.player
             and self.instance_digest == scope.instance_digest and self.package == scope.package
             and scope.issued_ns <= self.observed_ns < scope.expires_ns)


@dataclass(frozen=True, slots=True)
class WindowObservation:
    player: object
    root_hwnd: int
    render_hwnd: int
    width: int
    height: int
    supported_visible: bool
    render: object = None
    capture_nonce: str | None = None

    def require_v2(self):
        from .lifecycle import ProcessIdentity
        need(type(self.player) is ProcessIdentity and type(self.render) is ProcessIdentity
             and hex_value(self.capture_nonce, 32))
        return self


class CalibrationWindowView:
    """Observer-only adaptation of the retained-family PrintWindow boundary.

    No InputOwner, foreground change, cursor, guest transport or lifecycle operation. The
    worker owns/closes its duplicates. Native size comes from the sealed spec,
    never the framebuffer setting. Bytes are transient; this view has no sink.
    """
    def __init__(self, *, host, offer, scope, current, native_size, clock_ns=time.monotonic_ns):
        need(type(scope) is CalibrationScope and type(native_size) is tuple
             and len(native_size) == 2 and all(type(v) is int and 0 < v <= 4096 for v in native_size)
             and callable(current) and callable(clock_ns))
        self.host, self.offer, self.scope = host, offer, scope
        self._current, self._clock, self._size = current, clock_ns, native_size
        self._scope_bytes = canonical_v3(asdict(scope))
        self._offer_bytes = canonical_v3(asdict(offer))
        self._last, self._revoked = scope.issued_ns, False
        self.binding = self.desktop = None
        self._members = None

    def _check(self):
        need(not self._revoked)
        now = self._clock()
        need(type(now) is int and self._last <= now < self.scope.expires_ns
             and self._current() is True)
        after = self._clock()
        need(not self._revoked and type(after) is int and now <= after < self.scope.expires_ns
             and canonical_v3(asdict(self.scope)) == self._scope_bytes
             and canonical_v3(asdict(self.offer)) == self._offer_bytes)
        self._last = after

    def _bound(self):
        CalibrationWindowView._check(self)
        h, o = self.host, self.offer
        need(self._members is not None and not self._members._closed
             and self.binding.render_identity in self._members.identities
             and h.identity_from_handle(o.remote_player_handle, o.player.pid) == o.player
             and h.wait_process(o.remote_player_handle, 0) is False
             and h.job_limit_flags(o.remote_player_job_query) == 0
             and h.is_process_in_job(o.remote_player_handle, o.remote_player_job_query) is True
             and h._api.input_desktop_identity() == self.desktop)
        h._require_capture_binding(self.binding, o.remote_player_job_query)
        CalibrationWindowView._check(self)

    def bind(self, expected_title):
        from .lifecycle import PlayerBinding
        try:
            CalibrationWindowView._check(self)
            need(self.binding is None)
            members = self.host.stable_job_members(self.offer.remote_player_job_query)
            self._members = members
            binding = self.host.bind_exact(self.offer.player, expected_title, members)
            need(type(binding) is PlayerBinding and binding.identity == self.offer.player
                 and binding.render_identity in members.identities
                 and (binding.width, binding.height) == self._size)
            self.binding = binding
            self.desktop = self.host._api.input_desktop_identity()
            CalibrationWindowView._bound(self)
            return binding
        except BaseException:
            self._revoked = True
            if self._members is not None:
                self._members.close()
                self._members = None
            raise

    def close(self):
        self._revoked = True
        if self._members is not None:
            self._members.close()
            self._members = None

    def capture_bgr(self):
        import numpy as np
        import traceback
        raw = frame = None
        failed = False
        try:
            CalibrationWindowView._bound(self)
            width, height, raw = self.host.capture_bgra(self.binding,
                before_capture=lambda: CalibrationWindowView._bound(self))
            CalibrationWindowView._bound(self)
            need((width, height) == self._size and type(raw) is bytes
                 and len(raw) == width * height * 4)
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            CalibrationWindowView._check(self)
        except BaseException as exc:
            self._revoked = failed = True
            traceback.clear_frames(exc.__traceback__)
            if type(frame) is np.ndarray:
                frame.fill(0)
            frame = None
        finally:
            raw = None
        if failed:
            raise RuntimeError('WINDOW_BINDING_UNCERTAIN')
        return frame


class CalibrationSources:
    """Closed observer surface, never the parent's transport or lifecycle leaves.

    An optional parent fact is diagnostic only. A concrete approved
    window/screen/evidence source is still required; package facts cannot invent it.
    capture_private_evidence must return a read-back digest of approved narrow
    evidence under scope.private_root, never a full frame or arbitrary path.
    """
    def __init__(self, scope, *, current, observer=None, clock_ns=time.monotonic_ns,
                 parent_fact=None, package_metadata=None):
        need(type(scope) is CalibrationScope and callable(current) and callable(clock_ns))
        if observer is None:
            raise RuntimeError('CALIBRATION_WINDOW_SOURCE_UNAVAILABLE')
        self.scope, self._current, self._clock = scope, current, clock_ns
        # No package-query method is retained or invoked under Decision B.
        self._scope_bytes = canonical_v3(asdict(scope))
        self._parent_fact = parent_fact
        need(parent_fact is None or type(parent_fact) is ParentPackageFact)
        need(package_metadata is None or type(package_metadata) is PackageObservation)
        self._package_metadata = package_metadata
        self.package_status = 'UNAVAILABLE' if parent_fact is None and package_metadata is None else 'OBSERVED'
        self._window = observer.window_geometry
        self._observer = observer
        self._capture = observer.capture_private_evidence
        need(all(callable(p) for p in (self._window, self._capture)))
        self._last = scope.issued_ns
        self._used = False
        self._fact_bytes = None
        self._bracket = None

    def close(self):
        from .mvp_calibration_native import NativeCalibrationObserver
        if type(self._observer) is NativeCalibrationObserver:
            self._observer.view.close()

    def accept_parent_fact(self, fact, context):
        self.require_context(context)
        need(not self._used and type(fact) is ParentPackageFact
             and (self._parent_fact is None or self._parent_fact == fact))
        fact.require_context(context)
        need(fact.observed_ns <= self._last)
        snapshot = canonical_v3(asdict(fact))
        need(self._fact_bytes is None or snapshot == self._fact_bytes)
        self._parent_fact = fact
        self.package_status = 'OBSERVED'
        self._fact_bytes = snapshot

    def _require_fact(self, context):
        fact = self._parent_fact
        if fact is not None:
            fact.require_context(context)
            need(fact.observed_ns <= self._last)
            snapshot = canonical_v3(asdict(fact))
            need(self._fact_bytes is None or snapshot == self._fact_bytes)
            self._fact_bytes = snapshot

    def require_context(self, context):
        need(canonical_v3(asdict(self.scope)) == self._scope_bytes)
        need(type(context.calibration) is CalibrationCapability and context.calibration.scope == self.scope)
        self.scope.require_run(context.run)
        now = self._clock()
        need(type(now) is int and self._last <= now < self.scope.expires_ns and self._current() is True)
        after = self._clock()
        need(type(after) is int and now <= after < self.scope.expires_ns)
        need(canonical_v3(asdict(self.scope)) == self._scope_bytes)
        self._last = after

    def observe(self, context, lease):
        from .lifecycle import ProcessIdentity
        self.require_context(context)
        need(not self._used and lease.active() is True)
        self._used = True
        if self._parent_fact is not None:
            self._require_fact(context)
        metadata = self._package_metadata
        if metadata is not None:
            if not (type(metadata) is PackageObservation and type(metadata.player) is ProcessIdentity
                    and metadata.player == context.offer.player and metadata.running is True
                    and type(metadata.package) is str and metadata.package == self.scope.package
                    and type(metadata.instance_digest) is str
                    and metadata.instance_digest == self.scope.instance_digest):
                raise RuntimeError('PACKAGE_METADATA_CONFLICT')
        self.require_context(context)
        need(lease.active() is True)
        self._require_fact(context)
        window_started = self._last
        window = self._window(context.offer)
        need(type(window) is WindowObservation and type(window.player) is ProcessIdentity
             and window.player == context.offer.player
             and all(type(x) is int and x > 0 for x in
                     (window.root_hwnd, window.render_hwnd, window.width, window.height))
             and window.root_hwnd != window.render_hwnd and window.width * window.height <= 16_777_216
             and type(window.supported_visible) is bool)
        self.require_context(context)
        need(lease.active() is True)
        self._require_fact(context)
        capture_started = self._last
        window_bytes = canonical_v3(asdict(window))
        evidence = None
        if window.supported_visible:
            evidence = self._capture(self.scope, context.offer, window)
            need(hex_value(evidence, 64))
            self.require_context(context)
            need(lease.active() is True)
            # Persistence/readback can outlast the captured window. A digest is
            # not final binding proof, and callback mutation cannot rewrite the
            # detached pre-capture identity/geometry being compared here.
            final_window = self._window(context.offer)
            need(type(final_window) is WindowObservation
                 and canonical_v3(asdict(final_window)) == window_bytes
                 and canonical_v3(asdict(window)) == window_bytes)
        self.require_context(context)
        need(lease.active() is True)
        self._require_fact(context)
        # Ordering only, not a claim that a lease is a freshness window. Concrete
        # capture specifications must supply their sample/time criteria.
        self._bracket = (None if self._parent_fact is None else self._parent_fact.observed_ns,
                         window_started, capture_started, self._last)
        return CalibrationObservation(self.scope.digest,
            digest_v3(canonical_v3(asdict(context.offer))),
            'CAPTURED' if window.supported_visible else 'NEEDS_NAVIGATION_INPUT',
            self._last, evidence)
