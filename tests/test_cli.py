from __future__ import annotations

from clash_rush_rebuild.cli import main


class FakeCycle:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def visit_once(self) -> object:
        self.events.append("visit")
        return object()

    def initialize(self) -> object:
        self.events.append("initialize")
        return object()


def test_cli_refuses_inert_visit_without_explicit_owner_approval() -> None:
    events: list[str] = []

    status = main(
        ["visit-one", "--project-root", "X", "--slots", "Y"],
        cycle_builder=lambda *_args: events.append("build"),
    )

    assert status == 2
    assert events == []


def test_cli_composes_exactly_one_visit_after_explicit_owner_approval() -> None:
    events: list[str] = []

    def build(project_root: str, slots_path: str) -> FakeCycle:
        events.append(f"build:{project_root}:{slots_path}")
        return FakeCycle(events)

    status = main(
        [
            "visit-one",
            "--project-root",
            "X",
            "--slots",
            "Y",
            "--owner-approved",
        ],
        cycle_builder=build,
    )

    assert status == 0
    assert events == ["build:X:Y", "visit"]


def test_cli_initializes_state_without_live_approval_or_visit() -> None:
    events: list[str] = []

    def build(project_root: str, slots_path: str) -> FakeCycle:
        events.append(f"build:{project_root}:{slots_path}")
        return FakeCycle(events)

    status = main(
        ["initialize", "--project-root", "X", "--slots", "Y"],
        cycle_builder=build,
    )

    assert status == 0
    assert events == ["build:X:Y", "initialize"]
