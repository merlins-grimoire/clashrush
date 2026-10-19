"""Bounded, typed army deployment and natural-result return.

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
    CLAN = 'CLAN'  # Semantic support slot; profile icons supply its visual projection.
    SPELL = 'SPELL'
    UNKNOWN = 'UNKNOWN'


class Spell(StrEnum):
    NONE = 'NONE'
    LIGHTNING = 'LIGHTNING'
    EARTHQUAKE = 'EARTHQUAKE'
    RAGE = 'RAGE'
    HEAL = 'HEAL'
    UNKNOWN = 'UNKNOWN'


class DeploymentDisposition(StrEnum):
    DEPLOY = 'DEPLOY'
    RETAIN = 'RETAIN'


def _source_metadata(categories, context):
    if type(categories) is not tuple or type(context) is not tuple:
        raise DeploymentError('SOURCE_CONTEXT_INVALID')
    if categories:
        if (any(type(v) is not tuple or len(v)!=2 or any(type(s) is not str for s in v)
                for v in categories)
                or tuple(v[0] for v in categories)!=('ordinary','hero','spell','clan','siege')
                or any(v[1] not in ('POPULATED','EMPTY','LOCKED') for v in categories)):
            raise DeploymentError('SOURCE_CONTEXT_INVALID')
    if context:
        if (len(context)!=5 or any(type(v) is not str for v in context)
                or re.fullmatch(r'[0-9a-f]{64}',context[0]) is None
                or re.fullmatch(r'[0-9a-f]{40}',context[1]) is None
                or re.fullmatch(r'[0-9a-f]{64}',context[2]) is None
                or re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',context[3]) is None
                or context[4]!='REGULAR_MULTIPLAYER'):
            raise DeploymentError('SOURCE_CONTEXT_INVALID')


@dataclass(frozen=True, slots=True)
class SourceInventoryEntry:
    identity: str
    category: str
    kind: CardKind
    spell: Spell
    quantity: int
    state: CardState | None = None
    provenance: str = 'HOME_SOURCE'
    def __post_init__(self):
        if (type(self.identity) is not str or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}', self.identity) is None
                or type(self.category) is not str or re.fullmatch(r'[a-z][a-z0-9_.:-]{0,31}', self.category) is None
                or type(self.kind) is not CardKind or type(self.spell) is not Spell
                or self.kind is CardKind.UNKNOWN or self.spell is Spell.UNKNOWN
                or (self.kind is CardKind.SPELL) == (self.spell is Spell.NONE)
                or type(self.quantity) is not int or not 1 <= self.quantity <= 999
                or (self.state is not None and type(self.state) is not CardState)
                or type(self.provenance) is not str or not re.fullmatch(r'[A-Z][A-Z0-9_:-]{0,63}', self.provenance)):
            raise DeploymentError('SOURCE_INVENTORY_INVALID')


@dataclass(frozen=True, slots=True)
class CompiledDeploymentPlan:
    """Immutable finite single-page roster and exact initial quantities."""
    roster: tuple[tuple[str, CardKind, Spell, int], ...]
    viewport_limit: int
    digest: str = ''
    dispositions: tuple[tuple[str, DeploymentDisposition], ...] = ()
    source_inventory: tuple[SourceInventoryEntry, ...] = ()
    source_categories: tuple[tuple[str, str], ...] = ()
    source_context: tuple[str, ...] = ()
    match_entry_cost: int | None = None
    allow_empty_support: bool = False
    def __post_init__(self):
        if (type(self.allow_empty_support) is not bool
                or self.allow_empty_support and (not self.source_categories or not self.source_context)):
            raise DeploymentError('SOURCE_CONTEXT_INVALID')
        if self.match_entry_cost is not None and (type(self.match_entry_cost) is not int or not 1 <= self.match_entry_cost <= 1000000):
            raise DeploymentError('MATCH_COST_INVALID')
        _source_metadata(self.source_categories,self.source_context)
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
        if type(self.dispositions) is not tuple:
            raise DeploymentError('PLAN_INVALID')
        if self.dispositions:
            disposition_by_id = {}
            for item in self.dispositions:
                if (type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                        or type(item[1]) is not DeploymentDisposition
                        or item[1] is not DeploymentDisposition.DEPLOY):
                    raise DeploymentError('PLAN_INVALID')
                disposition_by_id[item[0]] = item[1]
            if (len(disposition_by_id)!=len(self.dispositions)
                    or set(disposition_by_id) != {entry[0] for entry in self.roster}):
                raise DeploymentError('PLAN_INVALID')
            dispositions = tuple((entry[0], disposition_by_id[entry[0]].value) for entry in self.roster)
        else:
            dispositions = ()
        if type(self.source_inventory) is not tuple or any(type(item) is not SourceInventoryEntry for item in self.source_inventory):
            raise DeploymentError('SOURCE_INVENTORY_INVALID')
        if len({item.identity for item in self.source_inventory}) != len(self.source_inventory):
            raise DeploymentError('SOURCE_INVENTORY_INVALID')
        if self.source_categories:
            categories=dict(self.source_categories)
            for category,state in self.source_categories:
                if bool([e for e in self.source_inventory if e.category==category])!=(state=='POPULATED'):
                    raise DeploymentError('SOURCE_CATEGORY_MISMATCH')
            support_states = ('POPULATED','EMPTY') if self.allow_empty_support else ('POPULATED',)
            if categories['clan'] not in support_states or categories['siege']!='LOCKED':
                raise DeploymentError('SOURCE_CATEGORY_MISMATCH')
            projected=[]
            prefixes={'ordinary':CardKind.TROOP,'hero':CardKind.HERO,'spell':CardKind.SPELL}
            for item in self.source_inventory:
                SourceInventoryEntry(item.identity,item.category,item.kind,item.spell,item.quantity,item.state,item.provenance)
                if item.state is not CardState.AVAILABLE or item.provenance!='HOME_SOURCE':
                    raise DeploymentError('SOURCE_STATE_INVALID')
                if item.category=='clan':
                    if not item.identity.startswith('reserve.') or item.kind not in (CardKind.TROOP,CardKind.SPELL):
                        raise DeploymentError('SOURCE_PROJECTION_INVALID')
                elif item.category in prefixes:
                    prefix=item.category+'.'
                    if not item.identity.startswith(prefix) or item.kind is not prefixes[item.category]:
                        raise DeploymentError('SOURCE_PROJECTION_INVALID')
                    projected.append((item.identity[len(prefix):],item.kind,item.spell,item.quantity))
                else:raise DeploymentError('SOURCE_PROJECTION_INVALID')
            ordinary=[entry for entry in self.roster if entry[1] is not CardKind.CLAN]
            clan=[entry for entry in self.roster if entry[1] is CardKind.CLAN]
            if set(projected)!=set(ordinary) or len(clan)!=int(categories['clan']=='POPULATED'):
                raise DeploymentError('SOURCE_PROJECTION_INVALID')
            if any(dict(self.dispositions).get(e[0]) is not DeploymentDisposition.DEPLOY for e in self.roster):
                raise DeploymentError('SOURCE_PROJECTION_INVALID')
        source = tuple((item.identity, item.category, item.kind.value, item.spell.value,
                        item.quantity, None if item.state is None else item.state.value,
                        item.provenance) for item in self.source_inventory)
        body = {'layout':'SINGLE_PAGE','limit':self.viewport_limit,'roster':normalized}
        if dispositions or source:
            body = {'schema':2, **body, 'dispositions':dispositions, 'source_inventory':source}
        if self.source_categories or self.source_context:
            body.update(schema=3,source_categories=self.source_categories,source_context=self.source_context)
        if self.match_entry_cost is not None: body['match_entry_cost']=self.match_entry_cost
        if self.allow_empty_support: body['allow_empty_support']=True
        payload=json.dumps(body,
                           sort_keys=True,separators=(',',':')).encode()
        computed=hashlib.sha256(payload).hexdigest()
        if self.digest and (type(self.digest) is not str or self.digest!=computed):
            raise DeploymentError('PLAN_DIGEST_MISMATCH')
        object.__setattr__(self,'digest',computed)
    def validated_digest(self):
        """Recompute and validate every exact typed authority-bearing field."""
        return type(self)(self.roster,self.viewport_limit,self.digest,
                          self.dispositions,self.source_inventory,self.source_categories,self.source_context,
                          self.match_entry_cost,self.allow_empty_support).digest
    @property
    def signatures(self):
        self.validated_digest()
        return tuple(entry[:3] for entry in self.roster)
    @property
    def quantities(self):
        self.validated_digest()
        return tuple(entry[3] for entry in self.roster)
    @property
    def deployment_dispositions(self):
        self.validated_digest()
        if self.dispositions:
            return self.dispositions
        return tuple((entry[0], DeploymentDisposition.DEPLOY) for entry in self.roster)


def _copy_plan(plan):
    if type(plan) is not CompiledDeploymentPlan:
        raise DeploymentError('PLAN_INVALID')
    seal=CompiledDeploymentPlan.validated_digest(plan)
    roster=tuple(tuple(entry) for entry in plan.roster)
    dispositions=tuple((identity, DeploymentDisposition(disposition.value))
                       for identity, disposition in plan.deployment_dispositions)
    source_inventory=tuple(SourceInventoryEntry(item.identity, item.category, item.kind,
                                               item.spell, item.quantity, item.state,
                                               item.provenance)
                           for item in plan.source_inventory)
    explicit_dispositions = dispositions if plan.dispositions else ()
    return CompiledDeploymentPlan(roster,plan.viewport_limit,seal,
                                  explicit_dispositions,source_inventory,plan.source_categories,plan.source_context,
                                  plan.match_entry_cost,plan.allow_empty_support)


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
    source_inventory: tuple[SourceInventoryEntry, ...] = ()
    source_categories: tuple[tuple[str, str], ...] = ()
    source_context: tuple[str, ...] = ()
    match_mode: str = ''
    match_cost: int | None = None
    def __post_init__(self):
        if (type(self.match_mode) is not str or self.match_mode not in ('','REGULAR_MULTIPLAYER','RANKED')
                or (self.match_cost is not None and (type(self.match_cost) is not int or not 1 <= self.match_cost <= 1000000))):
            raise DeploymentError('MATCH_EVIDENCE_INVALID')
        finite(self.captured_at)
        if (type(self.sequence) is not int or self.sequence < 1
                or type(self.view) is not str or not 1 <= len(self.view) <= 128
                or type(self.screen) is not Screen or type(self.intervention) is not Intervention
                or (self.page is not None and type(self.page) is not Page)
                or type(self.targets) is not tuple or len(self.targets) > 256
                or any(type(t) is not Target for t in self.targets)
                or type(self.controls) is not tuple or len(self.controls)>16
                or type(self.home_verified) is not bool
                or type(self.source_inventory) is not tuple
                or any(type(item) is not SourceInventoryEntry for item in self.source_inventory)):
            raise DeploymentError('OBSERVATION_INVALID')
        names=[]
        for item in self.controls:
            if (type(item) is not tuple or len(item)!=2 or type(item[0]) is not str
                    or item[0] not in {'attack','find_match','army_attack',
                                       'end_battle','confirm_end','return_home'}
                    or type(item[1]) is not Point):
                raise DeploymentError('CONTROL_INVALID')
            names.append(item[0])
        if len(set(names)) != len(names): raise DeploymentError('CONTROL_AMBIGUOUS')
        if self.screen is Screen.BATTLE and self.page is None:
            raise DeploymentError('PAGE_MISSING')
        if self.screen is not Screen.ARMY and self.source_inventory:
            raise DeploymentError('SOURCE_INVENTORY_INVALID')
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
    deployable_remaining: int | None = None
    retained_remaining: int = 0
    retained_controls: tuple[str, ...] = ()
    natural_result: bool = False
    returned_home: bool = False
    post_source_verified: bool = False
    reserve_payload_sha256: str = ''
    sealed_reserve_units: int = 0
    reserve_state: str = 'UNVERIFIED'
    clan_input_proposed: bool | None = None
    clan_input_delivered: bool | None = None
    receipt_schema: int = 1
    def __post_init__(self):
        if type(self.receipt_schema) is not int or self.receipt_schema not in (1,4):
            raise DeploymentError('RESULT_INVALID')
        if (type(self.reserve_payload_sha256) is not str
                or (self.reserve_payload_sha256 and re.fullmatch(r'[0-9a-f]{64}',self.reserve_payload_sha256) is None)
                or type(self.sealed_reserve_units) is not int or not 0<=self.sealed_reserve_units<=48000
                or type(self.reserve_state) is not str
                or self.reserve_state not in ('UNVERIFIED','SOURCE_BOUND','VERIFIED_UNCHANGED')):
            raise DeploymentError('RESULT_INVALID')
        for b in (self.clan_input_proposed,self.clan_input_delivered):
            if b is not None and type(b) is not bool: raise DeploymentError('RESULT_INVALID')
        if (self.clan_input_proposed is None)!=(self.clan_input_delivered is None):
            raise DeploymentError('RESULT_INVALID')
        for b in (self.complete,self.own_exit,self.home_verified,self.intervention_free):
            if type(b) is not bool: raise DeploymentError('RESULT_INVALID')
        if self.deployable_remaining is None:
            object.__setattr__(self, 'deployable_remaining', self.remaining)
        for b in (self.natural_result, self.returned_home, self.post_source_verified):
            if type(b) is not bool: raise DeploymentError('RESULT_INVALID')
        for n,limit in ((self.cards_total,48),(self.spells_consumed,48000),
                        (self.remaining,48000),(self.deployable_remaining,48000),
                        (self.retained_remaining,48000)):
            if type(n) is not int or not 0<=n<=limit: raise DeploymentError('RESULT_INVALID')
        if (type(self.retained_controls) is not tuple
                or any(type(item) is not str or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}', item) is None
                       for item in self.retained_controls)):
            raise DeploymentError('RESULT_INVALID')
        if (len(set(self.retained_controls))!=len(self.retained_controls)
                or self.remaining!=self.deployable_remaining+self.retained_remaining):
            raise DeploymentError('RESULT_INVALID')
        if self.retained_controls or self.reserve_payload_sha256:
            if (not self.reserve_payload_sha256 or self.sealed_reserve_units==0
                    or self.own_exit
                    or self.post_source_verified!=(self.reserve_state=='VERIFIED_UNCHANGED')):
                raise DeploymentError('RESULT_INVALID')
            complete_ok = (self.cards_total > 0 and self.home_verified and self.returned_home
                           and self.intervention_free and self.deployable_remaining == 0
                           and len(self.retained_controls)==1
                           and self.retained_remaining == len(self.retained_controls)
                           and self.natural_result
                           and (self.post_source_verified or (self.reserve_state=='SOURCE_BOUND'
                                and self.clan_input_proposed is False and self.clan_input_delivered is False)))
            if self.clan_input_proposed is not None:
                complete_ok = complete_ok and self.clan_input_proposed is False and self.clan_input_delivered is False
        else:
            complete_ok = (self.cards_total>0 and self.home_verified
                           and self.intervention_free and self.remaining==0)
        if self.receipt_schema == 4:
            if (self.retained_controls or self.retained_remaining or self.reserve_payload_sha256
                    or self.sealed_reserve_units or self.reserve_state != 'UNVERIFIED'
                    or self.post_source_verified or self.clan_input_proposed is not None
                    or self.clan_input_delivered is not None or self.own_exit):
                raise DeploymentError('RESULT_INVALID')
            complete_ok = complete_ok and self.natural_result and self.returned_home
        if self.complete and not complete_ok:
            raise DeploymentError('RESULT_INVALID')
        if type(self.reason) is not str or re.fullmatch(r'[A-Z][A-Z_]{0,63}',self.reason) is None:
            raise DeploymentError('RESULT_INVALID')
    def autonomous(self, retirement_verified: bool) -> bool:
        return self.complete and retirement_verified is True
    def public_payload(self):
        if self.receipt_schema == 4:
            return dict(schema=4,complete=self.complete,reason=self.reason,cards_total=self.cards_total,
                        spells_consumed=self.spells_consumed,own_exit=self.own_exit,
                        home_verified=self.home_verified,intervention_free=self.intervention_free,
                        remaining=self.remaining,natural_result=self.natural_result,
                        returned_home=self.returned_home)
        if self.retained_controls or self.reserve_payload_sha256:
            payload=dict(schema=2,complete=self.complete,reason=self.reason,
                        cards_total=self.cards_total,spells_consumed=self.spells_consumed,
                        own_exit=self.own_exit,home_verified=self.home_verified,
                        intervention_free=self.intervention_free,remaining=self.remaining,
                        deployable_remaining=self.deployable_remaining,
                        retained_remaining=self.retained_remaining,
                        retained_controls=list(self.retained_controls),
                        natural_result=self.natural_result,returned_home=self.returned_home,
                        post_source_verified=self.post_source_verified,
                        reserve_payload_sha256=self.reserve_payload_sha256,
                        sealed_reserve_units=self.sealed_reserve_units,reserve_state=self.reserve_state)
            if self.clan_input_proposed is not None:
                payload.update(schema=3,clan_input_proposed=self.clan_input_proposed,
                               clan_input_delivered=self.clan_input_delivered)
            return payload
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


def _source_point(value):
    if type(value) is not Point:
        raise DeploymentError('OBSERVATION_INVALID')
    x, y = value.x, value.y
    finite(x, 0, 1)
    finite(y, 0, 1)
    return x, y


def _detach_observation(value):
    """Validate the original hostile graph completely, then detach it."""
    if type(value) is not Observation:
        raise DeploymentError('OBSERVATION_INVALID')

    sequence = value.sequence
    captured_at = value.captured_at
    view = value.view
    screen = value.screen
    intervention = value.intervention
    home_verified = value.home_verified
    if (type(sequence) is not int or sequence < 1
            or type(view) is not str or not 1 <= len(view) <= 128
            or type(screen) is not Screen
            or type(intervention) is not Intervention
            or type(home_verified) is not bool):
        raise DeploymentError('OBSERVATION_INVALID')
    finite(captured_at)

    raw_page = value.page
    page_values = None
    if raw_page is not None:
        if type(raw_page) is not Page:
            raise DeploymentError('PAGE_INVALID')
        raw_cards = raw_page.cards
        left_edge = raw_page.left_edge
        right_edge = raw_page.right_edge
        if (type(raw_cards) is not tuple or not 1 <= len(raw_cards) <= 24
                or type(left_edge) is not bool or type(right_edge) is not bool):
            raise DeploymentError('PAGE_INVALID')
        card_values = []
        prior_x = None
        for item in raw_cards:
            if type(item) is not Card:
                raise DeploymentError('CARD_INVALID')
            identity = item.identity
            kind = item.kind
            spell = item.spell
            remaining = item.remaining
            state = item.state
            selected = item.selected
            if (type(identity) is not str
                    or re.fullmatch(r'[a-z0-9][a-z0-9_.:-]{0,63}', identity) is None
                    or type(kind) is not CardKind or type(spell) is not Spell
                    or type(state) is not CardState or type(selected) is not bool
                    or (remaining is not None and
                        (type(remaining) is not int or not 0 <= remaining <= 999))
                    or (kind not in (CardKind.SPELL, CardKind.UNKNOWN)
                        and spell is not Spell.NONE)):
                raise DeploymentError('CARD_INVALID')
            point = _source_point(item.point)
            if prior_x is not None and prior_x >= point[0]:
                raise DeploymentError('PAGE_INVALID')
            prior_x = point[0]
            card_values.append((identity, kind, spell, remaining, state, point, selected))
        if len({item[0] for item in card_values}) != len(card_values):
            raise DeploymentError('PAGE_INVALID')
        page_values = (tuple(card_values), left_edge, right_edge)
    if screen is Screen.BATTLE and page_values is None:
        raise DeploymentError('PAGE_MISSING')

    raw_targets = value.targets
    if type(raw_targets) is not tuple or len(raw_targets) > 256:
        raise DeploymentError('OBSERVATION_INVALID')
    target_values = []
    for item in raw_targets:
        if type(item) is not Target:
            raise DeploymentError('TARGET_INVALID')
        kind = item.kind
        confidence = item.confidence
        if type(kind) is not TargetKind:
            raise DeploymentError('TARGET_INVALID')
        point = _source_point(item.point)
        finite(confidence, 0, 1)
        target_values.append((kind, point, confidence))

    raw_controls = value.controls
    if type(raw_controls) is not tuple or len(raw_controls) > 16:
        raise DeploymentError('OBSERVATION_INVALID')
    control_values = []
    names = []
    allowed = {'attack', 'find_match', 'army_attack', 'end_battle', 'confirm_end', 'return_home'}
    for item in raw_controls:
        if type(item) is not tuple or len(item) != 2:
            raise DeploymentError('CONTROL_INVALID')
        name = item[0]
        if type(name) is not str or name not in allowed:
            raise DeploymentError('CONTROL_INVALID')
        point = _source_point(item[1])
        names.append(name)
        control_values.append((name, point))
    if len(set(names)) != len(names):
        raise DeploymentError('CONTROL_AMBIGUOUS')

    raw_source = value.source_inventory
    if type(raw_source) is not tuple or any(type(item) is not SourceInventoryEntry for item in raw_source):
        raise DeploymentError('SOURCE_INVENTORY_INVALID')
    if screen is not Screen.ARMY and raw_source:
        raise DeploymentError('SOURCE_INVENTORY_INVALID')
    source_values = []
    for item in raw_source:
        source_values.append((item.identity, item.category, item.kind, item.spell,
                              item.quantity, item.state, item.provenance))
    if len({item[0] for item in source_values}) != len(source_values):
        raise DeploymentError('SOURCE_INVENTORY_INVALID')

    page = None
    if page_values is not None:
        cards = tuple(Card(identity, kind, spell, remaining, state, Point(*point), selected)
                      for identity, kind, spell, remaining, state, point, selected
                      in page_values[0])
        page = Page(cards, page_values[1], page_values[2])
    targets = tuple(Target(kind, Point(*point), confidence)
                    for kind, point, confidence in target_values)
    controls = tuple((name, Point(*point)) for name, point in control_values)
    source_inventory = tuple(SourceInventoryEntry(*item) for item in source_values)
    _source_metadata(value.source_categories,value.source_context)
    return Observation(sequence, captured_at, view, screen, page, targets, controls,
                       intervention, home_verified, source_inventory,value.source_categories,value.source_context,
                       value.match_mode,value.match_cost)


def _copy_observation(value):
    try:
        return _detach_observation(value)
    except DeploymentError:
        raise
    except BaseException:
        raise DeploymentError('OBSERVATION_INVALID') from None


class _RunCore:
    __slots__ = (
        'plan', 'viewport_limit', 'digest', 'policy', 'ports', 'visit_limit',
        'deadline', 'last_clock', 'sequence', 'captured', 'view', 'inventory',
        'cards', 'holds', 'ground_steps', 'spells', 'actions', 'own_exit',
        'home', 'intervention_free', 'proof_fault', 'last_observation',
        'pending', 'possible_input', 'released', 'reason', 'complete',
        'dispositions', 'source_inventory', 'source_verified',
        'natural_result',
        'source_categories', 'source_context', 'explicit_dispositions', 'source_stage', 'match_entry_cost',
        'allow_empty_support',
    )
    def __init__(self, plan, policy, ports, visit_limit):
        self.plan = (() if plan is None else tuple(
            (identity, kind.value, spell.value, quantity)
            for identity, kind, spell, quantity in plan.roster))
        self.viewport_limit = 0 if plan is None else int(plan.viewport_limit)
        self.digest = '' if plan is None else str(plan.digest)
        self.source_categories=() if plan is None else plan.source_categories
        self.source_context=() if plan is None else plan.source_context
        self.allow_empty_support=False if plan is None else plan.allow_empty_support
        self.explicit_dispositions=bool(plan and plan.dispositions)
        self.dispositions = (() if plan is None else tuple(
            (identity, disposition.value)
            for identity, disposition in plan.deployment_dispositions))
        self.source_inventory = (() if plan is None else tuple(
            (item.identity, item.category, item.kind.value, item.spell.value,
             item.quantity, None if item.state is None else item.state.value,
             item.provenance) for item in plan.source_inventory))
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
        self.source_verified = False
        self.source_stage = 'NONE'
        self.match_entry_cost = None if plan is None else plan.match_entry_cost

        self.natural_result = False


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
    roster = tuple(
        (identity, CardKind(kind), Spell(spell), quantity)
        for identity, kind, spell, quantity in core.plan
    )
    dispositions = tuple((identity, DeploymentDisposition(disposition))
                         for identity, disposition in core.dispositions) if core.explicit_dispositions else ()
    if dispositions:
        disposition_map = {identity: disposition for identity, disposition in dispositions}
        dispositions = tuple((identity, disposition_map.get(identity, DeploymentDisposition.DEPLOY))
                             for identity, _kind, _spell, _quantity in roster)
    source_inventory = tuple(SourceInventoryEntry(identity, category, CardKind(kind),
                                                  Spell(spell), quantity,
                                                  None if state is None else CardState(state),
                                                  provenance)
                             for identity, category, kind, spell, quantity, state, provenance
                             in core.source_inventory)
    return CompiledDeploymentPlan(roster, core.viewport_limit, core.digest,
                                  dispositions, source_inventory,core.source_categories,core.source_context,
                                  core.match_entry_cost,core.allow_empty_support)


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
    if observation.intervention is not Intervention.CLEAR:
        core.intervention_free = False
        raise DeploymentError('INTERVENTION')
    if observation.screen is Screen.BATTLE:
        if core.view is not None and observation.view != core.view:
            raise DeploymentError('VIEW_CHANGED')
        _stage_battle(core, observation, validate_progress=False)
    else:
        if observation.screen is Screen.ARMY:
            _validate_source_observation(core,observation)
        core.sequence = observation.sequence
        core.captured = observation.captured_at
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


def _source_required(core):
    return bool(core.source_inventory)


def _validate_source_observation(core, observation):
    if core.source_stage != 'ARMY':
        raise DeploymentError('SOURCE_STAGE_INVALID')
    if (not core.source_categories or not core.source_context
            or observation.source_context!=core.source_context):
        raise DeploymentError('SOURCE_CONTEXT_INVALID')
    if (observation.source_categories!=core.source_categories
            or _source_tuple(observation)!=core.source_inventory):
        raise DeploymentError('SOURCE_INVENTORY_MISMATCH')
    observation.control('army_attack')


def _validate_match(core, observation):
    if (core.source_stage != 'MATCH' or observation.screen is not Screen.MATCH
            or observation.match_mode != 'REGULAR_MULTIPLAYER'):
        raise DeploymentError('MATCH_MODE_UNPROVED')
    if core.match_entry_cost is None or observation.match_cost != core.match_entry_cost:
        raise DeploymentError('MATCH_COST_UNPROVED')
    observation.control('find_match')


def _source_tuple(observation):
    return tuple((item.identity, item.category, item.kind.value, item.spell.value,
                  item.quantity, None if item.state is None else item.state.value,
                  item.provenance)
                 for item in observation.source_inventory)


def _detached_inventory(core):
    inventory = Inventory(core.inventory.max_cards, core.inventory.max_pages)
    inventory.cards = list(core.inventory.cards)
    inventory.complete = core.inventory.complete
    inventory.pages = core.inventory.pages
    return inventory


def _stage_battle(core, observation, permitted=None, validate_progress=True):
    if observation.screen is not Screen.BATTLE or observation.page is None:
        raise DeploymentError('BATTLE_UNPROVED')
    page = observation.page
    if not page.left_edge or not page.right_edge:
        raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
    observed = tuple(item.signature for item in page.cards)
    expected = _signatures(core)
    if len(observed) > core.viewport_limit:
        raise DeploymentError('PLAN_VIEWPORT_OVERFLOW')
    if observed != expected:
        if len(set(observed)) != len(observed):
            raise DeploymentError('ROSTER_DUPLICATE')
        if any(item not in expected for item in observed):
            raise DeploymentError('ROSTER_ADDITIONAL')
        raise DeploymentError('ROSTER_INCOMPLETE')
    for item in page.cards:
        item.require_known()
    inventory = _detached_inventory(core)
    if not inventory.complete:
        for item, quantity in zip(page.cards, _quantities(core)):
            if item.remaining != quantity or item.state is not CardState.AVAILABLE:
                raise DeploymentError('INITIAL_QUANTITY_MISMATCH')
            if item.kind is CardKind.CLAN and item.selected:
                raise DeploymentError('SUPPORT_ALREADY_SELECTED')
        inventory.add(page)
        cards = {item.identity: item for item in page.cards}
        holds = {identity: 0.0 for identity in cards}
    else:
        inventory.locate(page)
        cards = {}
        holds = None
        if validate_progress:
            for item in page.cards:
                old = core.cards[item.identity]
                if item.signature != old.signature:
                    raise DeploymentError('CARD_CHANGED')
                if (item.identity != permitted
                        and (item.remaining != old.remaining or item.state is not old.state)):
                    raise DeploymentError('UNEXPLAINED_CARD_CHANGE')
                cards[item.identity] = item
    return inventory, cards, holds


def _commit_battle(core, observation, inventory, cards, holds):
    core.sequence = observation.sequence
    core.captured = observation.captured_at
    core.view = observation.view
    core.last_observation = observation
    core.inventory = inventory
    core.cards = cards
    if holds is not None:
        core.holds = holds


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
    inventory, cards, holds = _stage_battle(core, observation, permitted)
    _commit_battle(core, observation, inventory, cards, holds)


def _proof_check(core, intent, before):
    stages={'attack':'HOME','find_match':'MATCH','army_attack':'ARMY'}
    if intent.verb in stages and core.source_stage != stages[intent.verb]:
        raise DeploymentError('ROUTE_UNPROVED')
    if intent.verb=='army_attack' and not core.source_verified:
        raise DeploymentError('SOURCE_PRECOMMIT_REQUIRED')
    observation = _read(core, intent.deadline)
    if observation.screen is not before.screen or observation.view != before.view:
        raise DeploymentError('STATE_CHANGED')
    if observation.screen is Screen.HOME and observation.home_verified is not True:
        raise DeploymentError('HOME_UNPROVED')
    if intent.verb=='find_match':
        _validate_match(core, before)
        _validate_match(core, observation)
    if intent.verb=='army_attack':
        _validate_source_observation(core, before)
        _validate_source_observation(core, observation)
    if intent.verb in stages:
        for evidence in (before, observation):
            if any(name != intent.verb and point.near(intent.point)
                   for name,point in evidence.controls):
                raise DeploymentError('CONTROL_ALIAS')
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
    # Legacy battle-only unit seam has no S004 source authority.
    if observation.screen is Screen.BATTLE and not _source_required(core):
        return observation
    if (not _source_required(core) or not core.source_categories or not core.source_context
            or observation.screen is not Screen.HOME):
        raise DeploymentError('SOURCE_PRECOMMIT_REQUIRED')
    for source, verb, destination in (
        (Screen.HOME, 'attack', Screen.MATCH),
        (Screen.MATCH, 'find_match', Screen.ARMY),
        (Screen.ARMY, 'army_attack', Screen.BATTLE),
    ):
        if observation.screen is not source:
            raise DeploymentError('ROUTE_UNPROVED')
        core.source_stage=source.value
        if source is Screen.HOME and observation.home_verified is not True:
            raise DeploymentError('HOME_UNPROVED')
        if source is Screen.MATCH:
            _validate_match(core, observation)
        if source is Screen.ARMY:
            _validate_source_observation(core, observation)
            core.source_verified=True
        deadline = min(core.deadline, _now(core) + 10)
        _send(core, Intent(verb, observation.control(verb),
                           Action.ATTACK_NAVIGATION, deadline=deadline), observation)
        # A delivered final Attack may leave the proved Army visible briefly.
        # Keep its evidence stage during read-only waiting, never replay input.
        core.source_stage=source.value if source is Screen.ARMY else destination.value
        observation = _await(core, {destination}, deadline, source)
        core.source_stage=destination.value
    return observation

def _scan(core, observation):
    inventory, cards, holds = _stage_battle(core, observation)
    _commit_battle(core, observation, inventory, cards, holds)
    return observation


def _consume(core, card_value, observation, deadline):
    if card_value.kind is CardKind.CLAN and card_value.selected:
        raise DeploymentError('SUPPORT_ALREADY_SELECTED')
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
        if card_value.kind is CardKind.CLAN:
            raise DeploymentError('TARGET_UNPROVED')
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
            if (confirmed.sequence <= observation.sequence
                    or confirmed.captured_at < observation.captured_at):
                raise DeploymentError('OBSERVATION_STALE')
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
        _sync(core, observation)
        _wait(core, .05, ack_deadline)
    raise DeploymentError('CONSUMPTION_UNPROVED')


def _deploy(core, observation, deadline):
    idle = 0
    while True:
        _gate(core, deadline)
        _sync(core, observation)
        pending = [item for item in core.cards.values()
                   if not item.exhausted]
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
    if any(not item.exhausted
           for item in observation.page.cards):
        raise DeploymentError('DEPLETION_UNPROVED')
    return observation


def _exit(core, observation, result_deadline):
    # Deployment is terminal. Battle input is now forbidden: observe until the
    # game itself presents Result, leaving exit_seconds for proved Return/Home.
    for _ in range(12000):
        if observation.screen is Screen.RESULT:
            core.natural_result = True
            break
        if observation.screen is not Screen.BATTLE:
            raise DeploymentError('UNEXPECTED_TRANSITION')
        _sync(core, observation)
        _wait(core, .05, result_deadline)
        observation = _read(core, result_deadline)
    else:
        raise DeploymentError('OBSERVATION_LIMIT')
    deadline = core.deadline
    _send(core, Intent('return_home', observation.control('return_home'),
                       Action.RETURN_HOME, deadline=deadline), observation)
    observation = _await(core, {Screen.HOME},
                         min(deadline, _now(core) + 10), Screen.RESULT)
    if not observation.home_verified:
        raise DeploymentError('HOME_UNPROVED')
    core.home = True
    return observation


def _snapshot(core, complete, reason):
    remaining = sum((item.remaining or 0) for item in core.cards.values())
    cards_total = len(core.plan) if core.cards else 0
    return DeploymentResult(
        complete, reason, cards_total, core.spells, core.own_exit, core.home,
        core.intervention_free, remaining, natural_result=core.natural_result,
        returned_home=core.home, receipt_schema=4,
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
        result_deadline = core.deadline - core.policy.exit_seconds
        observation = _exit(core, observation, result_deadline)
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
    if complete and not (core.home and core.intervention_free):
        complete = False
        reason = 'DEPLOYMENT_FAILED'
    result = _snapshot(core, complete, reason)
    public_result = DeploymentResult(
        result.complete, result.reason, result.cards_total, result.spells_consumed,
        result.own_exit, result.home_verified, result.intervention_free,
        result.remaining, result.deployable_remaining, result.retained_remaining,
        result.retained_controls, result.natural_result, result.returned_home,
        result.post_source_verified,
        result.reserve_payload_sha256,result.sealed_reserve_units,result.reserve_state,
        result.clan_input_proposed,result.clan_input_delivered,result.receipt_schema,
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
