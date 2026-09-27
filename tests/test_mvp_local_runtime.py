from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import clash_rush_rebuild.cli as cli_module
from clash_rush_rebuild.cli import main as cli_main
from clash_rush_rebuild.input_authorization import InputAction
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
INSTANCE = "slot-0"
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

    def click(
        self,
        binding: PlayerBinding,
        x: float,
        y: float,
        *,
        action: InputAction,
    ) -> bool:
        assert binding == BINDING
        self.events.append(("click", x, y, action))
        return True

    def key_down(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool:
        assert binding == BINDING
        self.events.append(("down", keys, action))
        return True

    def key_up(
        self, binding: PlayerBinding, *keys: int, action: InputAction
    ) -> bool:
        assert binding == BINDING
        self.events.append(("up", keys, action))
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


def test_control_store_rebinds_only_an_exact_stopped_configuration(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    store = LocalControlStore(path)
    store.setup(CONFIG)
    replacement = MvpConfiguration(TEAM, "account-synthetic-two", "slot-1", "b" * 64)

    assert store.prepare_stopped(replacement).configuration == replacement
    assert store.load().configuration == replacement
    assert store.load().mode is LocalBotMode.STOPPED


@pytest.mark.parametrize("mode", [LocalBotMode.RUNNING, LocalBotMode.PAUSED])
def test_control_store_never_rebinds_an_active_configuration(
    tmp_path: Path, mode: LocalBotMode
) -> None:
    path = tmp_path / "control.json"
    store = LocalControlStore(path)
    store.setup(CONFIG)
    store.transition(LocalBotMode.RUNNING)
    if mode is LocalBotMode.PAUSED:
        store.transition(LocalBotMode.PAUSED)
    before = path.read_bytes()

    with pytest.raises(RuntimeSafetyError, match="STOPPED"):
        store.prepare_stopped(
            MvpConfiguration(TEAM, "account-synthetic-two", "slot-1", "b" * 64)
        )

    assert path.read_bytes() == before


def test_control_store_never_rebinds_a_malformed_configuration(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    store = LocalControlStore(path)
    store.setup(CONFIG)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["configuration"]["instance_ref"] = "slot-01"
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")
    before = path.read_bytes()

    with pytest.raises(RuntimeSafetyError, match="malformed"):
        store.prepare_stopped(
            MvpConfiguration(TEAM, "account-synthetic-two", "slot-1", "b" * 64)
        )

    assert path.read_bytes() == before


@pytest.mark.parametrize("index", range(5))
def test_control_store_accepts_each_canonical_v1_slot_reference(
    tmp_path: Path, index: int
) -> None:
    store = LocalControlStore(tmp_path / f"control-{index}.json")
    configuration = MvpConfiguration(TEAM, ACCOUNT, f"slot-{index}", TAG_HASH)

    assert store.setup(configuration).configuration == configuration
    assert store.load().configuration == configuration


@pytest.mark.parametrize(
    "instance_ref",
    [
        "SyntheticDisplayName",
        "slot",
        "slot-",
        "slot-00",
        "slot-01",
        "slot-5",
        "Slot-0",
    ],
)
def test_control_store_setup_rejects_noncanonical_v1_slot_references(
    tmp_path: Path, instance_ref: str
) -> None:
    store = LocalControlStore(tmp_path / "control.json")

    with pytest.raises(RuntimeSafetyError, match="instance"):
        store.setup(MvpConfiguration(TEAM, ACCOUNT, instance_ref, TAG_HASH))

    assert not (tmp_path / "control.json").exists()


@pytest.mark.parametrize(
    "instance_ref", ["SyntheticDisplayName", "slot-00", "slot-5"]
)
def test_control_store_load_rejects_noncanonical_v1_slot_references(
    tmp_path: Path, instance_ref: str
) -> None:
    import json

    path = tmp_path / "control.json"
    store = LocalControlStore(path)
    store.setup(CONFIG)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["configuration"]["instance_ref"] = instance_ref
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")

    with pytest.raises(RuntimeSafetyError, match="malformed"):
        store.load()


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
    *,
    home: bool = False,
    army: bool = False,
    return_home: bool = False,
    home_color: tuple[int, int, int] = (20, 100, 220),
    army_color: tuple[int, int, int] = (30, 220, 30),
    slot_index: int | None = None,
    slot_color: tuple[int, int, int] = (30, 220, 30),
    home_pixels: int | None = None,
) -> bytes:
    width, height = BINDING.width, BINDING.height
    pixels = bytearray(width * height * 4)

    def paint(
        region: tuple[float, float, float, float],
        color: tuple[int, int, int],
        limit: int | None = None,
    ) -> None:
        x0, y0, x1, y1 = region
        painted = 0
        for y in range(int(height * y0), int(height * y1)):
            for x in range(int(width * x0), int(width * x1)):
                if limit is not None and painted >= limit:
                    return
                offset = (y * width + x) * 4
                pixels[offset : offset + 4] = bytes((*color, 255))
                painted += 1

    if home:
        paint((0.035, 0.90, 0.085, 0.97), home_color, home_pixels)
    if army:
        paint((0.89, 0.84, 0.99, 0.93), army_color)
    if return_home:
        paint((0.40, 0.82, 0.60, 0.90), (30, 220, 30))
    if slot_index is not None:
        x, y = DEPLOY_SLOTS[slot_index]
        paint((x - 0.02, y - 0.02, x + 0.02, y + 0.02), slot_color)
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


def test_bgra_recognizer_detects_depleted_slot_by_tight_saturation_crop() -> None:
    frames = iter(
        [
            _bgra_frame(slot_index=1, slot_color=(30, 220, 30)),
            _bgra_frame(slot_index=1, slot_color=(120, 120, 120)),
        ]
    )
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, next(frames)),
        account_verified=True,
    )

    assert recognizer.slot_is_grey(DEPLOY_SLOTS[1]) is False
    assert recognizer.slot_is_grey(DEPLOY_SLOTS[1]) is True


def test_army_ready_accepts_current_pale_green_attack_control() -> None:
    frame = _bgra_frame(army=True, army_color=(123, 216, 173))
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, frame),
        account_verified=True,
    )

    assert recognizer.army_ready() is True


@pytest.mark.parametrize(
    ("bgra", "expected"),
    [
        ((0, 32, 200), True),  # donor OpenCV HSV: H=5, S=255, V=200
        ((0, 200, 199), True),  # donor OpenCV HSV: H=30, S=255, V=200
        ((123, 160, 202), True),  # donor OpenCV HSV: H=14, S=100, V=202
        ((0, 20, 120), True),  # donor OpenCV HSV: H=5, S=255, V=120
        ((0, 16, 120), False),  # donor OpenCV HSV: H=4 (below hue floor)
        ((0, 200, 190), False),  # donor OpenCV HSV: H=31 (above hue ceiling)
        ((122, 160, 200), False),  # donor OpenCV HSV: S=99
        ((0, 20, 119), False),  # donor OpenCV HSV: V=119
    ],
)
def test_bgra_home_recognition_preserves_donor_hsv_boundary(
    bgra: tuple[int, int, int], expected: bool
) -> None:
    frame = _bgra_frame(home=True, home_color=bgra)
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, frame),
        account_verified=False,
    )

    assert recognizer.recognize(BINDING, ACCOUNT).home is expected


@pytest.mark.parametrize(
    ("home_pixels", "expected"),
    [(960, False), (961, True)],
)
def test_bgra_home_recognition_preserves_donor_roi_fraction_boundary(
    home_pixels: int, expected: bool
) -> None:
    frame = _bgra_frame(home=True, home_pixels=home_pixels)
    recognizer = BgraGameplayRecognizer(
        BINDING,
        lambda binding: (binding.width, binding.height, frame),
        account_verified=False,
    )

    assert recognizer.recognize(BINDING, ACCOUNT).home is expected


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


def test_legacy_fixed_slot_executor_is_retired_without_input():
    input_port = FakeInput()
    executor = BoundedAttackExecutor(
        BINDING, input_port, army_ready=lambda: True,
        return_home_visible=lambda: True, kill_switch_enabled=lambda: True,
        sleep=lambda _: None,
    )
    with pytest.raises(RuntimeSafetyError, match="LEGACY_DEPLOYMENT_RETIRED"):
        executor.run()
    assert input_port.events == []
