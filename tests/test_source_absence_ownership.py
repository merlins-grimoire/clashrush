"""B review regressions and local absence closure; synthetic pixels only."""
import copy
import json
import numpy as np
import pytest

from test_source_geometry import geometry_pack
from test_source_empty_support import empty_support
from test_s004_source_images import observe
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile


@pytest.mark.parametrize('key', ['source_a','context_source','green_attack','ordinary_header','ordinary_status'])
@pytest.mark.parametrize('duplicate', [False, True])
def test_numeric_alias_cannot_disable_semantic_global_ownership(key, duplicate):
    data,assets,frame=geometry_pack()
    layout=data['source_layout']
    layout['quantity']={'threshold':160,'minimum_component_area':1,
                        'glyphs':{k:[k] for k in 'x0123456789'}}
    layout['quantity']['glyphs']['x'].append(key)
    if duplicate:
        art=assets[key]
        frame[330:330+art.shape[0],530:530+art.shape[1]]=art
        with pytest.raises(DeploymentError):observe(data,assets,frame)
    else:
        assert observe(data,assets,frame).source_inventory


def whole_background(category='clan'):
    data,assets,frame=geometry_pack();empty_support(data)
    # The support policy changed after geometry_pack rendered a populated slot.
    # Rebuild its honest empty pixels before making a whole-background witness.
    c=data['source_layout']['categories'][3]
    for box,field in zip(c['slots'],c['geometry']['fields']):
        l,t,r,b=[int(v*s) for v,s in zip(box,(640,360,640,360))]
        frame[t:b,l:r]=assets[field['vacant']]
    c=next(c for c in data['source_layout']['categories'] if c['category']==category)
    g=c['geometry'];box=g['extent']
    l,t,r,b=[int(v*s) for v,s in zip(box,(640,360,640,360))]
    frame[t:b,l:r]=np.random.default_rng(1).integers(20,100,(b-t,r-l,3),dtype=np.uint8)
    key=category+'_whole';assets[key]=frame[t:b,l:r].copy()
    data['assets'][key]={'file':key+'.png','sha256':'b'*64}
    c['slots']=[];g.update(fields=[],background=[{'template':key,'roi':box}])
    return data,assets,frame,c,(l,t,r,b)


@pytest.mark.parametrize('category', ['clan','siege','hero'])
@pytest.mark.parametrize('size', [1,3,9])
def test_whole_background_cannot_average_away_unknown_partial_content(category,size):
    data,assets,frame,c,(l,t,r,b)=whole_background(category)
    baseline=observe(data,assets,frame.copy())
    assert (category,c['state']) in baseline.source_categories
    art=assets['card'][:size,:size];x,y=l+25,t+35
    frame[y:y+size,x:x+size]=art
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_unused_background_rejects_single_changed_pixel():
    data,assets,frame=geometry_pack();rule=data['source_layout']['categories'][0]['geometry']['background'][0]
    l,t,r,b=[int(v*s) for v,s in zip(rule['roi'],(640,360,640,360))]
    frame[t,l,0]^=255
    with pytest.raises(DeploymentError):observe(data,assets,frame)


@pytest.mark.parametrize('delta,accepted', [(0,True),(8,True),(9,False)])
def test_bounded_local_noise_tolerance_is_not_an_area_average(delta,accepted):
    data,assets,frame,c,(l,t,r,b)=whole_background()
    c['geometry']['maximum_pixel_delta']=8
    # Noise throughout the field passes at the exact declared boundary.
    frame[t:b,l:r]+=np.uint8(min(delta,8))
    frame[t+30,l+20,0]+=np.uint8(max(0,delta-8))
    if accepted:assert observe(data,assets,frame).source_categories[3]==('clan','EMPTY')
    else:
        with pytest.raises(DeploymentError):observe(data,assets,frame)


@pytest.mark.parametrize('value', [True,-1,17,1.0,None,'8'])
def test_absence_tolerance_has_closed_ingress(value):
    data,assets,_,c,_=whole_background();c['geometry']['maximum_pixel_delta']=value
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)


@pytest.mark.parametrize('fault', ['missing','wrong_size','grey','too_many','not_list'])
def test_absence_alternatives_are_bounded_whole_color_templates(fault):
    data,assets,_,c,_=whole_background();rule=c['geometry']['background'][0]
    key='alternative';assets[key]=assets[rule['template']].copy()
    data['assets'][key]={'file':key+'.png','sha256':'b'*64};rule['alternatives']=[key]
    if fault=='missing':rule['alternatives']=['absent']
    elif fault=='wrong_size':assets[key]=assets[key][1:]
    elif fault=='grey':assets[key]=assets[key][:,:,0]
    elif fault=='too_many':rule['alternatives']=[key]*8
    else:rule['alternatives']=key
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)


def test_alternatives_match_one_complete_observation_not_pixelwise_union():
    data,assets,frame,c,(l,t,r,b)=whole_background();rule=c['geometry']['background'][0]
    key='alternative';assets[key]=assets[rule['template']].copy()
    assets[key][20:25,20:25]=180;assets[key][40:45,40:45]=210
    data['assets'][key]={'file':key+'.png','sha256':'b'*64};rule['alternatives']=[key]
    assert observe(data,assets,frame.copy()).source_inventory
    frame[t:b,l:r]=assets[key]
    assert observe(data,assets,frame.copy()).source_inventory
    frame[t+20:t+25,l+20:l+25]=assets[rule['template']][20:25,20:25]
    with pytest.raises(DeploymentError):observe(data,assets,frame)


@pytest.mark.parametrize('geometry', [False,True])
def test_vacant_slot_uses_same_local_absence_check_only_in_explicit_geometry(geometry):
    data,assets,frame=geometry_pack();c=data['source_layout']['categories'][1]
    box=c['slots'][0];l,t,r,b=[int(v*s) for v,s in zip(box,(640,360,640,360))]
    frame[t+40,l+40,0]=64
    if not geometry:
        for category in data['source_layout']['categories']:del category['geometry']
        assert observe(data,assets,frame).source_categories[1]==('hero','EMPTY')
    else:
        with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_vacant_slot_alternatives_and_noise_share_geometry_policy():
    data,assets,frame=geometry_pack();c=data['source_layout']['categories'][1];g=c['geometry']
    field=g['fields'][0];box=c['slots'][0];l,t,r,b=[int(v*s) for v,s in zip(box,(640,360,640,360))]
    key='vacant_variant';assets[key]=assets[field['vacant']].copy();assets[key][40:43,40:43]=64
    data['assets'][key]={'file':key+'.png','sha256':'b'*64};field['vacant_alternatives']=[key];g['maximum_pixel_delta']=2
    frame[t:b,l:r]=assets[key];frame[t+40,l+40,0]+=2
    assert observe(data,assets,frame.copy()).source_categories[1]==('hero','EMPTY')
    frame[t+40,l+40,0]+=1
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_absence_configuration_is_sealed_detached_and_digest_bound(tmp_path,monkeypatch):
    import test_deployment_profile as fixture
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    data,assets,_,c,_=whole_background();g=c['geometry'];g['maximum_pixel_delta']=0
    rule=g['background'][0];key='alternative';assets[key]=assets[rule['template']].copy();assets[key][20,20,0]+=1
    data['assets'][key]={'file':key+'.png','sha256':'b'*64};rule['alternatives']=[key]
    monkeypatch.setattr(fixture,'profile_data',lambda:(data,assets));fixture.write_pack(tmp_path,runtime=False)
    first=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    path=tmp_path/'private/deployment/profile.json';raw=json.loads(path.read_text())
    raw['source_layout']['categories'][3]['geometry']['maximum_pixel_delta']=1
    path.write_bytes(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()+b'\n')
    second=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    assert first.digest!=second.digest and first.plan.digest!=second.plan.digest
    raw['source_layout']['categories'][3]['geometry']['background'][0]['alternatives']=[]
    path.write_bytes(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()+b'\n')
    third=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    assert second.digest!=third.digest and second.plan.digest!=third.plan.digest
    g['maximum_pixel_delta']=16;rule['alternatives'].clear();assets[key].fill(0)
    frozen=first.data['source_layout']['categories'][3]['geometry']
    assert frozen['maximum_pixel_delta']==0 and frozen['background'][0]['alternatives']==(key,)
    assert np.any(first.assets[key].array())
