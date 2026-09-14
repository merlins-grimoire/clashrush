from __future__ import annotations

import pytest

from clash_rush_rebuild.registry import RegistryError, load_exact_five


def _conf(
    names: tuple[str, ...], *, geometry: tuple[str, str, str] = ("1280", "720", "240")
) -> str:
    lines: list[str] = []
    for index, name in enumerate(names):
        internal = "Pie64" if index == 0 else f"Pie64_{index + 1}"
        lines.extend(
            [
                f'bst.instance.{internal}.display_name="{name}"',
                f'bst.instance.{internal}.fb_width="{geometry[0]}"',
                f'bst.instance.{internal}.fb_height="{geometry[1]}"',
                f'bst.instance.{internal}.dpi="{geometry[2]}"',
            ]
        )
    return "\n".join(lines)


def test_exact_five_preserves_operator_slot_order() -> None:
    configured = ("Account E", "Account C", "Account A", "Account D", "Account B")
    slots = load_exact_five(_conf(tuple(reversed(configured))), configured)
    assert tuple(slot.index for slot in slots) == (0, 1, 2, 3, 4)
    assert tuple(slot.display_name for slot in slots) == configured
    assert tuple(slot.internal_name for slot in slots) == (
        "Pie64_5",
        "Pie64_4",
        "Pie64_3",
        "Pie64_2",
        "Pie64",
    )


@pytest.mark.parametrize("count", [0, 1, 4, 6])
def test_registry_rejects_any_count_other_than_five(count: int) -> None:
    configured = tuple(f"A{i}" for i in range(count))
    with pytest.raises(RegistryError, match="exactly five"):
        load_exact_five(_conf(configured), configured)


def test_registry_rejects_string_instead_of_sequence() -> None:
    with pytest.raises(RegistryError, match="sequence"):
        load_exact_five(_conf(("A", "B", "C", "D", "E")), "ABCDE")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_name", ["", " A", "A ", "A\nB", 7, True])
def test_registry_rejects_invalid_private_display_names(bad_name: object) -> None:
    names: tuple[object, ...] = (bad_name, "B", "C", "D", "E")
    with pytest.raises(RegistryError, match="exact non-empty"):
        load_exact_five(_conf(("A", "B", "C", "D", "E")), names)  # type: ignore[arg-type]


def test_registry_rejects_ambiguous_display_name_mapping() -> None:
    names = ("A", "B", "C", "D", "E")
    conf = _conf(names) + '\nbst.instance.Pie64_99.display_name="A"\n'
    with pytest.raises(RegistryError, match="exactly one"):
        load_exact_five(conf, names)


def test_registry_rejects_duplicate_property() -> None:
    names = ("A", "B", "C", "D", "E")
    conf = _conf(names) + '\nbst.instance.Pie64.display_name="A"\n'
    with pytest.raises(RegistryError, match="duplicate"):
        load_exact_five(conf, names)


@pytest.mark.parametrize(
    "geometry",
    [("01280", "720", "240"), ("1280", "720x", "240"), ("1280", "720", "0240")],
)
def test_registry_rejects_noncanonical_numeric_geometry(
    geometry: tuple[str, str, str],
) -> None:
    names = ("A", "B", "C", "D", "E")
    with pytest.raises(RegistryError, match="malformed"):
        load_exact_five(_conf(names, geometry=geometry), names)


def test_registry_rejects_wrong_geometry() -> None:
    names = ("A", "B", "C", "D", "E")
    with pytest.raises(RegistryError, match="pinned"):
        load_exact_five(_conf(names, geometry=("1920", "1080", "240")), names)
