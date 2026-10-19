"""Real source predicates over synthetic private files/pixels and inert OS leaves."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest

from test_policy_ingress_command import database, fixture_sources, approve, run_request
from test_s004_source_images import source_pack
from clash_rush_rebuild.mvp_session_authority import RunBindingV3, canonical_v3, digest_v3
from clash_rush_rebuild.mvp_deployment import Observation, Screen, Intervention
from clash_rush_rebuild import mvp_policy_sources as sources


def prepared_sources(root, monkeypatch, world=None):
    database(root)
    ingress, api, port = fixture_sources(root, monkeypatch)
    ingress.migrate_stopped(backup_name='backup.sqlite3')
    data, assets = source_pack() if world is None else (world.data, world.assets)
    data['runtime_reviewed'] = True  # synthetic external review, not runtime admission
    data['source_layout']['build'] = '18.600.7'

    templates = root/'private/deployment/templates'; templates.mkdir()
    for key, image in assets.items():
        ok, encoded = cv2.imencode('.png', image); assert ok
        raw = bytes(encoded); (templates/(key+'.png')).write_bytes(raw)
        data['assets'][key]['sha256'] = digest_v3(raw)
    raw = canonical_v3(data) + b'\n'
    (templates.parent/'profile.json').write_bytes(raw)
    slots = canonical_v3({'schema':2,'slots':[
        {'slot_id':f'slot-{i:03d}', 'internal_name':f'Pie64_{i}',
         'display_label':f'Synthetic {i}'} for i in range(5)]})
    (root/'private/slots.json').write_bytes(slots)
    config = '\n'.join(f'bst.instance.Pie64_{i}.{field}="{value}"' for i in range(5)
        for field,value in [('display_name',f'Synthetic {i}'),('fb_width',1280),('fb_height',720),('dpi',240),
                            ('adb_port',5555+i*10),('status.adb_port',5555+i*10)])
    conf = root/'synthetic.conf'; conf.write_text(config, encoding='utf-8')
    monkeypatch.setattr(sources, 'CONFIG', conf)
    descriptor = dict(schema=2, source_tree='a'*40, account_ref='account', profile_digest=digest_v3(raw),
        slot=0, slots_digest=digest_v3(slots), configuration_digest=digest_v3(conf.read_bytes()),
        instance_digest=digest_v3(b'Pie64_0'), version_code=None, slot_id='slot-000',
        identity_policy='IN_GAME_ACCOUNT_IDENTITY_NOT_REQUIRED_BY_POLICY')
    (templates.parent/'provider.json').write_bytes(canonical_v3(descriptor))
    request = run_request(root)
    payload = approve(root, port, purpose='RUN_V3', run=request)
    run = RunBindingV3(**request, approval_digest=digest_v3(payload))
    reader = sources.PolicyWorkerSources(root, run, clock=(lambda:100.) if world is None else world.clock)
    return reader, assets, ingress, api, port


def test_actual_profile_and_one_use_same_frame_instance_home(tmp_path, monkeypatch):
    from test_s004_image_integration import ImageWorld
    world = ImageWorld()
    world.clock.now = 100.
    reader, assets, *_ = prepared_sources(tmp_path, monkeypatch, world)
    frame = world.capture()
    assert reader.approval_current(reader.run)
    assert reader.home_verified(frame)
    observation = Observation(1,99.9,reader.profile.digest,Screen.HOME,None,(),(),Intervention.CLEAR,True)
    assert reader.instance_current(reader.run, observation)
    assert not reader.instance_current(reader.run, observation)  # cannot replay prior frame
    assert reader.provider is None and reader.package_status == 'UNAVAILABLE'


@pytest.mark.parametrize('field', ['approval','profile','provider','config','slots','other_run'])
def test_mutable_source_changes_permanently_revoke(tmp_path,monkeypatch,field):
    reader, assets, *_ = prepared_sources(tmp_path,monkeypatch)
    paths = dict(approval='var/approvals/policy-v3.json',profile='private/deployment/profile.json',
        provider='private/deployment/provider.json',config='synthetic.conf',slots='private/slots.json')
    if field=='other_run': run=replace(reader.run,generation=2)
    else:
        path=tmp_path/paths[field]; old=path.read_bytes(); path.write_bytes(old+b' '); run=reader.run
    assert not reader.approval_current(run)
    if field!='other_run':
        path.write_bytes(old)
        assert not reader.approval_current(reader.run)


def test_unsupported_home_cannot_pass_without_account_marker(tmp_path,monkeypatch):
    reader, assets, *_ = prepared_sources(tmp_path,monkeypatch)
    frame=np.zeros((360,640,3),np.uint8)
    assert reader.home_verified(frame) is False
    assert reader._home_time is None


@pytest.mark.parametrize('fault',['held','pressed','desktop','unavailable'])
def test_stopped_native_input_reader_uses_desktop_and_two_exact_samples(monkeypatch,fault):
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild import win32_lifecycle_host as native
    class Read:
        count=0
        def __call__(self,key):
            self.count+=1
            return (0x8000 if fault=='held' else 1) if fault in ('held','pressed') and self.count==300 else 0
    calls=[]
    def desktop():
        calls.append(1)
        if fault=='unavailable':raise RuntimeError('synthetic inaccessible desktop')
        return (1,2,'WinSta0','changed' if fault=='desktop' and len(calls)>1 else 'Default')
    read=Read()
    monkeypatch.setattr(native,'NativeLifecycleApi',lambda:SimpleNamespace(
        input_desktop_identity=desktop,_user32=SimpleNamespace(GetAsyncKeyState=read)))
    with pytest.raises(RuntimeError):ingress._read_input_state()


def test_stopped_native_input_positive_uses_no_physical_operation(monkeypatch):
    from clash_rush_rebuild import mvp_policy_ingress as ingress
    from clash_rush_rebuild import win32_lifecycle_host as native
    class Read:
        count=0
        def __call__(self,key):self.count+=1;return 0
    read=Read(); calls=[]
    monkeypatch.setattr(native,'NativeLifecycleApi',lambda:SimpleNamespace(
        input_desktop_identity=lambda:(calls.append('desktop') or (1,2,'WinSta0','Default')),
        _user32=SimpleNamespace(GetAsyncKeyState=read)))
    assert ingress._read_input_state()==(0,)*256
    assert read.count==512 and len(calls)==3
