"""Offline builder for an owner-reviewed private readiness profile.

The narrow-crop extraction/import pattern is adapted from ClashAutomation's MIT
``dev_tools/harness.py::cmd_extract_template`` at pinned commit
``c41fe12a6df051e241c695b71b6859286e24c612``.  This adapter accepts only
already-reviewed narrow crops beneath ``private``; it never captures a frame or
sends input.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from .approval_reconciliation import _seal_private_path
from .mvp_account_ready import _REQUIRED_TEMPLATES


class ManualReadinessProfileError(RuntimeError):
    """The reviewed narrow-crop packet could not be safely published."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManualReadinessProfileError("review packet is malformed")
        result[key] = value
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_review_packet(project_root: str | Path, manifest_path: str | Path):
    try:
        project = Path(project_root).resolve(strict=True)
        private = (project / "private").resolve(strict=True)
        manifest = Path(manifest_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManualReadinessProfileError("review packet is unavailable") from exc
    target = private / "readiness"
    if (
        not _inside(manifest, private)
        or not manifest.is_file()
        or _inside(manifest, target)
    ):
        raise ManualReadinessProfileError("review packet path is invalid")
    try:
        raw_bytes = manifest.read_bytes()
        if len(raw_bytes) > 1_000_000:
            raise ManualReadinessProfileError("review packet is malformed")
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except ManualReadinessProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManualReadinessProfileError("review packet is malformed") from exc
    if (
        type(raw) is not dict
        or set(raw) != {"schema", "profile_id", "templates"}
        or type(raw.get("schema")) is not int
        or raw["schema"] != 1
        or type(raw.get("profile_id")) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw["profile_id"])
        is None
        or type(raw.get("templates")) is not dict
        or set(raw["templates"]) != _REQUIRED_TEMPLATES
    ):
        raise ManualReadinessProfileError("review packet is malformed")
    return private, target, manifest, raw


def _prepare_templates(private: Path, manifest: Path, raw: dict[str, object]):
    prepared: dict[str, tuple[bytes, dict[str, object]]] = {}
    filenames: set[str] = set()
    templates = raw["templates"]
    assert type(templates) is dict
    for name in sorted(_REQUIRED_TEMPLATES):
        entry = templates[name]
        if (
            type(entry) is not dict
            or set(entry) != {"file", "threshold_ppm", "roi_ppm"}
            or type(entry.get("file")) is not str
            or not entry["file"]
            or Path(entry["file"]).name != entry["file"]
            or entry["file"] in filenames
            or type(entry.get("threshold_ppm")) is not int
            or type(entry["threshold_ppm"]) is bool
            or not 500_000 <= entry["threshold_ppm"] <= 1_000_000
            or type(entry.get("roi_ppm")) is not list
            or len(entry["roi_ppm"]) != 4
            or any(type(value) is not int or type(value) is bool for value in entry["roi_ppm"])
            or not (
                0 <= entry["roi_ppm"][0] < entry["roi_ppm"][2] <= 1_000_000
                and 0 <= entry["roi_ppm"][1] < entry["roi_ppm"][3] <= 1_000_000
            )
        ):
            raise ManualReadinessProfileError("review packet is malformed")
        filenames.add(entry["file"])
        try:
            source = (manifest.parent / entry["file"]).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ManualReadinessProfileError("review packet is incomplete") from exc
        if (
            not _inside(source, manifest.parent)
            or not _inside(source, private)
            or not source.is_file()
        ):
            raise ManualReadinessProfileError("review packet path is invalid")
        try:
            encoded = source.read_bytes()
        except OSError as exc:
            raise ManualReadinessProfileError("review packet is unavailable") from exc
        if not encoded or len(encoded) > 4_000_000:
            raise ManualReadinessProfileError("review packet is malformed")
        pixels = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if (
            type(pixels) is not np.ndarray
            or pixels.dtype != np.uint8
            or pixels.ndim != 3
            or pixels.size == 0
            or min(pixels.shape[:2]) < 8
            or max(pixels.shape[:2]) > 320
            or not math.isfinite(float(pixels.std()))
            or float(pixels.std()) < 1.0
        ):
            raise ManualReadinessProfileError("reviewed input is not a narrow crop")
        ok, canonical = cv2.imencode(".png", pixels)
        if ok is not True or type(canonical) is not np.ndarray or canonical.size == 0:
            raise ManualReadinessProfileError("review packet encoding failed")
        payload = canonical.tobytes()
        prepared[name] = (
            payload,
            {
                "file": f"{name}.png",
                "file_sha256": hashlib.sha256(payload).hexdigest(),
                "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
                "threshold_ppm": entry["threshold_ppm"],
                "roi_ppm": entry["roi_ppm"],
            },
        )
        pixels.fill(0)
    return prepared


def build_manual_readiness_profile(
    project_root: str | Path,
    review_manifest: str | Path,
    *,
    permission_sealer: Callable[[Path, bool], None] = _seal_private_path,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
) -> str:
    """Seal exactly nine reviewed narrow crops into the runtime profile schema."""
    private, target, manifest, raw = _load_review_packet(project_root, review_manifest)
    if target.exists():
        try:
            resolved_target = target.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ManualReadinessProfileError("private runtime path is invalid") from exc
        if not _inside(resolved_target, private) or resolved_target != target.absolute():
            raise ManualReadinessProfileError("private runtime path escaped")
    prepared = _prepare_templates(private, manifest, raw)
    nonce = nonce_factory()
    if (
        type(nonce) is not str
        or len(nonce) != 32
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise ManualReadinessProfileError("publication nonce is malformed")
    profile = target / "profile.json"
    output_paths = tuple(target / f"{name}.png" for name in sorted(_REQUIRED_TEMPLATES))
    if profile.exists() or any(path.exists() for path in output_paths):
        raise ManualReadinessProfileError("private readiness profile already exists")
    manifest_temp = target / f".profile-{nonce}.json"
    created: list[Path] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        permission_sealer(target, True)
        if profile.exists() or manifest_temp.exists() or any(path.exists() for path in output_paths):
            raise ManualReadinessProfileError("private readiness profile already exists")
        entries: dict[str, object] = {}
        for name, path in zip(sorted(_REQUIRED_TEMPLATES), output_paths, strict=True):
            payload, entry = prepared[name]
            with path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            created.append(path)
            permission_sealer(path, False)
            entries[name] = entry
        manifest_payload = (
            json.dumps(
                {
                    "schema": 1,
                    "profile_id": raw["profile_id"],
                    "templates": entries,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        with manifest_temp.open("xb") as stream:
            stream.write(manifest_payload)
            stream.flush()
            os.fsync(stream.fileno())
        created.append(manifest_temp)
        permission_sealer(manifest_temp, False)
        if profile.exists():
            raise ManualReadinessProfileError("private readiness profile already exists")
        os.replace(manifest_temp, profile)
        return raw["profile_id"]
    except BaseException as exc:
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if target.exists() and not any(target.iterdir()):
                shutil.rmtree(target)
        except OSError:
            pass
        if type(exc) is ManualReadinessProfileError:
            raise
        raise ManualReadinessProfileError("private profile publication failed") from exc


__all__ = [
    "ManualReadinessProfileError",
    "build_manual_readiness_profile",
]
