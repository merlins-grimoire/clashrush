"""Bounded, typed army deployment and explicit battle exit.

Donor sequence: CoC_Bot a5c943a src/attacker.py (discover all cards, preserve
an overlap while scrolling, dispatch by card kind). The unsafe transport,
implicit classifications, random targets and restart tail are NOT retained.
Exit sequence: BasePilot 4ede1ef app/core/bot.py, return-only branch.

This module performs no native operation on import. Observation and delivery
ports are deliberately separate; a proposal is re-proved at the last input
boundary. A result is not a lifecycle retirement receipt.
"""
from __future__ import annotations

import math
import re
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable


class DeploymentError(RuntimeError):
    """Only a closed/sanitized reason crosses this boundary."""
    def __init__(self, code: str):
        if type(code) is not str or re.fullmatch(r'[A-Z][A-Z_]{0,63}', code) is None:
            code = 'DEPLOYMENT_INVALID'
        self.code = code
        super().__init__(code)


class CardKind(StrEnum):
    TROOP = 'TROOP'
    HERO = 'HERO'
    CLAN = 'CLAN'
    SPELL = 'SPELL'
    UNKNOWN = 'UNKNOWN'


class Spell(StrEnum):
    NONE = 'NONE'
    LIGHTNING = 'LIGHTNING'
    EARTHQUAKE = 'EARTHQUAKE'
    RAGE = 'RAGE'
    HEAL = 'HEAL'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True, slots=True)
class CompiledDeploymentPlan:
    """Immutable finite single-page roster and exact initial quantities."""
    roster: tuple[tuple[str, CardKind, Spell, int], ...]
    viewport_limit: int
    digest: str = ''
    def __post_init__(self):
        if (type(self.roster) is not tuple or type(self.viewport_limit) is not int
                or not 1 <= self.viewport_limit <= 6
                or not 1 <= len(self.roster) <= self.viewport_limit):
            code = ('PLAN_VIEWPORT_OVERFLOW' if type(self.roster) is tuple
                    and type(self.viewport_limit) is int and len(self.roster) > self.viewport_limit
                    else 'PLAN_INVALID')
            raise DeploymentError(code)
        normalized=[]
        for entry in self.roster:
            if (type(entry) is not tuple or len(entry)!=4 or type(entry[0]) is not str
                    or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}',entry[0]) is None
                    or type(entry[1]) is not CardKind or type(entry[2]) is not Spell
                    or type(entry[3]) is not int or not 1<=entry[3]<=999
                    or entry[1] is CardKind.UNKNOWN or entry[2] is Spell.UNKNOWN
                    or (entry[1] is CardKind.SPELL)==(entry[2] is Spell.NONE)
                    or (entry[1] in (CardKind.HERO,CardKind.CLAN) and entry[3]!=1)):
                raise DeploymentError('PLAN_INVALID')
            normalized.append((entry[0],entry[1].value,entry[2].value,entry[3]))
        if len({entry[0] for entry in self.roster})!=len(self.roster):
            raise DeploymentError('PLAN_INVALID')
        payload=json.dumps({'layout':'SINGLE_PAGE','limit':self.viewport_limit,'roster':normalized},
                           sort_keys=True,separators=(',',':')).encode()
        computed=hashlib.sha256(payload).hexdigest()
        if self.digest and (type(self.digest) is not str or self.digest!=computed):
            raise DeploymentError('PLAN_DIGEST_MISMATCH')
        object.__setattr__(self,'digest',computed)
    @property
    def signatures(self):
        return tuple(entry[:3] for entry in self.roster)
    @property
    def quantities(self):
        return tuple(entry[3] for entry in self.roster)


class CardState(StrEnum):
    AVAILABLE = 'AVAILABLE'
    DEPLETED = 'DEPLETED'
    DEPLOYED = 'DEPLOYED'
    UNKNOWN = 'UNKNOWN'


class Screen(StrEnum):
    HOME = 'HOME'
    MATCH = 'MATCH'
    ARMY = 'ARMY'
    BATTLE = 'BATTLE'
    END_CONFIRM = 'END_CONFIRM'
    RESULT = 'RESULT'
    UNKNOWN = 'UNKNOWN'


class Intervention(StrEnum):
    CLEAR = 'CLEAR'
    DETECTED = 'DETECTED'
    UNKNOWN = 'UNKNOWN'


class Action(StrEnum):
    # Exact subset of the existing InputAction vocabulary, not new capabilities.
    ATTACK_NAVIGATION = 'ATTACK_NAVIGATION'
    TROOP_DEPLOYMENT = 'TROOP_DEPLOYMENT'
    RETURN_HOME = 'RETURN_HOME'
    CLEANUP_RELEASE = 'CLEANUP_RELEASE'


class TargetKind(StrEnum):
    GROUND = 'GROUND'
    ENEMY_STRUCTURE = 'ENEMY_STRUCTURE'
    WALL_CLUSTER = 'WALL_CLUSTER'
    FRIENDLY_COHORT = 'FRIENDLY_COHORT'
    INJURED_COHORT = 'INJURED_COHORT'


def finite(value: object, minimum: float = 0.0, maximum: float = 1e12) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise DeploymentError('INVALID_NUMBER')
    return float(value)


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float
    def __post_init__(self):
        finite(self.x, 0, 1); finite(self.y, 0, 1)
    def near(self, other: Point, tolerance: float = .004) -> bool:
        return type(other) is Point and abs(self.x-other.x) <= tolerance and abs(self.y-other.y) <= tolerance


@dataclass(frozen=True, slots=True)
class Card:
    identity: str
    kind: CardKind
    spell: Spell
    remaining: int | None
    state: CardState
    point: Point
    selected: bool = False
    def __post_init__(self):
        if (type(self.identity) is not str or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}', self.identity) is None
                or type(self.kind) is not CardKind or type(self.spell) is not Spell
                or type(self.state) is not CardState or type(self.point) is not Point
                or type(self.selected) is not bool
                or (self.remaining is not None and (type(self.remaining) is not int or not 0 <= self.remaining <= 999))):
            raise DeploymentError('CARD_INVALID')
        if self.kind not in (CardKind.SPELL, CardKind.UNKNOWN) and self.spell is not Spell.NONE:
            raise DeploymentError('CARD_INVALID')
    @property
    def signature(self):
        return (self.identity, self.kind, self.spell)
    @property
    def exhausted(self) -> bool:
        if self.kind in (CardKind.HERO, CardKind.CLAN):
            return self.remaining == 0 and self.state in (CardState.DEPLOYED, CardState.DEPLETED)
        return self.remaining == 0 and self.state is CardState.DEPLETED
    def require_known(self):
        if (self.kind is CardKind.UNKNOWN or self.state is CardState.UNKNOWN
                or self.remaining is None
                or (self.kind is CardKind.SPELL and self.spell in (Spell.NONE, Spell.UNKNOWN))):
            raise DeploymentError('CARD_UNKNOWN')
        if self.kind in (CardKind.HERO, CardKind.CLAN) and self.remaining not in (0, 1):
            raise DeploymentError('SINGLETON_INVALID')
        if self.remaining == 0 and not self.exhausted:
            raise DeploymentError('DEPLETION_UNPROVED')
        if self.remaining and self.state is not CardState.AVAILABLE:
            raise DeploymentError('CARD_STATE_CONFLICT')


@dataclass(frozen=True, slots=True)
class Page:
    cards: tuple[Card, ...]
    left_edge: bool
    right_edge: bool
    def __post_init__(self):
        if (type(self.cards) is not tuple or not 1 <= len(self.cards) <= 24
                or any(type(c) is not Card for c in self.cards)
                or type(self.left_edge) is not bool or type(self.right_edge) is not bool
                or any(a.point.x >= b.point.x for a,b in zip(self.cards, self.cards[1:]))):
            raise DeploymentError('PAGE_INVALID')


@dataclass(frozen=True, slots=True)
class Target:
    kind: TargetKind
    point: Point
    confidence: float
    def __post_init__(self):
        if type(self.kind) is not TargetKind or type(self.point) is not Point:
            raise DeploymentError('TARGET_INVALID')
        finite(self.confidence, 0, 1)


@dataclass(frozen=True, slots=True)
class Observation:
    sequence: int
    captured_at: float
    view: str
    screen: Screen
    page: Page | None
    targets: tuple[Target, ...]
    controls: tuple[tuple[str, Point], ...]
    intervention: Intervention
    home_verified: bool = False
    def __post_init__(self):
        finite(self.captured_at)
        if (type(self.sequence) is not int or self.sequence < 1
                or type(self.view) is not str or not 1 <= len(self.view) <= 128
                or type(self.screen) is not Screen or type(self.intervention) is not Intervention
                or (self.page is not None and type(self.page) is not Page)
                or type(self.targets) is not tuple or len(self.targets) > 256
                or any(type(t) is not Target for t in self.targets)
                or type(self.controls) is not tuple or len(self.controls)>16
                or type(self.home_verified) is not bool):
            raise DeploymentError('OBSERVATION_INVALID')
        names=[]
        for item in self.controls:
            if (type(item) is not tuple or len(item)!=2 or type(item[0]) is not str
                    or item[0] not in {'attack','find_match','army_attack','end_battle','confirm_end','return_home'}
                    or type(item[1]) is not Point):
                raise DeploymentError('CONTROL_INVALID')
            names.append(item[0])
        if len(set(names)) != len(names): raise DeploymentError('CONTROL_AMBIGUOUS')
        if self.screen is Screen.BATTLE and self.page is None:
            raise DeploymentError('PAGE_MISSING')
    def control(self, name: str) -> Point:
        matches=[p for n,p in self.controls if n==name]
        if len(matches)!=1: raise DeploymentError('CONTROL_UNPROVED')
        return matches[0]


@dataclass(frozen=True, slots=True)
class Intent:
    verb: str
    point: Point
    action: Action
    card_id: str | None = None
    duration: float = 0.0
    destination: Point | None = None
    deadline: float = 0.0
    def __post_init__(self):
        if self.verb not in {'select','hold','tap','attack','find_match','army_attack','end_battle','confirm_end','return_home'}:
            raise DeploymentError('INTENT_INVALID')
        if type(self.point) is not Point or type(self.action) is not Action:
            raise DeploymentError('INTENT_INVALID')
        finite(self.duration, 0, .5); finite(self.deadline)
        if self.destination is not None and type(self.destination) is not Point:
            raise DeploymentError('INTENT_INVALID')


@dataclass(frozen=True, slots=True)
class Policy:
    deployment_seconds: float = 60.0
    visit_seconds: float = 240.0
    exit_seconds: float = 30.0
    troop_hold_seconds: float = .25
    card_hold_ceiling: float = 25.0
    observation_age: float = .75
    acknowledgement_seconds: float = 1.5
    max_pages: int = 16
    max_cards: int = 48
    max_actions: int = 1024
    target_confidence: float = .90
    def __post_init__(self):
        finite(self.deployment_seconds, .01, 60)
        finite(self.visit_seconds, 1, 600)
        finite(self.exit_seconds, 1, 30)
        finite(self.troop_hold_seconds, .01, .5)
        finite(self.card_hold_ceiling, .01, 25)
        finite(self.observation_age, .01, .75)
        finite(self.acknowledgement_seconds, .1, 1.5)
        finite(self.target_confidence, .8, 1)
        for value,maximum in ((self.max_pages,16),(self.max_cards,48),(self.max_actions,1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise DeploymentError('POLICY_INVALID')


class Budget:
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None], deadline: float):
        self.clock=clock; self.sleep=sleep
        self.deadline=finite(deadline)
        self.last=finite(clock())
        self.check()
    def check(self, deadline: float | None = None) -> float:
        now=finite(self.clock())
        if now < self.last: raise DeploymentError('CLOCK_REGRESSION')
        self.last=now
        if now >= min(self.deadline, self.deadline if deadline is None else finite(deadline)):
            raise DeploymentError('DEADLINE')
        return now
    def wait(self, seconds: float, deadline: float | None = None):
        now=self.check(deadline)
        end=min(self.deadline, self.deadline if deadline is None else deadline)
        self.sleep(min(finite(seconds,0,5), end-now))
        self.check(deadline)


class Inventory:
    """One complete, positively bounded single-page inventory."""
    def __init__(self, max_cards=48, max_pages=16):
        self.cards: list[Card]=[]; self.complete=False; self.pages=0
        self.max_cards=max_cards; self.max_pages=max_pages
    @property
    def identities(self): return tuple(c.identity for c in self.cards)
    def add(self, page: Page):
        if type(page) is not Page or self.complete or self.pages:
            raise DeploymentError('COVERAGE_INVALID')
        for c in page.cards: c.require_known()
        if not page.left_edge or not page.right_edge:
            raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
        self.cards.extend(page.cards)
        self.pages+=1
        if len(self.cards)>self.max_cards: raise DeploymentError('CARD_LIMIT')
        self.complete=True
    def locate(self, page: Page) -> int:
        signatures=tuple(c.signature for c in self.cards)
        observed=tuple(c.signature for c in page.cards)
        if not page.left_edge or not page.right_edge or observed!=signatures:
            raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
        return 0


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    complete: bool
    reason: str
    cards_total: int
    spells_consumed: int
    own_exit: bool
    home_verified: bool
    intervention_free: bool
    remaining: int = 0
    def __post_init__(self):
        for b in (self.complete,self.own_exit,self.home_verified,self.intervention_free):
            if type(b) is not bool: raise DeploymentError('RESULT_INVALID')
        for n,limit in ((self.cards_total,48),(self.spells_consumed,48000),(self.remaining,48000)):
            if type(n) is not int or not 0<=n<=limit: raise DeploymentError('RESULT_INVALID')
        if self.complete and not (self.cards_total>0 and self.own_exit and self.home_verified and self.intervention_free and self.remaining==0):
            raise DeploymentError('RESULT_INVALID')
        if type(self.reason) is not str or re.fullmatch(r'[A-Z][A-Z_]{0,63}',self.reason) is None:
            raise DeploymentError('RESULT_INVALID')
    def autonomous(self, retirement_verified: bool) -> bool:
        return self.complete and retirement_verified is True
    def public_payload(self):
        return dict(schema=1,complete=self.complete,reason=self.reason,cards_total=self.cards_total,
                    spells_consumed=self.spells_consumed,own_exit=self.own_exit,
                    home_verified=self.home_verified,intervention_free=self.intervention_free,
                    remaining=self.remaining)


class DeploymentEngine:
    """One run, one complete inventory, no retry after uncertain consumption.

    `deliver` MUST invoke the supplied final proof immediately before down and
    unconditionally pair release. An independent lifecycle owner must enforce
    hard retirement if an OS/capture call fails to return. The engine never
    acquires process authority or rewrites lifecycle state.
    """
    def __init__(self, *, observe, deliver, release, live_gate, monotonic, sleep,
                 policy: Policy=Policy(), visit_deadline: float | None=None,
                 plan: CompiledDeploymentPlan | None=None):
        if type(policy) is not Policy or not all(callable(c) for c in (observe,deliver,release,live_gate,monotonic,sleep)):
            raise DeploymentError('PORT_INVALID')
        if plan is not None and type(plan) is not CompiledDeploymentPlan:
            raise DeploymentError('PLAN_INVALID')
        self.observe=observe; self.deliver=deliver; self.release=release
        self.live_gate=live_gate; self.clock=monotonic; self.sleep=sleep; self.policy=policy
        self.visit_deadline=visit_deadline; self.plan=plan
        self.used=False; self.sequence=0; self.captured=-1.0; self.view=None
        self.inventory=Inventory(policy.max_cards,policy.max_pages)
        self.cards: dict[str, Card]={}; self.holds: dict[str,float]={}
        self.ground_steps: dict[str, int]={}
        self.spells=0; self.actions=0; self.own_exit=False; self.home=False
        self.intervention_free=True; self.proof_fault=None; self.last=None

    def _gate(self, deadline):
        self.budget.check(deadline)
        try: allowed=self.live_gate()
        except BaseException: allowed=False
        if allowed is not True: raise DeploymentError('AUTHORIZATION_LOST')

    def _read(self, deadline) -> Observation:
        self._gate(deadline)
        try: o=self.observe()
        except BaseException: raise DeploymentError('OBSERVATION_UNAVAILABLE') from None
        now=self.budget.check(deadline)
        if type(o) is not Observation: raise DeploymentError('OBSERVATION_INVALID')
        if o.sequence<=self.sequence or o.captured_at<self.captured or not 0<=now-o.captured_at<=self.policy.observation_age:
            raise DeploymentError('OBSERVATION_STALE')
        self.sequence=o.sequence; self.captured=o.captured_at
        if o.intervention is not Intervention.CLEAR:
            self.intervention_free=False
            raise DeploymentError('INTERVENTION')
        if o.screen is Screen.BATTLE:
            if self.view is not None and o.view != self.view: raise DeploymentError('VIEW_CHANGED')
            self.view=o.view
        self.last=o
        return o

    @staticmethod
    def _visible(o: Observation, identity: str) -> Card:
        cards=[] if o.page is None else [c for c in o.page.cards if c.identity==identity]
        if len(cards)!=1: raise DeploymentError('CARD_IDENTITY_UNPROVED')
        cards[0].require_known()
        return cards[0]

    def _target(self, o, c) -> Target | None:
        kind={Spell.LIGHTNING:TargetKind.ENEMY_STRUCTURE, Spell.EARTHQUAKE:TargetKind.WALL_CLUSTER,
              Spell.RAGE:TargetKind.FRIENDLY_COHORT, Spell.HEAL:TargetKind.INJURED_COHORT}.get(c.spell)
        if c.kind is not CardKind.SPELL: kind=TargetKind.GROUND
        candidates=[t for t in o.targets if t.kind is kind and t.confidence>=self.policy.target_confidence]
        if not candidates: return None
        candidates=sorted(candidates,key=lambda t:(-t.confidence,t.point.x,t.point.y))
        index=self.ground_steps.get(c.identity,0)%len(candidates) if kind is TargetKind.GROUND else 0
        return candidates[index]

    def _proof(self, intent, before):
        try:
            o=self._read(intent.deadline)
            if o.screen is not before.screen or o.view!=before.view:
                raise DeploymentError('STATE_CHANGED')
            if self.plan is not None and o.screen is Screen.BATTLE:
                self._sync(o)
            if intent.card_id is not None:
                old=self._visible(before,intent.card_id); c=self._visible(o,intent.card_id)
                if c.signature!=old.signature or c.remaining!=old.remaining or c.state is not old.state:
                    raise DeploymentError('CARD_CHANGED')
                if intent.verb=='select':
                    if not c.point.near(intent.point): raise DeploymentError('TARGET_MOVED')
                else:
                    if intent.verb=='hold' and c.kind is not CardKind.TROOP:
                        raise DeploymentError('CARD_KIND_CONFLICT')
                    target=self._target(o,c)
                    if not c.selected or target is None or not target.point.near(intent.point):
                        raise DeploymentError('TARGET_UNPROVED')
            else:
                if not o.control(intent.verb).near(intent.point): raise DeploymentError('CONTROL_CHANGED')
            self._gate(intent.deadline)
            return True
        except DeploymentError as e:
            self.proof_fault=e.code
            return False
        except BaseException:
            self.proof_fault='PROOF_UNAVAILABLE'; return False

    def _send(self, intent, before):
        self._gate(intent.deadline)
        self.actions+=1
        if self.actions>self.policy.max_actions: raise DeploymentError('ACTION_LIMIT')
        self.proof_fault=None
        try: sent=self.deliver(intent,lambda:self._proof(intent,before))
        except BaseException: raise DeploymentError('INPUT_UNCERTAIN') from None
        if sent is not True: raise DeploymentError(self.proof_fault or 'INPUT_UNCERTAIN')
        self._gate(intent.deadline)

    def _await(self, screens, deadline, source=None):
        for _ in range(64):
            o=self._read(deadline)
            if o.screen in screens: return o
            if o.screen not in (source, Screen.UNKNOWN): raise DeploymentError('UNEXPECTED_TRANSITION')
            self.budget.wait(.05,deadline)
        raise DeploymentError('OBSERVATION_LIMIT')

    def _navigate(self, o):
        for source,verb,dest in ((Screen.HOME,'attack',Screen.MATCH),(Screen.MATCH,'find_match',Screen.ARMY),(Screen.ARMY,'army_attack',Screen.BATTLE)):
            if o.screen is source:
                if source is Screen.HOME and o.home_verified is not True: raise DeploymentError('HOME_UNPROVED')
                deadline=min(self.budget.deadline,self.budget.check()+10)
                self._send(Intent(verb,o.control(verb),Action.ATTACK_NAVIGATION,deadline=deadline),o)
                o=self._await({dest},deadline,source)
        if o.screen is not Screen.BATTLE: raise DeploymentError('BATTLE_UNPROVED')
        return o

    def _scan(self, o, deadline):
        if self.plan is not None:
            if not o.page.left_edge or not o.page.right_edge:
                raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
            observed=tuple(c.signature for c in o.page.cards)
            if len(observed)>self.plan.viewport_limit:
                raise DeploymentError('PLAN_VIEWPORT_OVERFLOW')
            if observed!=self.plan.signatures:
                if len(set(observed))!=len(observed): raise DeploymentError('ROSTER_DUPLICATE')
                if any(item not in self.plan.signatures for item in observed):
                    raise DeploymentError('ROSTER_ADDITIONAL')
                raise DeploymentError('ROSTER_INCOMPLETE')
            for current,expected in zip(o.page.cards,self.plan.quantities):
                current.require_known()
                if current.remaining!=expected or current.state is not CardState.AVAILABLE:
                    raise DeploymentError('INITIAL_QUANTITY_MISMATCH')
            self.inventory.add(o.page)
            self.cards={c.identity:c for c in o.page.cards}
            self.holds={key:0.0 for key in self.cards}
            return o
        raise DeploymentError('PLAN_REQUIRED')

    def _sync(self,o, permitted=None):
        if o.screen is not Screen.BATTLE or o.page is None: raise DeploymentError('BATTLE_UNPROVED')
        if self.plan is not None:
            if not o.page.left_edge or not o.page.right_edge:
                raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
            observed=tuple(c.signature for c in o.page.cards)
            if observed!=self.plan.signatures:
                if len(set(observed))!=len(observed): raise DeploymentError('ROSTER_DUPLICATE')
                if any(item not in self.plan.signatures for item in observed):
                    raise DeploymentError('ROSTER_ADDITIONAL')
                raise DeploymentError('ROSTER_INCOMPLETE')
        self.inventory.locate(o.page)
        for c in o.page.cards:
            c.require_known(); old=self.cards[c.identity]
            if c.signature!=old.signature: raise DeploymentError('CARD_CHANGED')
            if c.identity != permitted and (c.remaining!=old.remaining or c.state is not old.state):
                raise DeploymentError('UNEXPLAINED_CARD_CHANGE')
            self.cards[c.identity]=c

    def _bring(self, identity, o, deadline):
        if self.plan is not None:
            self._sync(o)
            if not any(c.identity==identity for c in o.page.cards):
                raise DeploymentError('ROSTER_INCOMPLETE')
            return o
        raise DeploymentError('PLAN_REQUIRED')

    def _consume(self,c,o,deadline):
        if not c.selected:
            self._send(Intent('select',c.point,Action.TROOP_DEPLOYMENT,c.identity,deadline=deadline),o)
            select_deadline=min(deadline,self.budget.check()+1)
            for _ in range(24):
                o=self._read(select_deadline); self._sync(o)
                c=self._visible(o,c.identity)
                if c.selected: break
                self.budget.wait(.04,select_deadline)
            else: raise DeploymentError('SELECTION_UNPROVED')
        target=self._target(o,c)
        if target is None: return o,False
        verb='hold' if c.kind is CardKind.TROOP else 'tap'
        duration=0.0
        if verb=='hold':
            remaining=self.policy.card_hold_ceiling-self.holds[c.identity]
            duration=min(self.policy.troop_hold_seconds,remaining,deadline-self.budget.check())
            if duration<=0: raise DeploymentError('CARD_HOLD_LIMIT')
        before_count=c.remaining
        delivery_started=self.budget.check(deadline)
        self._send(Intent(verb,target.point,Action.TROOP_DEPLOYMENT,c.identity,duration,deadline=deadline),o)
        if verb=='hold':
            self.holds[c.identity]+=max(duration,self.budget.check(deadline)-delivery_started)
        ack_deadline=min(deadline,self.budget.check()+self.policy.acknowledgement_seconds)
        for _ in range(40):
            o=self._read(ack_deadline)
            if o.screen is not Screen.BATTLE: raise DeploymentError('EXTERNAL_OR_EARLY_RESULT')
            current=self._visible(o,c.identity)
            if current.remaining < before_count:
                delta=before_count-current.remaining
                if c.kind is not CardKind.TROOP and delta!=1: raise DeploymentError('COUNT_DELTA_UNCERTAIN')
                # A transient badge animation is not a durable consumption fact.
                self.budget.wait(.04,ack_deadline)
                confirmed=self._read(ack_deadline)
                check=self._visible(confirmed,c.identity)
                if check.remaining!=current.remaining or check.state is not current.state:
                    raise DeploymentError('COUNT_DELTA_UNCERTAIN')
                self._sync(confirmed,permitted=c.identity)
                o=confirmed
                if c.kind is not CardKind.SPELL:
                    self.ground_steps[c.identity]=self.ground_steps.get(c.identity,0)+1
                if c.kind is CardKind.SPELL: self.spells+=delta
                return o,True
            if current.remaining != before_count or current.state is not c.state:
                raise DeploymentError('COUNT_DELTA_UNCERTAIN')
            self.budget.wait(.05,ack_deadline)
        raise DeploymentError('CONSUMPTION_UNPROVED')

    def _deploy(self,o,deadline):
        idle_observations=0
        while True:
            self._gate(deadline); self._sync(o)
            pending=[c for c in self.cards.values() if not c.exhausted]
            if not pending: break
            # Ready support spells are interleaved before the next troop burst.
            pending.sort(key=lambda c:(0 if c.kind is CardKind.SPELL else 1, self.inventory.identities.index(c.identity)))
            progress=False
            for candidate in pending:
                o=self._bring(candidate.identity,o,deadline)
                c=self._visible(o,candidate.identity)
                if c.exhausted: continue
                if c.kind is CardKind.SPELL and self._target(o,c) is None:continue
                o,progress=self._consume(c,o,deadline)
                if progress: break
            if not progress:
                idle_observations+=1
                if idle_observations>20:raise DeploymentError('TARGET_UNPROVED')
                self.budget.wait(.1,deadline)
            else:idle_observations=0
            o=self._read(deadline)
        # Final complete rescan. A sealed single-page plan requires one fresh
        # whole-roster equality proof and never gains scrolling authority.
        if self.plan is not None:
            o=self._read(deadline)
            self._sync(o)
            if any(not c.exhausted for c in o.page.cards):
                raise DeploymentError('DEPLETION_UNPROVED')
            return o
        raise DeploymentError('PLAN_REQUIRED')

    def _exit(self,o):
        deadline=min(self.budget.deadline,self.budget.check()+self.policy.exit_seconds)
        self._send(Intent('end_battle',o.control('end_battle'),Action.RETURN_HOME,deadline=deadline),o)
        self.own_exit=True
        o=self._await({Screen.END_CONFIRM,Screen.RESULT},min(deadline,self.budget.check()+5),Screen.BATTLE)
        if o.screen is Screen.END_CONFIRM:
            self._send(Intent('confirm_end',o.control('confirm_end'),Action.RETURN_HOME,deadline=deadline),o)
            o=self._await({Screen.RESULT},min(deadline,self.budget.check()+5),Screen.END_CONFIRM)
        self._send(Intent('return_home',o.control('return_home'),Action.RETURN_HOME,deadline=deadline),o)
        o=self._await({Screen.HOME},min(deadline,self.budget.check()+10),Screen.RESULT)
        if not o.home_verified: raise DeploymentError('HOME_UNPROVED')
        self.home=True

    def run(self) -> DeploymentResult:
        if self.used: raise DeploymentError('EXECUTOR_ALREADY_USED')
        self.used=True; reason='DEPLOYMENT_FAILED'; complete=False
        try:
            start=finite(self.clock())
            deadline=start+self.policy.visit_seconds if self.visit_deadline is None else min(finite(self.visit_deadline),start+self.policy.visit_seconds)
            self.budget=Budget(self.clock,self.sleep,deadline)
            o=self._navigate(self._read(deadline))
            deploy_deadline=min(deadline-self.policy.exit_seconds,o.captured_at+self.policy.deployment_seconds)
            self.budget.check(deploy_deadline)
            o=self._scan(o,deploy_deadline)
            if not any(c.remaining for c in self.cards.values()): raise DeploymentError('EMPTY_ARMY')
            o=self._deploy(o,deploy_deadline)
            self._exit(o)
            complete=True; reason='AUTONOMOUS_DEPLOYMENT_COMPLETE'
        except DeploymentError as e: reason=e.code
        except BaseException: reason='DEPLOYMENT_UNAVAILABLE'
        finally:
            try: released=self.release()
            except BaseException: released=False
            if released is not True: complete=False; reason='RELEASE_UNPROVED'
        remaining=sum(c.remaining or 0 for c in self.cards.values())
        return DeploymentResult(complete,reason,len(self.cards),self.spells,
            self.own_exit,self.home,self.intervention_free,remaining)
