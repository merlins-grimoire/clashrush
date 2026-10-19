"""Cross-store closure permutations with real SQLite + durable Windows anchor."""
import pytest
from clash_rush_rebuild import mvp_frozen_stop as stop
from clash_rush_rebuild.mvp_canonical_runtime import CanonicalAdmission
from canonical_fixture import install_fixture


@pytest.fixture
def connected(tmp_path,monkeypatch):
    import test_policy_parent_session as parent
    import test_calibration_retirement as calibration
    original=parent.compose
    def compose(root):
        result=original(root)
        result[0].canonical=install_fixture(root,monkeypatch)
        return result
    monkeypatch.setattr(parent,'compose',compose)
    monkeypatch.setattr(calibration,'compose',compose)
    monkeypatch.setattr(stop,'_monotonic_ns',lambda:1_000_000_000)
    monkeypatch.setattr(stop,'_wall_ns',lambda:1_000_000_000)
    return tmp_path


@pytest.mark.parametrize('lane',['calibration','normal'])
@pytest.mark.parametrize('cut',['none','stop_intent','physical','handles','player_proof','ready',
                                'sqlite_closed','host_closed','guard_delete'])
def test_cross_store_cuts_never_replay_physical_stop(connected,monkeypatch,lane,cut):
    from test_calibration_retirement import captured
    from test_policy_normal_retirement import session_success
    session,host,state,clock,mutex=(captured if lane=='calibration' else session_success)(connected)
    def crash(*a,**kw):raise RuntimeError('disposable crash cut')
    if cut=='stop_intent':
        original=session.canonical.stop_intent
        def interrupted(*a):original(*a);crash()
        session.canonical.stop_intent=interrupted
    elif cut=='physical':
        original=host.terminate_job
        def interrupted(job):original(job);crash()
        host.terminate_job=interrupted
    elif cut=='handles':host.close_handle=crash
    elif cut=='player_proof':session.authority.record_retirement_v3=crash
    elif cut=='ready':session.authority.close_v3=crash
    elif cut=='sqlite_closed':session.canonical.closed=crash
    elif cut=='host_closed':
        original=session.canonical.closed
        def interrupted(*a,**kw):original(*a,**kw);crash()
        session.canonical.closed=interrupted
    elif cut=='guard_delete':state._port.delete_file_no_reparse=crash
    retire=session.retire_calibration if lane=='calibration' else session.retire_normal_success
    try:
        if cut=='none':
            result=retire(mutex=mutex,expected_title='Synthetic')
            assert session.canonical.anchor.load()['phase']=='CLOSED'
            assert session.canonical.stopped(session.authority,state)['phase']=='CLOSED'
            before=list(host.calls)
            assert retire(mutex=mutex,expected_title='Synthetic')==result
            assert host.calls==before
        else:
            with pytest.raises((RuntimeError,OSError)):retire(mutex=mutex,expected_title='Synthetic')
            anchor=session.canonical.anchor
            reopened=CanonicalAdmission(anchor,'a'*64,project=connected,package='synthetic',
                executable='synthetic',coverage=lambda:None)
            with pytest.raises((RuntimeError,OSError)):reopened.stopped(session.authority,state)
            before=list(host.calls)
            with pytest.raises((RuntimeError,OSError)):retire(mutex=mutex,expected_title='Synthetic')
            assert host.calls==before
        if lane=='calibration':
            outcome=session.authority.outcome_v3(1)
            assert (outcome.outcome,outcome.reason)==('BLOCKED','EVIDENCE_INCOMPLETE')
    finally:session.authority.close()


def test_status_reports_separate_durable_dimensions_without_mutation(connected,monkeypatch):
    from test_calibration_retirement import captured
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
    session,host,state,clock,mutex=captured(connected)
    monkeypatch.setattr(ingress,'_require_private',lambda path:None)
    def reader(path,*,read_only):
        assert read_only is True
        return DurableSessionAuthority(connected/'session.sqlite3',read_only=True)
    monkeypatch.setattr(ingress,'DurableSessionAuthority',reader)
    monkeypatch.setattr(ingress,'LifecycleStateStore',lambda *a:state)
    try:
        before=list(host.calls)
        unresolved=ingress.read_runtime_dimensions(connected)
        assert unresolved==dict(outcome='BLOCKED',reason='EVIDENCE_INCOMPLETE',lifecycle='UNRESOLVED',host='PUBLISHED')
        assert host.calls==before
        session.retire_calibration(mutex=mutex,expected_title='Synthetic')
        before=list(host.calls)
        closed=ingress.read_runtime_dimensions(connected)
        assert closed==dict(outcome='BLOCKED',reason='EVIDENCE_INCOMPLETE',lifecycle='LIFECYCLE_VERIFIED',host='CLOSED')
        assert host.calls==before
    finally:session.authority.close()
