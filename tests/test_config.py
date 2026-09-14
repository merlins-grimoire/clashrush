from __future__ import annotations

import json

import pytest

from clash_rush_rebuild.config import ConfigError, load_private_registry


def _bluestacks_conf() -> str:
    lines: list[str] = []
    for index in range(5):
        internal = "Pie64" if index == 0 else f"Pie64_{index + 1}"
        name = f"Private {index}"
        lines += [
            f'bst.instance.{internal}.display_name="{name}"',
            f'bst.instance.{internal}.fb_width="1280"',
            f'bst.instance.{internal}.fb_height="720"',
            f'bst.instance.{internal}.dpi="240"',
        ]
    return "\n".join(lines)


def test_private_loader_accepts_only_private_slots_json(tmp_path) -> None:
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    slots = private / "slots.json"
    slots.write_text(
        json.dumps({"schema": 1, "display_names": [f"Private {i}" for i in range(5)]})
    )
    conf = tmp_path / "bluestacks.conf"
    conf.write_text(_bluestacks_conf())
    registry = load_private_registry(project, slots, conf)
    assert tuple(slot.index for slot in registry) == (0, 1, 2, 3, 4)


def test_private_loader_rejects_configuration_outside_private_root(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    slots = project / "slots.json"
    slots.write_text(
        json.dumps({"schema": 1, "display_names": [f"Private {i}" for i in range(5)]})
    )
    conf = tmp_path / "bluestacks.conf"
    conf.write_text(_bluestacks_conf())
    with pytest.raises(ConfigError, match="private"):
        load_private_registry(project, slots, conf)


def test_private_loader_rejects_extra_fields(tmp_path) -> None:
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    slots = private / "slots.json"
    slots.write_text(
        json.dumps(
            {
                "schema": 1,
                "display_names": [f"Private {i}" for i in range(5)],
                "tag": "forbidden",
            }
        )
    )
    conf = tmp_path / "bluestacks.conf"
    conf.write_text(_bluestacks_conf())
    with pytest.raises(ConfigError, match="schema"):
        load_private_registry(project, slots, conf)
