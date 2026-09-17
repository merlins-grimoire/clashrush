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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

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
    def click(self, binding: PlayerBinding, x: float, y: float) -> bool: ...

    def key_down(self, binding: PlayerBinding, *keys: int) -> bool: ...

    def key_up(self, binding: PlayerBinding, *keys: int) -> bool: ...


class AuditPort(Protocol):
    def intent(self, transaction_ref: str) -> None: ...

    def outcome(self, transaction_ref: str, confirmed: bool) -> None: ...


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

    def _frame(self) -> tuple[int, int, bytes]:
        width, height, pixels = self._capture(self._binding)
        if (
            type(width) is not int
            or type(height) is not int
            or (width, height) != (self._binding.width, self._binding.height)
            or type(pixels) is not bytes
            or len(pixels) != width * height * 4
        ):
            raise RuntimeSafetyError("transient PrintWindow frame is malformed")
        return width, height, pixels

    @staticmethod
    def _fraction(
        frame: tuple[int, int, bytes],
        region: tuple[float, float, float, float],
        predicate: Callable[[int, int, int], bool],
    ) -> float:
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

    @staticmethod
    def _orange(blue: int, green: int, red: int) -> bool:
        return red >= 120 and 30 <= green <= 190 and blue <= 100 and red > green

    @staticmethod
    def _green(blue: int, green: int, red: int) -> bool:
        return green >= 120 and green >= red * 3 // 2 and green >= blue * 3 // 2

    def recognize(self, binding: PlayerBinding, account_ref: str) -> Recognition:
        if binding != self._binding:
            raise RuntimeSafetyError("capture binding changed")
        if type(account_ref) is not str or _REFERENCE.fullmatch(account_ref) is None:
            raise RuntimeSafetyError("account binding is malformed")
        frame = self._frame()
        home = self._fraction(frame, self._HOME_REGION, self._orange) > 0.30
        # Clash Anytime removed training/healing waits.  As in the donor, a
        # positive HOME gate is the preflight army-ready signal; the My Army
        # green control is checked again inside the executor before commitment.
        return Recognition(home, True if home else None, self._account_verified)

    def army_ready(self) -> bool | None:
        return self._fraction(self._frame(), self._ARMY_REGION, self._green) > 0.25

    def return_home_visible(self) -> bool:
        return self._fraction(self._frame(), self._RETURN_REGION, self._green) > 0.20

    def capture_scout_source(self) -> None:
        """Bind later scout evidence to the frame immediately before input."""
        self._scout_source = self._frame()[2]

    def scout_ready(self) -> bool:
        frame = self._frame()
        source = self._scout_source
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


class LocalControlStore:
    """One-host/Team/account/instance control persisted beneath local ``var``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def setup(self, configuration: MvpConfiguration) -> PersistentControl:
        if self._path.exists():
            raise RuntimeSafetyError("local MVP is already configured")
        if type(configuration) is not MvpConfiguration:
            raise RuntimeSafetyError("exact local MVP configuration required")
        values = (
            configuration.team_ref,
            configuration.account_ref,
            configuration.instance_ref,
        )
        if (
            any(
                type(value) is not str or _REFERENCE.fullmatch(value) is None
                for value in values
            )
            or type(configuration.player_tag_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", configuration.player_tag_sha256) is None
        ):
            raise RuntimeSafetyError("one exact Team, account, and instance are required")
        state = PersistentControl(configuration, LocalBotMode.STOPPED)
        self._write(state, exclusive=True)
        return state

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
        references = (
            configuration.team_ref,
            configuration.account_ref,
            configuration.instance_ref,
        )
        if (
            type(data["schema"]) is not int
            or data["schema"] != 2
            or any(
                type(value) is not str or _REFERENCE.fullmatch(value) is None
                for value in references
            )
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
        return_home_visible: Callable[[], bool],
        kill_switch_enabled: Callable[[], bool],
        sleep: Callable[[float], None],
    ) -> None:
        if type(binding) is not PlayerBinding:
            raise RuntimeSafetyError("exact player binding required")
        self._binding = binding
        self._input = input_port
        self._army_ready = army_ready
        self._begin_scout_transition = begin_scout_transition
        self._scout_ready = scout_ready
        self._return_home_visible = return_home_visible
        self._kill_switch = kill_switch_enabled
        self._sleep = sleep

    def _authorized(self) -> bool:
        try:
            return self._kill_switch() is True
        except BaseException:
            return False

    def _click(self, point: tuple[float, float]) -> tuple[bool, str]:
        if not self._authorized():
            return False, "KILL_SWITCH"
        try:
            sent = self._input.click(self._binding, point[0], point[1])
        except BaseException:
            return False, "INPUT_UNAVAILABLE"
        return (True, "INPUT_SENT") if sent is True else (False, "INPUT_FAILED")

    def run(self) -> tuple[bool, str]:
        for point, failure in (
            (ATTACK_BTN, "ATTACK_CLICK_FAILED"),
            (FIND_MATCH_BTN, "FIND_MATCH_CLICK_FAILED"),
        ):
            sent, reason = self._click(point)
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
        sent, reason = self._click(ARMY_ATTACK_BTN)
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

        for slot in DEPLOY_SLOTS:
            sent, reason = self._click(slot)
            if not sent:
                return False, reason if reason == "KILL_SWITCH" else "DEPLOY_SELECT_FAILED"
            if not self._authorized():
                return False, "KILL_SWITCH"
            down = False
            try:
                down = self._input.key_down(self._binding, *DEPLOY_KEYS) is True
                if not down:
                    return False, "DEPLOY_KEY_DOWN_FAILED"
                self._sleep(0.5)
            except BaseException:
                return False, "DEPLOY_KEY_DOWN_FAILED"
            finally:
                try:
                    released = self._input.key_up(self._binding, *DEPLOY_KEYS) is True
                except BaseException:
                    released = False
            if not released:
                return False, "DEPLOY_KEY_UP_FAILED"
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
        sent, reason = self._click(RETURN_HOME_BTN)
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
