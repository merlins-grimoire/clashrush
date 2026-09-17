from __future__ import annotations

import json

import pytest

from clash_rush_rebuild import mvp_local_native
from clash_rush_rebuild.config import load_private_registry
from clash_rush_rebuild.lifecycle_state import Ready
from clash_rush_rebuild.mvp_local_gameplay import MvpConfiguration
from clash_rush_rebuild.mvp_local_runtime import LocalControlStore, RuntimeSafetyError


TAG_HASH = "a" * 64


def _private_registry(tmp_path):
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    names = [f"Private Slot {index} / Owner" for index in range(5)]
    slots_path = private / "slots.json"
    slots_path.write_text(
        json.dumps({"schema": 1, "display_names": names}), encoding="utf-8"
    )
    conf_path = tmp_path / "bluestacks.conf"
    conf_path.write_text(
        "\n".join(
            line
            for index, name in enumerate(names)
            for line in (
                f'bst.instance.Pie64_{index}.display_name="{name}"',
                f'bst.instance.Pie64_{index}.fb_width="1280"',
                f'bst.instance.Pie64_{index}.fb_height="720"',
                f'bst.instance.Pie64_{index}.dpi="240"',
            )
        ),
        encoding="utf-8",
    )
    return project, load_private_registry(project, slots_path, conf_path)


def test_private_display_name_binds_through_opaque_slot_reference(tmp_path) -> None:
    project, slots = _private_registry(tmp_path)
    configuration = MvpConfiguration("team-0", "account-2", "slot-2", TAG_HASH)
    store = LocalControlStore(project / "var" / "mvp-local-control.json")

    persisted = store.setup(configuration)
    selected = mvp_local_native._select_configured_slot(
        slots, Ready(2), persisted.configuration.instance_ref
    )

    assert selected is slots[2]
    raw = (project / "var" / "mvp-local-control.json").read_text(encoding="utf-8")
    assert '"instance_ref":"slot-2"' in raw
    assert slots[2].display_name not in raw


def test_opaque_slot_reference_must_exactly_match_lifecycle_cursor(tmp_path) -> None:
    _project, slots = _private_registry(tmp_path)

    with pytest.raises(RuntimeSafetyError, match="WINDOW_BINDING"):
        mvp_local_native._select_configured_slot(slots, Ready(2), "slot-1")
