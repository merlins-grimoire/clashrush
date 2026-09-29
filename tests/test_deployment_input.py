import ctypes
from types import SimpleNamespace

import pytest
from clash_rush_rebuild.mvp_deployment import (
    Action,
    DeploymentError,
    Intent,
    Intervention,
    Point,
)
from clash_rush_rebuild.mvp_deployment_input import (
    GuardedDeploymentInput,
    TaggedWin32Mouse,
)

class Clock:
    def __init__(self): self.t=1.
    def __call__(self): return self.t
    def wait(self,s): self.t+=s
class Mouse:
    def __init__(self): self.events=[]; self.fail=False
    def prepare(self,p): self.events.append('prepare'); return True
    def move(self,p): self.events.append('move'); return True
    def at_target(self,p): return True
    def down(self):
        self.events.append('down')
        if self.fail: raise RuntimeError('private-native-value')
    def up(self): self.events.append('up')
class Lease:
    def __init__(self): self.flags=[]
    def set_held(self,flag): self.flags.append(flag)


def tagged_mouse_with_invalid_binding():
    from ctypes import wintypes

    binding = SimpleNamespace(root_hwnd=11, render_hwnd=22, width=640, height=360)
    calls = []

    def require(current):
        calls.append(current)
        raise DeploymentError('BINDING_INVALIDATED')

    class User:
        def GetForegroundWindow(self): return 33
        def GetAncestor(self, _window, _flag): return 11
        def ClientToScreen(self, _window, point):
            value = ctypes.cast(point, ctypes.POINTER(wintypes.POINT)).contents
            value.x, value.y = 256, 144
            return True
        def GetCursorPos(self, point):
            value = ctypes.cast(point, ctypes.POINTER(wintypes.POINT)).contents
            value.x, value.y = 256, 144
            return True
        def mouse_event(self, *_args): return None

    mouse = TaggedWin32Mouse.__new__(TaggedWin32Mouse)
    mouse.bound = SimpleNamespace(_binding=binding, _require_binding=require)
    mouse.user = User()
    mouse.w = wintypes
    mouse.marker = 42
    mouse.pixel = (256, 144)
    return mouse, calls


def subject():
    clock=Clock(); mouse=Mouse(); lease=Lease()
    obj=GuardedDeploymentInput(mouse=mouse,authorize=lambda _a:True,
        monitor=lambda:Intervention.CLEAR,lease=lease,monotonic=clock,wait=clock.wait,
        observed_events=lambda:sum(e in ('move','down','up') for e in mouse.events))
    return obj,clock,mouse,lease


def test_discrete_cast_is_one_mouse_pair_no_key_events():
    obj,clock,mouse,lease=subject()
    assert obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert mouse.events.count('down')==mouse.events.count('up')==1
    assert lease.flags==[True,False]


def test_unknown_final_proof_never_presses():
    obj,_,mouse,lease=subject()
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:None)
    assert 'down' not in mouse.events and not lease.flags


def test_down_exception_still_releases_possible_held_button():
    obj,_,mouse,lease=subject(); mouse.fail=True
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert mouse.events[-2:]==['down','up'] and lease.flags[-1] is False


def test_expiry_during_proof_never_presses():
    obj,clock,mouse,_=subject()
    def proof(): clock.t=4.; return True
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),proof)
    assert 'down' not in mouse.events


def test_revocation_during_hold_releases_without_more_movement():
    obj,clock,mouse,lease=subject()
    obj.authorize=lambda _a:clock.t<1.10
    assert not obj.deliver(Intent('hold',Point(.4,.4),Action.TROOP_DEPLOYMENT,'troop',.25,deadline=4),lambda:True)
    assert mouse.events[-1]=='up' and lease.flags[-1] is False

@pytest.mark.parametrize('state',[Intervention.DETECTED,Intervention.UNKNOWN])
def test_intervention_or_unhealthy_monitor_has_no_new_input(state):
    obj,_,mouse,_=subject(); obj.monitor=lambda:state
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert not mouse.events


def test_wider_action_or_wrong_verb_cannot_be_smuggled():
    obj,_,mouse,_=subject()
    intent=Intent('end_battle',Point(.1,.7),Action.RETURN_HOME,deadline=4)
    object.__setattr__(intent,'action',Action.TROOP_DEPLOYMENT)
    assert not obj.deliver(intent,lambda:True)
    assert not mouse.events


def test_forged_scroll_intent_has_no_native_or_prepare_events():
    obj,_,mouse,lease=subject()
    intent=Intent('tap',Point(.2,.9),Action.TROOP_DEPLOYMENT,'troop',deadline=4)
    object.__setattr__(intent,'verb','scroll_right')
    object.__setattr__(intent,'destination',Point(.8,.9))
    object.__setattr__(intent,'duration',.1)

    assert not obj.deliver(intent,lambda:True)
    assert mouse.events==[] and lease.flags==[]


@pytest.mark.parametrize('field,value',[
    ('verb','unsupported'),
    ('card_id',None),
    ('card_id','INVALID CARD'),
    ('duration',.2),
    ('deadline',True),
])
def test_post_construction_intent_mutation_is_rejected_at_delivery_ingress(field,value):
    obj,_,mouse,lease=subject()
    intent=Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4)
    object.__setattr__(intent,field,value)

    assert not obj.deliver(intent,lambda:True)
    assert mouse.events==[] and lease.flags==[]


def test_mutated_point_is_revalidated_at_delivery_ingress():
    obj,_,mouse,lease=subject()
    point=Point(.4,.4)
    intent=Intent('tap',point,Action.TROOP_DEPLOYMENT,'zap',deadline=4)
    object.__setattr__(point,'x',float('nan'))

    assert not obj.deliver(intent,lambda:True)
    assert mouse.events==[] and lease.flags==[]


def test_cleanup_is_idempotent_and_not_a_new_press():
    obj,_,mouse,_=subject()
    assert obj.release() and obj.release() and not mouse.events


def test_releasing_failure_remains_held_for_parent_cleanup():
    obj,_,mouse,lease=subject()
    def up(): mouse.events.append('up-failed'); raise RuntimeError()
    mouse.up=up
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert lease.flags==[True] and obj.held


def test_silent_or_removed_hook_blocks_down_even_with_timer_health():
    obj,_,mouse,_=subject()
    obj.observed_events=lambda:0
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert 'down' not in mouse.events


def test_missing_up_observation_keeps_parent_release_obligation():
    obj,_,mouse,lease=subject()
    obj.observed_events=lambda:sum(e in ('move','down') for e in mouse.events)
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert obj.held and lease.flags==[True]


def test_final_target_check_revalidates_exact_binding():
    mouse, calls = tagged_mouse_with_invalid_binding()
    with pytest.raises(DeploymentError, match='BINDING_INVALIDATED'):
        mouse.at_target(Point(.4, .4))
    assert len(calls) == 1


def test_mouse_down_revalidates_exact_binding_at_mutation_seam():
    mouse, calls = tagged_mouse_with_invalid_binding()
    with pytest.raises(DeploymentError, match='BINDING_INVALIDATED'):
        mouse.down()
    assert len(calls) == 1
