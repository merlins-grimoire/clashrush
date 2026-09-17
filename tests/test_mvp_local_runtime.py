from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

import clash_rush_rebuild.cli as cli_module
from clash_rush_rebuild.cli import main as cli_main
from clash_rush_rebuild.lifecycle import PlayerBinding, ProcessIdentity
from clash_rush_rebuild.mvp_local_gameplay import LocalBotMode, MvpConfiguration
from clash_rush_rebuild.mvp_local_runtime import (
    ARMY_ATTACK_BTN,
    ATTACK_BTN,
    BgraGameplayRecognizer,
    DEPLOY_KEYS,
    DEPLOY_SLOTS,
    FIND_MATCH_BTN,
    RETURN_HOME_BTN,
    BoundedAttackExecutor,
    ConcreteAttackVisitPorts,
    LocalControlStore,
    LocalMvpComposition,
    Recognition,
    RuntimeSafetyError,
)


TEAM = "team-synthetic-one"
ACCOUNT = "account-synthetic-one"
INSTANCE = "instance-synthetic-one"
TAG_HASH = "a" * 64
CONFIG = MvpConfiguration(TEAM, ACCOUNT, INSTANCE, TAG_HASH)
BINDING = PlayerBinding(
    ProcessIdentity(100, 200),
    ProcessIdentity(100, 200),
    10,
    11,
    1280,
    720,
    "0" * 32,
)


class FakeCapture:
    def __init__(self, recognitions: list[Recognition]) -> None:
        self.recognitions = recognitions
        self.calls = 0

    def recognize(self, binding: PlayerBinding, account_ref: str) -> Recognition:
        assert binding == BINDING
        assert account_ref == ACCOUNT
        self.calls += 1
        return self.recognitions.pop(0)


@dataclass
class FakeInput:
    events: list[object] = field(default_factory=list)

    def click(self, binding: PlayerBinding, x: float, y: float) -> bool:
        assert binding == BINDING
        self.events.append(("click", x, y))
        return True

    def key_down(self, binding: PlayerBinding, *keys: int) -> bool:
        assert binding == BINDING
        self.events.append(("down", keys))
        return True

    def key_up(self, binding: PlayerBinding, *keys: int) -> bool:
        assert binding == BINDING
        self.events.append(("up", keys))
        return True


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def intent(self, transaction_ref: str) -> None:
        self.events.append(("intent", transaction_ref))

    def outcome(self, transaction_ref: str, confirmed: bool) -> None:
        self.events.append(("outcome", transaction_ref, confirmed))


def test_control_store_persists_pause_and_stop_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    first = LocalControlStore(path)
    first.setup(CONFIG)
    first.transition(LocalBotMode.RUNNING)
    first.transition(LocalBotMode.PAUSED)

    reopened = LocalControlStore(path)
    assert reopened.load().configuration == CONFIG
    assert reopened.load().mode is LocalBotMode.PAUSED

    reopened.transition(LocalBotMode.STOPPED)
    assert LocalControlStore(path).load().mode is LocalBotMode.STOPPED


def test_control_store_rejects_second_team_account_or_instance(tmp_path: Path) -> None:
    store = LocalControlStore(tmp_path / "control.json")
    store.setup(CONFIG)

    with pytest.raises(RuntimeSafetyError, match="already configured"):
        store.setup(MvpConfiguration("team-other", ACCOUNT, INSTANCE, TAG_HASH))


@pytest.mark.parametrize(
    "key,replacement",
    [
        ("schema", True),
        ("configuration.account_ref", True),
        ("configuration.player_tag_sha256", True),
    ],
)
def test_control_store_rejects_booleans_and_malformed_types(
    tmp_path: Path, key: str, replacement: object
) -> None:
    import json

    path = tmp_path / "control.json"
    store = LocalControlStore(path)
    store.setup(CONFIG)
    value = json.loads(path.read_text(encoding="utf-8"))
    if key.startswith("configuration."):
        value["configuration"][key.split(".", 1)[1]] = replacement
    else:
        value[key] = replacement
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")

    with pytest.raises(RuntimeSafetyError, match="malformed"):
        store.load()


def test_executor_rechecks_kill_switch_immediately_before_every_input() -> None:
    input_port = FakeInput()
    checks = iter([True, True, False])
    executor = BoundedAttackExecutor(
        BINDING,
        input_port,
        army_ready=lambda: True,
        return_home_visible=lambda: True,
        kill_switch_enabled=lambda: next(checks),
        sleep=lambda _seconds: None,
    )

    assert executor.run() == (False, "KILL_SWITCH")
    assert input_port.events == [
        ("click", *ATTACK_BTN),
        ("click", *FIND_MATCH_BTN),
    ]


def test_executor_adapts_bounded_attack_deploy_and_return_home_sequence() -> None:
    input_port = FakeInput()
    executor = BoundedAttackExecutor(
        BINDING,
        input_port,
        army_ready=lambda: True,
        return_home_visible=lambda: True,
        kill_switch_enabled=lambda: True,
        sleep=lambda _seconds: None,
    )

    assert executor.run() == (True, "ATTACK_COMPLETED")
    assert input_port.events[:3] == [
        ("click", *ATTACK_BTN),
        ("click", *FIND_MATCH_BTN),
        ("click", *ARMY_ATTACK_BTN),
    ]
    selected = [event[1:] for event in input_port.events if event[0] == "click"]
    assert tuple(selected[3:-1]) == DEPLOY_SLOTS
    assert input_port.events[-1] == ("click", *RETURN_HOME_BTN)
    assert sum(event == ("down", DEPLOY_KEYS) for event in input_port.events) == 5
    assert sum(event == ("up", DEPLOY_KEYS) for event in input_port.events) == 5


def test_synthetic_cli_composition_runs_recognition_intent_attack_cleanup_postcondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    store = LocalControlStore(tmp_path / "control.json")
    store.setup(CONFIG)
    store.transition(LocalBotMode.RUNNING)
    capture = FakeCapture(
        [
            Recognition(home=True, army_ready=True, account_matches=True),
            Recognition(home=True, army_ready=None, account_matches=True),
        ]
    )
    input_port = FakeInput()
    audit = FakeAudit()
    cleanup_events: list[str] = []
    ports = ConcreteAttackVisitPorts(
        binding=BINDING,
        account_ref=ACCOUNT,
        recognizer=capture,
        executor=BoundedAttackExecutor(
            BINDING,
            input_port,
            army_ready=lambda: True,
            return_home_visible=lambda: True,
            kill_switch_enabled=lambda: True,
            sleep=lambda _seconds: None,
        ),
        kill_switch_enabled=lambda: True,
        audit=audit,
        cleanup=lambda: cleanup_events.append("cleanup"),
    )
    composition = LocalMvpComposition(store, ports)

    result_holder: list[object] = []

    def run(_root: str, _slots: str, transaction_ref: str):
        result = composition.visit_once(transaction_ref)
        result_holder.append(result)
        return result

    monkeypatch.setattr(cli_module, "run_native_mvp_visit", run)

    status = cli_main(
        [
            "mvp-visit-one",
            "--project-root", str(tmp_path),
            "--slots", "synthetic-slots",
            "--transaction-ref", "tx-synthetic-e2e",
        ],
    )
    result = result_holder[0]

    assert status == 0
    assert (result.status, result.reason_code, result.confirmed) == (
        "completed",
        "RETURNED_HOME",
        True,
    )
    assert capture.calls == 2
    assert audit.events == [
        ("intent", "tx-synthetic-e2e"),
        ("outcome", "tx-synthetic-e2e", True),
    ]
    assert cleanup_events == ["cleanup"]
    assert input_port.events
    assert "account-synthetic-one" not in capsys.readouterr().out


def test_non_running_persistent_mode_emits_no_capture_or_input(tmp_path: Path) -> None:
    store = LocalControlStore(tmp_path / "control.json")
    store.setup(CONFIG)
    capture = FakeCapture([])
    input_port = FakeInput()
    ports = ConcreteAttackVisitPorts(
        binding=BINDING,
        account_ref=ACCOUNT,
        recognizer=capture,
        executor=BoundedAttackExecutor(
            BINDING,
            input_port,
            army_ready=lambda: True,
            return_home_visible=lambda: True,
            kill_switch_enabled=lambda: True,
            sleep=lambda _seconds: None,
        ),
        kill_switch_enabled=lambda: True,
        audit=FakeAudit(),
        cleanup=lambda: None,
    )

    with pytest.raises(RuntimeSafetyError, match="RUNNING"):
        LocalMvpComposition(store, ports).visit_once("tx-not-running")
    assert capture.calls == 0
    assert input_port.events == []


def _bgra_frame(
    *, home: bool = False, army: bool = False, return_home: bool = False
) -> bytes:
    width, height = BINDING.width, BINDING.height
    pixels = bytearray(width * height * 4)

    def paint(region: tuple[float, float, float, float], color: tuple[int, int, int]) -> None:
        x0, y0, x1, y1 = region
        for y in range(int(height * y0), int(height * y1)):
            for x in range(int(width * x0), int(width * x1)):
                offset = (y * width + x) * 4
                pixels[offset : offset + 4] = bytes((*color, 255))

    if home:
        paint((0.035, 0.90, 0.085, 0.97), (20, 100, 220))
    if army:
        paint((0.89, 0.84, 0.99, 0.93), (30, 220, 30))
    if return_home:
        paint((0.40, 0.82, 0.60, 0.90), (30, 220, 30))
    return bytes(pixels)


def test_bgra_recognizer_adapts_donor_home_army_and_return_home_regions() -> None:
    frames = iter(
        [
            _bgra_frame(home=True),
            _bgra_frame(army=True),
            _bgra_frame(return_home=True),
        ]
    )
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, next(frames)),
        account_verified=True,
    )

    assert recognizer.recognize(BINDING, ACCOUNT) == Recognition(True, True, True)
    assert recognizer.army_ready() is True
    assert recognizer.return_home_visible() is True


def test_bgra_recognizer_rejects_changed_binding_without_capture() -> None:
    calls: list[str] = []
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda _binding: calls.append("capture") or (1280, 720, b""),
        account_verified=True,
    )
    changed = PlayerBinding(
        BINDING.identity,
        BINDING.render_identity,
        BINDING.root_hwnd,
        BINDING.render_hwnd,
        BINDING.width,
        BINDING.height,
        "1" * 32,
    )

    with pytest.raises(RuntimeSafetyError, match="binding"):
        recognizer.recognize(changed, ACCOUNT)
    assert calls == []


def test_scout_requires_fresh_material_transition_and_target_signature() -> None:
    colorful = bytes((30, 180, 40, 255)) * (BINDING.width * BINDING.height)
    changed = bytearray(colorful)
    for y in range(120, 600):
        for x in range(250, 1030):
            offset = (y * BINDING.width + x) * 4
            changed[offset : offset + 4] = bytes((25, 80, 190, 255))
    frames = iter([colorful, colorful, bytes(changed)])
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, next(frames)),
        account_verified=True,
    )

    recognizer.capture_scout_source()
    assert recognizer.scout_ready() is False
    assert recognizer.scout_ready() is True
