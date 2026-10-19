"""W consumes only query capabilities; all images here are synthetic bytes."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_policy_process_topology import InertHost,controller,prepare
from test_policy_finalization_v3 import binding_v3
from clash_rush_rebuild.lifecycle import PlayerBinding
from clash_rush_rebuild.mvp_local_native import PolicyPlayerView


def view(run=None,monotonic=None):
    from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology
    host=InertHost()
    topology=controller(host) if run is None else ParentProcessTopology(host,
        run_nonce=run.run_nonce,generation=run.generation)
    offer=prepare(topology);host.calls.clear()
    host.current_process_handle=lambda:offer.worker.pid
    host.current_process_id=host.current_process_handle
    host.identities[offer.remote_player_handle]=offer.player
    host.handle_is_inheritable=lambda h:False
    host.flags[offer.remote_player_job_query]=0
    host.jobs[offer.remote_player_job_query]=[offer.remote_player_handle]
    binding=PlayerBinding(offer.player,offer.player,100,101,640,360,'c'*32)
    clock=SimpleNamespace(now=1.)
    host._api=SimpleNamespace(input_desktop_identity=lambda:(2,7,'WinSta0','Default'),foreground_root=lambda:100)
    host.bind_exact_window=lambda *args:binding
    def require(b,job):
        assert b is binding and job==offer.remote_player_job_query
        host.calls.append(('binding',))
    host._require_capture_binding=require
    def capture(b, *, before_capture):
        before_capture()
        host.calls.append(('capture',))
        return 640,360,bytes([10,20,30,0])*(640*360)
    host.capture_bgra=capture
    if run is None:run=replace(binding_v3(),run_nonce=offer.run_nonce,started_ns=0,deadline_ns=10_000_000_000)
    result=PolicyPlayerView(host=host,offer=offer,run=run,enabled=lambda:True,
        clock=monotonic if monotonic is not None else lambda:clock.now)
    result.bind('synthetic window')
    host.calls.clear()
    return result,host,clock


def test_same_run_owner_checks_do_not_capture_focus_create_or_kill():
    v,host,clock=view()
    assert v.ownership_live()
    clock.now=10.
    assert v.ownership_live()  # inverse-release ownership is independent of D
    with pytest.raises(RuntimeError):v.capture_bgr()
    assert host.calls==[('binding',),('binding',)]
    v.close();v.close()
    assert host.calls[-2:]==[('close',v.offer.remote_player_handle),('close',v.offer.remote_player_job_query)]
    assert not v.ownership_live()


@pytest.mark.parametrize('fault',['desktop','thread','session','foreground','process','geometry'])
def test_lost_owner_is_latched_and_cannot_be_rebound(fault):
    v,host,clock=view()
    if fault in ('desktop','thread','session'):
        original=host._api.input_desktop_identity
        changed=list(original())
        index={'desktop':3,'thread':1,'session':0}[fault]
        changed[index]='Other' if fault=='desktop' else 8
        host._api.input_desktop_identity=lambda:tuple(changed)
    if fault=='foreground':host._api.foreground_root=lambda:999
    if fault=='process':host.wait_process=lambda *args:True
    if fault=='geometry':host._require_capture_binding=lambda *a:(_ for _ in ()).throw(RuntimeError('changed'))
    assert v.ownership_live() is False
    if fault in ('desktop','thread','session'):host._api.input_desktop_identity=original
    assert v.ownership_live() is False
    with pytest.raises(RuntimeError):v.bind('synthetic window')
    assert not [c for c in host.calls if c[0]=='capture']


def test_capture_is_bgr_and_deadline_follows_slow_readonly_preparation():
    v,host,clock=view()
    frame=v.capture_bgr()
    assert tuple(frame[0,0])==(10,20,30)
    assert frame.shape==(360,640,3)
    host.calls.clear()
    def late(*args):clock.now=10.
    host._require_capture_binding=late
    with pytest.raises(RuntimeError):v.capture_bgr()
    assert not [c for c in host.calls if c[0]=='capture']


def test_final_authority_check_crossing_deadline_cannot_start_capture():
    v,host,clock=view();checks=[]
    def enabled():
        checks.append(1)
        if len(checks)==2:clock.now=10.
        return True
    v.enabled=enabled
    with pytest.raises(RuntimeError):v.capture_bgr()
    assert not [c for c in host.calls if c[0]=='capture']


def test_late_frame_is_discarded_and_not_retained_by_consumer_traceback():
    v,host,clock=view()
    def late(*args, before_capture):
        before_capture()
        clock.now=10.
        return 640,360,bytes([10,20,30,0])*(640*360)
    host.capture_bgra=late
    with pytest.raises(RuntimeError) as failure:v.capture_bgr()
    tb=failure.value.__traceback__
    found=False
    while tb:
        if tb.tb_frame.f_code.co_name=='capture_bgr':
            found=True
            assert tb.tb_frame.f_locals['raw'] is None
            assert tb.tb_frame.f_locals['frame'] is None
        tb=tb.tb_next
    assert found


def test_first_handle_close_failure_does_not_skip_second_query_handle():
    v,host,clock=view();calls=[]
    def close(handle):
        calls.append(handle)
        if handle==v.offer.remote_player_handle:raise OSError('synthetic close failure')
    host.close_handle=close
    with pytest.raises(OSError):v.close()
    assert calls==[v.offer.remote_player_handle,v.offer.remote_player_job_query]
    assert not v.ownership_live()
