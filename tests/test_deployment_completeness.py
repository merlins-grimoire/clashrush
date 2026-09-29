"""Generated complete-frame/card recognition matrix; no private pixels."""
import cv2
import numpy as np
import pytest

from clash_rush_rebuild.mvp_deployment import DeploymentError, Intervention
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile, FrameObserver
from test_deployment_profile import profile_data


def _glyphs():
    result = {}
    for index, key in enumerate("x0123456789", 1):
        rng = np.random.default_rng(900 + index)
        glyph = (rng.integers(0, 2, (9, 7), dtype=np.uint8) * 255)
        glyph[:, 0] = 255
        glyph[0, :] = 0
        glyph[-1, :] = 0
        result[key] = cv2.cvtColor(glyph, cv2.COLOR_GRAY2BGR)
    return result


def _put(frame, asset, x, y):
    h, w = asset.shape[:2]
    frame[y:y + h, x:x + w] = asset


def _profile():
    data, assets = profile_data()
    data["bar"]["geometry"] = dict(
        card_width=.12, width_tolerance=.006, peak_height=.7, peak_distance=5)
    glyphs = _glyphs()
    assets.update(glyphs)
    return DeploymentProfile(data, assets, "a" * 64), assets


def _card(frame, assets, left, *, icon="card", partial=False):
    top = round(360 * .82)
    frame[top + 5:round(360 * .98) - 5, left:left + 77] = 100
    placements = (
        ("frame_tl", 2, 7), ("frame_tr", 66, 7),
        ("frame_bl", 2, 40), ("frame_br", 66, 40),
    )
    for index, (key, x, y) in enumerate(placements):
        if partial and index:
            continue
        _put(frame, np.asarray(assets[key]), left + x, top + y)
    if not partial:
        _put(frame, np.asarray(assets[icon]), left + 30, top + 16)
        _put(frame, np.asarray(assets["x"]), left + 20, top + 39)
        _put(frame, np.asarray(assets["1"]), left + 34, top + 39)


def _frame(profile, assets):
    frame = np.zeros((360, 640, 3), np.uint8)
    _card(frame, assets, 20)
    _put(frame, np.asarray(assets["left_end"]), 5, 100)
    _put(frame, np.asarray(assets["right_end"]), 25, 100)
    return frame


def _page(profile, frame):
    observer = FrameObserver(profile=profile, capture=lambda: frame,
        monotonic=lambda: 1., intervention=lambda: Intervention.CLEAR,
        home_verified=lambda _: False)
    return observer._page(frame)


def test_complete_frame_identity_quantity_and_positive_endpoints_form_one_page():
    profile, assets = _profile()
    page = _page(profile, _frame(profile, assets))
    assert tuple(card.identity for card in page.cards) == ("troop",)
    assert page.cards[0].remaining == 1
    assert page.left_edge and page.right_edge


def test_partial_frame_residue_cannot_be_ignored_after_expected_roster():
    profile, assets = _profile()
    frame = _frame(profile, assets)
    _card(frame, assets, 120, partial=True)
    with pytest.raises(DeploymentError, match="BAR_RESIDUE"):
        _page(profile, frame)


def test_identity_witness_inside_card_but_outside_relative_icon_roi_is_residue():
    profile, assets = _profile()
    frame = _frame(profile, assets)
    top = round(360 * .82)
    _put(frame, np.asarray(assets["card"]), 22, top + 22)
    with pytest.raises(DeploymentError, match="UNEXPLAINED_WITNESS"):
        _page(profile, frame)


def test_selected_marker_in_artwork_is_not_borrowed_as_card_state():
    profile, assets = _profile()
    frame = _frame(profile, assets)
    top = round(360 * .82)
    _put(frame, np.asarray(assets["selected"]), 45, top + 7)
    with pytest.raises(DeploymentError, match="UNEXPLAINED_WITNESS"):
        _page(profile, frame)


def test_continuation_endpoint_cannot_complete_single_page_capability():
    profile, assets = _profile()
    frame = _frame(profile, assets)
    frame[100:109, 5:14] = 0
    _put(frame, np.asarray(assets["left_more"]), 5, 100)
    with pytest.raises(DeploymentError, match="SINGLE_PAGE_COVERAGE_UNPROVED"):
        _page(profile, frame)
