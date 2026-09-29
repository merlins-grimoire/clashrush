"""Generated H0 counterexamples. No game art, private pixels, or live input."""

import cv2
import numpy as np
import pytest

from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile
from clash_rush_rebuild.mvp_deployment_vision import (
    CardGeometry,
    classify_icon,
    discover_card_boxes,
)
from test_deployment_profile import profile_data


def _bar_with_internal_art_edge() -> np.ndarray:
    frame = np.zeros((120, 1000, 3), np.uint8)
    for left in (20, 100, 180, 260):
        frame[5:115, left : left + 68] = 230
    frame[5:115, 208:212] = 0
    return frame


def test_internal_art_edge_cannot_truncate_complete_card_coverage():
    boxes = discover_card_boxes(_bar_with_internal_art_edge(), CardGeometry())

    assert len(boxes) == 4
    assert tuple(box[0] for box in boxes) == pytest.approx((20, 100, 180, 260), abs=2)


def _ambiguity_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    rng = np.random.default_rng(7)
    crop = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
    indexes = np.arange(crop.size)
    rng.shuffle(indexes)

    def changed(count: int) -> np.ndarray:
        candidate = crop.copy().reshape(-1)
        candidate[indexes[:count]] = 255 - candidate[indexes[:count]]
        return candidate.reshape(crop.shape)

    winner = changed(37)
    runner_up = changed(57)
    winner_score = float(
        cv2.matchTemplate(crop, winner, cv2.TM_CCOEFF_NORMED)[0, 0]
    )
    runner_up_score = float(
        cv2.matchTemplate(crop, runner_up, cv2.TM_CCOEFF_NORMED)[0, 0]
    )
    return crop, winner, runner_up, winner_score, runner_up_score


def test_subthreshold_incompatible_runner_up_still_enforces_margin():
    crop, winner, runner_up, winner_score, runner_up_score = _ambiguity_fixture()
    assert winner_score >= 0.93
    assert runner_up_score < 0.93
    assert winner_score - runner_up_score < 0.04

    with pytest.raises(DeploymentError, match="CARD_CLASS_AMBIGUOUS"):
        classify_icon(
            crop,
            {"winner": (winner,), "incompatible": (runner_up,)},
            threshold=0.93,
            margin=0.04,
        )


def test_compiled_profile_data_is_deeply_immutable():
    data, assets = profile_data()
    profile = DeploymentProfile(data, assets, "a" * 64)

    with pytest.raises(TypeError):
        profile.data["cards"][0]["capacity"] = 2


def test_compiled_template_authority_cannot_be_made_writeable():
    data, assets = profile_data()
    profile = DeploymentProfile(data, assets, "a" * 64)

    with pytest.raises(ValueError):
        profile.assets["card"].setflags(write=True)
