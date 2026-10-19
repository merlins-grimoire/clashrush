"""Concrete deployment composition behind the existing owned MVP transaction.

No native activity at import. Runtime construction requires an exact calibrated
profile, a durable profile-bound run grant, and the parent-owned worker lease.
"""
from __future__ import annotations
from dataclasses import replace
import time

from .mvp_deployment import DeploymentEngine, DeploymentError, DeploymentResult, Intervention


class InstalledProfileReader:
    """Adapt the actual one-use PackageManager provider, not expected metadata.

    The provider remains default-denied for native activation. Inert leaves use
    this same adapter in offline verification. Each read is fresh and any source
    change permanently revokes this run; no TTL or rebinding of old evidence.
    """
    def __init__(self, *, provider, build_binding, player_binding, profile, version_code, live_gate):
        from .lifecycle import PlayerBinding
        from .mvp_installed_build import BuildBinding,InstalledBuildProvider
        from .mvp_deployment_profile import DeploymentProfile
        if (type(provider) is not InstalledBuildProvider or type(build_binding) is not BuildBinding
                or type(player_binding) is not PlayerBinding or player_binding.identity!=build_binding.process_identity
                or type(profile) is not DeploymentProfile or profile.data['schema']!=3
                or type(version_code) is not int or version_code<=0 or not callable(live_gate)):
            raise DeploymentError('INSTALLED_BUILD_BINDING')
        self.provider=provider;self.build_binding=build_binding;self.player_binding=player_binding
        self.profile=profile;self.version_code=version_code;self.gate=live_gate
        self.baseline=None;self.last_digest=None;self.revoked=False

    def invalidate(self):
        self.revoked=True
        self.provider.invalidate()

    def __call__(self, binding):
        from dataclasses import asdict
        from .mvp_deployment_profile import InstallationEvidence
        from .mvp_session_authority import canonical_v3,digest_v3
        try:
            if self.revoked or binding is not self.player_binding or self.gate() is not True:
                raise DeploymentError('INSTALLED_BUILD_REVOKED')
            self.provider.acquire(self.build_binding,self.profile.plan.source_context[3],self.version_code)
            record=self.provider.consume(self.build_binding)
            baseline=(record.source,record.package)
            if (self.gate() is not True or (self.baseline is not None and baseline!=self.baseline)):
                raise DeploymentError('INSTALLED_BUILD_CHANGED')
            self.baseline=baseline
            reduced=asdict(record)
            for key in ('start_utc','end_utc'):
                reduced[key]=reduced[key].isoformat()
            self.last_digest=digest_v3(canonical_v3(reduced))
            return InstallationEvidence(binding,record.package.package_name,record.package.version_name)
        except BaseException:
            self.invalidate()
            raise DeploymentError('INSTALLED_BUILD_UNPROVED') from None

    def evidence_digest(self, run):
        if (run.run_nonce!=self.build_binding.run_nonce or run.generation!=self.build_binding.generation
                or run.profile_digest!=self.profile.digest):
            self.invalidate()
            raise DeploymentError('INSTALLED_BUILD_BINDING')
        self(self.player_binding)
        return self.last_digest


class OwnedProfileReader:
    """Decision B current owned-view evidence, not a guest package certificate.

    BUILD_CHECK's schema-3 digest column retains its historical name. This
    issuer explicitly binds MVP_UI_ADMISSION and UNAVAILABLE package metadata.
    Fresh supported UI/account evidence is independently required by the observer
    and engine; this read-only ownership check cannot authorize an action alone.
    """
    def __init__(self, player_view, profile, live_gate):
        self.view, self.profile, self.gate = player_view, profile, live_gate
        self.revoked = False

    def __call__(self, binding):
        try:
            if self.revoked or binding is not self.view.binding or self.gate() is not True:
                raise DeploymentError('WINDOW_BINDING_UNCERTAIN')
            self.view.require_binding(binding)
            if self.revoked or self.gate() is not True:
                raise DeploymentError('POLICY_REVOKED')
            return None  # Honest absence, never InstallationEvidence(expected...).
        except BaseException:
            self.revoked = True
            raise

    def evidence_digest(self, run):
        from dataclasses import asdict
        from .mvp_session_authority import canonical_v3, digest_v3
        if run != self.view.run or run.profile_digest != self.profile.digest:
            self.revoked = True
            raise DeploymentError('POLICY_RUN_BINDING')
        self(self.view.binding)
        return digest_v3(canonical_v3(dict(
            basis='MVP_UI_ADMISSION', package_metadata='UNAVAILABLE',
            run_digest=run.digest, profile_digest=self.profile.digest,
            window=asdict(self.view.binding), observed_ns=self.view.last)))


class NativeDeploymentExecutor:
    """Keep rich proof until durable commit; legacy tuple is only an adapter."""
    def __init__(self,*,observe,deliver,release,live_gate,monotonic,wait,deadline,
                 monitor,record_result,plan=None,evidence=None,sink=None):
        self.observe=observe;self.deliver=deliver;self.release=release
        self.gate=live_gate;self.clock=monotonic;self.wait=wait;self.deadline=deadline
        self.monitor=monitor;self.record=record_result;self.used=False
        self.plan=plan;self.evidence=evidence;self.sink=sink
    def run(self):
        if self.used:raise DeploymentError('EXECUTOR_ALREADY_USED')
        self.used=True
        result=DeploymentResult(False,'DEPLOYMENT_UNAVAILABLE',0,0,False,False,False)
        try:
            self.monitor.start()
            result=DeploymentEngine(observe=self.observe,deliver=self.deliver,
                release=self.release,live_gate=self.gate,monotonic=self.clock,
                sleep=self.wait,visit_deadline=self.deadline,plan=self.plan).run()
            if self.monitor.state() is not Intervention.CLEAR:
                result=replace(result,complete=False,reason='INTERVENTION',intervention_free=False)
        except BaseException:
            result=replace(result,complete=False,reason='DEPLOYMENT_UNAVAILABLE')
        finally:
            try:released=self.release() is True
            except BaseException:released=False
            if self.evidence is not None and result.complete and released:
                try:self.evidence.seal(result,self.sink)
                except BaseException:
                    result=replace(result,complete=False,reason='POLICY_SEAL_INCOMPLETE')
            try:closed=self.monitor.close() is True
            except BaseException:closed=False
            if not released:
                result=replace(result,complete=False,reason='RELEASE_UNPROVED')
            if not closed:
                result=replace(result,complete=False,reason='INTERVENTION_MONITOR_UNAVAILABLE',intervention_free=False)
        try:
            if self.evidence is not None:
                if result.complete:self.evidence.publish()
            else:self.record(result)
        except BaseException:raise DeploymentError('PROOF_COMMIT_FAILED') from None
        return result.complete,result.reason
    def cleanup(self):
        if self.release() is not True:raise DeploymentError('RELEASE_UNPROVED')
        # Deliberately return None for ConcreteAttackVisitPorts' cleanup contract.


def build_policy_executor(*,player_view,authority,epoch,profile,lease,provider,build_binding,
                          version_code,approval_current,home_verified,account_current=None,
                          instance_current=None):
    """Complete W consumer: retained handoff -> build provider -> S004 -> seal.

    The parent/command boundary has already reserved and W consumed the exact run.
    There is no player creation/termination, metadata-only build fallback, legacy
    transaction boolean, or native-activation issuer hidden in this composition.
    """
    from .mvp_local_native import PolicyPlayerView,Win32BoundInput
    from .input_authorization import InputAuthorization
    from .mvp_policy_evidence import RunEvidence
    from .mvp_session_authority import DurableSessionAuthority
    if (type(player_view) is not PolicyPlayerView or type(authority) is not DurableSessionAuthority
            or player_view.binding is None or player_view.owner is None):
        raise DeploymentError('POLICY_PLAYER_REQUIRED')
    run=player_view.run
    authority._bound_v3(run,producer=True)
    if run.profile_digest!=profile.digest:raise DeploymentError('POLICY_PROFILE_BINDING')
    if provider is not None:
        # A historical query provider is not optional observed metadata. Never
        # silently ignore or invoke it, even if an injected leaf would succeed.
        raise DeploymentError('PACKAGE_METADATA_CONFLICT')
    def enabled():
        return lease.active() and player_view.enabled() is True and approval_current(run) is True
    # No historical provider invocation. Missing guest diagnostics do not remove
    # ownership or fresh frame/account gates and never become package proof.
    reader=OwnedProfileReader(player_view,profile,enabled)
    evidence=RunEvidence(authority,run,epoch=epoch,clock=player_view.clock,
        approval_current=approval_current,installed_current=reader.evidence_digest,
        account_current=account_current,instance_current=instance_current)
    bound=Win32BoundInput(player_view.binding,player_view.require_binding,
        InputAuthorization.monitored_attack(enabled),deadline=run.deadline_ns/1_000_000_000,
        monotonic=player_view.clock)
    return build_native_executor(binding=player_view.binding,bound_input=bound,
        capture_bgr=player_view.capture_bgr,home_verified=home_verified,enabled=enabled,
        authority=authority,transaction_ref=run.transaction_ref,profile=profile,lease=lease,
        installed_build=reader,evidence=evidence,owner=player_view.owner,
        ownership_live=player_view.ownership_live)


def build_native_executor(*,binding,bound_input,capture_bgr,home_verified,enabled,
                          authority,transaction_ref,profile,lease,installed_build=None,
                          evidence=None,owner=None,ownership_live=None):
    from .input_authorization import InputAction
    from .mvp_deployment_input import (
        GuardedDeploymentInput, TaggedWin32Mouse, WindowsInterventionMonitor, InputOwner,
    )
    from .mvp_deployment_profile import FrameObserver,require_installation

    from .mvp_policy_evidence import RunEvidence
    if profile.data['schema']!=3:raise DeploymentError('SOURCE_PROFILE_REQUIRED')
    if type(evidence) is not RunEvidence or type(owner) is not InputOwner or not callable(ownership_live):
        raise DeploymentError('POLICY_EVIDENCE_REQUIRED')
    if (evidence.authority is not authority or evidence.binding.transaction_ref!=transaction_ref
            or evidence.binding.profile_digest!=profile.digest
            or (owner.run_nonce,owner.generation,owner.binding_digest)!=(
                evidence.binding.run_nonce,evidence.binding.generation,evidence.binding.binding_digest)):
        raise DeploymentError('POLICY_RUN_BINDING')
    def active():
        return lease.active() and enabled() is True and evidence.active()
    if not active():raise DeploymentError('POLICY_REVOKED')
    require_installation(profile,binding,installed_build)
    if not active():raise DeploymentError('POLICY_REVOKED')
    monitor=WindowsInterventionMonitor(lease.marker)
    def authorized(action):
        if not active():return False
        bound_input._authorization.require(InputAction(action.value))
        return True
    mouse=TaggedWin32Mouse(bound_input,lease.marker)
    delivery=GuardedDeploymentInput(mouse=mouse,authorize=authorized,monitor=monitor.state,
        lease=lease,monotonic=time.monotonic,wait=time.sleep,observed_events=monitor.events,
        owner=owner,arm_event=monitor.arm,ownership_live=ownership_live,
        before_event=evidence.before_event,after_event=evidence.after_event)
    def capture():
        # FrameObserver's fresh build read can consume the remaining budget.
        # Check again after that read, adjacent to capture; late frames vanish.
        if not active():raise DeploymentError('POLICY_DEADLINE')
        frame=capture_bgr()
        if not active():raise DeploymentError('POLICY_DEADLINE')
        return frame
    observer=FrameObserver(profile=profile,capture=capture,monotonic=time.monotonic,
        intervention=monitor.state,home_verified=home_verified,build_binding=binding,
        installed_build=installed_build)
    def observe():
        if not active():raise DeploymentError('POLICY_DEADLINE')
        return evidence.observe(observer())
    def deliver(intent,proof):
        evidence.begin_gesture(intent)
        delivered=delivery.deliver(intent,proof)
        if delivered and intent.verb=='return_home':evidence.returned(delivery.gesture)
        return delivered
    return NativeDeploymentExecutor(observe=observe,deliver=deliver,
          release=delivery.release,live_gate=active,monotonic=time.monotonic,
          wait=time.sleep,deadline=evidence.binding.deadline_ns/1_000_000_000,monitor=monitor,
          record_result=None,plan=profile.plan,evidence=evidence,sink=delivery)
