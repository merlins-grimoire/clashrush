"""Closed values for the durable non-replayable action journal."""

from __future__ import annotations

import re
from enum import Enum
from types import MappingProxyType

from .scheduler_contract import Lane, VisitGeneration


_NONCE = re.compile(r"[0-9a-f]{32}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


class ActionState(Enum):
    PLANNED = "PLANNED"
    INTENT_RECORDED = "INTENT_RECORDED"
    INPUT_STARTED = "INPUT_STARTED"
    INPUT_COMPLETED = "INPUT_COMPLETED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


TERMINAL_ACTION_STATES = frozenset(
    {ActionState.CONFIRMED, ActionState.FAILED, ActionState.UNCERTAIN}
)
REPLAYABLE_ACTION_STATES = frozenset(
    {ActionState.PLANNED, ActionState.INTENT_RECORDED}
)
ACTION_TRANSITIONS = MappingProxyType(
    {
        ActionState.PLANNED: frozenset({ActionState.INTENT_RECORDED}),
        ActionState.INTENT_RECORDED: frozenset({ActionState.INPUT_STARTED}),
        ActionState.INPUT_STARTED: frozenset(
            {ActionState.INPUT_COMPLETED, *TERMINAL_ACTION_STATES}
        ),
        ActionState.INPUT_COMPLETED: TERMINAL_ACTION_STATES,
        ActionState.CONFIRMED: frozenset(),
        ActionState.FAILED: frozenset(),
        ActionState.UNCERTAIN: frozenset(),
    }
)


class SchedulerJournalError(ValueError):
    """A journal value or transition is outside the closed contract."""


class JournalAction(tuple[object, ...]):
    """A validated tuple-backed action value that cannot be field-mutated."""

    __slots__ = ()

    def __new__(
        cls,
        *,
        action_generation: int,
        action_nonce: str,
        intent_fingerprint: str,
        visit_generation: VisitGeneration,
        visit_nonce: str,
        lane: Lane,
        input_started: bool,
        input_completed: bool,
        state: ActionState,
    ) -> JournalAction:
        if type(action_generation) is not int or action_generation <= 0:
            raise SchedulerJournalError("action generation must be a positive integer")
        if type(action_nonce) is not str or _NONCE.fullmatch(action_nonce) is None:
            raise SchedulerJournalError("action nonce must be 32 lowercase hex characters")
        if (
            type(intent_fingerprint) is not str
            or _FINGERPRINT.fullmatch(intent_fingerprint) is None
        ):
            raise SchedulerJournalError(
                "intent fingerprint must be 64 lowercase hex characters"
            )
        if type(visit_generation) is not VisitGeneration:
            raise SchedulerJournalError("action visit generation is invalid")
        if type(visit_nonce) is not str or _NONCE.fullmatch(visit_nonce) is None:
            raise SchedulerJournalError("action visit nonce must be 32 lowercase hex characters")
        if type(lane) is not Lane or type(state) is not ActionState:
            raise SchedulerJournalError("action lane or state is invalid")
        if type(input_started) is not bool or type(input_completed) is not bool:
            raise SchedulerJournalError("action input markers must be exact booleans")
        if input_completed and not input_started:
            raise SchedulerJournalError("completed input must have started")
        if state in REPLAYABLE_ACTION_STATES and (input_started or input_completed):
            raise SchedulerJournalError("replayable action has input evidence")
        if state is ActionState.INPUT_STARTED and (not input_started or input_completed):
            raise SchedulerJournalError("INPUT_STARTED markers are inconsistent")
        if state is ActionState.INPUT_COMPLETED and not input_completed:
            raise SchedulerJournalError("INPUT_COMPLETED markers are inconsistent")
        if state in TERMINAL_ACTION_STATES and not input_started:
            raise SchedulerJournalError("terminal action has no input-start evidence")
        return tuple.__new__(
            cls,
            (
                action_generation,
                action_nonce,
                intent_fingerprint,
                visit_generation.value,
                visit_nonce,
                lane,
                input_started,
                input_completed,
                state,
            ),
        )

    action_generation = property(lambda self: self[0])
    action_nonce = property(lambda self: self[1])
    intent_fingerprint = property(lambda self: self[2])
    visit_generation = property(lambda self: VisitGeneration(self[3]))
    visit_nonce = property(lambda self: self[4])
    lane = property(lambda self: self[5])
    input_started = property(lambda self: self[6])
    input_completed = property(lambda self: self[7])
    state = property(lambda self: self[8])

    @property
    def replayable(self) -> bool:
        return require_pristine_action(self).state in REPLAYABLE_ACTION_STATES


def require_pristine_action(action: object) -> JournalAction:
    if type(action) is not JournalAction:
        raise SchedulerJournalError("action must be an exact JournalAction")
    try:
        rebuilt = JournalAction(
            action_generation=action.action_generation,
            action_nonce=action.action_nonce,
            intent_fingerprint=action.intent_fingerprint,
            visit_generation=action.visit_generation,
            visit_nonce=action.visit_nonce,
            lane=action.lane,
            input_started=action.input_started,
            input_completed=action.input_completed,
            state=action.state,
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise SchedulerJournalError("journal action storage is invalid") from exc
    if tuple(rebuilt) != tuple(action):
        raise SchedulerJournalError("journal action storage is noncanonical")
    return rebuilt


def transition_action(
    action: JournalAction,
    *,
    next_state: ActionState,
) -> JournalAction:
    action = require_pristine_action(action)
    if type(next_state) is not ActionState:
        raise SchedulerJournalError("action transition states are invalid")
    if next_state not in ACTION_TRANSITIONS[action.state]:
        raise SchedulerJournalError(
            f"action transition {action.state.value} to {next_state.value} is forbidden"
        )
    return JournalAction(
        action_generation=action.action_generation,
        action_nonce=action.action_nonce,
        intent_fingerprint=action.intent_fingerprint,
        visit_generation=action.visit_generation,
        visit_nonce=action.visit_nonce,
        lane=action.lane,
        state=next_state,
        input_started=(
            action.input_started
            or next_state
            in {
                ActionState.INPUT_STARTED,
                ActionState.INPUT_COMPLETED,
                *TERMINAL_ACTION_STATES,
            }
        ),
        input_completed=(
            action.input_completed or next_state is ActionState.INPUT_COMPLETED
        ),
    )
