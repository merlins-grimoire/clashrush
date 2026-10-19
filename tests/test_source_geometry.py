"""Complete, heterogeneous source panels; generated pixels, no game artwork."""
import copy
import json
import numpy as np
import pytest

from test_s004_source_images import source_pack, source_frame, observe
from test_deployment_vision import quantity
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile


def geometry_pack():
    data, assets = source_pack()
    frame = source_frame(data, assets)
    layout = data['source_layout']
    rng = np.random.default_rng(1604)
    # Unlike count glyphs, background is a whole-field witness, not a substring.
    for i, c in enumerate(layout['categories']):
        left, top, right, bottom = c['slots'][0]
        end = c['slots'][-1][3]
        gap = [left, c['slots'][0][3], right, c['slots'][1][1]]
        x0,y0,x1,y1 = int(left*640),int(gap[1]*360),int(right*640),int(gap[3]*360)
        frame[y0:y1,x0:x1] = rng.integers(0,80,(y1-y0,x1-x0,3),dtype=np.uint8)
        key = c['category']+'_background'
        assets[key] = frame[y0:y1,x0:x1].copy()
        data['assets'][key] = dict(file=key+'.png',sha256='b'*64)
        fields = [{k:copy.deepcopy(layout[k]) for k in
                   ('icon_roi','count_roi','state_roi','empty','available','selected','vacant')}
                  for _ in c['slots']]
        c['geometry'] = dict(extent=[left,top,right,end], fields=fields,
                             background=[dict(template=key,roi=gap)])
    return data, assets, frame


def test_explicit_geometry_uses_observed_counts_and_keeps_legacy_default():
    data, assets, frame = geometry_pack()
    result = observe(data,assets,frame)
    assert [e.quantity for e in result.source_inventory] == [1,4]
    old, art = source_pack()
    assert 'geometry' not in DeploymentProfile(old,art,'a'*64).data['source_layout']['categories'][0]


def test_heterogeneous_vacancies_cover_large_and_small_regions():
    data, assets, frame = geometry_pack()
    c = data['source_layout']['categories'][1]
    # The empty hero region is one tall field, not two pretend small cards.
    extent = c['geometry']['extent']; x0,y0,x1,y1 = [int(v*s) for v,s in zip(extent,(640,360,640,360))]
    c['slots'] = [extent]
    fields = c['geometry']['fields'][:1]
    frame[203:212,130:139]=0  # One complete tall vacancy, not two markers.
    fields[0]['icon_roi']=[0,0,.4,1]
    fields[0]['vacant'] = 'hero_whole'
    assets['hero_whole'] = frame[y0:y1,x0:x1].copy()
    data['assets']['hero_whole'] = dict(file='hero_whole.png',sha256='b'*64)
    c['geometry'].update(fields=fields,background=[])
    assert observe(data,assets,frame).source_categories[1] == ('hero','EMPTY')


@pytest.mark.parametrize('fault',['hole','overlap','outside','missing_fields','mixed','bad_roi','vacant_size'])
def test_geometry_ingress_rejects_incomplete_or_ambiguous_coverage(fault):
    data, assets, _ = geometry_pack(); c=data['source_layout']['categories'][0]; g=c['geometry']
    if fault=='hole':g['background']=[]
    elif fault=='overlap':g['background'].append(copy.deepcopy(g['background'][0]))
    elif fault=='outside':g['extent'][2]=.1
    elif fault=='missing_fields':g['fields'].pop()
    elif fault=='mixed':del data['source_layout']['categories'][1]['geometry']
    elif fault=='bad_roi':g['fields'][0]['count_roi']=[0,0,2,1]
    else:g['fields'][0]['vacant']='source_empty'
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)


@pytest.mark.parametrize('fault',['unknown_background','neighbor_quantity','missing_quantity',
    'capacity','support_populated','siege_available','duplicate_global','missing_global','overlap_global'])
def test_complete_geometry_rejects_changed_contents_and_witnesses(fault):
    data, assets, frame = geometry_pack(); layout=data['source_layout']; c=layout['categories'][0]
    if fault=='unknown_background':frame[199:201,10:19]=255
    elif fault=='neighbor_quantity':
        frame[148:175,2:60]=0
        q,_=quantity('x2');frame[201:201+q.shape[0],4:4+q.shape[1]]=q[:,:,None]
    elif fault=='missing_quantity':frame[148:190,2:80]=0
    elif fault=='capacity':layout['entries'][1]['capacity']=3
    elif fault=='support_populated':frame[208:217,390:399]=assets['card']
    elif fault=='siege_available':frame[110:119,582:591]=assets['source_available']
    elif fault=='duplicate_global':frame[340:349,540:549]=assets['context_source']
    elif fault=='missing_global':frame[344:360,128:256]=0
    else:data['controls']['ARMY']['army_attack']=copy.deepcopy(data['screens']['ARMY'][0])
    with pytest.raises(DeploymentError):observe(data,assets,frame)


@pytest.mark.parametrize('edge',['left','top','right','bottom'])
def test_complete_geometry_rejects_clipped_source_frame(edge):
    data,assets,frame=geometry_pack()
    frame={'left':frame[:,1:],'right':frame[:,:-1],'top':frame[1:],'bottom':frame[:-1]}[edge].copy()
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_geometry_canonical_bytes_bind_plan_and_are_detached(tmp_path,monkeypatch):
    import test_deployment_profile as fixture
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    monkeypatch.setattr(fixture,'profile_data',lambda:geometry_pack()[:2])
    fixture.write_pack(tmp_path,runtime=False)
    first=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    path=tmp_path/'private/deployment/profile.json'; raw=json.loads(path.read_text())
    raw['source_layout']['categories'][0]['geometry']['fields'][0]['count_roi'][1]=.39
    path.write_bytes(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()+b'\n')
    second=load_profile(tmp_path,candidate_tree='a'*40,runtime=False)
    assert first.digest!=second.digest and first.plan.digest!=second.plan.digest
    assert first.data['source_layout']['categories'][0]['geometry']['fields'][0]['count_roi'][1]==.4


def test_numeric_header_is_not_an_extra_card_but_background_count_is_rejected():
    data,assets,frame=geometry_pack()
    q,_=quantity('x2')
    # A non-card screen field is not a globally unique quantity alphabet.
    frame[5:5+q.shape[0],530:530+q.shape[1]]=q[:,:,None]
    assert [e.quantity for e in observe(data,assets,frame).source_inventory]==[1,4]
    data,assets,frame=geometry_pack()
    # The same pixels inserted into declared unused source area must reject.
    frame[198:201,10:40]=255
    with pytest.raises(DeploymentError):observe(data,assets,frame)


def test_zero_slot_empty_and_locked_categories_require_whole_background_proof():
    data,assets,frame=geometry_pack()
    for i in (1,2,4):
        c=data['source_layout']['categories'][i];g=c['geometry'];box=g['extent']
        l,t,r,b=[int(v*s) for v,s in zip(box,(640,360,640,360))]
        # Remove the obsolete slot markers: no pretend common vacancy needed.
        frame[t:b,l:r]=np.random.default_rng(i).integers(0,100,(b-t,r-l,3),dtype=np.uint8)
        key=c['category']+'_whole';assets[key]=frame[t:b,l:r].copy()
        data['assets'][key]=dict(file=key+'.png',sha256='b'*64)
        c['slots']=[];g.update(fields=[],background=[dict(template=key,roi=box)])
    assert observe(data,assets,frame).source_categories[-1]==('siege','LOCKED')


def test_per_slot_count_geometry_is_bounded_to_its_owner():
    data,assets,frame=geometry_pack()
    field=data['source_layout']['categories'][3]['geometry']['fields'][0]
    field['count_roi']=[0,.4,.6,1]
    assert observe(data,assets,frame).source_inventory[-1].quantity==4
    field['count_roi']=[-1,.4,.6,1]
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)
