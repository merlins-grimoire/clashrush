"""Protected exact-run source composition. No approval or calibration issuer.

The schema-2 descriptor binds explicit slot/internal-instance identity, not the
game account currently loaded inside it. External review, fresh supported Home,
owned native binding and calibration remain mandatory. Only a one-use Home
timestamp escapes the transient observer, never pixels or account text.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import time

from .mvp_deployment import DeploymentError, Screen
from .mvp_deployment_profile import load_profile, read_private, _read_windows, _template
from .mvp_deployment_vision import match_unique, roi
from .mvp_installed_build import BuildBinding, CONFIG
from .mvp_policy_ingress import PolicyIngress, _require_private
from .mvp_session_authority import canonical_v3, digest_v3


def _need(value):
    if not value:
        raise DeploymentError('POLICY_SOURCE_UNPROVED')


class PolicyWorkerSources:
    """One run, one exact protected descriptor, no callable from wire/config."""
    def __init__(self, project, run, *, clock=time.monotonic):
        self.project = Path(project).resolve(strict=True)
        self.run = run
        self.clock = clock
        self.ingress = PolicyIngress(self.project)
        self._home_time = None
        self._last_sequence = 0
        self._revoked = False
        self.raw = self._descriptor_bytes()
        _need(digest_v3(self.raw) == run.provider_digest)
        data = json.loads(self.raw)
        _need(type(data) is dict and canonical_v3(data) == self.raw and set(data) == {
            'schema', 'source_tree', 'account_ref', 'profile_digest', 'slot', 'slots_digest',
            'configuration_digest', 'instance_digest', 'version_code', 'slot_id', 'identity_policy'})
        _need(type(data['schema']) is int and data['schema'] == 2
              and data['source_tree'] == run.source_tree and data['account_ref'] == run.account_ref
              and data['profile_digest'] == run.profile_digest
              and data['identity_policy'] == 'IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY'
              and type(data['slot']) is int and 0 <= data['slot'] < 5
              and (data['version_code'] is None or
                   type(data['version_code']) is int and data['version_code'] > 0))
        for field in ('slots_digest', 'configuration_digest', 'instance_digest'):
            value = data[field]
            _need(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))
        self.data = data
        self.profile = load_profile(self.project, candidate_tree=run.source_tree)
        _need(self.profile.digest == run.profile_digest)
        # Optional descriptor expectation is not current installed evidence.
        # No provider/transport is constructed under Decision B.
        self.provider = None
        self.package_status = 'UNAVAILABLE'
        self.version_code = data['version_code']
        self.slot = self._slot()
        self.expected_title = self.slot.display_name

    def _descriptor_bytes(self):
        path = self.project / 'private/deployment/provider.json'
        _require_private(path)
        return read_private(self.project, 'private/deployment/provider.json', 8192)

    def _slot(self):
        from .registry import load_instance_slots
        slots = _read_windows(self.project, ('private', 'slots.json'), 8192)
        _need(digest_v3(slots) == self.data['slots_digest'])
        registration = json.loads(slots)
        raw = CONFIG.read_bytes()
        _need(digest_v3(raw) == self.data['configuration_digest'])
        slot = load_instance_slots(raw.decode('utf-8'), registration)[self.data['slot']]
        _need(slot.slot_id == self.data['slot_id']
              and digest_v3(slot.internal_name.encode()) == self.data['instance_digest'])
        return slot

    def approval_current(self, run):
        try:
            if self._revoked or run != self.run or self.clock() * 1e9 >= run.deadline_ns:
                return False
            raw, payload = self.ingress._approval('RUN_V3')
            request = asdict(run)
            del request['approval_digest']
            _need(raw['run'] == request and digest_v3(payload) == run.approval_digest
                  and self._descriptor_bytes() == self.raw
                  and digest_v3(read_private(self.project, 'private/deployment/profile.json', 256 * 1024))
                  == run.profile_digest and self._slot() == self.slot)
            # Configuration/profile reads cannot make an earlier approval sample
            # standing authority. Re-read protected bytes and expiry at the end.
            _, current = self.ingress._approval('RUN_V3')
            _need(current == payload)
            return True
        except BaseException:
            self._revoked = True
            return False

    def require_context(self, context):
        _need(context.run == self.run and self.approval_current(self.run))

    def build_binding(self, context):
        self.require_context(context)
        return BuildBinding(self.run.run_nonce, self.run.generation, context.offer.player,
                            self.data['instance_digest'], self.data['configuration_digest'])

    def home_verified(self, frame):
        """Called on the same positively identified HOME frame, before it is wiped."""
        self._home_time = None
        if not self.approval_current(self.run):
            return False
        patch = None
        try:
            # Reuse the profile's supported-screen witnesses, never a name ROI.
            # FrameObserver also performs ambiguity/control/intervention checks.
            supported = []
            for screen, rules in self.profile.data['screens'].items():
                hits = []
                for rule in rules:
                    patch = roi(frame, tuple(rule['roi']))
                    hits.append(match_unique(patch, _template(self.profile.assets[rule['template']]), .93) is not None)
                if rules and all(hits):
                    supported.append(screen)
            if supported != ['HOME'] or not self.approval_current(self.run):
                return False
            now = self.clock()
            _need(now * 1e9 < self.run.deadline_ns)
            self._home_time = now
            return True
        finally:
            patch = None
            frame = None

    def instance_current(self, run, observation):
        when, self._home_time = self._home_time, None
        valid = (run == self.run and when is not None and observation.screen is Screen.HOME
                 and observation.home_verified is True and observation.sequence > self._last_sequence
                 and observation.captured_at <= when <= self.clock()
                 and self.approval_current(run))
        if valid:
            self._last_sequence = observation.sequence
        return bool(valid)
