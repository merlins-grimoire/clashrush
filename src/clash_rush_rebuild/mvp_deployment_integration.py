"""Concrete deployment composition behind the existing owned MVP transaction.

No native activity at import. Runtime construction requires an exact calibrated
profile, a durable profile-bound run grant, and the parent-owned worker lease.
"""
from __future__ import annotations
from dataclasses import replace
import time

from .mvp_deployment import DeploymentEngine, DeploymentError, DeploymentResult, Intervention


class NativeDeploymentExecutor:
    """Keep rich proof until durable commit; legacy tuple is only an adapter."""
    def __init__(self,*,observe,deliver,release,live_gate,monotonic,wait,deadline,
                 monitor,record_result):
        self.observe=observe;self.deliver=deliver;self.release=release
        self.gate=live_gate;self.clock=monotonic;self.wait=wait;self.deadline=deadline
        self.monitor=monitor;self.record=record_result;self.used=False
    def run(self):
        if self.used:raise DeploymentError('EXECUTOR_ALREADY_USED')
        self.used=True
        result=DeploymentResult(False,'DEPLOYMENT_UNAVAILABLE',0,0,False,False,False)
        try:
            self.monitor.start()
            result=DeploymentEngine(observe=self.observe,deliver=self.deliver,
                release=self.release,live_gate=self.gate,monotonic=self.clock,
                sleep=self.wait,visit_deadline=self.deadline).run()
            if self.monitor.state() is not Intervention.CLEAR:
                result=replace(result,complete=False,reason='INTERVENTION',intervention_free=False)
        except BaseException:
            result=replace(result,complete=False,reason='DEPLOYMENT_UNAVAILABLE')
        finally:
            try:released=self.release() is True
            except BaseException:released=False
            try:closed=self.monitor.close() is True
            except BaseException:closed=False
            if not released:
                result=replace(result,complete=False,reason='RELEASE_UNPROVED')
            if not closed:
                result=replace(result,complete=False,reason='INTERVENTION_MONITOR_UNAVAILABLE',intervention_free=False)
        try:self.record(result)
        except BaseException:raise DeploymentError('PROOF_COMMIT_FAILED') from None
        return result.complete,result.reason
    def cleanup(self):
        if self.release() is not True:raise DeploymentError('RELEASE_UNPROVED')
        # Deliberately return None for ConcreteAttackVisitPorts' cleanup contract.


def build_native_executor(*,binding,bound_input,capture_bgr,home_verified,enabled,
                          authority,transaction_ref,profile,lease):
    from .input_authorization import InputAction
    from .mvp_deployment_input import (
        GuardedDeploymentInput, TaggedWin32Mouse, WindowsInterventionMonitor,
    )
    from .mvp_deployment_profile import FrameObserver
    from .mvp_deployment_receipt import record_proof,require_profile
    from .mvp_deployment_worker import STOP_RESERVE
    require_profile(authority,authority.transaction(transaction_ref).run_nonce,profile.digest)
    monitor=WindowsInterventionMonitor(lease.marker)
    def active():
        return lease.active() and enabled() is True
    def authorized(action):
        if not active():return False
        bound_input._authorization.require(InputAction(action.value))
        return True
    mouse=TaggedWin32Mouse(bound_input,lease.marker)
    delivery=GuardedDeploymentInput(mouse=mouse,authorize=authorized,monitor=monitor.state,
                                   lease=lease,monotonic=time.monotonic,wait=time.sleep,observed_events=monitor.events)
    observer=FrameObserver(profile=profile,capture=capture_bgr,monotonic=time.monotonic,
                           intervention=monitor.state,home_verified=home_verified)
    return NativeDeploymentExecutor(observe=observer,deliver=delivery.deliver,
          release=delivery.release,live_gate=active,monotonic=time.monotonic,
          wait=time.sleep,deadline=lease.deadline-STOP_RESERVE,monitor=monitor,
          record_result=lambda result:record_proof(authority,transaction_ref,result,profile.digest))
