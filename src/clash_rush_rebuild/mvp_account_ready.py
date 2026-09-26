"""Bounded startup-to-account-ready sequence for the local MVP.

The startup polling order is adapted from CoC_Bot ``start_coc`` at pinned commit
``a5c943afed0ed3b9abedbbc228b0889145ecaf24``.  Positive Home evidence follows
the BasePilot-derived local recognizer contract.  All visual assets are supplied
from an operator-owned private profile; this module bundles no font or profile
image and persists no captured frame.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .input_authorization import InputAction
from .lifecycle import PlayerBinding
from .mvp_local_world_export import ExportCaptureError, summarize_world_export
from .win32_state_io import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_OPEN_REPARSE_POINT,
    GENERIC_READ,
    OPEN_EXISTING,
    NativeWin32StateApi,
    StateFileApi,
    Win32StateIoError,
)


class AccountReadinessError(RuntimeError):
    """The closed AccountReady chain could not be proved."""


@dataclass(frozen=True, slots=True)
class AccountReady:
    run_nonce: str
    profile_id: str

    def __post_init__(self) -> None:
        if (
            type(self.run_nonce) is not str
            or len(self.run_nonce) != 32
            or any(character not in "0123456789abcdef" for character in self.run_nonce)
            or type(self.profile_id) is not str
            or not self.profile_id
            or len(self.profile_id) > 64
        ):
            raise AccountReadinessError("AccountReady binding is malformed")


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    name: str
    template: np.ndarray
    sha256: str
    threshold: float
    roi: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        template = self.template
        if (
            type(self.name) is not str
            or not self.name
            or type(template) is not np.ndarray
            or template.dtype != np.uint8
            or template.ndim not in (2, 3)
            or template.size == 0
            or type(self.sha256) is not str
            or hashlib.sha256(template.tobytes()).hexdigest() != self.sha256
            or type(self.threshold) is not float
            or not math.isfinite(self.threshold)
            or not 0.0 < self.threshold <= 1.0
            or type(self.roi) is not tuple
            or len(self.roi) != 4
            or any(type(value) is not float or not math.isfinite(value) for value in self.roi)
            or not (0.0 <= self.roi[0] < self.roi[2] <= 1.0)
            or not (0.0 <= self.roi[1] < self.roi[3] <= 1.0)
        ):
            raise AccountReadinessError("visual profile template is malformed")
        frozen = template.copy()
        frozen.flags.writeable = False
        object.__setattr__(self, "template", frozen)


_REQUIRED_TEMPLATES = frozenset(
    {
        "home",
        "settings_button",
        "settings",
        "more_button",
        "more",
        "export",
        "more_close",
        "settings_close",
    }
)


@dataclass(frozen=True, slots=True)
class ReadinessVisualProfile:
    profile_id: str
    specs: tuple[TemplateSpec, ...]

    def __post_init__(self) -> None:
        if (
            type(self.profile_id) is not str
            or not self.profile_id
            or len(self.profile_id) > 64
            or type(self.specs) is not tuple
            or any(type(spec) is not TemplateSpec for spec in self.specs)
            or frozenset(spec.name for spec in self.specs) != _REQUIRED_TEMPLATES
            or len(self.specs) != len(_REQUIRED_TEMPLATES)
        ):
            raise AccountReadinessError("visual profile is malformed")

    def spec(self, name: str) -> TemplateSpec:
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise AccountReadinessError("visual profile is incomplete")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AccountReadinessError("visual profile has duplicate keys")
        result[key] = value
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


_BOOTSTRAP_NAMES = frozenset({"launcher"})


def _read_handle_bound(
    path: Path, limit: int, api: StateFileApi,
) -> bytes:
    handle = api.create_file(
        str(path), GENERIC_READ, 0, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT
    )
    if handle is None or handle == 0:
        raise Win32StateIoError("exclusive private-file open failed")
    failure: BaseException | None = None
    try:
        if api.handle_attributes(handle) & FILE_ATTRIBUTE_REPARSE_POINT:
            raise Win32StateIoError("opened private path is a reparse point")
        actual = os.path.normcase(os.path.abspath(api.final_path(handle)))
        expected = os.path.normcase(os.path.abspath(path))
        if actual != expected:
            raise Win32StateIoError("opened private path changed")
        result = bytearray()
        while True:
            remaining = limit + 1 - len(result)
            chunk = api.read_file(handle, remaining)
            if type(chunk) is not bytes or len(chunk) > remaining:
                raise Win32StateIoError("private-file read returned invalid data")
            result.extend(chunk)
            if len(result) > limit:
                raise Win32StateIoError("private file exceeds read bound")
            if not chunk:
                return bytes(result)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if api.close_handle(handle) is not True and failure is None:
            raise Win32StateIoError("private-file handle close failed")


def _decode_narrow_png(payload: bytes) -> np.ndarray:
    if (
        type(payload) is not bytes
        or len(payload) < 33
        or payload[:8] != b"\x89PNG\r\n\x1a\n"
        or int.from_bytes(payload[8:12], "big") != 13
        or payload[12:16] != b"IHDR"
    ):
        raise AccountReadinessError("bootstrap asset is not a narrow PNG")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if not (1 <= width <= 320 and 1 <= height <= 320):
        raise AccountReadinessError("bootstrap asset is not a narrow PNG")
    pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if (
        type(pixels) is not np.ndarray
        or pixels.dtype != np.uint8
        or pixels.ndim != 3
        or pixels.shape[:2] != (height, width)
        or pixels.shape[2] != 3
    ):
        raise AccountReadinessError("bootstrap asset is not a narrow PNG")
    return pixels


class PrivateBootstrapLocator:
    """Strict digest-sealed locator adapted from the reverted S003 calibrator."""

    def __init__(
        self, project_root: str | Path, *, file_api: StateFileApi | None = None
    ) -> None:
        api = NativeWin32StateApi() if file_api is None else file_api
        try:
            project = Path(project_root).resolve(strict=True)
            private = (project / "private").resolve(strict=True)
            root = (private / "readiness" / "bootstrap").resolve(strict=True)
            manifest = (root / "profile.json").resolve(strict=True)
            if (
                not _inside(private, project)
                or not _inside(root, private)
                or not _inside(manifest, root)
                or root.is_symlink()
                or manifest.is_symlink()
                or manifest.parent != root
            ):
                raise AccountReadinessError("bootstrap path escaped")
            if not manifest.is_file():
                raise OSError
            raw_bytes = _read_handle_bound(manifest, 64_000, api)
            raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
        except AccountReadinessError:
            raise
        except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
            raise AccountReadinessError("bootstrap manifest is unavailable") from exc
        controls = raw.get("controls") if type(raw) is dict else None
        if (
            type(raw) is not dict
            or set(raw) != {"schema", "controls"}
            or type(raw.get("schema")) is not int
            or raw["schema"] != 1
            or type(controls) is not dict
            or set(controls) != _BOOTSTRAP_NAMES
        ):
            raise AccountReadinessError("bootstrap manifest is malformed")
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
                raise AccountReadinessError("bootstrap manifest is malformed")
            try:
                asset = (root / entry["file"]).resolve(strict=True)
                if not _inside(asset, root) or asset.parent != root or asset.is_symlink():
                    raise AccountReadinessError("bootstrap path escaped")
                payload = _read_handle_bound(asset, 4_000_000, api)
            except AccountReadinessError:
                raise
            except (OSError, RuntimeError) as exc:
                raise AccountReadinessError(
                    f"{name} bootstrap evidence unavailable"
                ) from exc
            if hashlib.sha256(payload).hexdigest() != entry["file_sha256"]:
                raise AccountReadinessError(f"{name} bootstrap evidence unavailable")
            pixels = _decode_narrow_png(payload)
            if hashlib.sha256(pixels.tobytes()).hexdigest() != entry["pixel_sha256"]:
                raise AccountReadinessError(f"{name} bootstrap evidence unavailable")
            pixels.flags.writeable = False
            loaded[name] = (
                pixels,
                tuple(entry["roi_ppm"]),
                entry["threshold_ppm"] / 1_000_000,
            )
        self._controls = loaded

    def __call__(self, frame: np.ndarray, name: str) -> _Match | None:
        if type(name) is not str or name not in self._controls:
            raise AccountReadinessError("control bootstrap evidence unavailable")
        if type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim != 3:
            raise AccountReadinessError(f"{name} bootstrap evidence unavailable")
        template, roi, threshold = self._controls[name]
        height, width = frame.shape[:2]
        left, top, right, bottom = (
            width * roi[0] // 1_000_000,
            height * roi[1] // 1_000_000,
            width * roi[2] // 1_000_000,
            height * roi[3] // 1_000_000,
        )
        region = frame[top:bottom, left:right]
        if width < 2 or height < 2:
            raise AccountReadinessError(f"{name} bootstrap evidence unavailable")
        if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
            return None
        try:
            response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _minimum, score, _minimum_point, point = cv2.minMaxLoc(response)
        except cv2.error as exc:
            raise AccountReadinessError(f"{name} bootstrap evidence unavailable") from exc
        if not math.isfinite(float(score)) or float(score) < threshold:
            return None
        return _Match(
            (left + point[0] + (template.shape[1] - 1) / 2) / (width - 1),
            (top + point[1] + (template.shape[0] - 1) / 2) / (height - 1),
            float(score),
        )


def load_private_visual_profile(
    project_root: str | Path, manifest_path: str | Path
) -> ReadinessVisualProfile:
    """Load one exact, digest-sealed profile exclusively beneath ``private``."""
    try:
        project = Path(project_root).resolve(strict=True)
        private = (project / "private").resolve(strict=True)
        manifest = Path(manifest_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AccountReadinessError("private visual profile is unavailable") from exc
    if not _inside(manifest, private) or not manifest.is_file():
        raise AccountReadinessError("private visual profile path escaped")
    try:
        raw_bytes = manifest.read_bytes()
        if len(raw_bytes) > 1_000_000:
            raise AccountReadinessError("private visual profile is oversized")
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except AccountReadinessError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccountReadinessError("private visual profile is malformed") from exc
    if (
        type(raw) is not dict
        or set(raw) != {"schema", "profile_id", "templates"}
        or type(raw.get("schema")) is not int
        or raw["schema"] != 1
        or type(raw.get("profile_id")) is not str
        or type(raw.get("templates")) is not dict
        or set(raw["templates"]) != _REQUIRED_TEMPLATES
    ):
        raise AccountReadinessError("private visual profile is malformed")
    specs: list[TemplateSpec] = []
    for name in sorted(_REQUIRED_TEMPLATES):
        entry = raw["templates"][name]
        if (
            type(entry) is not dict
            or set(entry) != {
                "file", "file_sha256", "pixel_sha256", "threshold_ppm", "roi_ppm"
            }
            or type(entry.get("file")) is not str
            or not entry["file"]
            or Path(entry["file"]).name != entry["file"]
            or type(entry.get("file_sha256")) is not str
            or type(entry.get("pixel_sha256")) is not str
            or type(entry.get("threshold_ppm")) is not int
            or type(entry["threshold_ppm"]) is bool
            or not 1 <= entry["threshold_ppm"] <= 1_000_000
            or type(entry.get("roi_ppm")) is not list
            or len(entry["roi_ppm"]) != 4
            or any(type(value) is not int or type(value) is bool for value in entry["roi_ppm"])
        ):
            raise AccountReadinessError("private visual profile is malformed")
        try:
            asset = (manifest.parent / entry["file"]).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AccountReadinessError("private visual asset is unavailable") from exc
        if not _inside(asset, private) or not asset.is_file():
            raise AccountReadinessError("private visual asset path escaped")
        try:
            encoded = asset.read_bytes()
        except OSError as exc:
            raise AccountReadinessError("private visual asset is unavailable") from exc
        if (
            len(encoded) > 4_000_000
            or hashlib.sha256(encoded).hexdigest() != entry["file_sha256"]
        ):
            raise AccountReadinessError("private visual asset digest mismatch")
        pixels = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if (
            type(pixels) is not np.ndarray
            or pixels.size == 0
            or hashlib.sha256(pixels.tobytes()).hexdigest() != entry["pixel_sha256"]
        ):
            raise AccountReadinessError("private visual asset pixel digest mismatch")
        roi = tuple(float(value) / 1_000_000 for value in entry["roi_ppm"])
        specs.append(
            TemplateSpec(
                name,
                pixels,
                entry["pixel_sha256"],
                float(entry["threshold_ppm"]) / 1_000_000,
                roi,
            )
        )
    return ReadinessVisualProfile(raw["profile_id"], tuple(specs))


@dataclass(frozen=True, slots=True)
class _Match:
    x: float
    y: float
    confidence: float


class ExportInputPort(Protocol):
    def click(
        self,
        binding: PlayerBinding,
        x: float,
        y: float,
        *,
        action: InputAction,
        pre_input_check: Callable[[], bool] | None = None,
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


class StartupInputPort(Protocol):
    def click_continue(
        self, binding: PlayerBinding, x: float, y: float
    ) -> bool: ...


def _match_template(frame: np.ndarray, spec: TemplateSpec) -> _Match | None:
    if type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim not in (2, 3):
        raise AccountReadinessError("captured frame is malformed")
    height, width = frame.shape[:2]
    x0, y0, x1, y1 = spec.roi
    left, top = int(width * x0), int(height * y0)
    right, bottom = int(width * x1), int(height * y1)
    region = frame[top:bottom, left:right]
    template = spec.template
    if region.ndim != template.ndim:
        if region.ndim == 3:
            region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        if template.ndim == 3:
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    template_height, template_width = template.shape[:2]
    if (
        region.size == 0
        or template_height > region.shape[0]
        or template_width > region.shape[1]
    ):
        return None
    try:
        response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _minimum, confidence, _minimum_location, location = cv2.minMaxLoc(response)
    except cv2.error as exc:
        raise AccountReadinessError("visual matcher failed") from exc
    if not math.isfinite(float(confidence)) or float(confidence) < spec.threshold:
        return None
    return _Match(
        (left + location[0] + template_width / 2) / width,
        (top + location[1] + template_height / 2) / height,
        float(confidence),
    )


class AccountReadinessController:
    """One absolute-deadline, source/destination-gated readiness chain."""

    _SCROLL = (0.50, 0.74, 0.50, 0.24)

    def __init__(
        self,
        *,
        binding: PlayerBinding,
        run_nonce: str,
        capture: Callable[[], np.ndarray],
        profile: ReadinessVisualProfile,
        startup_input: StartupInputPort,
        export_input: ExportInputPort,
        find_continue: Callable[[np.ndarray], object | None],
        home_ready: Callable[[np.ndarray], bool] | None = None,
        live_gate: Callable[[], bool],
        clipboard_read: Callable[[], str],
        clipboard_clear: Callable[[], None],
        expected_tag_sha256: str,
        wall_clock: Callable[[], object],
        monotonic: Callable[[], float],
        wait: Callable[[float], None],
        locate_bootstrap: Callable[[np.ndarray, str], _Match | None] | None = None,
    ) -> None:
        if (
            type(binding) is not PlayerBinding
            or type(run_nonce) is not str
            or len(run_nonce) != 32
            or any(character not in "0123456789abcdef" for character in run_nonce)
            or type(profile) is not ReadinessVisualProfile
            or not all(
                callable(value)
                for value in (
                    capture,
                    find_continue,
                    live_gate,
                    clipboard_read,
                    clipboard_clear,
                    wall_clock,
                    monotonic,
                    wait,
                )
            )
            or not callable(getattr(startup_input, "click_continue", None))
            or not callable(getattr(export_input, "click", None))
            or not callable(getattr(export_input, "drag", None))
            or (home_ready is not None and not callable(home_ready))
            or (locate_bootstrap is not None and not callable(locate_bootstrap))
        ):
            raise AccountReadinessError("readiness boundary is malformed")
        self._binding = binding
        self._run_nonce = run_nonce
        self._capture = capture
        self._profile = profile
        self._startup_input = startup_input
        self._export_input = export_input
        self._find_continue = find_continue
        self._home_ready = home_ready
        self._live_gate = live_gate
        self._clipboard_read = clipboard_read
        self._clipboard_clear = clipboard_clear
        self._expected_tag_sha256 = expected_tag_sha256
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._wait = wait
        self._locate_bootstrap = locate_bootstrap

    def _time(self, deadline: float) -> float:
        try:
            value = self._monotonic()
        except BaseException as exc:
            raise AccountReadinessError("readiness clock failed") from exc
        if (
            type(value) not in (int, float)
            or type(value) is bool
            or not math.isfinite(float(value))
            or float(value) >= deadline
        ):
            raise AccountReadinessError("readiness deadline expired")
        return float(value)

    def _capture_frame(self, deadline: float) -> np.ndarray:
        self._time(deadline)
        try:
            frame = self._capture()
        except BaseException as exc:
            raise AccountReadinessError("readiness capture failed") from exc
        try:
            self._time(deadline)
            if (
                type(frame) is not np.ndarray
                or frame.dtype != np.uint8
                or frame.ndim not in (2, 3)
                or frame.size == 0
            ):
                raise AccountReadinessError("captured frame is malformed")
            return frame
        except BaseException:
            if type(frame) is np.ndarray and frame.flags.writeable:
                frame.fill(0)
            frame = None
            raise

    @contextmanager
    def _transient_frame(self, deadline: float):
        frame = self._capture_frame(deadline)
        try:
            yield frame
        finally:
            try:
                frame.fill(0)
            finally:
                frame = None

    def _match(self, frame: np.ndarray, name: str, deadline: float) -> _Match | None:
        self._time(deadline)
        match = _match_template(frame, self._profile.spec(name))
        self._time(deadline)
        return match

    def _require_state(self, frame: np.ndarray, name: str, deadline: float) -> None:
        if self._match(frame, name, deadline) is None:
            raise AccountReadinessError(f"{name} evidence unavailable")

    def _enabled(self, deadline: float) -> None:
        self._time(deadline)
        try:
            enabled = self._live_gate()
        except BaseException as exc:
            raise AccountReadinessError("readiness live gate unavailable") from exc
        if enabled is not True:
            raise AccountReadinessError("readiness live gate disabled")
        self._time(deadline)

    def _settle(self, seconds: float, deadline: float) -> None:
        now = self._time(deadline)
        try:
            self._wait(min(seconds, deadline - now))
        except BaseException as exc:
            raise AccountReadinessError("readiness wait failed") from exc
        self._time(deadline)

    def _click(self, match: _Match, deadline: float) -> None:
        self._enabled(deadline)
        try:
            sent = self._export_input.click(
                self._binding,
                match.x,
                match.y,
                action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
            )
        except BaseException as exc:
            raise AccountReadinessError("readiness input unavailable") from exc
        if sent is not True:
            raise AccountReadinessError("readiness input failed")
        self._settle(0.75, deadline)

    def _drag(self, deadline: float) -> None:
        self._enabled(deadline)
        try:
            sent = self._export_input.drag(
                self._binding,
                *self._SCROLL,
                action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
            )
        except BaseException as exc:
            raise AccountReadinessError("readiness input unavailable") from exc
        if sent is not True:
            raise AccountReadinessError("readiness input failed")
        self._settle(0.15, deadline)

    def _same_launcher_center(self, first: _Match, second: _Match) -> bool:
        return bool(
            type(first) is _Match
            and type(second) is _Match
            and abs((first.x - second.x) * (self._binding.width - 1)) <= 2.0
            and abs((first.y - second.y) * (self._binding.height - 1)) <= 2.0
        )

    def _launch_game(self, match: _Match, deadline: float) -> None:
        self._enabled(deadline)

        def launcher_still_present() -> bool:
            if self._locate_bootstrap is None:
                return False
            with self._transient_frame(deadline) as fresh:
                located = self._locate_bootstrap(fresh, "launcher")
                return self._same_launcher_center(match, located)

        try:
            sent = self._export_input.click(
                self._binding,
                match.x,
                match.y,
                action=InputAction.STARTUP_LAUNCH_GAME,
                pre_input_check=launcher_still_present,
            )
        except BaseException as exc:
            raise AccountReadinessError("startup launcher input unavailable") from exc
        if sent is not True:
            raise AccountReadinessError("startup launcher input failed")

    def _stable_launcher(
        self, frame: np.ndarray, target: _Match, deadline: float
    ) -> _Match:
        if self._locate_bootstrap is None:
            raise AccountReadinessError("launcher bootstrap evidence unavailable")
        for _ in range(2):
            self._settle(0.05, deadline)
            with self._transient_frame(deadline) as fresh:
                try:
                    located = self._locate_bootstrap(fresh, "launcher")
                except AccountReadinessError:
                    raise
                except BaseException as exc:
                    raise AccountReadinessError(
                        "launcher bootstrap evidence unavailable"
                    ) from exc
                if not self._same_launcher_center(target, located):
                    raise AccountReadinessError("launcher stability unavailable")
        return target

    def _clear_clipboard(self) -> None:
        try:
            self._clipboard_clear()
        except BaseException as exc:
            raise AccountReadinessError("clipboard cleanup failed") from exc

    def _reach_home(self, deadline: float) -> None:
        continue_used = False
        launcher_used = False
        while True:
            with self._transient_frame(deadline) as frame:
                profile_home = self._match(frame, "home", deadline) is not None
                try:
                    classified_home = (
                        profile_home
                        if self._home_ready is None
                        else self._home_ready(frame) is True
                    )
                except BaseException as exc:
                    raise AccountReadinessError("Home classification failed") from exc
                self._time(deadline)
                if profile_home and classified_home:
                    return
                self._time(deadline)
                try:
                    match = self._find_continue(frame)
                except BaseException as exc:
                    raise AccountReadinessError("startup classification failed") from exc
                self._time(deadline)
                if match is not None:
                    if continue_used:
                        raise AccountReadinessError("startup Continue cap exhausted")
                    try:
                        x, y = match.x, match.y
                    except BaseException as exc:
                        raise AccountReadinessError("startup Continue target malformed") from exc
                    if any(
                        type(value) not in (int, float)
                        or type(value) is bool
                        or not math.isfinite(float(value))
                        or not 0.0 <= float(value) <= 1.0
                        for value in (x, y)
                    ):
                        raise AccountReadinessError("startup Continue target malformed")
                    self._enabled(deadline)
                    try:
                        sent = self._startup_input.click_continue(
                            self._binding, float(x), float(y)
                        )
                    except BaseException as exc:
                        raise AccountReadinessError("startup Continue input unavailable") from exc
                    if sent is not True:
                        raise AccountReadinessError("startup Continue input failed")
                    continue_used = True
                    self._time(deadline)
                elif self._locate_bootstrap is not None:
                    try:
                        launcher = self._locate_bootstrap(frame, "launcher")
                    except AccountReadinessError:
                        raise
                    except BaseException as exc:
                        raise AccountReadinessError(
                            "launcher bootstrap evidence unavailable"
                        ) from exc
                    if launcher is not None and type(launcher) is not _Match:
                        raise AccountReadinessError(
                            "launcher bootstrap evidence unavailable"
                        )
                    if launcher_used and launcher is not None:
                        raise AccountReadinessError("startup launcher cap exhausted")
                    if launcher is not None:
                        launcher = self._stable_launcher(frame, launcher, deadline)
                        self._launch_game(launcher, deadline)
                        launcher_used = True
                        self._time(deadline)
            remaining = deadline - self._time(deadline)
            self._wait(min(0.25, remaining))

    def run(self, *, deadline: float) -> AccountReady:
        if (
            type(deadline) not in (int, float)
            or type(deadline) is bool
            or not math.isfinite(float(deadline))
        ):
            raise AccountReadinessError("readiness deadline is malformed")
        deadline = float(deadline)
        self._reach_home(deadline)
        previous = ""
        exported = ""
        result: AccountReady | None = None
        failure: BaseException | None = None
        try:
            previous = self._clipboard_read()
            self._clear_clipboard()

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "home", deadline)
                target = self._match(frame, "settings_button", deadline)
                if target is None:
                    raise AccountReadinessError("Settings target unavailable")
                self._click(target, deadline)

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "settings", deadline)
                target = self._match(frame, "more_button", deadline)
                if target is None:
                    raise AccountReadinessError("More Settings target unavailable")
                self._click(target, deadline)

            for _ in range(3):
                with self._transient_frame(deadline) as frame:
                    self._require_state(frame, "more", deadline)
                    self._drag(deadline)

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "more", deadline)
                target = self._match(frame, "export", deadline)
                if target is None:
                    raise AccountReadinessError("Export target unavailable")
                self._click(target, deadline)

            exported = self._clipboard_read()
            if type(exported) is not str or not exported or exported == previous:
                raise AccountReadinessError("world export did not replace clipboard")
            try:
                summary = summarize_world_export(
                    exported,
                    expected_tag_sha256=self._expected_tag_sha256,
                    now=self._wall_clock(),
                )
            except ExportCaptureError as exc:
                raise AccountReadinessError("world export proof failed") from exc
            if summary.account_matches is not True:
                raise AccountReadinessError("world export proof failed")
            self._clear_clipboard()

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "more", deadline)
                target = self._match(frame, "more_close", deadline)
                if target is None:
                    raise AccountReadinessError("More Settings close target unavailable")
                self._click(target, deadline)

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "settings", deadline)
                target = self._match(frame, "settings_close", deadline)
                if target is None:
                    raise AccountReadinessError("Settings close target unavailable")
                self._click(target, deadline)

            with self._transient_frame(deadline) as frame:
                self._require_state(frame, "home", deadline)
                result = AccountReady(self._run_nonce, self._profile.profile_id)
        except BaseException as exc:
            failure = exc
        finally:
            exported = ""
            previous = ""
            try:
                self._clear_clipboard()
            except BaseException as exc:
                failure = AccountReadinessError("clipboard cleanup failed")
                failure.__cause__ = exc
        if failure is not None:
            raise failure
        if type(result) is not AccountReady:
            raise AccountReadinessError("AccountReady result unavailable")
        return result


__all__ = [
    "AccountReady",
    "AccountReadinessController",
    "AccountReadinessError",
    "ReadinessVisualProfile",
    "TemplateSpec",
    "load_private_visual_profile",
]
