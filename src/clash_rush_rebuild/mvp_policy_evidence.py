"""Live-producer adapter from S004 observations/input to the sole SQLite authority.

No capture, native input, clock reconstruction, or finalization occurs on replay.
The owning composition supplies trusted approval/build/account observations. Public
S004 booleans and historical receipts are not accepted as source evidence.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import threading

from .mvp_deployment import DeploymentError, DeploymentResult, Observation, Screen, Intervention
from .mvp_deployment_input import InputOwner, PhysicalReceipt
from .mvp_session_authority import (
    DurableSessionAuthority, FactV3, InputV3, RunBindingV3, SnapshotV3,
    canonical_v3, digest_v3,
)


def nanoseconds(seconds: float) -> int:
    from .mvp_deployment import finite
    return int(finite(seconds, 0) * 1_000_000_000)


class RunEvidence:
    """One live producer, immutable seal; persistence may finish after D.

    The pre-event writer is write-ahead only, not authorization. The input sink
    performs its final deadline/ownership check AFTER every fallible callback.
    A stalled emergency journal cannot delay the owned inverse release.
    """
    def __init__(self, authority, binding, *, epoch, clock, approval_current,
                 installed_current, account_current=None, instance_current=None):
        if (account_current is None) == (instance_current is None):
            raise DeploymentError('POLICY_IDENTITY_SOURCE')
        self._instance_policy = instance_current is not None
        current = instance_current if self._instance_policy else account_current
        if (type(authority) is not DurableSessionAuthority or type(binding) is not RunBindingV3
                or type(epoch) is not int or epoch < 1
                or not all(callable(v) for v in (clock, approval_current, installed_current, current))):
            raise DeploymentError('POLICY_EVIDENCE_INGRESS')
        if authority.read_run_v3(binding.generation) != binding:
            raise DeploymentError('POLICY_RUN_BINDING')
        self.authority=authority; self.binding=binding; self.epoch=epoch
        self.clock=clock; self.approval_current=approval_current
        self.installed_current=installed_current; self.account_current=current
        self._thread=threading.get_ident(); self._facts=[]; self._events={}
        self._checks={}; self._seal=None; self._result=None; self._return=None
        self._home=None; self._last_observation=0; self._last_time=binding.started_ns
        self._failed=False; self._emergency=False; self._target=None
        self._receipt_keys=set()
        self._observation_digest=None

    def _now(self):
        now=nanoseconds(self.clock())
        if threading.get_ident()!=self._thread or now < self._last_time:
            raise DeploymentError('POLICY_CLOCK_OR_OWNER')
        self._last_time=now
        return now

    def active(self):
        try:
            if self._failed or self._seal is not None or self._now()>=self.binding.deadline_ns:
                return False
            self.authority._bound_v3(self.binding,producer=True)
            if self.authority.outcome_v3(self.binding.generation) is not None:
                return False
            status=self.authority.status_v3()
            return (status.writer_epoch==self.epoch
                    and status.current_generation==self.binding.generation
                    and status.revocation_epoch==self.binding.revocation_epoch
                    and self.approval_current(self.binding) is True)
        except BaseException:
            return False

    def _fact(self, kind, sequence, observed, **payload):
        return FactV3(kind,sequence,observed,canonical_v3({
            'schema':3,'binding_digest':self.binding.digest,**payload}))

    def begin_gesture(self, intent):
        if not self.active(): raise DeploymentError('POLICY_REVOKED')
        # One control identity for matching DOWN/UP; MOVE is not held authority.
        self._target=digest_v3(canonical_v3({'control':'LEFT_MOUSE',
            'point':[intent.point.x,intent.point.y],'verb':intent.verb,'card':intent.card_id}))

    def before_event(self, owner, gesture, event, kind, marker, started):
        if (type(owner) is not InputOwner or owner.run_nonce!=self.binding.run_nonce
                or owner.generation!=self.binding.generation
                or owner.binding_digest!=self.binding.binding_digest or self._target is None
                or self._observation_digest is None or not self.active()):
            raise DeploymentError('POLICY_INPUT_BINDING')
        # Provider is checked at ingress and before each gesture by composition;
        # this port returns a current, issuer-owned evidence digest, not a version.
        build=self.installed_current(self.binding)
        if type(build) is not str or len(build)!=64:
            raise DeploymentError('INSTALLED_BUILD_UNPROVED')
        check_time=self._now()
        if check_time>=self.binding.deadline_ns or not self.active():
            raise DeploymentError('POLICY_DEADLINE')
        key=(gesture,event)
        if key in self._events: raise DeploymentError('POLICY_INPUT_REPLAY')
        item=InputV3(gesture,event,kind,'PROPOSED',1,None,None,None,
                     str(marker),self._target,self._observation_digest,0,0,'NOT_REQUIRED')
        self.authority.record_input_v3(self.binding,item,producer_epoch=self.epoch,expected_version=None)
        self._events[key]=item
        item=self._advance(item,phase='AUTHORIZED')
        # The write-ahead lower bound is not a claim of physical delivery.
        item=self._advance(item,phase='SUBMITTING',submission_started_ns=self._now(),
                           effect_possible=1,release_state='REQUIRED' if kind=='DOWN' else
                           'SUBMITTING' if kind=='UP' else 'NOT_REQUIRED')
        self._checks[key]=(check_time,build)
        return True

    def _advance(self, item, **changes):
        next_item=replace(item,state_version=item.state_version+1,**changes)
        self.authority.record_input_v3(self.binding,next_item,producer_epoch=self.epoch,
                                       expected_version=item.state_version)
        self._events[item.gesture_seq,item.event_seq]=next_item
        return next_item

    def after_event(self, receipt, emergency):
        if type(receipt) is not PhysicalReceipt or type(emergency) is not bool:
            raise DeploymentError('POLICY_INPUT_RECEIPT')
        key=(receipt.gesture,receipt.event)
        self._emergency |= emergency
        if key in self._receipt_keys: raise DeploymentError('POLICY_INPUT_REPLAY')
        self._receipt_keys.add(key)
        item=self._events.get(key)
        if item is None:
            # UP's durable row follows its physical receipt: a stalled writer
            # must not stand between attributed DOWN and the one owned inverse.
            if receipt.kind!='UP': raise DeploymentError('POLICY_INPUT_INTENT_MISSING')
            self._failed |= emergency
            item=InputV3(receipt.gesture,receipt.event,receipt.kind,'PROPOSED',1,None,None,None,
                str(receipt.marker),self._target,self._observation_digest,0,0,'NOT_REQUIRED')
            self.authority.record_input_v3(self.binding,item,producer_epoch=self.epoch,expected_version=None)
            self._events[key]=item
            item=self._advance(item,phase='AUTHORIZED')
            item=self._advance(item,phase='SUBMITTING',effect_possible=1,
                submission_started_ns=nanoseconds(receipt.started),release_state='SUBMITTING')
            down_key=(receipt.gesture,receipt.event-1)
            if down_key not in self._checks or self._events[down_key].event_kind!='DOWN':
                raise DeploymentError('POLICY_RELEASE_BINDING')
            self._checks[key]=self._checks[down_key]
        observed=receipt.observation
        complete=receipt.completed
        late=(complete is None or nanoseconds(complete)>=self.binding.deadline_ns)
        if (observed is None or complete is None or emergency or late):
            self._failed=True
            self._advance(item,phase='TERMINAL',uncertainty=1,release_state='UNCERTAIN',
                          hook_observed_ns=None if observed is None else nanoseconds(observed.observed),
                          completed_ns=None if complete is None else nanoseconds(complete))
            return
        if (observed.kind!=item.event_kind or str(observed.marker)!=item.marker
                or receipt.owner.run_nonce!=self.binding.run_nonce
                or receipt.owner.generation!=self.binding.generation):
            self._failed=True
            raise DeploymentError('POLICY_INPUT_RECEIPT')
        item=self._advance(item,phase='SUBMITTED')
        item=self._advance(item,phase='OBSERVED',hook_observed_ns=nanoseconds(observed.observed))
        item=self._advance(item,phase='TERMINAL',completed_ns=nanoseconds(complete),
                           release_state='OBSERVED' if item.event_kind in ('DOWN','UP') else 'NOT_REQUIRED')

    def observe(self, observation):
        if type(observation) is not Observation or not self.active():
            raise DeploymentError('POLICY_OBSERVATION')
        when=nanoseconds(observation.captured_at); now=self._now()
        if (observation.sequence<=self._last_observation or not self.binding.started_ns<=when<=now
                or now>=self.binding.deadline_ns or observation.intervention is not Intervention.CLEAR):
            raise DeploymentError('POLICY_OBSERVATION')
        self._last_observation=observation.sequence
        reduced=digest_v3(canonical_v3({'sequence':observation.sequence,'observed_ns':when,
                        'screen':observation.screen.value,'view':observation.view,
                        'binding_digest':self.binding.digest}))
        self._observation_digest=reduced
        if observation.screen is Screen.RESULT and self._result is None:
            self._result=self._fact('NATURAL_RESULT',1,when,observation_digest=reduced,natural=True)
        if observation.screen is Screen.HOME and observation.home_verified and self._return is not None:
            # Historical callers retain their old fact semantics. The current
            # production composition supplies a fresh exact-slot Home witness.
            if self.account_current(self.binding,observation) is not True:
                raise DeploymentError('INSTANCE_SLOT_HOME_UNPROVED' if self._instance_policy else 'SAME_ACCOUNT_UNPROVED')
            finished=self._now()
            if finished>=self.binding.deadline_ns: raise DeploymentError('POLICY_DEADLINE')
            if when<=self._return.observed_ns: raise DeploymentError('POLICY_HOME_NOT_FRESH')
            kind = 'INSTANCE_SLOT_HOME' if self._instance_policy else 'SAME_ACCOUNT_HOME'
            policy = {'identity_policy':'IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY',
                      'provider_digest':self.binding.provider_digest} if self._instance_policy else {}
            self._home=self._fact(kind,1,finished,observation_digest=reduced,
                                 return_digest=self._return.digest,account_ref=self.binding.account_ref,fresh=True,
                                 **policy)
        return observation

    def returned(self, gesture):
        if self._result is None or self._return is not None or not self.active():
            raise DeploymentError('POLICY_RETURN_CHAIN')
        events=tuple(v for k,v in sorted(self._events.items()) if k[0]==gesture)
        if not events or events[-1].event_kind!='UP' or events[-1].uncertainty:
            raise DeploymentError('POLICY_RETURN_UNPROVED')
        self._return=self._fact('RETURN_HOME',1,self._now(),
            observation_digest=digest_v3(canonical_v3([e.digest for e in events])),
            result_digest=self._result.digest,gesture_seq=gesture)

    def seal(self, result, sink):
        if (type(result) is not DeploymentResult or not result.complete or self._failed
                or self._emergency or sink.uncertain or sink.emergency_release_required
                or self._seal is not None or not self.active()
                or any(v is None for v in (self._result,self._return,self._home))):
            raise DeploymentError('POLICY_SEAL_INCOMPLETE')
        items=tuple(v for _,v in sorted(self._events.items()))
        facts=[self._result,self._return,self._home]
        for seq,item in enumerate(items,1):
            when,build=self._checks[item.gesture_seq,item.event_seq]
            facts.extend((self._fact('APPROVAL_CHECK',seq,when,
                approval_digest=self.binding.approval_digest,revocation_epoch=self.binding.revocation_epoch),
                self._fact('BUILD_CHECK',seq,when,provider_digest=self.binding.provider_digest,
                runner_manifest_digest=self.binding.runner_manifest_digest,source_tree=self.binding.source_tree,
                build_evidence_digest=build),
                self._fact('INPUT_RECEIPT',seq,item.completed_ns,input_digest=item.digest,
                           approval_sequence=seq,build_sequence=seq)))
        seal=self._fact('PRE_D_SEAL',1,self._now(),
            facts=[[f.kind,f.sequence,f.digest] for f in sorted(facts,key=lambda f:(f.kind,f.sequence))],
            inputs=[[i.gesture_seq,i.event_seq,i.digest] for i in items],emergency_release_required=False)
        snapshot=SnapshotV3(self.binding,self.epoch,tuple(facts)+(seal,),items)
        DurableSessionAuthority._complete_v3(self.binding,snapshot.facts,snapshot.inputs)
        self._seal=snapshot
        return snapshot

    def publish(self):
        """Administrative write of the original immutable pre-D memory only."""
        if self._seal is None: raise DeploymentError('POLICY_SEAL_MISSING')
        self.authority.record_snapshot_v3(self._seal)
        return self._seal
