import hashlib
import json
from pathlib import Path
import copy
import os
import subprocess
import cv2
import numpy as np
import pytest
from clash_rush_rebuild.mvp_deployment import DeploymentError, Screen, Intervention, CardKind, Spell
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile, FrameObserver, load_profile, read_private


def redirect_directory(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name != 'nt' or getattr(error, 'winerror', None) != 1314:
            raise
        result = subprocess.run(
            ['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip('directory redirection unavailable on this Windows host')


def profile_data():
    rng=np.random.default_rng(410)
    assets={k:rng.integers(0,255,(9,9,3),dtype=np.uint8) for k in
            ['a','b','card','available','selected','depleted','deployed','blocker','otherblocker',
             'frame_tl','frame_tr','frame_bl','frame_br','left_end','left_more','right_end','right_more']+
            list('x0123456789')}
    loc=lambda k,box=[0.,0.,1.,1.]:{'template':k,'roi':box}
    screens={s.value:[loc('a'),loc('b')] for s in Screen if s is not Screen.UNKNOWN}
    # Other screens use different marker ROIs rather than sharing proof locations.
    for s in screens:
        if s!='HOME':screens[s]=[loc('a',[.5,.5,1.,1.]),loc('b',[.5,.5,1.,1.])]
    data=dict(schema=2,profile_id='synthetic',reviewed_tree='a'*40,runtime_reviewed=False,size=[640,360],
       assets={k:{'file':k+'.png','sha256':'b'*64} for k in assets},screens=screens,
       controls={k:{v:loc('a')} for k,v in [('HOME','attack'),('MATCH','find_match'),('ARMY','army_attack'),
                 ('BATTLE','end_battle'),('END_CONFIRM','confirm_end'),('RESULT','return_home')]},
       bar={'roi':[0.,.82,1.,.98],
          'left_edge':{'end':loc('left_end'),'continues':loc('left_more')},
          'right_edge':{'end':loc('right_end'),'continues':loc('right_more')},
          'frame':[loc('frame_tl',[0.,0.,.3,.3]),loc('frame_tr',[.7,0.,1.,.3]),
                   loc('frame_bl',[0.,.7,.3,1.]),loc('frame_br',[.7,.7,1.,1.])],
          'geometry':dict(card_width=.068,width_tolerance=.010,peak_height=.8,peak_distance=10)},
       cards=[dict(identity='troop',kind='TROOP',spell='NONE',capacity=99,icons=['card'],icon_roi=[.2,.2,.8,.6],
                   count_roi=[.2,.6,.8,1.],available=['available'],depleted=['depleted'],deployed=[],selected=['selected'],
                   state_rois={'available':[0.,0.,.3,.3],'depleted':[.7,0.,1.,.3],
                               'deployed':[0.,.7,.3,1.],'selected':[.7,.7,1.,1.]})],
       plan={'layout':'SINGLE_PAGE','viewport_limit':6,
             'roster':[{'identity':'troop','initial_quantity':1}]},
       glyphs={k:k for k in 'x0123456789'},targets=[],
       ground=dict(marker=loc('a'),overlay_hsv=[[0,100,100],[10,255,255]],playfield=[[.1,.1],[.9,.1],[.9,.8],[.1,.8]],margin_pixels=10),
       view_anchors=[loc('a'),loc('b'),loc('card')],blockers=[loc('blocker'),loc('otherblocker')])
    return data,assets


def write_pack(project,runtime=False):
    data,assets=profile_data();data['runtime_reviewed']=runtime
    folder=project/'private/deployment/templates';folder.mkdir(parents=True)
    for name,a in assets.items():
        ok,buf=cv2.imencode('.png',a);assert ok
        raw=bytes(buf);(folder/(name+'.png')).write_bytes(raw)
        data['assets'][name]['sha256']=hashlib.sha256(raw).hexdigest()
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()+b'\n'
    (folder.parent/'profile.json').write_bytes(raw)
    return data,assets


def test_unreviewed_pack_cannot_enable_runtime(tmp_path):
    write_pack(tmp_path)
    with pytest.raises(DeploymentError):load_profile(tmp_path,candidate_tree='a'*40)
    assert load_profile(tmp_path,candidate_tree='a'*40,runtime=False).data['profile_id']=='synthetic'


def test_reviewed_pack_is_tree_and_byte_bound(tmp_path):
    write_pack(tmp_path,True)
    p=load_profile(tmp_path,candidate_tree='a'*40)
    assert not p.assets['card'].flags.writeable
    with pytest.raises(DeploymentError):load_profile(tmp_path,candidate_tree='c'*40)
    (tmp_path/'private/deployment/templates/card.png').write_bytes(b'wrong')
    with pytest.raises(DeploymentError):load_profile(tmp_path,candidate_tree='a'*40)


def test_profile_compiles_exact_single_page_plan_from_private_schema():
    data,assets=profile_data()
    profile=DeploymentProfile(data,assets,'a'*64)
    sealed=profile.assets['card'].payload
    data['plan']['roster'][0]['initial_quantity']=7
    assets['card'].fill(0)
    assert tuple(entry[0] for entry in profile.plan.roster)==('troop',)
    assert profile.plan.quantities==(1,)
    assert profile.plan.viewport_limit==6
    assert profile.assets['card'].payload==sealed
    with pytest.raises(TypeError):profile.plan=type(profile.plan)((('other',CardKind.TROOP,Spell.NONE,1),),6)
    with pytest.raises(TypeError):profile.data.items_tuple=()
    with pytest.raises(DeploymentError,match='PLAN_DIGEST_MISMATCH'):
        type(profile.plan)(profile.plan.roster,profile.plan.viewport_limit,'0'*64)


def test_no_symlink_or_outside_profile_read(tmp_path):
    real=tmp_path/'real';real.mkdir();(real/'secret').write_bytes(b'synthetic')
    project=tmp_path/'project';(project/'private').mkdir(parents=True)
    redirect_directory(project/'private/deployment',real)
    with pytest.raises(DeploymentError):read_private(project,'private/deployment/secret',30)
    with pytest.raises(DeploymentError):read_private(project,'private/deployment/../../real/secret',30)


def test_symlink_project_ancestor_rejected(tmp_path):
    real=tmp_path/'real';(real/'project/private/deployment').mkdir(parents=True)
    (real/'project/private/deployment/profile.json').write_bytes(b'{}')
    alias=tmp_path/'alias';redirect_directory(alias,real)
    with pytest.raises(DeploymentError):read_private(alias/'project','private/deployment/profile.json',30)


def test_real_frame_observer_clears_pixels_and_produces_home_scalars():
    data,assets=profile_data();p=DeploymentProfile(data,assets,'a'*64)
    frame=np.zeros((360,640,3),np.uint8)
    frame[25:34,25:34]=assets['a'];frame[45:54,45:54]=assets['b']
    observer=FrameObserver(profile=p,capture=lambda:frame,monotonic=lambda:1.,
                           intervention=lambda:Intervention.CLEAR,home_verified=lambda _:True)
    result=observer()
    assert result.screen is Screen.HOME and result.home_verified
    assert not frame.any() and result.page is None


def test_known_forbidden_overlay_prevents_underlying_control_proof():
    data,assets=profile_data();p=DeploymentProfile(data,assets,'a'*64)
    frame=np.zeros((360,640,3),np.uint8)
    for i,k in enumerate(['a','b','blocker']):frame[20+i*20:29+i*20,25:34]=assets[k]
    observer=FrameObserver(profile=p,capture=lambda:frame,monotonic=lambda:1.,
                           intervention=lambda:Intervention.CLEAR,home_verified=lambda _:True)
    r=observer();assert r.screen is Screen.UNKNOWN and r.controls==()
    assert not frame.any()


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(runtime_reviewed=1),
    lambda d:d['cards'][0].update(kind='UNKNOWN'),
    lambda d:d['cards'][0].update(kind='SPELL',spell='UNKNOWN'),
    lambda d:d.update(blockers=[]),
    lambda d:d['ground'].update(playfield=[[False,.1],[.9,.1],[.9,.8]]),
])
def test_bad_profile_rejected_before_observation(mutation):
    d,a=profile_data();mutation(d)
    with pytest.raises((DeploymentError,ValueError)):DeploymentProfile(d,a,'a'*64)
