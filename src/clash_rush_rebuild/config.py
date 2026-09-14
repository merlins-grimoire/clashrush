"""Strict private slot configuration loader."""

from __future__ import annotations

import json
from pathlib import Path

from .registry import RegistryError, Slot, load_exact_five


class ConfigError(RuntimeError):
    """Private configuration is unreadable or outside its allowed root."""


def load_private_registry(
    project_root: Path,
    slots_path: Path,
    bluestacks_conf_path: Path,
) -> tuple[Slot, ...]:
    """Load exact display names without exposing them in errors or output."""
    try:
        project = Path(project_root).resolve(strict=True)
        private_root = (project / "private").resolve(strict=True)
        slots = Path(slots_path).resolve(strict=True)
        slots.relative_to(private_root)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            "slot configuration must exist beneath the private root"
        ) from exc

    try:
        raw = json.loads(slots.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("private slot configuration is unreadable") from exc
    if type(raw) is not dict or set(raw) != {"schema", "display_names"}:
        raise ConfigError("private slot schema fields are invalid")
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ConfigError("private slot schema version is invalid")
    names = raw["display_names"]
    if type(names) is not list:
        raise ConfigError("private slot schema display_names must be a list")

    try:
        conf_text = Path(bluestacks_conf_path).read_text(encoding="utf-8")
        return load_exact_five(conf_text, names)
    except (OSError, UnicodeError, RegistryError) as exc:
        raise ConfigError("private slot registry could not be bound safely") from exc
