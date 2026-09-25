"""Bounded private S003 readiness-profile calibration transaction.

The navigation order and normalized controls are adapted from first-party
ClashRush ``world_export_live.py`` at pinned commit
``949497bf0a543a43ec6ef39a8c897e366bc10362``.  The transaction never imports
that donor and exposes only the existing account-export navigation capability.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .approval_reconciliation import _seal_private_path
from .input_authorization import InputAction
from .lifecycle import PlayerBinding


class CalibrationError(RuntimeError):
    """The calibration transaction failed closed."""


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    profile_id: str

    def __post_init__(self) -> None:
        if (
            type(self.profile_id) is not str
            or not self.profile_id.startswith("readiness-s003-")
            or len(self.profile_id) != 27
        ):
            raise CalibrationError("calibration result is malformed")


@dataclass(frozen=True, slots=True)
class LocatedControl:
    """One template-proved control and the exact live bounds that matched it."""

    x: float
    y: float
    bounds: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if (
            type(self.x) not in (int, float)
            or type(self.y) not in (int, float)
            or not 0 < float(self.x) < 1
            or not 0 < float(self.y) < 1
            or type(self.bounds) is not tuple
            or len(self.bounds) != 4
            or any(type(value) not in (int, float) for value in self.bounds)
            or not (0 <= self.bounds[0] < self.bounds[2] <= 1)
            or not (0 <= self.bounds[1] < self.bounds[3] <= 1)
        ):
            raise CalibrationError("located control is malformed")

    @property
    def center(self) -> tuple[float, float]:
        return float(self.x), float(self.y)

_BOOTSTRAP_NAMES = frozenset(
    {"launcher", "more_button", "export", "more_close", "settings_close", "ordinary_card"}
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationError("bootstrap manifest is malformed")
        result[key] = value
    return result


class PrivateBootstrapLocator:
    """Strict digest-sealed locator for owner-reviewed private controls."""

    def __init__(self, project_root: str | Path) -> None:
        try:
            project = Path(project_root).resolve(strict=True)
            private = (project / "private").resolve(strict=True)
            root = (private / "readiness" / "bootstrap").resolve(strict=True)
            manifest = (root / "profile.json").resolve(strict=True)
            if root.is_symlink() or manifest.is_symlink() or manifest.parent != root:
                raise OSError
            raw_bytes = manifest.read_bytes()
            if len(raw_bytes) > 64_000:
                raise OSError
            raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
        except CalibrationError:
            raise
        except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise CalibrationError("bootstrap manifest is unavailable") from exc
        controls = raw.get("controls") if type(raw) is dict else None
        if (
            type(raw) is not dict
            or set(raw) != {"schema", "controls"}
            or type(raw.get("schema")) is not int
            or raw["schema"] != 1
            or type(controls) is not dict
            or not controls
            or not set(controls) <= _BOOTSTRAP_NAMES
        ):
            raise CalibrationError("bootstrap manifest is malformed")
        loaded: dict[str, tuple[np.ndarray, tuple[int, int, int, int], float]] = {}
        for name, entry in controls.items():
            if (
                type(entry) is not dict
                or set(entry) != {
                    "file", "file_sha256", "pixel_sha256", "roi_ppm", "threshold_ppm"
                }
                or type(entry.get("file")) is not str
                or Path(entry["file"]).name != entry["file"]
                or type(entry.get("file_sha256")) is not str
                or type(entry.get("pixel_sha256")) is not str
                or type(entry.get("roi_ppm")) is not list
                or len(entry["roi_ppm"]) != 4
                or any(type(value) is not int for value in entry["roi_ppm"])
                or not (0 <= entry["roi_ppm"][0] < entry["roi_ppm"][2] <= 1_000_000)
                or not (0 <= entry["roi_ppm"][1] < entry["roi_ppm"][3] <= 1_000_000)
                or type(entry.get("threshold_ppm")) is not int
                or not 800_000 <= entry["threshold_ppm"] <= 1_000_000
            ):
                raise CalibrationError("bootstrap manifest is malformed")
            try:
                asset = (root / entry["file"]).resolve(strict=True)
                if asset.parent != root or asset.is_symlink():
                    raise OSError
                payload = asset.read_bytes()
            except (OSError, RuntimeError) as exc:
                raise CalibrationError(f"{name} bootstrap evidence unavailable") from exc
            pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
            if (
                len(payload) > 4_000_000
                or hashlib.sha256(payload).hexdigest() != entry["file_sha256"]
                or type(pixels) is not np.ndarray
                or pixels.size == 0
                or max(pixels.shape[:2]) > 320
                or hashlib.sha256(pixels.tobytes()).hexdigest() != entry["pixel_sha256"]
            ):
                raise CalibrationError(f"{name} bootstrap evidence unavailable")
            pixels.flags.writeable = False
            loaded[name] = (
                pixels,
                tuple(entry["roi_ppm"]),
                entry["threshold_ppm"] / 1_000_000,
            )
        self._controls = loaded

    def has(self, name: str) -> bool:
        return type(name) is str and name in self._controls

    def __call__(self, frame: np.ndarray, name: str) -> LocatedControl:
        if type(name) is not str or name not in _BOOTSTRAP_NAMES or name not in self._controls:
            raise CalibrationError(f"{name if type(name) is str else 'control'} bootstrap evidence unavailable")
        if type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim != 3:
            raise CalibrationError(f"{name} bootstrap evidence unavailable")
        template, roi, threshold = self._controls[name]
        height, width = frame.shape[:2]
        left, top, right, bottom = (
            width * roi[0] // 1_000_000,
            height * roi[1] // 1_000_000,
            width * roi[2] // 1_000_000,
            height * roi[3] // 1_000_000,
        )
        region = frame[top:bottom, left:right]
        if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
            raise CalibrationError(f"{name} bootstrap evidence unavailable")
        try:
            response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _minimum, score, _minimum_point, point = cv2.minMaxLoc(response)
        except cv2.error as exc:
            raise CalibrationError(f"{name} bootstrap evidence unavailable") from exc
        if not math.isfinite(float(score)) or float(score) < threshold:
            raise CalibrationError(f"{name} bootstrap evidence unavailable")
        x0, y0 = left + point[0], top + point[1]
        x1, y1 = x0 + template.shape[1], y0 + template.shape[0]
        return LocatedControl(
            (x0 + (template.shape[1] - 1) / 2) / (width - 1),
            (y0 + (template.shape[0] - 1) / 2) / (height - 1),
            (x0 / width, y0 / height, x1 / width, y1 / height),
        )


class CalibrationInputPort(Protocol):
    def click(
        self, binding: PlayerBinding, x: float, y: float, *, action: InputAction
    ) -> bool: ...

    def drag(
        self,
        binding: PlayerBinding,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        action: InputAction,
    ) -> bool: ...


# First-party donor controls. They are used only after a fresh positive source
# state proof, never as a blind fallback.
_SETTINGS_BUTTON = (0.9547, 0.7271)
_MORE_SETTINGS_BUTTON = (0.5110, 0.8340)
_EXPORT_BUTTON = (0.7058, 0.6288)
_SCROLL_DRAG = (0.50, 0.74, 0.50, 0.24)
_SETTINGS_CLOSE = (0.802, 0.119)

# Narrow installation-specific pixels to publish. Matching ROIs are deliberately
# broader than crop ROIs so the runtime tolerates small layout drift.
_CROPS: dict[str, tuple[float, float, float, float]] = {
    "home": (0.900, 0.620, 1.000, 0.840),
    "settings_button": (0.900, 0.620, 1.000, 0.840),
    "settings": (0.380, 0.080, 0.620, 0.300),
    "more_button": (0.420, 0.750, 0.600, 0.920),
    "more": (0.680, 0.030, 0.860, 0.220),
    "export": (0.620, 0.540, 0.790, 0.720),
    "more_close": (0.720, 0.030, 0.880, 0.220),
    "settings_close": (0.720, 0.030, 0.880, 0.220),
    "ordinary_card": (0.030, 0.250, 0.260, 0.650),
}
_MATCH_ROIS_PPM: dict[str, list[int]] = {
    "home": [850_000, 500_000, 1_000_000, 900_000],
    "settings_button": [850_000, 500_000, 1_000_000, 900_000],
    "settings": [100_000, 20_000, 900_000, 950_000],
    "more_button": [300_000, 650_000, 720_000, 980_000],
    "more": [600_000, 0, 920_000, 300_000],
    "export": [500_000, 400_000, 900_000, 850_000],
    "more_close": [600_000, 0, 920_000, 300_000],
    "settings_close": [600_000, 0, 920_000, 300_000],
    "ordinary_card": [0, 100_000, 450_000, 950_000],
}
_REQUIRED_NAMES = frozenset(_CROPS)


class ReadinessCalibrationController:
    """Create one profile under one deadline without ever admitting attack input."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        binding: PlayerBinding,
        capture: Callable[[], np.ndarray],
        input_port: CalibrationInputPort,
        prove_state: Callable[[np.ndarray, str], bool],
        locate_control: Callable[[np.ndarray, str], LocatedControl],
        live_gate: Callable[[], bool],
        clipboard_read: Callable[[], str],
        clipboard_clear: Callable[[], None],
        monotonic: Callable[[], float],
        wait: Callable[[float], None],
        nonce_factory: Callable[[], str] | None = None,
        seal_private: Callable[[Path, bool], None] = _seal_private_path,
    ) -> None:
        try:
            project = Path(project_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CalibrationError("private readiness path is unavailable") from exc
        if (
            type(binding) is not PlayerBinding
            or not all(
                callable(value)
                for value in (
                    capture,
                    prove_state,
                    locate_control,
                    live_gate,
                    clipboard_read,
                    clipboard_clear,
                    monotonic,
                    wait,
                    seal_private,
                )
            )
            or not callable(getattr(input_port, "click", None))
            or not callable(getattr(input_port, "drag", None))
            or (nonce_factory is not None and not callable(nonce_factory))
        ):
            raise CalibrationError("calibration boundary is malformed")
        private = project / "private"
        readiness = private / "readiness"
        try:
            private.mkdir(exist_ok=True)
            if private.resolve(strict=True) != private:
                raise CalibrationError("private readiness path escaped")
            readiness.mkdir(exist_ok=True)
            if readiness.resolve(strict=True) != readiness:
                raise CalibrationError("private readiness path escaped")
            seal_private(private, True)
            seal_private(readiness, True)
        except CalibrationError:
            raise
        except (OSError, RuntimeError) as exc:
            raise CalibrationError("private readiness path is unavailable") from exc
        self._readiness = readiness
        self._binding = binding
        self._capture = capture
        self._input = input_port
        self._prove_state = prove_state
        self._locate_control = locate_control
        self._live_gate = live_gate
        self._clipboard_read = clipboard_read
        self._clipboard_clear = clipboard_clear
        self._monotonic = monotonic
        self._wait = wait
        self._nonce_factory = nonce_factory or (lambda: secrets.token_hex(16))
        self._seal = seal_private

    def _now(self, deadline: float) -> float:
        try:
            value = self._monotonic()
        except BaseException as exc:
            raise CalibrationError("calibration clock failed") from exc
        if (
            type(value) not in (int, float)
            or type(value) is bool
            or not math.isfinite(float(value))
            or float(value) >= deadline
        ):
            raise CalibrationError("calibration deadline expired")
        return float(value)

    def _enabled(self, deadline: float) -> None:
        self._now(deadline)
        try:
            enabled = self._live_gate()
        except BaseException as exc:
            raise CalibrationError("calibration authorization unavailable") from exc
        if enabled is not True:
            raise CalibrationError("calibration authorization revoked")
        self._now(deadline)

    def _checkpoint(
        self,
        state: str,
        index: int,
        deadline: float,
        directory: Path,
    ) -> np.ndarray:
        self._now(deadline)
        try:
            frame = self._capture()
        except BaseException as exc:
            raise CalibrationError("calibration capture failed") from exc
        try:
            if (
                type(frame) is not np.ndarray
                or frame.dtype != np.uint8
                or frame.ndim != 3
                or frame.shape[2] != 3
                or frame.shape[:2] != (self._binding.height, self._binding.width)
            ):
                raise CalibrationError("calibration frame is malformed")
            self._now(deadline)
            try:
                proved = self._prove_state(frame, state)
            except BaseException as exc:
                raise CalibrationError(f"{state} evidence unavailable") from exc
            if proved is not True:
                raise CalibrationError(f"{state} evidence unavailable")
            ok, encoded = cv2.imencode(".png", frame)
            if ok is not True:
                raise CalibrationError("calibration checkpoint encoding failed")
            checkpoint = directory / f"{index:02d}-{state}.png"
            checkpoint.write_bytes(encoded.tobytes())
            self._seal(checkpoint, False)
            self._now(deadline)
            return frame
        except BaseException:
            if type(frame) is np.ndarray and frame.flags.writeable:
                frame.fill(0)
            raise

    @staticmethod
    def _crop(frame: np.ndarray, name: str) -> np.ndarray:
        x0, y0, x1, y1 = _CROPS[name]
        height, width = frame.shape[:2]
        crop = frame[
            int(height * y0) : int(height * y1),
            int(width * x0) : int(width * x1),
        ].copy()
        if crop.size == 0 or max(crop.shape[:2]) > 320:
            crop.fill(0)
            raise CalibrationError("calibration crop is malformed")
        return crop

    def _checkpoint_pair(
        self,
        state: str,
        index: int,
        deadline: float,
        directory: Path,
    ) -> tuple[np.ndarray, np.ndarray]:
        first = self._checkpoint(state, index, deadline, directory)
        try:
            self._wait(min(0.10, deadline - self._now(deadline)))
            second = self._checkpoint(state, index + 1, deadline, directory)
        except BaseException:
            first.fill(0)
            raise
        return first, second

    def _stable_crop(
        self,
        first: np.ndarray,
        second: np.ndarray,
        name: str,
        deadline: float,
    ) -> np.ndarray:
        one = self._crop(first, name)
        two = self._crop(second, name)
        try:
            self._now(deadline)
            difference = float(np.abs(one.astype(np.int16) - two.astype(np.int16)).mean())
            if one.shape != two.shape or not math.isfinite(difference) or difference > 1.0:
                raise CalibrationError(f"{name} stable samples unavailable")
            return one
        except BaseException:
            one.fill(0)
            raise
        finally:
            two.fill(0)

    def _stable_control_crop(
        self,
        first: np.ndarray,
        second: np.ndarray,
        control: LocatedControl,
        name: str,
        deadline: float,
    ) -> np.ndarray:
        height, width = first.shape[:2]
        x0, y0, x1, y1 = control.bounds
        bounds = (
            max(0, int(width * x0)),
            max(0, int(height * y0)),
            min(width, int(width * x1)),
            min(height, int(height * y1)),
        )
        one = first[bounds[1] : bounds[3], bounds[0] : bounds[2]].copy()
        two = second[bounds[1] : bounds[3], bounds[0] : bounds[2]].copy()
        try:
            self._now(deadline)
            if (
                one.size == 0
                or one.shape != two.shape
                or max(one.shape[:2]) > 320
            ):
                raise CalibrationError(f"{name} stable samples unavailable")
            difference = float(np.abs(one.astype(np.int16) - two.astype(np.int16)).mean())
            if not math.isfinite(difference) or difference > 1.0:
                raise CalibrationError(f"{name} stable samples unavailable")
            return one
        except BaseException:
            one.fill(0)
            raise
        finally:
            two.fill(0)

    def _semantic_control(
        self,
        frame: np.ndarray,
        name: str,
        expected: tuple[float, float] | None,
        deadline: float,
    ) -> LocatedControl:
        self._now(deadline)
        try:
            located = self._locate_control(frame, name)
        except CalibrationError:
            raise
        except BaseException as exc:
            raise CalibrationError(f"{name} target evidence unavailable") from exc
        if type(located) is not LocatedControl:
            raise CalibrationError(f"{name} target evidence unavailable")
        if expected is not None and (
            abs(located.x - expected[0]) > 0.02
            or abs(located.y - expected[1]) > 0.02
        ):
            raise CalibrationError(f"{name} donor proximity unavailable")
        self._now(deadline)
        return located

    def _gesture(self, kind: str, values: tuple[float, ...], deadline: float) -> None:
        self._enabled(deadline)
        try:
            if kind == "click":
                sent = self._input.click(
                    self._binding,
                    *values,
                    action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
                )
                settle = 0.75
            else:
                sent = self._input.drag(
                    self._binding,
                    *values,
                    action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
                )
                settle = 0.15
        except BaseException as exc:
            raise CalibrationError("calibration input unavailable") from exc
        if sent is not True:
            raise CalibrationError("calibration input failed")
        self._wait(min(settle, deadline - self._now(deadline)))
        self._now(deadline)

    def _publish(
        self,
        crops: dict[str, np.ndarray],
        nonce: str,
        deadline: float,
    ) -> CalibrationResult:
        profile = self._readiness / "profile.json"
        if set(crops) != _REQUIRED_NAMES or profile.exists():
            raise CalibrationError("calibration publication boundary is unavailable")
        temp = self._readiness / f".publish-{nonce}"
        profile_temp = self._readiness / f".profile-{nonce}.json"
        published: list[Path] = []
        try:
            self._now(deadline)
            temp.mkdir()
            self._seal(temp, True)
            profile_id = "readiness-s003-" + nonce[:12]
            result = CalibrationResult(profile_id)
            entries: dict[str, dict[str, object]] = {}
            for name in sorted(_REQUIRED_NAMES):
                self._now(deadline)
                ok, encoded = cv2.imencode(".png", crops[name])
                if ok is not True:
                    raise CalibrationError("calibration crop encoding failed")
                payload = encoded.tobytes()
                decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                if type(decoded) is not np.ndarray or not np.array_equal(decoded, crops[name]):
                    raise CalibrationError("calibration crop verification failed")
                source = temp / f"{name}.png"
                source.write_bytes(payload)
                self._seal(source, False)
                entries[name] = {
                    "file": source.name,
                    "file_sha256": hashlib.sha256(payload).hexdigest(),
                    "pixel_sha256": hashlib.sha256(decoded.tobytes()).hexdigest(),
                    "threshold_ppm": 900_000,
                    "roi_ppm": _MATCH_ROIS_PPM[name],
                }
            manifest = (
                json.dumps(
                    {"schema": 1, "profile_id": profile_id, "templates": entries},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
                + b"\n"
            )
            profile_temp.write_bytes(manifest)
            self._seal(profile_temp, False)
            for crop in crops.values():
                crop.fill(0)
            crops.clear()
            for name in sorted(_REQUIRED_NAMES):
                self._now(deadline)
                destination = self._readiness / f"{name}.png"
                if destination.exists():
                    raise CalibrationError("calibration publication target exists")
                os.replace(temp / f"{name}.png", destination)
                published.append(destination)
            self._now(deadline)
            temp.rmdir()
            self._now(deadline)
            # This atomic replace is deliberately the final fallible operation.
            # No cleanup, clock, callback, allocation, or rollback follows it.
            os.replace(profile_temp, profile)
            return result
        except BaseException:
            cleanup_failed = False
            for path in published:
                try:
                    path.unlink()
                except OSError:
                    cleanup_failed = True
            try:
                profile_temp.unlink(missing_ok=True)
                if temp.exists():
                    shutil.rmtree(temp)
            except OSError:
                cleanup_failed = True
            if cleanup_failed:
                raise CalibrationError("calibration publication cleanup failed") from None
            raise

    def run(self, *, deadline: float) -> CalibrationResult:
        if (
            type(deadline) not in (int, float)
            or type(deadline) is bool
            or not math.isfinite(float(deadline))
        ):
            raise CalibrationError("calibration deadline is malformed")
        deadline = float(deadline)
        nonce = self._nonce_factory()
        if (
            type(nonce) is not str
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
        ):
            raise CalibrationError("calibration nonce is malformed")
        calibration = self._readiness / "calibration"
        run_directory = calibration / nonce
        crops: dict[str, np.ndarray] = {}
        try:
            calibration.mkdir(exist_ok=True)
            self._seal(calibration, True)
            run_directory.mkdir()
            self._seal(run_directory, True)
            self._clipboard_clear()
            first, second = self._checkpoint_pair("home", 1, deadline, run_directory)
            try:
                crops["home"] = self._stable_crop(first, second, "home", deadline)
                settings = self._semantic_control(
                    second, "settings_button", _SETTINGS_BUTTON, deadline
                )
                crops["settings_button"] = self._stable_control_crop(
                    first, second, settings, "settings_button", deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            self._gesture("click", settings.center, deadline)
            first, second = self._checkpoint_pair("settings", 3, deadline, run_directory)
            try:
                crops["settings"] = self._stable_crop(first, second, "settings", deadline)
                more = self._semantic_control(
                    second, "more_button", _MORE_SETTINGS_BUTTON, deadline
                )
                crops["more_button"] = self._stable_control_crop(
                    first, second, more, "more_button", deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            self._gesture("click", more.center, deadline)
            for pass_index in range(3):
                first, second = self._checkpoint_pair(
                    "more", 5 + pass_index * 2, deadline, run_directory
                )
                try:
                    close_guard = self._semantic_control(
                        second, "more_close", _SETTINGS_CLOSE, deadline
                    )
                    if pass_index == 0:
                        crops["more"] = self._stable_crop(first, second, "more", deadline)
                        crops["more_close"] = self._stable_control_crop(
                            first, second, close_guard, "more_close", deadline
                        )
                finally:
                    first.fill(0)
                    second.fill(0)
                if type(close_guard) is not LocatedControl:
                    raise CalibrationError("more close evidence unavailable")
                self._gesture("drag", _SCROLL_DRAG, deadline)
            first, second = self._checkpoint_pair("more", 11, deadline, run_directory)
            try:
                export = self._semantic_control(second, "export", _EXPORT_BUTTON, deadline)
                crops["export"] = self._stable_control_crop(
                    first, second, export, "export", deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            self._gesture("click", export.center, deadline)
            try:
                exported = self._clipboard_read()
            except BaseException as exc:
                raise CalibrationError("calibration export proof unavailable") from exc
            if type(exported) is not str or not exported:
                raise CalibrationError("calibration export proof unavailable")
            exported = ""
            self._clipboard_clear()
            first, second = self._checkpoint_pair("more", 13, deadline, run_directory)
            try:
                # Re-prove the reviewed close template immediately before clicking.
                more_close = self._semantic_control(
                    second, "more_close", _SETTINGS_CLOSE, deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            self._gesture("click", more_close.center, deadline)
            first, second = self._checkpoint_pair("settings", 15, deadline, run_directory)
            try:
                settings_close = self._semantic_control(
                    second, "settings_close", _SETTINGS_CLOSE, deadline
                )
                crops["settings_close"] = self._stable_control_crop(
                    first, second, settings_close, "settings_close", deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            self._gesture("click", settings_close.center, deadline)
            first, second = self._checkpoint_pair("home", 17, deadline, run_directory)
            try:
                ordinary_card = self._semantic_control(
                    second, "ordinary_card", None, deadline
                )
                crops["ordinary_card"] = self._stable_control_crop(
                    first, second, ordinary_card, "ordinary_card", deadline
                )
            finally:
                first.fill(0)
                second.fill(0)
            # Every cleanup that can fail precedes profile publication.
            shutil.rmtree(run_directory)
            try:
                self._clipboard_clear()
            except BaseException as cleanup_exc:
                raise CalibrationError("clipboard cleanup failed") from cleanup_exc
            return self._publish(crops, nonce, deadline)
        except BaseException as exc:
            for crop in crops.values():
                crop.fill(0)
            try:
                self._clipboard_clear()
            except BaseException as cleanup_exc:
                failure = CalibrationError("clipboard cleanup failed")
                failure.__cause__ = cleanup_exc
                raise failure
            if type(exc) is CalibrationError:
                raise
            raise CalibrationError("calibration transaction failed") from exc


__all__ = [
    "CalibrationError",
    "CalibrationResult",
    "LocatedControl",
    "PrivateBootstrapLocator",
    "ReadinessCalibrationController",
]
