"""Exact five-slot BlueStacks configuration registry.

Adapted from CoC_Bot's exact display-name lookup and the sealed Clash Rush
configuration parser. Android-debug transport fields are intentionally ignored.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


class RegistryError(RuntimeError):
    """The private slot list cannot be bound uniquely and safely."""


@dataclass(frozen=True, slots=True)
class Slot:
    index: int
    internal_name: str
    display_name: str
    framebuffer_width: int
    framebuffer_height: int
    dpi: int


_CONFIG_LINE = re.compile(
    r'bst\.instance\.([A-Za-z0-9_]+)\.(display_name|fb_width|fb_height|dpi)="([^"]*)"'
)
_PINNED_GEOMETRY = (1280, 720, 240)


def _parse(text: str) -> dict[str, dict[str, str]]:
    instances: dict[str, dict[str, str]] = {}
    for raw in text.splitlines():
        match = _CONFIG_LINE.fullmatch(raw.strip())
        if match is None:
            continue
        internal, field, value = match.groups()
        fields = instances.setdefault(internal, {})
        if field in fields:
            raise RegistryError("duplicate instance property")
        fields[field] = value
    return instances


def load_exact_five(
    conf_text: str, configured_display_names: Sequence[str]
) -> tuple[Slot, ...]:
    """Bind five private display names in operator-defined slot order."""
    if not isinstance(conf_text, str):
        raise RegistryError("BlueStacks configuration must be text")
    if isinstance(configured_display_names, (str, bytes)) or not isinstance(
        configured_display_names, Sequence
    ):
        raise RegistryError("configured slots must be a sequence")
    names = tuple(configured_display_names)
    if len(names) != 5:
        raise RegistryError("exactly five configured slots required")
    if any(
        type(name) is not str
        or not name
        or name != name.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        for name in names
    ):
        raise RegistryError("each slot requires an exact non-empty display name")
    if len(set(names)) != 5:
        raise RegistryError("configured display names must be distinct")

    parsed = _parse(conf_text)
    slots: list[Slot] = []
    used_internal: set[str] = set()
    for index, display_name in enumerate(names):
        matches = [
            (internal, fields)
            for internal, fields in parsed.items()
            if fields.get("display_name") == display_name
        ]
        if len(matches) != 1:
            raise RegistryError(
                "display name must map to exactly one configured instance"
            )
        internal, fields = matches[0]
        if re.fullmatch(r"Pie64(?:_[A-Za-z0-9_]+)?", internal) is None:
            raise RegistryError("instance is not on the pinned Pie64 engine")
        if internal in used_internal:
            raise RegistryError("internal instance binding must be distinct")
        try:
            raw_geometry = tuple(
                fields[key] for key in ("fb_width", "fb_height", "dpi")
            )
            if any(
                re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None
                for value in raw_geometry
            ):
                raise ValueError("noncanonical integer")
            geometry = tuple(int(value) for value in raw_geometry)
        except (KeyError, ValueError) as exc:
            raise RegistryError("instance geometry is missing or malformed") from exc
        if geometry != _PINNED_GEOMETRY:
            raise RegistryError("instance geometry does not match pinned profile")
        used_internal.add(internal)
        slots.append(Slot(index, internal, display_name, *geometry))
    return tuple(slots)
