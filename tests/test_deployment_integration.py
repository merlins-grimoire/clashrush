from dataclasses import replace
import pytest
from test_deployment_repair import World,card
from clash_rush_rebuild.mvp_deployment import Intervention, DeploymentError, CompiledDeploymentPlan
from clash_rush_rebuild.mvp_deployment_integration import NativeDeploymentExecutor

class Monitor:
    def __init__(self):self.started=False;self.closed=False;self.failed=False
    def start(self):self.started=True
    def state(self):return Intervention.CLEAR
    def events(self):return 0
    def close(self):self.closed=True;return not self.failed


def compose(w,monitor=None,record=None):
    m=monitor or Monitor();records=[]
    plan=CompiledDeploymentPlan(tuple((c.identity,c.kind,c.spell,c.remaining)
                                      for c in w.cards),6)
    e=NativeDeploymentExecutor(observe=w.observe,deliver=w.deliver,release=w.release,
         live_gate=lambda:True,monotonic=w.clock,wait=w.clock.sleep,deadline=100.,
         monitor=m,record_result=record or records.append,plan=plan)
    return e,m,records


def test_real_engine_result_is_persisted_before_true_return():
    w=World([card(count=1)]);e,m,records=compose(w)
    assert e.run()==(True,'AUTONOMOUS_DEPLOYMENT_COMPLETE')
    assert len(records)==1 and records[0].complete and m.closed


def test_monitor_cleanup_failure_never_records_autonomous_success():
    w=World([card(count=1)]);m=Monitor();m.failed=True;e,_,records=compose(w,m)
    success,_=e.run();assert not success
    assert len(records)==1 and not records[0].complete and not records[0].intervention_free


def test_receipt_failure_after_complete_input_is_not_true_return():
    w=World([card(count=1)])
    def record(_):raise RuntimeError('private receipt failure')
    e,m,_=compose(w,record=record)
    with pytest.raises(DeploymentError) as error:e.run()
    assert 'private' not in str(error.value) and w.closed and m.closed


def test_monitor_unavailable_no_field_input_but_records_incomplete():
    w=World([card(count=1)]);m=Monitor()
    def start():raise RuntimeError('not ready')
    m.start=start;e,_,records=compose(w,m)
    assert e.run()[0] is False and not w.events and not records[0].complete


def test_native_factory_passes_monitor_callable_not_monitor_object(monkeypatch):
    import sys
    from types import ModuleType,SimpleNamespace
    from clash_rush_rebuild import mvp_deployment_input as inp
    from clash_rush_rebuild import mvp_deployment_profile as prof
    from clash_rush_rebuild import mvp_deployment_receipt as rec
    from clash_rush_rebuild.mvp_deployment_integration import build_native_executor
    module=ModuleType('clash_rush_rebuild.input_authorization')
    from clash_rush_rebuild.mvp_deployment import Action
    module.InputAction=Action
    monkeypatch.setitem(sys.modules,module.__name__,module)
    w=World([card(count=1)]);m=Monitor()
    monkeypatch.setattr(inp,'WindowsInterventionMonitor',lambda _:m)
    monkeypatch.setattr(inp,'TaggedWin32Mouse',lambda *_:object())
    class Delivery:
        def __init__(self,**kwargs):
            assert callable(kwargs['monitor'])
            assert kwargs['monitor']() is Intervention.CLEAR
        deliver=staticmethod(w.deliver)
        release=staticmethod(w.release)
    monkeypatch.setattr(inp,'GuardedDeploymentInput',Delivery)
    monkeypatch.setattr(prof,'FrameObserver',lambda **_:w.observe)
    monkeypatch.setattr(rec,'require_profile',lambda *_:None)
    a=SimpleNamespace(transaction=lambda _:SimpleNamespace(run_nonce='1'*32))
    e=build_native_executor(binding=object(),bound_input=object(),capture_bgr=lambda:None,
        home_verified=lambda _:True,enabled=lambda:True,authority=a,transaction_ref='tx',
        profile=SimpleNamespace(digest='a'*64,
            plan=CompiledDeploymentPlan((('troop',w.cards[0].kind,w.cards[0].spell,1),),6)),
        lease=SimpleNamespace(marker=42,deadline=100.,active=lambda:True))
    assert isinstance(e,NativeDeploymentExecutor)
