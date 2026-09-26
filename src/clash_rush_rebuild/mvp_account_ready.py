"""Manual-Home account-readiness sequence for the local MVP.

The operator opens Clash of Clans before this sequence begins, matching BasePilot's
``Bot.start`` precondition at pinned commit ``4ede1efd220ffc79a5b490cfd3788b44d2584da4``.
An owned frame must satisfy both the sealed private Home template and the
BasePilot-derived local recognizer contract before any input adapter exists. All
visual assets are operator-owned and private; captured frames are transient.
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
from .no_input_home_diagnostic import (
    HomeDiagnosticResult,
    NoInputHomeDiagnosticController,
)
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
        raise AccountReadinessError("private visual asset is not a narrow PNG")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if not (1 <= width <= 320 and 1 <= height <= 320):
        raise AccountReadinessError("private visual asset is not a narrow PNG")
    pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if (
        type(pixels) is not np.ndarray
        or pixels.dtype != np.uint8
        or pixels.ndim != 3
        or pixels.shape[:2] != (height, width)
        or pixels.shape[2] != 3
    ):
        raise AccountReadinessError("private visual asset is not a narrow PNG")
    return pixels


def load_private_visual_profile(
    project_root: str | Path,
    manifest_path: str | Path,
    *,
    file_api: StateFileApi | None = None,
) -> ReadinessVisualProfile:
    """Load one exact, handle-bound digest-sealed profile beneath ``private``."""
    api = NativeWin32StateApi() if file_api is None else file_api
    try:
        project = Path(project_root).resolve(strict=True)
        expected_private = project / "private"
        private = expected_private.resolve(strict=True)
        manifest = Path(os.path.abspath(os.fspath(manifest_path)))
    except (OSError, RuntimeError) as exc:
        raise AccountReadinessError("private visual profile is unavailable") from exc
    if (
        private != expected_private.absolute()
        or not _inside(private, project)
        or not _inside(manifest, private)
    ):
        raise AccountReadinessError("private visual profile path escaped")
    try:
        raw_bytes = _read_handle_bound(manifest, 1_000_000, api)
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except AccountReadinessError:
        raise
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
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
        asset = manifest.parent / entry["file"]
        if not _inside(asset, private):
            raise AccountReadinessError("private visual asset path escaped")
        try:
            encoded = _read_handle_bound(asset, 4_000_000, api)
        except (OSError, RuntimeError) as exc:
            raise AccountReadinessError("private visual asset is unavailable") from exc
        if hashlib.sha256(encoded).hexdigest() != entry["file_sha256"]:
            raise AccountReadinessError("private visual asset digest mismatch")
        pixels = _decode_narrow_png(encoded)
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


def manual_home_frame_verified(
    frame: np.ndarray, profile: ReadinessVisualProfile
) -> bool:
    """Return exact positive sealed-profile and donor-derived HOME proof."""
    if type(profile) is not ReadinessVisualProfile:
        raise AccountReadinessError("manual Home profile is malformed")
    try:
        profile_home = _match_template(frame, profile.spec("home")) is not None
        diagnostic_home = (
            NoInputHomeDiagnosticController.detect_frame(frame)
            is HomeDiagnosticResult.HOME
        )
    except AccountReadinessError:
        raise
    except BaseException as exc:
        raise AccountReadinessError("manual Home classification failed") from exc
    return bool(profile_home and diagnostic_home)


def require_manual_home_frame(
    frame: np.ndarray, profile: ReadinessVisualProfile
) -> None:
    """Require both sealed-profile and donor-derived HOME proof on one frame."""
    if not manual_home_frame_verified(frame, profile):
        raise AccountReadinessError("manual Home first frame was not verified")


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
        export_input: ExportInputPort,
        live_gate: Callable[[], bool],
        clipboard_read: Callable[[], str],
        clipboard_clear: Callable[[], None],
        expected_tag_sha256: str,
        wall_clock: Callable[[], object],
        monotonic: Callable[[], float],
        wait: Callable[[float], None],
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
                    live_gate,
                    clipboard_read,
                    clipboard_clear,
                    wall_clock,
                    monotonic,
                    wait,
                )
            )
            or not callable(getattr(export_input, "click", None))
            or not callable(getattr(export_input, "drag", None))
        ):
            raise AccountReadinessError("readiness boundary is malformed")
        self._binding = binding
        self._run_nonce = run_nonce
        self._capture = capture
        self._profile = profile
        self._export_input = export_input
        self._live_gate = live_gate
        self._clipboard_read = clipboard_read
        self._clipboard_clear = clipboard_clear
        self._expected_tag_sha256 = expected_tag_sha256
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._wait = wait

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

    def _clear_clipboard(self) -> None:
        try:
            self._clipboard_clear()
        except BaseException as exc:
            raise AccountReadinessError("clipboard cleanup failed") from exc

    def run(self, *, deadline: float) -> AccountReady:
        if (
            type(deadline) not in (int, float)
            or type(deadline) is bool
            or not math.isfinite(float(deadline))
        ):
            raise AccountReadinessError("readiness deadline is malformed")
        deadline = float(deadline)
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
    "manual_home_frame_verified",
    "require_manual_home_frame",
]
