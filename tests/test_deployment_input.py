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
    InputOwner,
    ObservedInput,
    TaggedWin32Mouse,
)

class Clock:
    def __init__(self): self.t=1.
    def __call__(self): return self.t
    def wait(self,s): self.t+=s
class Mouse:
    def __init__(self): self.events=[]; self.fail=False; self.observations=[]
    def observe(self,kind):
        self.observations.append(ObservedInput(len(self.observations)+1,kind,self.marker,self.clock()))
    def prepare(self,p): self.events.append('prepare'); return True
    def move(self,p): self.events.append('move'); self.observe('MOVE'); return True
    def at_target(self,p): return True
    def down(self):
        self.events.append('down')
        if self.fail: raise RuntimeError('private-native-value')
        self.observe('DOWN')
    def up(self): self.events.append('up'); self.observe('UP')
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
    mouse.clock=clock
    obj=GuardedDeploymentInput(mouse=mouse,authorize=lambda _a:True,
        monitor=lambda:Intervention.CLEAR,lease=lease,monotonic=clock,wait=clock.wait,
        observed_events=lambda:tuple(mouse.observations),arm_event=lambda kind,marker:True,
        owner=InputOwner('a'*32,1,'b'*64,'c'*64),ownership_live=lambda:True)
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


def test_down_exception_does_not_authorize_guessed_release():
    obj,_,mouse,lease=subject(); mouse.fail=True
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert mouse.events[-1]=='down' and 'up' not in mouse.events and obj.uncertain


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
    with pytest.raises(DeploymentError,match='INTENT_INVALID'):
        Intent('end_battle',Point(.1,.7),Action.TROOP_DEPLOYMENT,deadline=4)
    assert not obj.deliver(Intent('end_battle',Point(.1,.7),Action.RETURN_HOME,deadline=4),lambda:True)
    assert not mouse.events


def test_cleanup_is_idempotent_and_not_a_new_press():
    obj,_,mouse,_=subject()
    assert obj.release() and obj.release() and not mouse.events


def test_releasing_failure_remains_uncertain_without_parent_takeover():
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


def test_missing_up_observation_keeps_uncertainty_not_repeat_release():
    obj,_,mouse,lease=subject()
    obj.observed_events=lambda:tuple(e for e in mouse.observations if e.kind!='UP')
    assert not obj.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert obj.held and lease.flags==[True]
    assert not obj.release() and mouse.events.count('up')==1


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
