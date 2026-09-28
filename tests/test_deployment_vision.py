from clash_rush_rebuild.mvp_deployment import Point
"""All images in this file are generated geometric test patterns, not game art."""
import numpy as np
import cv2
import pytest
from clash_rush_rebuild.mvp_deployment import DeploymentError, CardKind, Spell
from clash_rush_rebuild.mvp_deployment_vision import (
    CardGeometry, discover_card_boxes, match_unique, read_quantity,
    candidate_ring, validated_ground, classify_icon, exact_grey,
)


def pattern(seed, w=16,h=20):
    return np.random.default_rng(seed).integers(0,256,(h,w,3),dtype=np.uint8)


def test_unique_template_rejects_duplicate_targets():
    t=pattern(1); f=np.zeros((100,200,3),np.uint8)
    f[20:40,30:46]=t
    assert match_unique(f,t,.99) is not None
    f[20:40,100:116]=t
    with pytest.raises(DeploymentError): match_unique(f,t,.99)


def test_flat_template_is_never_positive_evidence():
    with pytest.raises(DeploymentError):
        match_unique(np.zeros((100,100,3),np.uint8),np.zeros((8,8,3),np.uint8),.9)


def test_missing_template_is_not_a_fallback_click():
    assert match_unique(np.zeros((100,100,3),np.uint8),pattern(2),.99) is None


def bar(n):
    f=np.zeros((120,1000,3),np.uint8)
    for i in range(n):
        x=20+i*80
        f[5:115,x:x+68]=230
    return f


def test_coc_edge_spine_discovers_five_cards_without_fixed_slot_coordinates():
    boxes=discover_card_boxes(bar(5),CardGeometry(.068,.008,.7,5))
    assert len(boxes)==5
    assert boxes[0][0] < 22 and boxes[-1][0]>330


def test_coc_edge_spine_discovers_more_than_five_visible_cards():
    assert len(discover_card_boxes(bar(9),CardGeometry(.068,.008,.7,5)))==9

def test_coc_edge_spine_ignores_translucent_bar_outer_edge_and_busy_background():
    """A scenery-bearing bar edge must not be paired as a phantom first card."""
    width=1000
    background=np.zeros((120,width,3),np.uint8)
    # Strong crop/bar edge whose distance to the first real card resembles a
    # card width, followed by generated scenery behind translucent empty slots.
    background[:,0:3]=255
    for index,left in enumerate(range(450,950,50)):
        background[:,left:left+25]=(30+index*17,80+index*11,140+index*7)
    starts=(80,155,238,321)
    for left in starts:
        right=left+68
        background[5:115,left:right]=230

    boxes=discover_card_boxes(background,CardGeometry(.068,.012,.7,5))

    assert len(boxes)==4
    assert all(abs(actual-expected)<=2 for actual,expected in
               zip((box[0] for box in boxes),starts))

@pytest.mark.parametrize('frame',[np.zeros((120,1000,3),np.uint8),np.full((120,1000,3),255,np.uint8)])
def test_empty_or_flat_bar_rejected(frame):
    with pytest.raises(DeploymentError): discover_card_boxes(frame,CardGeometry())


def digits():
    bank={}
    for ch in 'x0123456789':
        canvas=np.zeros((40,30),np.uint8)
        cv2.putText(canvas,ch,(2,31),cv2.FONT_HERSHEY_SIMPLEX,1.,255,2,cv2.LINE_8)
        ys,xs=np.where(canvas>0); bank[ch]=canvas[ys.min():ys.max()+1,xs.min():xs.max()+1]
    return bank


def quantity(text):
    bank=digits(); h=40
    glyphs=[bank[c] for c in text]; width=sum(g.shape[1]+5 for g in glyphs)+10
    image=np.zeros((h,width),np.uint8); x=5
    for glyph in glyphs:
        image[3:3+glyph.shape[0],x:x+glyph.shape[1]]=glyph; x+=glyph.shape[1]+5
    return image,bank

@pytest.mark.parametrize('text,value',[('x2',2),('x10',10),('x0',0),('x123',123)])
def test_finite_glyph_bank_reads_whole_quantity(text,value):
    img,bank=quantity(text)
    assert read_quantity(img,bank,999)==value


def test_unknown_quantity_never_defaults_to_one():
    with pytest.raises(DeploymentError): read_quantity(np.zeros((30,80),np.uint8),digits(),99)


def test_quantity_over_capacity_and_leading_zero_rejected():
    image,bank=quantity('x123')
    with pytest.raises(DeploymentError): read_quantity(image,bank,99)
    image,bank=quantity('x02')
    with pytest.raises(DeploymentError): read_quantity(image,bank,99)


def test_icon_classification_uses_catalog_not_gap_or_missing_x():
    troop=pattern(10); spell=pattern(11)
    catalog={'troop':(troop,), 'lightning':(spell,)}
    assert classify_icon(spell,catalog,.95,.04)=='lightning'
    assert classify_icon(pattern(12),catalog,.95,.04) is None


def test_icon_classification_rejects_ambiguous_identity():
    p=pattern(13)
    with pytest.raises(DeploymentError): classify_icon(p,{'a':(p,), 'b':(p,)},.9,.03)


def test_grey_is_exact_boolean_feature_not_a_presence_detector():
    assert exact_grey(np.full((30,30,3),120,np.uint8)) is True
    assert exact_grey(np.full((30,30,3),(0,255,0),np.uint8)) is False
    with pytest.raises(DeploymentError): exact_grey(np.zeros((0,0,3),np.uint8))


def test_ring_candidates_are_not_legal_ground_permission():
    f=np.full((720,1280,3),100,np.uint8)
    for x in range(400,900,20): cv2.line(f,(x,220),(x,500),(220,220,220),2)
    points=candidate_ring(f)
    assert points
    # Without a positive closed forbidden-boundary observation, accept NOTHING.
    assert validated_ground(f,points,overlay_hsv=None,playfield=None)==()


def test_blank_field_does_not_fabricate_base_ring():
    assert candidate_ring(np.zeros((720,1280,3),np.uint8))==()


def test_legal_ground_rejects_small_outposts_not_just_largest_boundary():
    f=np.full((400,600,3),60,np.uint8)
    cv2.rectangle(f,(220,110),(420,270),(0,0,255),2)
    cv2.rectangle(f,(80,150),(100,170),(0,0,255),2)
    bad=Point(90/599,160/399); good=Point(.8,.7)
    accepted=validated_ground(f,(bad,good),overlay_hsv=((0,200,200),(10,255,255)),
       playfield=((.02,.02),(.98,.02),(.98,.98),(.02,.98)),margin_pixels=6)
    assert accepted==(good,)
