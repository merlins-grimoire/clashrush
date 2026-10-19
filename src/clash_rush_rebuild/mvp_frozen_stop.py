"""Synchronous parent-only stop snapshot; no durable/recoverable kill permit."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import threading
from time import monotonic_ns as _monotonic_ns, time_ns as _wall_ns


@dataclass(frozen=True, slots=True)
class FrozenStopFacts:
    binding_digest: str
    retirement_epoch: int
    deadline_ns: int
    wall_deadline_ns: int

    def __post_init__(self):
        if (type(self.binding_digest) is not str or len(self.binding_digest) != 64
                or any(c not in '0123456789abcdef' for c in self.binding_digest)
                or any(type(x) is not int or x < 0 for x in
                       (self.retirement_epoch, self.deadline_ns, self.wall_deadline_ns))):
            raise RuntimeError('POLICY_STOP_SNAPSHOT_DENIED')


class FrozenStop:
    __slots__ = ('topology', 'owner', 'facts', 'used', 'issued_ns')

    def __init__(self, topology, facts):
        if type(facts) is not FrozenStopFacts:
            raise RuntimeError('POLICY_FROZEN_STOP_REQUIRED')
        self.topology, self.facts = topology, facts
        self.owner = (os.getpid(), threading.get_ident())
        self.used = False
        self.issued_ns = _monotonic_ns()

    def consume(self, topology, physical_leaf, job):
        if (self.used or topology is not self.topology
                or self.owner != (os.getpid(), threading.get_ident())):
            raise RuntimeError('POLICY_STOP_REPLAY')
        self.used = True
        # Bind all Python values and native arguments before the trusted samples.
        deadline, wall_deadline = self.facts.deadline_ns, self.facts.wall_deadline_ns
        leaf, target = physical_leaf, job
        wall = _wall_ns()
        now = _monotonic_ns()
        if not 0 <= self.issued_ns <= now < deadline or not 0 <= wall < wall_deadline:
            raise RuntimeError('POLICY_STOP_DEADLINE')
        # No callback, DB, filesystem, serialization or IPC after the time sample.
        # Production physical_leaf is pre-bound ctypes TerminateJobObject itself.
        result = leaf(target, 1)
        if result != 1:
            raise RuntimeError('POLICY_STOP_NATIVE_FAILURE')


def physical_leaf(host):
    from .win32_lifecycle_host import Win32LifecycleHost
    if isinstance(host, Win32LifecycleHost):
        return host._runtime._api._kernel32.TerminateJobObject
    # Inert low-level host doubles are not a production composition entrypoint.
    def inert(job, exit_code):
        host.terminate_job(job)
        return 1
    return inert


@contextmanager
def pinned_stop_sources(sources, run):
    """Deny write/delete while validating all production stop sources.

    Both purposes use the same protected mutex at their parent caller. Native
    handles additionally exclude external write/delete during the frozen leaf.
    No caller-supplied clock is admitted to this production snapshot.
    """
    from .mvp_calibration_native import CalibrationConfiguration
    from .mvp_policy_sources import PolicyWorkerSources
    from .mvp_installed_build import CONFIG
    from .mvp_policy_ingress import _require_private
    from .win32_state_io import NativeWin32StateApi
    from .mvp_canonical_runtime import require, plain
    import json
    require(type(sources) in (CalibrationConfiguration, PolicyWorkerSources))
    calibration = type(sources) is CalibrationConfiguration
    names = ['var/approvals/policy-v3.json', 'private/slots.json',
             'private/deployment/runner-manifest.json']
    names += ['private/deployment/' + name for name in
              (('calibration.json', 'calibration-spec.json', 'calibration-host.json')
               if calibration else ('provider.json', 'profile.json'))]
    if calibration:
        names.append(sources.scope.private_root + '/' + run.run_nonce + '.json')
    paths = [sources.project / name for name in names] + [CONFIG]
    api = NativeWin32StateApi()
    handles = []
    try:
        for path in paths:
            plain(path)
            if path != CONFIG:
                _require_private(path)
            handle = api.create_file(str(path), 0x80000000, 1, 3, 0x00200000)
            require(handle not in (None, 0))
            handles.append(handle)
            require(api.handle_attributes(handle) & 0x410 == 0
                    and Path(api.final_path(handle)) == path)
        require(sources.approval_current(run) is True)
        raw, _ = sources.ingress._approval('CALIBRATION_READ_ONLY' if calibration else 'RUN_V3')
        wall_deadline = raw['expires_at'] * 1_000_000_000
        deadline = run.deadline_ns
        if calibration:
            wall_deadline = min(wall_deadline, json.loads(sources.witness)['expires_at'] * 1_000_000_000)
            deadline = min(deadline, sources.scope.expires_ns)
        # Use a start-of-sampling monotonic value: no duration refresh.
        now = _monotonic_ns()
        wall = _wall_ns()
        require(0 <= now < deadline and 0 <= wall < wall_deadline)
        deadline = min(deadline, now + wall_deadline - wall)
        yield deadline, wall_deadline
    finally:
        closed = True
        for handle in reversed(handles):
            try:
                closed = api.close_handle(handle) is True and closed
            except BaseException:
                closed = False
        if not closed:
            raise RuntimeError('POLICY_STOP_SOURCE_CLOSE')
