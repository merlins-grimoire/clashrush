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
import threading
import weakref
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, NamedTuple


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
    def validated_digest(self):
        """Recompute and validate every exact typed authority-bearing field."""
        return type(self)(self.roster,self.viewport_limit,self.digest).digest
    @property
    def signatures(self):
        self.validated_digest()
        return tuple(entry[:3] for entry in self.roster)
    @property
    def quantities(self):
        self.validated_digest()
        return tuple(entry[3] for entry in self.roster)


def _copy_plan(plan):
    if type(plan) is not CompiledDeploymentPlan:
        raise DeploymentError('PLAN_INVALID')
    seal=CompiledDeploymentPlan.validated_digest(plan)
    roster=tuple(tuple(entry) for entry in plan.roster)
    return CompiledDeploymentPlan(roster,plan.viewport_limit,seal)


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
        deployment={'select','hold','tap'}
        navigation={'attack','find_match','army_attack'}
        returning={'end_battle','confirm_end','return_home'}
        if (type(self.verb) is not str or self.verb not in deployment|navigation|returning
                or type(self.point) is not Point or type(self.action) is not Action
                or self.destination is not None):
            raise DeploymentError('INTENT_INVALID')
        finite(self.point.x,0,1); finite(self.point.y,0,1)
        finite(self.duration, 0, .5); finite(self.deadline)
        if self.verb in deployment:
            if (type(self.card_id) is not str
                    or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}',self.card_id) is None
                    or self.action is not Action.TROOP_DEPLOYMENT):
                raise DeploymentError('INTENT_INVALID')
        elif self.card_id is not None:
            raise DeploymentError('INTENT_INVALID')
        expected=(Action.ATTACK_NAVIGATION if self.verb in navigation else
                  Action.RETURN_HOME if self.verb in returning else Action.TROOP_DEPLOYMENT)
        if self.action is not expected:
            raise DeploymentError('INTENT_INVALID')
        if (self.verb=='hold')!=(self.duration>0):
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



class _PolicyValues(NamedTuple):
    deployment_seconds: float
    visit_seconds: float
    exit_seconds: float
    troop_hold_seconds: float
    card_hold_ceiling: float
    observation_age: float
    acknowledgement_seconds: float
    max_pages: int
    max_cards: int
    max_actions: int
    target_confidence: float


def _copy_policy(policy):
    if type(policy) is not Policy:
        raise DeploymentError('POLICY_INVALID')
    values = _PolicyValues(
        finite(policy.deployment_seconds, .01, 60),
        finite(policy.visit_seconds, 1, 600),
        finite(policy.exit_seconds, 1, 30),
        finite(policy.troop_hold_seconds, .01, .5),
        finite(policy.card_hold_ceiling, .01, 25),
        finite(policy.observation_age, .01, .75),
        finite(policy.acknowledgement_seconds, .1, 1.5),
        policy.max_pages, policy.max_cards, policy.max_actions,
        finite(policy.target_confidence, .8, 1),
    )
    for value, maximum in ((values.max_pages, 16), (values.max_cards, 48),
                           (values.max_actions, 1024)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise DeploymentError('POLICY_INVALID')
    return values


def _copy_observation(value):
    if type(value) is not Observation:
        raise DeploymentError('OBSERVATION_INVALID')
    page = None
    if value.page is not None:
        page = Page(tuple(Card(
            item.identity, CardKind(item.kind.value), Spell(item.spell.value),
            item.remaining, CardState(item.state.value),
            Point(item.point.x, item.point.y), item.selected,
        ) for item in value.page.cards), value.page.left_edge, value.page.right_edge)
    return Observation(
        value.sequence, value.captured_at, value.view, Screen(value.screen.value), page,
        tuple(Target(TargetKind(item.kind.value), Point(item.point.x, item.point.y),
                     item.confidence) for item in value.targets),
        tuple((name, Point(point.x, point.y)) for name, point in value.controls),
        Intervention(value.intervention.value), value.home_verified,
    )


class _RunCore:
    __slots__ = (
        'plan', 'viewport_limit', 'digest', 'policy', 'ports', 'visit_limit',
        'deadline', 'last_clock', 'sequence', 'captured', 'view', 'inventory',
        'cards', 'holds', 'ground_steps', 'spells', 'actions', 'own_exit',
        'home', 'intervention_free', 'proof_fault', 'last_observation',
        'pending', 'possible_input', 'released', 'reason', 'complete',
    )
    def __init__(self, plan, policy, ports, visit_limit):
        self.plan = (() if plan is None else tuple(
            (identity, kind.value, spell.value, quantity)
            for identity, kind, spell, quantity in plan.roster))
        self.viewport_limit = 0 if plan is None else int(plan.viewport_limit)
        self.digest = '' if plan is None else str(plan.digest)
        self.policy = policy
        self.ports = ports
        self.visit_limit = visit_limit
        self.deadline = None
        self.last_clock = None
        self.sequence = 0
        self.captured = -1.0
        self.view = None
        self.inventory = Inventory(policy.max_cards, policy.max_pages)
        self.cards = {}
        self.holds = {}
        self.ground_steps = {}
        self.spells = 0
        self.actions = 0
        self.own_exit = False
        self.home = False
        self.intervention_free = True
        self.proof_fault = None
        self.last_observation = None
        self.pending = None
        self.possible_input = False
        self.released = None
        self.reason = 'DEPLOYMENT_FAILED'
        self.complete = False


class _RunEntry:
    __slots__ = ('facade', 'core', 'lock', 'claimed')
    def __init__(self, facade, core):
        self.facade = facade
        self.core = core
        self.lock = threading.Lock()
        self.claimed = False


class _Proof:
    __slots__ = ()
    def __call__(self):
        return _consume_proof(self)


_RUNS = {}
_USED = {}
_PROOFS = {}
_RUNS_LOCK = threading.Lock()
_PROOFS_LOCK = threading.Lock()


def _used_locked(facade):
    key = id(facade)
    reference = _USED.get(key)
    if reference is None:
        return False
    target = reference()
    if target is facade:
        return True
    if _USED.get(key) is reference:
        _USED.pop(key, None)
    return False


def _retire_entry(facade, entry):
    key = id(facade)
    def discard(reference, identity=key):
        with _RUNS_LOCK:
            if _USED.get(identity) is reference:
                _USED.pop(identity, None)
    reference = weakref.ref(facade, discard)
    with _RUNS_LOCK:
        if _RUNS.get(key) is entry:
            _RUNS.pop(key)
        _USED[key] = reference
    entry.core = None
    entry.facade = None


def _entry_for(facade):
    with _RUNS_LOCK:
        entry = _RUNS.get(id(facade))
        used = _used_locked(facade)
    if used:
        raise DeploymentError('EXECUTOR_ALREADY_USED')
    if entry is None or entry.facade is not facade:
        raise DeploymentError('PLAN_REQUIRED')
    return entry


def _public_plan(facade):
    entry = _entry_for(facade)
    core = entry.core
    return CompiledDeploymentPlan(tuple(
        (identity, CardKind(kind), Spell(spell), quantity)
        for identity, kind, spell, quantity in core.plan
    ), core.viewport_limit, core.digest)


def _now(core, deadline=None):
    try:
        value = core.ports[4]()
    except BaseException:
        raise DeploymentError('CLOCK_UNAVAILABLE') from None
    now = finite(value)
    if core.last_clock is not None and now < core.last_clock:
        raise DeploymentError('CLOCK_REGRESSION')
    core.last_clock = now
    effective = core.deadline
    if deadline is not None:
        supplied = finite(deadline)
        effective = supplied if effective is None else min(effective, supplied)
    if effective is not None and now >= effective:
        raise DeploymentError('DEADLINE')
    return now


def _gate(core, deadline):
    _now(core, deadline)
    try:
        allowed = core.ports[3]()
    except BaseException:
        allowed = False
    _now(core, deadline)
    if allowed is not True:
        raise DeploymentError('AUTHORIZATION_LOST')


def _wait(core, seconds, deadline):
    now = _now(core, deadline)
    end = min(core.deadline, finite(deadline))
    delay = min(finite(seconds, 0, 5), max(0.0, end - now))
    try:
        core.ports[5](delay)
    except BaseException:
        raise DeploymentError('WAIT_UNAVAILABLE') from None
    _now(core, deadline)


def _read(core, deadline):
    _gate(core, deadline)
    try:
        raw = core.ports[0]()
    except BaseException:
        raise DeploymentError('OBSERVATION_UNAVAILABLE') from None
    try:
        observation = _copy_observation(raw)
    except DeploymentError:
        raise
    except BaseException:
        raise DeploymentError('OBSERVATION_INVALID') from None
    now = _now(core, deadline)
    if (observation.sequence <= core.sequence
            or observation.captured_at < core.captured
            or not 0 <= now - observation.captured_at <= core.policy.observation_age):
        raise DeploymentError('OBSERVATION_STALE')
    core.sequence = observation.sequence
    core.captured = observation.captured_at
    if observation.intervention is not Intervention.CLEAR:
        core.intervention_free = False
        raise DeploymentError('INTERVENTION')
    if observation.screen is Screen.BATTLE:
        if core.view is not None and observation.view != core.view:
            raise DeploymentError('VIEW_CHANGED')
        core.view = observation.view
    core.last_observation = observation
    return observation


def _visible(observation, identity):
    cards = [] if observation.page is None else [
        item for item in observation.page.cards if item.identity == identity]
    if len(cards) != 1:
        raise DeploymentError('CARD_IDENTITY_UNPROVED')
    cards[0].require_known()
    return cards[0]


def _signatures(core):
    return tuple((identity, CardKind(kind), Spell(spell))
                 for identity, kind, spell, _quantity in core.plan)


def _quantities(core):
    return tuple(quantity for _identity, _kind, _spell, quantity in core.plan)


def _target(core, observation, card_value):
    kind = {
        Spell.LIGHTNING: TargetKind.ENEMY_STRUCTURE,
        Spell.EARTHQUAKE: TargetKind.WALL_CLUSTER,
        Spell.RAGE: TargetKind.FRIENDLY_COHORT,
        Spell.HEAL: TargetKind.INJURED_COHORT,
    }.get(card_value.spell)
    if card_value.kind is not CardKind.SPELL:
        kind = TargetKind.GROUND
    candidates = [item for item in observation.targets
                  if item.kind is kind and item.confidence >= core.policy.target_confidence]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item.confidence, item.point.x, item.point.y))
    index = (core.ground_steps.get(card_value.identity, 0) % len(candidates)
             if kind is TargetKind.GROUND else 0)
    return candidates[index]


def _sync(core, observation, permitted=None):
    if observation.screen is not Screen.BATTLE or observation.page is None:
        raise DeploymentError('BATTLE_UNPROVED')
    if not observation.page.left_edge or not observation.page.right_edge:
        raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
    observed = tuple(item.signature for item in observation.page.cards)
    expected = _signatures(core)
    if observed != expected:
        if len(set(observed)) != len(observed):
            raise DeploymentError('ROSTER_DUPLICATE')
        if any(item not in expected for item in observed):
            raise DeploymentError('ROSTER_ADDITIONAL')
        raise DeploymentError('ROSTER_INCOMPLETE')
    core.inventory.locate(observation.page)
    reconciled = {}
    for item in observation.page.cards:
        item.require_known()
        old = core.cards[item.identity]
        if item.signature != old.signature:
            raise DeploymentError('CARD_CHANGED')
        if item.identity != permitted and (item.remaining != old.remaining or item.state is not old.state):
            raise DeploymentError('UNEXPLAINED_CARD_CHANGE')
        reconciled[item.identity] = item
    core.cards = reconciled


def _proof_check(core, intent, before):
    observation = _read(core, intent.deadline)
    if observation.screen is not before.screen or observation.view != before.view:
        raise DeploymentError('STATE_CHANGED')
    if observation.screen is Screen.BATTLE:
        _sync(core, observation)
    if intent.card_id is not None:
        old = _visible(before, intent.card_id)
        current = _visible(observation, intent.card_id)
        if (current.signature != old.signature or current.remaining != old.remaining
                or current.state is not old.state):
            raise DeploymentError('CARD_CHANGED')
        if intent.verb == 'select':
            if not current.point.near(intent.point):
                raise DeploymentError('TARGET_MOVED')
        else:
            if intent.verb == 'hold' and current.kind is not CardKind.TROOP:
                raise DeploymentError('CARD_KIND_CONFLICT')
            target = _target(core, observation, current)
            if not current.selected or target is None or not target.point.near(intent.point):
                raise DeploymentError('TARGET_UNPROVED')
    elif not observation.control(intent.verb).near(intent.point):
        raise DeploymentError('CONTROL_CHANGED')
    _gate(core, intent.deadline)
    return True


def _consume_proof(token):
    with _PROOFS_LOCK:
        record = _PROOFS.pop(id(token), None)
    if record is None or record[0] is not token:
        return False
    core, intent, before = record[1:]
    if core.pending != (intent.verb, intent.card_id, intent.point.x, intent.point.y,
                        intent.duration, intent.deadline, intent.action.value):
        core.proof_fault = 'PROOF_INVALID'
        return False
    try:
        return _proof_check(core, intent, before)
    except DeploymentError as error:
        core.proof_fault = error.code
        return False
    except BaseException:
        core.proof_fault = 'PROOF_UNAVAILABLE'
        return False


def _send(core, intent, before):
    _gate(core, intent.deadline)
    core.actions += 1
    if core.actions > core.policy.max_actions:
        raise DeploymentError('ACTION_LIMIT')
    private_intent = Intent(
        intent.verb, Point(intent.point.x, intent.point.y), Action(intent.action.value),
        intent.card_id, intent.duration, None, intent.deadline,
    )
    core.pending = (private_intent.verb, private_intent.card_id,
                    private_intent.point.x, private_intent.point.y,
                    private_intent.duration, private_intent.deadline,
                    private_intent.action.value)
    core.proof_fault = None
    token = _Proof()
    with _PROOFS_LOCK:
        _PROOFS[id(token)] = (token, core, private_intent, before)
    try:
        sent = core.ports[1](private_intent, token)
        core.possible_input = True
    except BaseException:
        core.possible_input = True
        raise DeploymentError('INPUT_UNCERTAIN') from None
    finally:
        with _PROOFS_LOCK:
            _PROOFS.pop(id(token), None)
        core.pending = None
    _now(core, intent.deadline)
    if sent is not True:
        raise DeploymentError(core.proof_fault or 'INPUT_UNCERTAIN')
    _gate(core, intent.deadline)


def _await(core, screens, deadline, source=None):
    for _ in range(64):
        observation = _read(core, deadline)
        if observation.screen in screens:
            return observation
        if observation.screen not in (source, Screen.UNKNOWN):
            raise DeploymentError('UNEXPECTED_TRANSITION')
        _wait(core, .05, deadline)
    raise DeploymentError('OBSERVATION_LIMIT')


def _navigate(core, observation):
    for source, verb, destination in (
        (Screen.HOME, 'attack', Screen.MATCH),
        (Screen.MATCH, 'find_match', Screen.ARMY),
        (Screen.ARMY, 'army_attack', Screen.BATTLE),
    ):
        if observation.screen is source:
            if source is Screen.HOME and observation.home_verified is not True:
                raise DeploymentError('HOME_UNPROVED')
            deadline = min(core.deadline, _now(core) + 10)
            _send(core, Intent(verb, observation.control(verb),
                               Action.ATTACK_NAVIGATION, deadline=deadline), observation)
            observation = _await(core, {destination}, deadline, source)
    if observation.screen is not Screen.BATTLE:
        raise DeploymentError('BATTLE_UNPROVED')
    return observation


def _scan(core, observation):
    if not observation.page.left_edge or not observation.page.right_edge:
        raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
    observed = tuple(item.signature for item in observation.page.cards)
    expected = _signatures(core)
    if len(observed) > core.viewport_limit:
        raise DeploymentError('PLAN_VIEWPORT_OVERFLOW')
    if observed != expected:
        if len(set(observed)) != len(observed):
            raise DeploymentError('ROSTER_DUPLICATE')
        if any(item not in expected for item in observed):
            raise DeploymentError('ROSTER_ADDITIONAL')
        raise DeploymentError('ROSTER_INCOMPLETE')
    for current, quantity in zip(observation.page.cards, _quantities(core)):
        current.require_known()
        if current.remaining != quantity or current.state is not CardState.AVAILABLE:
            raise DeploymentError('INITIAL_QUANTITY_MISMATCH')
    core.inventory.add(observation.page)
    core.cards = {item.identity: item for item in observation.page.cards}
    core.holds = {identity: 0.0 for identity in core.cards}
    return observation


def _consume(core, card_value, observation, deadline):
    if not card_value.selected:
        _send(core, Intent('select', card_value.point, Action.TROOP_DEPLOYMENT,
                           card_value.identity, deadline=deadline), observation)
        select_deadline = min(deadline, _now(core) + 1)
        for _ in range(24):
            observation = _read(core, select_deadline)
            _sync(core, observation)
            card_value = _visible(observation, card_value.identity)
            if card_value.selected:
                break
            _wait(core, .04, select_deadline)
        else:
            raise DeploymentError('SELECTION_UNPROVED')
    target = _target(core, observation, card_value)
    if target is None:
        return observation, False
    verb = 'hold' if card_value.kind is CardKind.TROOP else 'tap'
    duration = 0.0
    if verb == 'hold':
        remaining = core.policy.card_hold_ceiling - core.holds[card_value.identity]
        duration = min(core.policy.troop_hold_seconds, remaining,
                       deadline - _now(core, deadline))
        if duration <= 0:
            raise DeploymentError('CARD_HOLD_LIMIT')
    before_count = card_value.remaining
    delivery_started = _now(core, deadline)
    _send(core, Intent(verb, target.point, Action.TROOP_DEPLOYMENT,
                       card_value.identity, duration, deadline=deadline), observation)
    if verb == 'hold':
        core.holds[card_value.identity] += max(
            duration, _now(core, deadline) - delivery_started)
        if core.holds[card_value.identity] > core.policy.card_hold_ceiling:
            raise DeploymentError('CARD_HOLD_LIMIT')
    ack_deadline = min(deadline, _now(core) + core.policy.acknowledgement_seconds)
    for _ in range(40):
        observation = _read(core, ack_deadline)
        if observation.screen is not Screen.BATTLE:
            raise DeploymentError('EXTERNAL_OR_EARLY_RESULT')
        current = _visible(observation, card_value.identity)
        if current.remaining < before_count:
            delta = before_count - current.remaining
            if card_value.kind is not CardKind.TROOP and delta != 1:
                raise DeploymentError('COUNT_DELTA_UNCERTAIN')
            _wait(core, .04, ack_deadline)
            confirmed = _read(core, ack_deadline)
            check = _visible(confirmed, card_value.identity)
            if check.remaining != current.remaining or check.state is not current.state:
                raise DeploymentError('COUNT_DELTA_UNCERTAIN')
            _sync(core, confirmed, permitted=card_value.identity)
            if card_value.kind is not CardKind.SPELL:
                core.ground_steps[card_value.identity] = core.ground_steps.get(card_value.identity, 0) + 1
            else:
                core.spells += delta
            return confirmed, True
        if current.remaining != before_count or current.state is not card_value.state:
            raise DeploymentError('COUNT_DELTA_UNCERTAIN')
        _wait(core, .05, ack_deadline)
    raise DeploymentError('CONSUMPTION_UNPROVED')


def _deploy(core, observation, deadline):
    idle = 0
    while True:
        _gate(core, deadline)
        _sync(core, observation)
        pending = [item for item in core.cards.values() if not item.exhausted]
        if not pending:
            break
        pending.sort(key=lambda item: (
            0 if item.kind is CardKind.SPELL else 1,
            core.inventory.identities.index(item.identity)))
        progress = False
        for candidate in pending:
            _sync(core, observation)
            card_value = _visible(observation, candidate.identity)
            if card_value.exhausted:
                continue
            if card_value.kind is CardKind.SPELL and _target(core, observation, card_value) is None:
                continue
            observation, progress = _consume(core, card_value, observation, deadline)
            if progress:
                break
        if not progress:
            idle += 1
            if idle > 20:
                raise DeploymentError('TARGET_UNPROVED')
            _wait(core, .1, deadline)
        else:
            idle = 0
        observation = _read(core, deadline)
    observation = _read(core, deadline)
    _sync(core, observation)
    if any(not item.exhausted for item in observation.page.cards):
        raise DeploymentError('DEPLETION_UNPROVED')
    return observation


def _exit(core, observation):
    deadline = min(core.deadline, _now(core) + core.policy.exit_seconds)
    _send(core, Intent('end_battle', observation.control('end_battle'),
                       Action.RETURN_HOME, deadline=deadline), observation)
    core.own_exit = True
    observation = _await(core, {Screen.END_CONFIRM, Screen.RESULT},
                         min(deadline, _now(core) + 5), Screen.BATTLE)
    if observation.screen is Screen.END_CONFIRM:
        _send(core, Intent('confirm_end', observation.control('confirm_end'),
                           Action.RETURN_HOME, deadline=deadline), observation)
        observation = _await(core, {Screen.RESULT},
                             min(deadline, _now(core) + 5), Screen.END_CONFIRM)
    _send(core, Intent('return_home', observation.control('return_home'),
                       Action.RETURN_HOME, deadline=deadline), observation)
    observation = _await(core, {Screen.HOME},
                         min(deadline, _now(core) + 10), Screen.RESULT)
    if not observation.home_verified:
        raise DeploymentError('HOME_UNPROVED')
    core.home = True


def _snapshot(core, complete, reason):
    remaining = sum(item.remaining or 0 for item in core.cards.values())
    cards_total = len(core.plan) if core.cards else 0
    return DeploymentResult(
        complete, reason, cards_total, core.spells, core.own_exit, core.home,
        core.intervention_free, remaining,
    )


def _execute_registered_run(facade):
    entry = _entry_for(facade)
    with entry.lock:
        if entry.claimed:
            raise DeploymentError('EXECUTOR_ALREADY_USED')
        entry.claimed = True
    core = entry.core
    complete = False
    reason = 'DEPLOYMENT_FAILED'
    try:
        if not core.plan:
            raise DeploymentError('PLAN_REQUIRED')
        start = _now(core)
        core.deadline = min(
            start + core.policy.visit_seconds,
            core.visit_limit if core.visit_limit is not None else start + core.policy.visit_seconds,
        )
        _now(core, core.deadline)
        observation = _navigate(core, _read(core, core.deadline))
        deploy_deadline = min(
            core.deadline - core.policy.exit_seconds,
            observation.captured_at + core.policy.deployment_seconds,
        )
        _now(core, deploy_deadline)
        observation = _scan(core, observation)
        if not any(item.remaining for item in core.cards.values()):
            raise DeploymentError('EMPTY_ARMY')
        observation = _deploy(core, observation, deploy_deadline)
        _exit(core, observation)
        _now(core, core.deadline)
        complete = True
        reason = 'AUTONOMOUS_DEPLOYMENT_COMPLETE'
    except DeploymentError as error:
        reason = error.code
    except BaseException:
        reason = 'DEPLOYMENT_UNAVAILABLE'
    finally:
        cleanup_time_fault = None
        if core.last_clock is not None:
            try:
                _now(core, core.deadline)
            except DeploymentError as error:
                cleanup_time_fault = error.code
        try:
            released = core.ports[2]() is True
        except BaseException:
            released = False
        core.released = released
        late = cleanup_time_fault is not None
        if core.last_clock is not None:
            try:
                _now(core, core.deadline)
            except DeploymentError as error:
                late = True
                cleanup_time_fault = cleanup_time_fault or error.code
        if not released:
            complete = False
            reason = 'RELEASE_UNPROVED'
        elif late and complete:
            complete = False
            reason = cleanup_time_fault
    if complete and not (core.own_exit and core.home and core.intervention_free):
        complete = False
        reason = 'DEPLOYMENT_FAILED'
    result = _snapshot(core, complete, reason)
    public_result = DeploymentResult(
        result.complete, result.reason, result.cards_total, result.spells_consumed,
        result.own_exit, result.home_verified, result.intervention_free,
        result.remaining,
    )
    _retire_entry(facade, entry)
    return public_result


class DeploymentEngine:
    """Inert one-use façade; all live run authority is service-owned."""
    __slots__ = ('__dict__', '__weakref__')

    @property
    def plan(self):
        return _public_plan(self)

    @plan.setter
    def plan(self, _value):
        raise TypeError('sealed deployment plan')

    @property
    def observe(self):
        return 'DETACHED'

    @observe.setter
    def observe(self, value):
        # Legacy pre-run test/setup adapter. Once execution is claimed, callback
        # replacement cannot reach or replace the private original port.
        if not callable(value):
            raise DeploymentError('PORT_INVALID')
        entry = _entry_for(self)
        with entry.lock:
            if entry.claimed or entry.core is None:
                raise TypeError('claimed deployment port')
            ports = entry.core.ports
            entry.core.ports = (value,) + ports[1:]

    def __init__(self, *, observe, deliver, release, live_gate, monotonic, sleep,
                 policy: Policy=Policy(), visit_deadline: float | None=None,
                 plan: CompiledDeploymentPlan | None=None):
        if not all(callable(item) for item in
                   (observe, deliver, release, live_gate, monotonic, sleep)):
            raise DeploymentError('PORT_INVALID')
        owned_plan = None if plan is None else _copy_plan(plan)
        owned_policy = _copy_policy(policy)
        limit = None if visit_deadline is None else finite(visit_deadline)
        core = _RunCore(owned_plan, owned_policy,
                        (observe, deliver, release, live_gate, monotonic, sleep), limit)
        entry = _RunEntry(self, core)
        with _RUNS_LOCK:
            if id(self) in _RUNS or _used_locked(self):
                raise DeploymentError('EXECUTOR_ALREADY_USED')
            _RUNS[id(self)] = entry

    def run(self) -> DeploymentResult:
        return _execute_registered_run(self)
