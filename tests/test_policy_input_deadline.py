"""PLAN_READY behavioral regressions. All physical leaves are inert."""
from dataclasses import replace
import pytest
from test_deployment_input import subject
from clash_rush_rebuild.mvp_deployment import Action, Intent, Point
from clash_rush_rebuild.mvp_deployment_worker import retire_mouse


def test_unattributed_counter_cannot_prove_down_or_release():
    sink, clock, mouse, lease = subject()
    count = 0
    def unrelated_counter():
        nonlocal count
        count += 1
        return count
    sink.observed_events = unrelated_counter
    assert not sink.deliver(Intent('tap', Point(.4, .4), Action.TROOP_DEPLOYMENT,
                                   'zap', deadline=4), lambda: True)
    assert 'down' not in mouse.events and 'up' not in mouse.events


def test_foreign_input_during_hold_invalidates_release_ownership():
    from clash_rush_rebuild.mvp_deployment import Intervention
    sink, clock, mouse, lease = subject()
    sink.monitor = lambda: Intervention.DETECTED if 'down' in mouse.events else Intervention.CLEAR
    assert not sink.deliver(Intent('hold', Point(.4, .4), Action.TROOP_DEPLOYMENT,
                                   'troop', .25, deadline=4), lambda: True)
    assert 'up' not in mouse.events


def test_deadline_owned_release_is_once_and_never_success():
    sink, clock, mouse, lease = subject()
    wait = clock.wait
    def advance(seconds):
        if 'down' in mouse.events:
            clock.t = 4.
        else:
            wait(seconds)
    sink.wait = advance
    assert not sink.deliver(Intent('hold', Point(.4, .4), Action.TROOP_DEPLOYMENT,
                                   'troop', .25, deadline=4), lambda: True)
    assert mouse.events.count('up') == 1
    assert sink.emergency_release_required
    sink.release()
    assert mouse.events.count('up') == 1


def test_ambiguous_down_does_not_authorize_guessed_release():
    sink, clock, mouse, lease = subject()
    mouse.fail = True
    assert not sink.deliver(Intent('tap', Point(.4, .4), Action.TROOP_DEPLOYMENT,
                                   'zap', deadline=4), lambda: True)
    assert 'down' in mouse.events
    assert 'up' not in mouse.events


def test_ambiguous_up_is_never_replayed_by_duplicate_cleanup():
    sink, clock, mouse, lease = subject()
    def up():
        mouse.events.append('up')
        raise RuntimeError('synthetic delivery ambiguity')
    mouse.up = up
    assert not sink.deliver(Intent('tap', Point(.4, .4), Action.TROOP_DEPLOYMENT,
                                   'zap', deadline=4), lambda: True)
    assert not sink.release()
    assert not sink.release()
    assert mouse.events.count('up') == 1


def test_parent_cannot_release_from_historical_held_bit():
    class HistoricalLease:
        held = True
        marker = 42
        def revoke(self): pass
        def set_held(self, value): self.held = value
    calls = []
    assert retire_mouse(HistoricalLease(), lambda marker: calls.append(marker)) is False
    assert calls == []


@pytest.mark.parametrize('corruption',['kind','marker','sequence','future','repeated','gap'])
def test_exact_down_attribution_not_other_tagged_activity(corruption):
    sink, clock, mouse, lease = subject()
    original=mouse.observe
    def observe(kind):
        original(kind)
        if kind!='DOWN': return
        event=mouse.observations[-1]
        if corruption=='kind': mouse.observations[-1]=replace(event,kind='MOVE')
        if corruption=='marker': mouse.observations[-1]=replace(event,marker=event.marker+1)
        if corruption=='sequence': mouse.observations[-1]=replace(event,sequence=event.sequence+1)
        if corruption=='future': mouse.observations[-1]=replace(event,observed=99.)
        if corruption=='repeated': mouse.observations.append(event)
        if corruption=='gap': mouse.observations.pop(0)
    mouse.observe=observe
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert 'down' in mouse.events and 'up' not in mouse.events
    assert sink.uncertain
    assert not sink.release() and not sink.release()


def test_exact_late_down_can_only_get_one_owned_inverse():
    sink, clock, mouse, lease=subject()
    down=mouse.down
    def late():
        clock.t=4.
        down()
    mouse.down=late
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert mouse.events==['prepare','move','down','up']
    assert sink.emergency_release_required and sink.uncertain
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=9),lambda:True)
    assert mouse.events==['prepare','move','down','up']


def test_lost_generation_or_untyped_owner_denies_before_prepare():
    sink, clock, mouse, lease=subject()
    sink.owner=None
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    assert mouse.events==[]


def test_duplicate_cleanup_after_success_adds_no_event_or_receipt():
    sink, clock, mouse, lease=subject()
    assert sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,'zap',deadline=4),lambda:True)
    receipts=tuple(sink.receipts)
    assert [r.kind for r in receipts]==['MOVE','DOWN','UP']
    assert len({r.marker for r in receipts})==3
    clock.t=9.
    assert sink.release() and sink.release()
    assert tuple(sink.receipts)==receipts


def test_real_monitor_classifier_rejects_duplicate_and_wrong_kind_without_hooks():
    from clash_rush_rebuild.mvp_deployment import Intervention
    from clash_rush_rebuild.mvp_deployment_input import WindowsInterventionMonitor
    monitor=WindowsInterventionMonitor(42)  # No start(): no hooks or input.
    monitor.state=lambda:Intervention.CLEAR
    assert monitor.arm('DOWN',123)
    monitor._observe_mouse('DOWN',123,True,1.)
    assert len(monitor.events())==1
    monitor._observe_mouse('DOWN',123,True,1.1)
    assert monitor.foreign and len(monitor.events())==1
    assert not monitor.arm('DOWN',123)
    other=WindowsInterventionMonitor(43)
    other.state=lambda:Intervention.CLEAR
    assert other.arm('DOWN',124)
    other._observe_mouse('UP',124,True,1.)
    assert other.foreign and other.events()==()


def test_desktop_loss_does_not_authorize_emergency_release():
    sink, clock, mouse, lease = subject()
    sink.ownership_live = lambda: 'down' not in mouse.events
    assert not sink.deliver(Intent('hold', Point(.4,.4), Action.TROOP_DEPLOYMENT,
                                   'troop', .25, deadline=4), lambda: True)
    assert 'down' in mouse.events and 'up' not in mouse.events
    assert not sink.release()


def test_move_preparation_crossing_deadline_never_submits_move():
    sink, clock, mouse, lease = subject()
    class PreparedMouse(type(mouse)):
        def prepare_move(self, point):
            clock.t = 4.
            return point
        def commit_move(self, point):
            return self.move(point)
    mouse.__class__ = PreparedMouse
    assert not sink.deliver(Intent('tap', Point(.4,.4), Action.TROOP_DEPLOYMENT,
                                   'zap', deadline=4), lambda: True)
    assert mouse.events == ['prepare']


def test_receipt_writer_cannot_prevent_owned_release():
    sink, clock, mouse, lease = subject()
    calls=[]
    def persist(receipt, emergency):
        calls.append(receipt.kind)
        if receipt.kind=='DOWN':
            assert 'up' in mouse.events
            raise OSError('synthetic storage failure')
    sink.after_event=persist
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,
                                   'zap',deadline=4),lambda:True)
    assert mouse.events.count('up')==1
    assert calls==['MOVE','DOWN']
    assert sink.uncertain


def test_release_never_waits_on_pre_event_storage_callback():
    sink, clock, mouse, lease = subject()
    calls=[]
    def persist(owner, gesture, event, kind, marker, started):
        calls.append(kind)
        if kind=='UP':
            clock.t=9.
            raise OSError('synthetic storage stall')
        return True
    sink.before_event=persist
    assert sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,
                              'zap',deadline=4),lambda:True)
    assert mouse.events.count('up')==1
    assert calls==['MOVE','DOWN']


def test_deadline_crossing_last_release_guard_gets_only_one_inverse():
    from clash_rush_rebuild.mvp_deployment_input import HeldState
    sink, clock, mouse, lease=subject()
    authorize=sink.authorize
    def delayed(action):
        if sink.state is HeldState.RELEASE_SUBMITTING:
            clock.t=4.
        return authorize(action)
    sink.authorize=delayed
    assert not sink.deliver(Intent('tap',Point(.4,.4),Action.TROOP_DEPLOYMENT,
                                  'zap',deadline=4),lambda:True)
    assert mouse.events==['prepare','move','down','up']
    assert sink.uncertain and sink.emergency_release_required
    sink.release()
    assert mouse.events.count('up')==1
