"""Concrete local attack-only runtime and persistent control boundary.

The bounded gesture order and normalized targets are adapted from the first-party
``clash-rush-automation`` M9/native-click seam at immutable revision
949497bf0a543a43ec6ef39a8c897e366bc10362.  This module deliberately exposes
no upgrade, research, reward, placement, donation, cart, or purchase action.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .input_authorization import InputAction
from .lifecycle import PlayerBinding
from .mvp_local_gameplay import (
    AttackObservation,
    LocalBotMode,
    LocalMvpBot,
    MvpConfiguration,
    Screen,
    VisitResult,
)

ATTACK_BTN = (0.0661, 0.8764)
FIND_MATCH_BTN = (0.2174, 0.717)
ARMY_ATTACK_BTN = (0.94, 0.8831)
DEPLOY_SLOTS = tuple((0.03183 + 0.070023 * index, 0.92) for index in range(5))
DEPLOY_KEYS = (0x52, 0x4A, 0x56)
RETURN_HOME_BTN = (0.511, 0.834)
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_V1_SLOT_REFS = frozenset(f"slot-{index}" for index in range(5))


class RuntimeSafetyError(RuntimeError):
    """The concrete local runtime could not establish an exact safe boundary."""


@dataclass(frozen=True, slots=True)
class PersistentControl:
    configuration: MvpConfiguration
    mode: LocalBotMode


@dataclass(frozen=True, slots=True)
class Recognition:
    home: bool
    army_ready: bool | None
    account_matches: bool

    def __post_init__(self) -> None:
        if (
            type(self.home) is not bool
            or (self.army_ready is not None and type(self.army_ready) is not bool)
            or type(self.account_matches) is not bool
        ):
            raise RuntimeSafetyError("recognition values must be exact")


class RecognitionPort(Protocol):
    def recognize(
        self, binding: PlayerBinding, account_ref: str
    ) -> Recognition: ...


class InputPort(Protocol):
    def click(
        self,
        binding: PlayerBinding,
        x: float,
        y: float,
        *,
        action: InputAction,
    ) -> bool: ...

    def key_down(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool: ...

    def key_up(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool: ...


class AuditPort(Protocol):
    def intent(self, transaction_ref: str) -> None: ...

    def outcome(self, transaction_ref: str, confirmed: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class HomeDiagnostic:
    """Scalar-only reduction of the existing HOME ROI, never a capture artifact.

    Counts are independently aggregated predicate components, not extra gates.
    No account/binding identity, raw color, image, OCR, or digest is retained.
    """

    width: int
    height: int
    left: int
    top: int
    right: int
    bottom: int
    roi_pixels: int
    hue_pixels: int
    saturation_pixels: int
    value_pixels: int
    orange_pixels: int

    def __post_init__(self) -> None:
        _home_payload(self)

    @property
    def reason(self) -> str:
        return _home_payload(self)["reason"]

    def to_json(self) -> str:
        return _serialize_home_payload(_home_payload(self))


def _home_payload(diagnostic: HomeDiagnostic) -> dict[str, int | str]:
    """Snapshot exact scalars before comparisons; never dispatch on the value."""
    if type(diagnostic) is not HomeDiagnostic:
        raise RuntimeSafetyError("HOME_DIAGNOSTIC_INVALID")
    names = (
        "width", "height", "left", "top", "right", "bottom", "roi_pixels",
        "hue_pixels", "saturation_pixels", "value_pixels", "orange_pixels",
    )
    values = {}
    missing = False
    try:
        for name in names:
            values[name] = object.__getattribute__(diagnostic, name)
    except AttributeError:
        missing = True
    if missing or any(type(value) is not int for value in values.values()):
        raise RuntimeSafetyError("HOME_DIAGNOSTIC_INVALID")
    width, height = values["width"], values["height"]
    left, top, right, bottom = (values[name] for name in names[2:6])
    count = values["roi_pixels"]
    hue, saturation, value, orange = (values[name] for name in names[7:])
    if (
        not (640 <= width <= 64 * 1024 * 1024 // (360 * 4))
        or not (360 <= height <= 64 * 1024 * 1024 // (640 * 4))
        or width * height * 4 > 64 * 1024 * 1024
        or (left, top, right, bottom) != (
            int(width * .035), int(height * .90),
            int(width * .085), int(height * .97),
        )
        or not (0 <= left <= right <= width and 0 <= top <= bottom <= height)
        or count != (right - left) * (bottom - top)
        or not all(0 <= component <= count for component in (hue, saturation, value, orange))
        or not (max(0, hue + saturation + value - 2 * count) <= orange <= min(hue, saturation, value))
    ):
        raise RuntimeSafetyError("HOME_DIAGNOSTIC_INVALID")
    reason = (
        "ROI_EMPTY" if count == 0 else
        "COLOR_ABSENT" if orange == 0 else
        "HOME_POSITIVE" if orange / count > 0.30 else "FRACTION_LOW"
    )
    return {"schema": 1, "reason": reason, **values}


def _serialize_home_payload(payload: dict[str, int | str]) -> str:
    result = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if type(result) is not str:
        raise RuntimeSafetyError("HOME_DIAGNOSTIC_INVALID")
    return result


def home_not_verified_message(diagnostic: HomeDiagnostic | None) -> str:
    """Format an unbound scalar value; not authoritative native evidence.

    Native emission is owned atomically by recognize(require_home=True).
    """
    try:
        payload = _home_payload(diagnostic)
        if payload["reason"] != "HOME_POSITIVE":
            return "HOME_NOT_VERIFIED " + _serialize_home_payload(payload)
    except RuntimeSafetyError:
        pass
    return "HOME_NOT_VERIFIED"


def _clear_capture_tracebacks(error: BaseException) -> None:
    """Clear unwound callback frames, including chained exceptions, in memory."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(BaseException.__traceback__.__get__(current))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(BaseExceptionGroup.exceptions.__get__(current))
        for linked in (
            BaseException.__cause__.__get__(current),
            BaseException.__context__.__get__(current),
        ):
            if linked is not None:
                pending.append(linked)


class BgraGameplayRecognizer:
    """Pure transient-frame HOME/army/return recognizer for one exact binding.

    The reviewed donor uses the orange HOME Attack control, the green My Army
    Attack control, and the green post-battle Return Home control.  This port
    adapts those positive color/region gates without retaining pixels.
    """

    _HOME_REGION = (0.035, 0.90, 0.085, 0.97)
    _ARMY_REGION = (0.89, 0.84, 0.99, 0.93)
    _RETURN_REGION = (0.40, 0.82, 0.60, 0.90)

    def __init__(
        self,
        binding: PlayerBinding,
        capture: Callable[[PlayerBinding], tuple[int, int, bytes]],
        *,
        account_verified: bool,
    ) -> None:
        if (
            type(binding) is not PlayerBinding
            or not callable(capture)
            or type(account_verified) is not bool
        ):
            raise RuntimeSafetyError("exact binding and capture seam required")
        self._binding = binding
        self._capture = capture
        self._account_verified = account_verified
        self._scout_source: bytes | None = None
        self._home_diagnostic: HomeDiagnostic | None = None

    @property
    def home_diagnostic(self) -> HomeDiagnostic | None:
        return self._home_diagnostic

    def _frame(self) -> tuple[int, int, bytes]:
        frame = None
        reason = "FRAME_CAPTURE_FAILED"
        try:
            frame = self._capture(self._binding)
            reason = "FRAME_VALIDATION_FAILED"
            if type(frame) is not tuple or len(frame) != 3:
                reason = "FRAME_SHAPE_INVALID"
            elif (
                type(frame[0]) is not int
                or type(frame[1]) is not int
                or (frame[0], frame[1]) != (self._binding.width, self._binding.height)
            ):
                reason = "FRAME_GEOMETRY_MISMATCH"
            elif type(frame[2]) is not bytes or len(frame[2]) != frame[0] * frame[1] * 4:
                reason = "FRAME_BYTES_INVALID"
            else:
                return frame
        except BaseException as error:
            _clear_capture_tracebacks(error)
        finally:
            frame = None
        # Raise outside the handler: the discarded exception is not a context.
        raise RuntimeSafetyError(reason)

    @staticmethod
    def _fraction(
        frame: tuple[int, int, bytes],
        region: tuple[float, float, float, float],
        predicate: Callable[[int, int, int], bool],
    ) -> float:
        pixels = None
        try:
            width, height, pixels = frame
            x0, y0, x1, y1 = region
            left, right = int(width * x0), int(width * x1)
            top, bottom = int(height * y0), int(height * y1)
            count = max(0, right - left) * max(0, bottom - top)
            if count <= 0:
                return 0.0
            matches = 0
            for y in range(top, bottom):
                row = y * width * 4
                for x in range(left, right):
                    offset = row + x * 4
                    if predicate(pixels[offset], pixels[offset + 1], pixels[offset + 2]):
                        matches += 1
            return matches / count
        finally:
            frame = pixels = width = height = None

    @staticmethod
    def _hsv(blue: int, green: int, red: int) -> tuple[int, int, int]:
        value = max(red, green, blue)
        minimum = min(red, green, blue)
        difference = value - minimum
        if value == 0 or difference == 0:
            saturation = 0
            hue = 0
        else:
            # Match OpenCV's 8-bit BGR-to-HSV lookup-table arithmetic.
            shift = 12
            saturation_divisor = round((255 << shift) / value)
            hue_divisor = round((180 << shift) / (6 * difference))
            saturation = (
                difference * saturation_divisor + (1 << (shift - 1))
            ) >> shift
            if value == red:
                hue_numerator = green - blue
            elif value == green:
                hue_numerator = blue - red + 2 * difference
            else:
                hue_numerator = red - green + 4 * difference
            hue = (
                hue_numerator * hue_divisor + (1 << (shift - 1))
            ) >> shift
            if hue < 0:
                hue += 180
        return hue, saturation, value

    @staticmethod
    def _orange(blue: int, green: int, red: int) -> bool:
        hue, saturation, value = BgraGameplayRecognizer._hsv(blue, green, red)
        return 5 <= hue <= 30 and saturation >= 100 and value >= 120

    @staticmethod
    def _diagnose_home(frame: tuple[int, int, bytes]) -> HomeDiagnostic:
        pixels = hsv = hue = saturation = value = None
        try:
            width, height, pixels = frame
            x0, y0, x1, y1 = BgraGameplayRecognizer._HOME_REGION
            left, right = int(width * x0), int(width * x1)
            top, bottom = int(height * y0), int(height * y1)
            count = max(0, right - left) * max(0, bottom - top)
            hues = saturations = values = oranges = 0
            for y in range(top, bottom):
                for x in range(left, right):
                    offset = (y * width + x) * 4
                    hsv = BgraGameplayRecognizer._hsv(
                        pixels[offset], pixels[offset + 1], pixels[offset + 2]
                    )
                    if type(hsv) is not tuple or len(hsv) != 3:
                        raise RuntimeSafetyError("HOME_REDUCTION_FAILED")
                    hue, saturation, value = hsv
                    if (
                        type(hue) is not int or type(saturation) is not int
                        or type(value) is not int or not 0 <= hue <= 179
                        or not 0 <= saturation <= 255 or not 0 <= value <= 255
                    ):
                        raise RuntimeSafetyError("HOME_REDUCTION_FAILED")
                    hue_ok = 5 <= hue <= 30
                    saturation_ok = saturation >= 100
                    value_ok = value >= 120
                    hues += hue_ok
                    saturations += saturation_ok
                    values += value_ok
                    oranges += hue_ok and saturation_ok and value_ok
            return HomeDiagnostic(
                width, height, left, top, right, bottom, count,
                hues, saturations, values, oranges,
            )
        finally:
            frame = pixels = width = height = hsv = hue = saturation = value = None

    @staticmethod
    def _green(blue: int, green: int, red: int) -> bool:
        return green >= 120 and green >= red * 3 // 2 and green >= blue * 3 // 2

    @staticmethod
    def _green_control(blue: int, green: int, red: int) -> bool:
        hue, saturation, value = BgraGameplayRecognizer._hsv(blue, green, red)
        return 35 <= hue <= 60 and saturation >= 50 and value >= 100

    def recognize(
        self, binding: PlayerBinding, account_ref: str, *, require_home: bool = False,
    ) -> Recognition:
        """Reduce once; native rejection is sealed before exposing any object.

        The property is observational, not an emission/provenance boundary.
        No caller callback or property read separates the local HOME decision
        from serialization of that exact local scalar payload.
        """
        self._home_diagnostic = None
        if type(require_home) is not bool:
            raise RuntimeSafetyError("HOME_REQUIREMENT_INVALID")
        if binding != self._binding:
            raise RuntimeSafetyError("capture binding changed")
        if type(account_ref) is not str or _REFERENCE.fullmatch(account_ref) is None:
            raise RuntimeSafetyError("account binding is malformed")
        frame = BgraGameplayRecognizer._frame(self)
        failed = False
        diagnostic = None
        message = None
        try:
            diagnostic = BgraGameplayRecognizer._diagnose_home(frame)
            # One scalar reduction owns both the strict joint fraction and report.
            payload = _home_payload(diagnostic)
            home = payload["reason"] == "HOME_POSITIVE"
            if require_home and not home:
                message = "HOME_NOT_VERIFIED " + _serialize_home_payload(payload)
        except BaseException as error:
            _clear_capture_tracebacks(error)
            failed = True
        finally:
            frame = None
        if failed:
            diagnostic = None
            raise RuntimeSafetyError("HOME_REDUCTION_FAILED")
        if message is not None:
            # Raise before publication/Recognition construction: no mutable
            # public object can substitute valid-but-unrelated scalar counts.
            diagnostic = None
            raise RuntimeSafetyError(message)
        self._home_diagnostic = diagnostic
        # Clash Anytime removed training/healing waits.  As in the donor, a
        # positive HOME gate is the preflight army-ready signal; the My Army
        # green control is checked again inside the executor before commitment.
        return Recognition(home, True if home else None, self._account_verified)

    def army_ready(self) -> bool | None:
        return (
            self._fraction(self._frame(), self._ARMY_REGION, self._green_control)
            > 0.25
        )

    def slot_is_grey(self, slot: tuple[float, float]) -> bool:
        """Adapt the donor's tight icon-crop saturation depletion signal."""
        if type(slot) is not tuple or slot not in DEPLOY_SLOTS:
            raise RuntimeSafetyError("deploy slot is not reviewed")
        frame = self._frame()
        try:
            width, height, pixels = frame
            x, y = slot
            left, right = int(width * (x - 0.02)), int(width * (x + 0.02))
            top, bottom = int(height * (y - 0.02)), int(height * (y + 0.02))
            count = max(0, right - left) * max(0, bottom - top)
            if count <= 0:
                raise RuntimeSafetyError("deploy slot crop is empty")
            saturation = 0
            for row in range(top, bottom):
                for column in range(left, right):
                    offset = (row * width + column) * 4
                    saturation += self._hsv(
                        pixels[offset], pixels[offset + 1], pixels[offset + 2]
                    )[1]
            return saturation / count < 80.0
        finally:
            frame = pixels = width = height = None

    def return_home_visible(self) -> bool:
        return self._fraction(self._frame(), self._RETURN_REGION, self._green) > 0.20

    def capture_scout_source(self) -> None:
        """Bind later scout evidence to the frame immediately before input."""
        self._scout_source = self._frame()[2]

    def scout_ready(self) -> bool:
        frame = self._frame()
        source = self._scout_source
        try:
            if source is None or len(source) != len(frame[2]):
                return False
            changed = sum(
                1
                for offset in range(0, len(source), 4)
                if max(
                    abs(source[offset] - frame[2][offset]),
                    abs(source[offset + 1] - frame[2][offset + 1]),
                    abs(source[offset + 2] - frame[2][offset + 2]),
                ) >= 35
            ) / (len(source) // 4)
            colorful = self._fraction(
                frame,
                (0.20, 0.15, 0.80, 0.85),
                lambda blue, green, red: (
                    max(blue, green, red) >= 80
                    and max(blue, green, red) - min(blue, green, red) >= 40
                ),
            )
            near_white = self._fraction(
                frame,
                (0.0, 0.0, 1.0, 1.0),
                lambda blue, green, red: min(blue, green, red) >= 250,
            )
            return changed >= 0.03 and colorful >= 0.05 and near_white < 0.90
        finally:
            frame = source = None


class LocalControlStore:
    """One-host/Team/account/instance control persisted beneath local ``var``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @staticmethod
    def _require_configuration(configuration: MvpConfiguration) -> None:
        if type(configuration) is not MvpConfiguration:
            raise RuntimeSafetyError("exact local MVP configuration required")
        references = (configuration.team_ref, configuration.account_ref)
        if (
            any(
                type(value) is not str or _REFERENCE.fullmatch(value) is None
                for value in references
            )
            or type(configuration.instance_ref) is not str
            or configuration.instance_ref not in _V1_SLOT_REFS
            or type(configuration.player_tag_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", configuration.player_tag_sha256) is None
        ):
            raise RuntimeSafetyError("one exact Team, account, and instance are required")

    def setup(self, configuration: MvpConfiguration) -> PersistentControl:
        if self._path.exists():
            raise RuntimeSafetyError("local MVP is already configured")
        self._require_configuration(configuration)
        state = PersistentControl(configuration, LocalBotMode.STOPPED)
        self._write(state, exclusive=True)
        return state

    def prepare_stopped(self, configuration: MvpConfiguration) -> PersistentControl:
        """Create or rebind control only while its durable mode is STOPPED.

        The caller owns the surrounding lifecycle mutex and absence/approval
        checks.  This store method only owns the stopped-only durable rewrite.
        """
        self._require_configuration(configuration)
        if not self._path.exists():
            return self.setup(configuration)
        current = self.load()
        if current.mode is not LocalBotMode.STOPPED:
            raise RuntimeSafetyError("control preparation requires STOPPED mode")
        if current.configuration == configuration:
            return current
        expected = PersistentControl(configuration, LocalBotMode.STOPPED)
        self._write(expected, exclusive=False)
        persisted = self.load()
        if persisted != expected:
            raise RuntimeSafetyError("control preparation read-back mismatch")
        return persisted

    def load(self) -> PersistentControl:
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeSafetyError("local control state is unavailable") from exc
        if type(data) is not dict or set(data) != {"schema", "mode", "configuration"}:
            raise RuntimeSafetyError("local control state is malformed")
        config = data.get("configuration")
        if type(config) is not dict or set(config) != {
            "team_ref",
            "account_ref",
            "instance_ref",
            "player_tag_sha256",
        }:
            raise RuntimeSafetyError("local control configuration is malformed")
        try:
            if type(data["mode"]) is not str:
                raise TypeError
            configuration = MvpConfiguration(**config)
            mode = LocalBotMode(data["mode"])
        except (TypeError, ValueError) as exc:
            raise RuntimeSafetyError("local control state is malformed") from exc
        references = (configuration.team_ref, configuration.account_ref)
        if (
            type(data["schema"]) is not int
            or data["schema"] != 2
            or any(
                type(value) is not str or _REFERENCE.fullmatch(value) is None
                for value in references
            )
            or type(configuration.instance_ref) is not str
            or configuration.instance_ref not in _V1_SLOT_REFS
            or type(configuration.player_tag_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", configuration.player_tag_sha256) is None
        ):
            raise RuntimeSafetyError("local control state is malformed")
        if mode is LocalBotMode.UNCONFIGURED:
            raise RuntimeSafetyError("persisted control state cannot be unconfigured")
        state = PersistentControl(configuration, mode)
        if encode_control_state(state) != raw.encode("utf-8"):
            raise RuntimeSafetyError("local control state is malformed")
        return state

    def transition(self, target: LocalBotMode) -> PersistentControl:
        if type(target) is not LocalBotMode or target is LocalBotMode.UNCONFIGURED:
            raise RuntimeSafetyError("invalid persistent control target")
        current = self.load()
        allowed = {
            LocalBotMode.STOPPED: {LocalBotMode.RUNNING},
            LocalBotMode.RUNNING: {LocalBotMode.PAUSED, LocalBotMode.STOPPED},
            LocalBotMode.PAUSED: {LocalBotMode.RUNNING, LocalBotMode.STOPPED},
        }
        if target not in allowed[current.mode]:
            raise RuntimeSafetyError("invalid persistent control transition")
        updated = PersistentControl(current.configuration, target)
        self._write(updated, exclusive=False)
        return updated

    def _write(self, state: PersistentControl, *, exclusive: bool) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = encode_control_state(state).decode("ascii")
        if exclusive:
            try:
                with self._path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise RuntimeSafetyError("local MVP is already configured") from exc
            return
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def encode_control_state(state: PersistentControl) -> bytes:
    if type(state) is not PersistentControl:
        raise RuntimeSafetyError("local control state is malformed")
    return (
        json.dumps(
            {
                "schema": 2,
                "mode": state.mode.value,
                "configuration": asdict(state.configuration),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


class BoundedAttackExecutor:
    """Attack-only donor gesture seam with a fresh gate before every input."""

    def __init__(
        self,
        binding: PlayerBinding,
        input_port: InputPort,
        *,
        army_ready: Callable[[], bool | None],
        begin_scout_transition: Callable[[], None] = lambda: None,
        scout_ready: Callable[[], bool] = lambda: True,
        slot_is_grey: Callable[[tuple[float, float]], bool] | None = None,
        return_home_visible: Callable[[], bool],
        kill_switch_enabled: Callable[[], bool],
        monotonic: Callable[[], float] | None = None,
        deployment_window_seconds: float = 60.0,
        sleep: Callable[[float], None],
    ) -> None:
        if type(binding) is not PlayerBinding:
            raise RuntimeSafetyError("exact player binding required")
        self._binding = binding
        self._input = input_port
        self._army_ready = army_ready
        self._begin_scout_transition = begin_scout_transition
        self._scout_ready = scout_ready
        self._slot_is_grey = slot_is_grey
        self._return_home_visible = return_home_visible
        self._kill_switch = kill_switch_enabled
        self._monotonic = monotonic
        self._deployment_window_seconds = deployment_window_seconds
        self._sleep = sleep

    def _authorized(self) -> bool:
        try:
            return self._kill_switch() is True
        except BaseException:
            return False

    def _click(
        self, point: tuple[float, float], action: InputAction
    ) -> tuple[bool, str]:
        if not self._authorized():
            return False, "KILL_SWITCH"
        try:
            sent = self._input.click(
                self._binding, point[0], point[1], action=action
            )
        except BaseException:
            return False, "INPUT_UNAVAILABLE"
        return (True, "INPUT_SENT") if sent is True else (False, "INPUT_FAILED")

    def _deploy_slot(
        self,
        slot: tuple[float, float],
        *,
        window_deadline: float | None = None,
    ) -> tuple[bool, str]:
        sent, reason = self._click(slot, InputAction.TROOP_DEPLOYMENT)
        if not sent:
            return False, reason if reason == "KILL_SWITCH" else "DEPLOY_SELECT_FAILED"
        if not self._authorized():
            return False, "KILL_SWITCH"
        released = False
        try:
            down = self._input.key_down(
                self._binding,
                *DEPLOY_KEYS,
                action=InputAction.TROOP_DEPLOYMENT,
            ) is True
            if not down:
                return False, "DEPLOY_KEY_DOWN_FAILED"
            if self._slot_is_grey is None:
                self._sleep(0.5)
            else:
                hold_deadline = None
                if self._monotonic is not None:
                    hold_deadline = self._monotonic() + 25.0
                    if window_deadline is not None:
                        hold_deadline = min(hold_deadline, window_deadline)
                for _ in range(50):
                    if hold_deadline is None:
                        delay = 0.5
                    else:
                        remaining = hold_deadline - self._monotonic()
                        if remaining <= 0:
                            break
                        delay = min(0.5, remaining)
                    self._sleep(delay)
                    if not self._authorized():
                        return False, "KILL_SWITCH"
                    if (
                        hold_deadline is not None
                        and self._monotonic() >= hold_deadline
                    ):
                        break
                    try:
                        grey = self._slot_is_grey(slot)
                    except BaseException:
                        return False, "DEPLOY_STATE_UNKNOWN"
                    if type(grey) is not bool:
                        return False, "DEPLOY_STATE_UNKNOWN"
                    if grey:
                        break
        except BaseException:
            return False, "DEPLOY_KEY_DOWN_FAILED"
        finally:
            try:
                released = self._input.key_up(
                    self._binding,
                    *DEPLOY_KEYS,
                    action=InputAction.CLEANUP_RELEASE,
                ) is True
            except BaseException:
                released = False
        if not released:
            return False, "DEPLOY_KEY_UP_FAILED"
        return True, "DEPLOYED_SLOT"

    def _deploy_all_slots(self) -> tuple[bool, str]:
        if self._slot_is_grey is None or self._monotonic is None:
            for slot in DEPLOY_SLOTS:
                deployed, reason = self._deploy_slot(slot)
                if not deployed:
                    return False, reason
            return True, "DEPLOYED_ALL_SLOTS"

        if (
            type(self._deployment_window_seconds) not in (int, float)
            or type(self._deployment_window_seconds) is bool
            or not 0 < float(self._deployment_window_seconds) <= 60.0
        ):
            return False, "DEPLOY_POLICY_INVALID"
        deadline = self._monotonic() + float(self._deployment_window_seconds)
        first_pass = True
        while first_pass or self._monotonic() < deadline:
            for slot in DEPLOY_SLOTS:
                if not first_pass:
                    if self._monotonic() >= deadline:
                        break
                    try:
                        grey = self._slot_is_grey(slot)
                    except BaseException:
                        return False, "DEPLOY_STATE_UNKNOWN"
                    if type(grey) is not bool:
                        return False, "DEPLOY_STATE_UNKNOWN"
                    if grey:
                        continue
                    if self._monotonic() >= deadline:
                        break
                deployed, reason = self._deploy_slot(
                    slot,
                    window_deadline=None if first_pass else deadline,
                )
                if not deployed:
                    return False, reason
            first_pass = False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(2.0, remaining))
        return True, "DEPLOYED_ALL_SLOTS"

    def run(self) -> tuple[bool, str]:
        for point, action, failure in (
            (ATTACK_BTN, InputAction.ATTACK_NAVIGATION, "ATTACK_CLICK_FAILED"),
            (FIND_MATCH_BTN, InputAction.ATTACK_NAVIGATION, "FIND_MATCH_CLICK_FAILED"),
        ):
            sent, reason = self._click(point, action)
            if not sent:
                return False, reason if reason == "KILL_SWITCH" else failure
            self._sleep(0.01)
        try:
            army = self._army_ready()
        except BaseException:
            army = None
        if type(army) is not bool:
            return False, "MY_ARMY_READINESS_UNKNOWN"
        if not army:
            return False, "MY_ARMY_NOT_READY"
        try:
            source_result = self._begin_scout_transition()
        except BaseException:
            return False, "SCOUT_SOURCE_UNAVAILABLE"
        if source_result is not None:
            return False, "SCOUT_SOURCE_UNAVAILABLE"
        sent, reason = self._click(
            ARMY_ATTACK_BTN, InputAction.ATTACK_NAVIGATION
        )
        if not sent:
            return False, reason if reason == "KILL_SWITCH" else "ARMY_ATTACK_CLICK_FAILED"
        scout_ready = False
        for _ in range(60):
            try:
                scout_ready = self._scout_ready() is True
            except BaseException:
                scout_ready = False
            if scout_ready:
                break
            self._sleep(0.5)
        if not scout_ready:
            return False, "BASE_LOAD_TIMEOUT"

        deployed, reason = self._deploy_all_slots()
        if not deployed:
            return False, reason
        home_control = False
        for _ in range(120):
            try:
                home_control = self._return_home_visible() is True
            except BaseException:
                home_control = False
            if home_control:
                break
            self._sleep(0.5)
        if home_control is not True:
            return False, "RETURN_HOME_NOT_FOUND"
        sent, reason = self._click(RETURN_HOME_BTN, InputAction.RETURN_HOME)
        if not sent:
            return False, reason if reason == "KILL_SWITCH" else "RETURN_HOME_CLICK_FAILED"
        self._sleep(2.5)
        return True, "ATTACK_COMPLETED"


class ConcreteAttackVisitPorts:
    """Concrete transaction ports bound to one immutable lifecycle binding."""

    def __init__(
        self,
        *,
        binding: PlayerBinding,
        account_ref: str,
        recognizer: RecognitionPort,
        executor: BoundedAttackExecutor,
        kill_switch_enabled: Callable[[], bool],
        audit: AuditPort,
        cleanup: Callable[[], None],
    ) -> None:
        if (
            type(binding) is not PlayerBinding
            or type(account_ref) is not str
            or _REFERENCE.fullmatch(account_ref) is None
        ):
            raise RuntimeSafetyError("exact binding and account reference required")
        self._binding = binding
        self._account_ref = account_ref
        self._recognizer = recognizer
        self._executor = executor
        self._kill_switch = kill_switch_enabled
        self._audit = audit
        self._cleanup = cleanup

    def observe(self) -> AttackObservation:
        result = self._recognizer.recognize(self._binding, self._account_ref)
        if type(result) is not Recognition:
            raise RuntimeSafetyError("recognition result is malformed")
        return AttackObservation(
            screen=Screen.HOME if result.home else Screen.UNKNOWN,
            account_ref=self._account_ref if result.account_matches else None,
            window_bound=True,
            army_ready=result.army_ready,
        )

    def kill_switch_enabled(self) -> bool:
        return self._kill_switch()

    def record_intent(self, account_ref: str, transaction_ref: str) -> None:
        if account_ref != self._account_ref:
            raise RuntimeSafetyError("intent account binding mismatch")
        self._audit.intent(transaction_ref)

    def execute_attack(self) -> tuple[bool, str]:
        return self._executor.run()

    def cleanup(self) -> None:
        self._cleanup()

    def record_outcome(
        self, account_ref: str, transaction_ref: str, confirmed: bool
    ) -> None:
        if account_ref != self._account_ref or type(confirmed) is not bool:
            raise RuntimeSafetyError("outcome account binding mismatch")
        self._audit.outcome(transaction_ref, confirmed)


class LocalMvpComposition:
    """Rehydrate the transaction adapter from persistent control state."""

    def __init__(self, store: LocalControlStore, ports: ConcreteAttackVisitPorts) -> None:
        self._store = store
        self._ports = ports

    def visit_once(self, transaction_ref: str) -> VisitResult:
        state = self._store.load()
        if state.mode is not LocalBotMode.RUNNING:
            raise RuntimeSafetyError("bounded visit requires persistent RUNNING mode")
        bot = LocalMvpBot(self._ports)
        bot.setup(state.configuration)
        bot.run()
        return bot.visit_once(transaction_ref)


__all__ = [
    "ARMY_ATTACK_BTN",
    "ATTACK_BTN",
    "DEPLOY_KEYS",
    "DEPLOY_SLOTS",
    "FIND_MATCH_BTN",
    "RETURN_HOME_BTN",
    "BoundedAttackExecutor",
    "BgraGameplayRecognizer",
    "ConcreteAttackVisitPorts",
    "LocalControlStore",
    "LocalMvpComposition",
    "PersistentControl",
    "Recognition",
    "RuntimeSafetyError",
    "encode_control_state",
]
