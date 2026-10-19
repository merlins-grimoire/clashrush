"""Generated geometric evidence only; source policy must not alter Battle defaults."""
import json

import numpy as np
import pytest

from test_deployment_vision import quantity
from test_s004_source_images import source_pack, source_frame, observe
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile
from clash_rush_rebuild.mvp_deployment_vision import read_quantity


def policy_pack():
    data, assets = source_pack()
    data['source_layout']['quantity'] = dict(threshold=235, minimum_component_area=5,
        glyphs={key:[key] for key in 'x0123456789'})
    return data, assets


def rim_field(text):
    field, bank = quantity(text)
    # A bright card rim is connected to all glyphs at the old foreground floor.
    field[1, 2:-2] = 210
    field[1:5, 5] = 210
    return field, bank


@pytest.mark.parametrize('text,value', [('x27',27), ('x408',408)])
def test_source_policy_consumes_whole_rim_connected_field(text,value):
    field, bank = rim_field(text)
    with pytest.raises(DeploymentError):read_quantity(field,bank,999)
    variants={key:(value,) for key,value in bank.items()}
    assert read_quantity(field,variants,999,threshold=235,minimum_component_area=5)==value


def test_source_profile_routes_policy_without_inventing_expected_count():
    data, assets = policy_pack()
    frame=source_frame(data,assets,reserve=2)
    # Source expects four; pixel observer must still report two.
    for x,y in ((0,108),(384,108)):
        frame[y+41,x+4:x+55]=210
        frame[y+41:y+46,x+7]=210
    result=observe(data,assets,frame)
    assert [e.quantity for e in result.source_inventory]==[1,2]


def test_legacy_reader_and_profile_remain_unchanged():
    data,assets=source_pack()
    assert 'quantity' not in DeploymentProfile(data,assets,'a'*64).data['source_layout']
    field,bank=quantity('x27')
    assert read_quantity(field,bank,999)==27
    noisy=field.copy();noisy[1,3:-3]=210
    with pytest.raises(DeploymentError):read_quantity(noisy,bank,999)


@pytest.mark.parametrize('edge',['left','right','top','bottom'])
def test_preprocessing_cannot_erase_clipped_evidence(edge):
    field,bank=quantity('x27')
    ys,xs=np.where(field>=235)
    if edge=='left':field=field[:,xs.min():]
    elif edge=='right':field=field[:,:xs.max()+1]
    elif edge=='top':field=field[ys.min():]
    else:field=field[:ys.max()+1]
    with pytest.raises(DeploymentError):
        read_quantity(field,bank,999,threshold=235,minimum_component_area=5)
    # Even a one-pixel boundary fragment must reject BEFORE the area filter.
    field,bank=quantity('x27');field[0,2]=255
    with pytest.raises(DeploymentError,match='QUANTITY_CLIPPED'):
        read_quantity(field,bank,999,threshold=235,minimum_component_area=5)


@pytest.mark.parametrize('fault',['missing','rim_only','merged','ambiguous','over_capacity','leading_zero'])
def test_policy_preserves_closed_quantity_rejections(fault):
    field,bank=quantity('x27')
    maximum=999
    if fault=='missing':field.fill(0)
    elif fault=='rim_only':field.fill(0);field[2:4,2:-2]=210
    elif fault=='merged':field[3,4:-4]=255
    elif fault=='ambiguous':bank['8']=bank['7'].copy()
    elif fault=='over_capacity':maximum=26
    else:field,bank=quantity('x027')
    with pytest.raises(DeploymentError):
        read_quantity(field,{k:(v,) for k,v in bank.items()},maximum,
                      threshold=235,minimum_component_area=5)


def test_competing_variant_is_not_hidden_by_best_same_label_variant():
    field,bank=quantity('x27')
    variants={k:(v,) for k,v in bank.items()}
    variants['8']=(bank['8'],bank['7'])
    with pytest.raises(DeploymentError,match='QUANTITY_UNKNOWN'):
        read_quantity(field,variants,999,threshold=235,minimum_component_area=5)


def test_small_unowned_partial_character_cannot_be_filtered_away():
    field,bank=quantity('x27')
    field[1,1:3]=255
    with pytest.raises(DeploymentError,match='QUANTITY_UNKNOWN'):
        read_quantity(field,bank,999,threshold=235,minimum_component_area=5)


@pytest.mark.parametrize('field,value',[('threshold',True),('threshold',159),('threshold',255),
    ('minimum_component_area',True),('minimum_component_area',0),('minimum_component_area',9),
    ('glyphs',{}),('extra',1)])
def test_invalid_source_policy_is_rejected(field,value):
    data,assets=policy_pack();data['source_layout']['quantity'][field]=value
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)


def test_source_policy_is_detached_and_canonical_bytes_bind_authority(tmp_path,monkeypatch):
    import test_deployment_profile as fixtures
    from clash_rush_rebuild.mvp_deployment_profile import load_profile
    monkeypatch.setattr(fixtures,'profile_data',policy_pack)
    data,assets=fixtures.write_pack(tmp_path,runtime=True)
    first=load_profile(tmp_path,candidate_tree='a'*40)
    path=tmp_path/'private/deployment/profile.json'
    raw=json.loads(path.read_text());raw['source_layout']['quantity']['threshold']=236
    path.write_bytes(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()+b'\n')
    second=load_profile(tmp_path,candidate_tree='a'*40)
    assert first.digest!=second.digest and first.plan.digest!=second.plan.digest
    data['source_layout']['quantity']['threshold']=200
    assert first.data['source_layout']['quantity']['threshold']==235
    assert first.data['source_layout']['quantity']['glyphs']['x']==('x',)


def test_source_bank_alias_is_rejected_at_profile_ingress():
    data,assets=policy_pack()
    data['source_layout']['quantity']['glyphs']['8'].append('7')
    with pytest.raises(DeploymentError):DeploymentProfile(data,assets,'a'*64)
