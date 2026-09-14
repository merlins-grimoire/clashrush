from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pytest

import clash_rush_rebuild.application_service as application_service_module

from clash_rush_rebuild.application_service import (
    Actor,
    ApplicationService,
    Command,
    CommandKind,
    ControlMode,
    ControlRejected,
    DesiredState,
    DurableIntent,
    DiagnosticStatus,
    LifecycleStatus,
    LocalControlAdapter,
    Resource,
    SchedulerStatus,
    StatusBlockedReason,
    StatusOutcome,
    StatusView,
)


TEAM = "team-synthetic-a"
ACTOR = Actor("operator-synthetic-a")


@dataclass
class FakeAuthorization:
    allowed: bool = True
    calls: list[tuple[Actor, str, CommandKind]] = field(default_factory=list)

    def authorize(self, actor: Actor, team_ref: str, action: CommandKind) -> bool:
        self.calls.append((actor, team_ref, action))
        return self.allowed


@dataclass
class FakeDesiredStates:
    current: DesiredState = DesiredState(TEAM, ControlMode.STOPPED, 0)
    persisted: list[Command] = field(default_factory=list)

    def load(self, team_ref: str) -> DesiredState:
        assert team_ref == TEAM
        return self.current

    def persist(self, command: Command) -> DurableIntent:
        self.persisted.append(command)
        mode = {
            CommandKind.RUN: ControlMode.RUNNING,
            CommandKind.PAUSE: ControlMode.PAUSED,
            CommandKind.RESUME: ControlMode.RUNNING,
            CommandKind.STOP: ControlMode.STOPPED,
        }.get(command.kind, self.current.mode)
        self.current = DesiredState(command.team_ref, mode, self.current.epoch + 1)
        return DurableIntent(command.command_ref, command.team_ref, command.kind, mode, self.current.epoch, command.account_ref, command.debug_minutes)


@dataclass
class FakeScheduler:
    online: bool = True
    accepted: list[DurableIntent] = field(default_factory=list)

    def is_online(self, team_ref: str) -> bool:
        assert team_ref == TEAM
        return self.online

    def accept_desired(self, intent: DurableIntent) -> None:
        self.accepted.append(intent)

    def status(self, team_ref: str) -> SchedulerStatus:
        assert team_ref == TEAM
        return SchedulerStatus(
            queue_depth=2,
            next_due_at="2030-01-01T00:00:00Z",
            blocked_reason=None,
            current_account_ref="account-synthetic-a",
            last_outcome=StatusOutcome.IDLE,
        )


class FakeLifecycle:
    def status(self, team_ref: str) -> LifecycleStatus:
        assert team_ref == TEAM
        return LifecycleStatus(active=False, sanitized_slot_index=None)


@dataclass
class FakeSetup:
    configured: list[tuple[str, frozenset[Resource]]] = field(default_factory=list)

    def configure(self, team_ref: str, resources: frozenset[Resource]) -> str:
        self.configured.append((team_ref, resources))
        return "generation-synthetic-a"


@dataclass
class FakeResources:
    enabled: frozenset[Resource] = frozenset()

    def enabled_for(self, team_ref: str) -> frozenset[Resource]:
        assert team_ref == TEAM
        return self.enabled


@dataclass
class FakeDiagnostics:
    accepted: list[DurableIntent] = field(default_factory=list)

    def accept_desired(self, intent: DurableIntent) -> None:
        self.accepted.append(intent)

    def status(self, team_ref: str) -> DiagnosticStatus:
        assert team_ref == TEAM
        return DiagnosticStatus(active=False, remaining_seconds=0)


def make_service(*, allowed: bool = True, online: bool = True, mode: ControlMode = ControlMode.STOPPED):
    authorization = FakeAuthorization(allowed)
    desired = FakeDesiredStates(DesiredState(TEAM, mode, 3))
    scheduler = FakeScheduler(online)
    setup = FakeSetup()
    resources = FakeResources()
    diagnostics = FakeDiagnostics()
    service = ApplicationService(
        authorization,
        desired,
        scheduler,
        FakeLifecycle(),
        setup,
        resources,
        diagnostics,
    )
    return service, authorization, desired, scheduler, setup, resources, diagnostics


def command(kind: CommandKind, **kwargs: object) -> Command:
    return Command(kind=kind, command_ref=f"command-{kind.value.lower()}-synthetic", team_ref=TEAM, **kwargs)


def without_field(result: object, field_name: str) -> object:
    object.__delattr__(result, field_name)
    return result


def test_adapter_cannot_bypass_denied_authorization() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service(allowed=False)
    adapter = LocalControlAdapter(service)

    with pytest.raises(ControlRejected, match="not authorized"):
        adapter.submit(ACTOR, command(CommandKind.RUN))

    assert authorization.calls == [(ACTOR, TEAM, CommandKind.RUN)]
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_adapter_supported_public_api_is_submit_only() -> None:
    service, _, _, _, _, _, _ = make_service()
    adapter = LocalControlAdapter(service)

    assert {name for name in dir(adapter) if not name.startswith("_")} == {"submit"}


def test_adapter_submit_returns_a_contract_value_not_authority() -> None:
    service, authorization, desired, scheduler, setup, resources, diagnostics = (
        make_service()
    )

    result = LocalControlAdapter(service).submit(ACTOR, command(CommandKind.STATUS))

    assert type(result) is StatusView
    assert all(
        result is not authority
        for authority in (
            service,
            authorization,
            desired,
            scheduler,
            setup,
            resources,
            diagnostics,
        )
    )


def test_module_does_not_keep_a_live_service_registry() -> None:
    assert "_SEALED_SERVICES" not in vars(application_service_module)


@pytest.mark.parametrize("malformed_kind", ["none", "object", "function", "bound"])
def test_adapter_malformed_dispatch_state_fails_closed(malformed_kind: str) -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    adapter = LocalControlAdapter(service)
    malformed = {
        "none": None,
        "object": object(),
        "function": lambda actor, requested: None,
        "bound": service.execute,
    }[malformed_kind]
    object.__setattr__(adapter, "_LocalControlAdapter__dispatch", malformed)

    with pytest.raises(ControlRejected) as caught:
        adapter.submit(ACTOR, command(CommandKind.RUN))

    assert str(caught.value) == "adapter dispatch is unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_adapter_missing_dispatch_state_fails_closed() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    adapter = LocalControlAdapter(service)
    object.__delattr__(adapter, "_LocalControlAdapter__dispatch")

    with pytest.raises(ControlRejected) as caught:
        adapter.submit(ACTOR, command(CommandKind.RUN))

    assert str(caught.value) == "adapter dispatch is unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_adapter_ignores_instance_shadowed_service_execute() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service(allowed=False)
    service.execute = lambda actor, requested: "authorization-bypassed"  # type: ignore[method-assign]
    adapter = LocalControlAdapter(service)

    with pytest.raises(ControlRejected, match="not authorized"):
        adapter.submit(ACTOR, command(CommandKind.RUN))

    assert authorization.calls == [(ACTOR, TEAM, CommandKind.RUN)]
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_adapter_cannot_reach_an_instance_shadowed_authorization_helper() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service(allowed=False)
    service._authorize = lambda actor_ref, requested: None  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="not authorized"):
        LocalControlAdapter(service).submit(ACTOR, command(CommandKind.RUN))

    assert authorization.calls == [(ACTOR, TEAM, CommandKind.RUN)]
    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    ("kind", "mode", "kwargs"),
    [
        (CommandKind.RUN, ControlMode.STOPPED, {}),
        (CommandKind.SETUP, ControlMode.STOPPED, {}),
        (CommandKind.STATUS, ControlMode.STOPPED, {}),
        (CommandKind.DEBUG_START, ControlMode.RUNNING, {"debug_minutes": 2}),
        (CommandKind.DEBUG_STATUS, ControlMode.STOPPED, {}),
    ],
)
def test_adapter_dispatch_ignores_every_instance_shadowed_service_helper(
    kind: CommandKind, mode: ControlMode, kwargs: dict[str, object]
) -> None:
    service, _, _, _, _, _, _ = make_service(mode=mode)

    def poisoned_helper(*args: object, **helper_kwargs: object) -> object:
        raise AssertionError("instance-shadowed helper was dispatched")

    for helper_name in (
        "_validate_request",
        "_authorize",
        "_load_desired",
        "_scheduler_online",
        "_persist",
        "_admit_scheduler",
        "_load_scheduler_status",
        "_load_lifecycle_status",
        "_load_resource_authority",
        "_configure_setup",
        "_load_diagnostic_status",
        "_admit_diagnostic",
        "_copy_intent",
    ):
        setattr(service, helper_name, poisoned_helper)

    LocalControlAdapter(service).submit(ACTOR, command(kind, **kwargs))


@pytest.mark.parametrize(
    ("mode", "kind"),
    [
        (ControlMode.STOPPED, CommandKind.PAUSE),
        (ControlMode.STOPPED, CommandKind.RESUME),
        (ControlMode.RUNNING, CommandKind.RUN),
        (ControlMode.PAUSED, CommandKind.PAUSE),
    ],
)
def test_invalid_control_transition_is_rejected_before_persistence(
    mode: ControlMode, kind: CommandKind
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service(mode=mode)

    with pytest.raises(ControlRejected, match="transition"):
        service.execute(ACTOR, command(kind))

    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    ("mode", "kind"),
    [
        (ControlMode.STOPPED, CommandKind.RUN),
        (ControlMode.PAUSED, CommandKind.RESUME),
    ],
)
def test_offline_run_and_resume_are_not_queued(
    mode: ControlMode, kind: CommandKind
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service(online=False, mode=mode)

    with pytest.raises(ControlRejected, match="offline"):
        service.execute(ACTOR, command(kind))

    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    ("mode", "kind", "online"),
    [
        (ControlMode.STOPPED, CommandKind.RUN, True),
        (ControlMode.RUNNING, CommandKind.PAUSE, False),
        (ControlMode.PAUSED, CommandKind.RESUME, True),
        (ControlMode.RUNNING, CommandKind.STOP, False),
    ],
)
def test_control_hands_only_the_durably_persisted_intent_to_scheduler(
    mode: ControlMode, kind: CommandKind, online: bool
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service(online=online, mode=mode)
    requested = command(kind)

    result = service.execute(ACTOR, requested)

    assert desired.persisted == [requested]
    assert scheduler.accepted == [result]
    assert result.epoch == 4


@pytest.mark.parametrize("persisted_epoch", [1, 9, 11, 2**63 - 1])
def test_persisted_intent_requires_the_exact_next_loaded_epoch(
    persisted_epoch: int,
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.current = DesiredState(TEAM, ControlMode.STOPPED, 9)
    desired.persist = lambda requested: DurableIntent(  # type: ignore[method-assign]
        requested.command_ref,
        requested.team_ref,
        requested.kind,
        ControlMode.RUNNING,
        persisted_epoch,
    )

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert scheduler.accepted == []


def test_maximum_loaded_epoch_rejects_before_persistence_or_admission() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.current = DesiredState(TEAM, ControlMode.STOPPED, 2**63 - 1)

    with pytest.raises(ControlRejected, match="epoch"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert desired.persisted == []
    assert scheduler.accepted == []


def test_persist_callback_cannot_rebind_validation_to_a_mutated_epoch() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.current = DesiredState(TEAM, ControlMode.STOPPED, 9)

    def mutate_epoch_and_forge(requested: Command) -> DurableIntent:
        desired.current = DesiredState(TEAM, ControlMode.STOPPED, 99)
        return DurableIntent(
            requested.command_ref,
            requested.team_ref,
            requested.kind,
            ControlMode.RUNNING,
            100,
        )

    desired.persist = mutate_epoch_and_forge  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert scheduler.accepted == []


@pytest.mark.parametrize("kind", [CommandKind.QUARANTINE, CommandKind.UNQUARANTINE])
def test_account_controls_require_an_account_and_use_durable_admission_handoff(
    kind: CommandKind,
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    requested = command(kind, account_ref="account-synthetic-a")

    result = service.execute(ACTOR, requested)

    assert desired.persisted == [requested]
    assert scheduler.accepted == [result]
    assert result.account_ref == "account-synthetic-a"

    with pytest.raises(ControlRejected, match="account"):
        service.execute(ACTOR, command(kind))


def test_setup_resource_authority_is_default_off() -> None:
    service, _, desired, scheduler, setup, _, _ = make_service()

    result = service.execute(ACTOR, command(CommandKind.SETUP))

    assert result == "generation-synthetic-a"
    assert setup.configured == [(TEAM, frozenset())]
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_status_is_a_fixed_redacted_view() -> None:
    service, _, _, scheduler, _, resources, _ = make_service(mode=ControlMode.PAUSED)
    scheduler.unlisted_field = "sensitive-sentinel"
    resources.enabled = frozenset({Resource.HOME_GOLD})

    result = service.execute(ACTOR, command(CommandKind.STATUS))

    assert type(result) is StatusView
    assert result.mode is ControlMode.PAUSED
    assert result.current_account_ref == "account-synthetic-a"
    assert result.resource_authority == tuple(
        (resource, resource is Resource.HOME_GOLD) for resource in Resource
    )
    rendered = repr(asdict(result))
    assert "sensitive-sentinel" not in rendered


@pytest.mark.parametrize("minutes", [2, 5])
def test_debug_is_bounded_and_cannot_start_or_resume_automation(minutes: int) -> None:
    service, _, desired, scheduler, _, _, diagnostics = make_service(
        mode=ControlMode.RUNNING
    )
    requested = command(CommandKind.DEBUG_START, debug_minutes=minutes)

    result = service.execute(ACTOR, requested)

    assert desired.persisted == [requested]
    assert diagnostics.accepted == [result]
    assert scheduler.accepted == []
    assert result.mode is ControlMode.RUNNING

    stopped, _, stopped_desired, _, _, _, stopped_diagnostics = make_service()
    with pytest.raises(ControlRejected, match="running"):
        stopped.execute(ACTOR, requested)
    assert stopped_desired.persisted == []
    assert stopped_diagnostics.accepted == []


@pytest.mark.parametrize("minutes", [None, 1, 6, True])
def test_debug_rejects_an_unbounded_duration(minutes: object) -> None:
    service, _, desired, _, _, _, diagnostics = make_service(
        mode=ControlMode.RUNNING
    )

    with pytest.raises(ControlRejected, match="duration"):
        service.execute(ACTOR, command(CommandKind.DEBUG_START, debug_minutes=minutes))

    assert desired.persisted == []
    assert diagnostics.accepted == []


def test_adapter_cannot_bypass_closed_command_types() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    malformed = Command(
        kind="RUN",  # type: ignore[arg-type]
        command_ref="command-malformed-synthetic",
        team_ref=TEAM,
    )

    with pytest.raises(ControlRejected, match="exact command"):
        LocalControlAdapter(service).submit(ACTOR, malformed)

    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_debug_status_is_read_only() -> None:
    service, _, desired, scheduler, _, _, diagnostics = make_service()

    result = service.execute(ACTOR, command(CommandKind.DEBUG_STATUS))

    assert result == DiagnosticStatus(active=False, remaining_seconds=0)
    assert desired.persisted == []
    assert scheduler.accepted == []
    assert diagnostics.accepted == []


class StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "actor_ref",
    ["", "   ", True, StringSubclass("operator-synthetic-a"), "operator/unsafe"],
)
def test_malformed_actor_reference_is_rejected_before_authorization(
    actor_ref: object,
) -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()

    with pytest.raises(ControlRejected, match="actor reference"):
        service.execute(Actor(actor_ref), command(CommandKind.RUN))  # type: ignore[arg-type]

    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_structurally_corrupted_actor_is_sanitized_before_authorization() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    malformed = without_field(Actor("operator-synthetic-a"), "actor_ref")

    with pytest.raises(ControlRejected) as caught:
        service.execute(malformed, command(CommandKind.RUN))  # type: ignore[arg-type]

    assert str(caught.value) == "control request is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    "field_name",
    ["kind", "command_ref", "team_ref", "account_ref", "debug_minutes", "resources"],
)
def test_every_structurally_corrupted_command_field_is_sanitized_before_callbacks(
    field_name: str,
) -> None:
    service, authorization, desired, scheduler, _, _, diagnostics = make_service()
    malformed = without_field(command(CommandKind.RUN), field_name)

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, malformed)  # type: ignore[arg-type]

    assert str(caught.value) == "control request is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []
    assert diagnostics.accepted == []


@pytest.mark.parametrize(
    "account_ref",
    [None, "", "   ", True, StringSubclass("account-synthetic-a"), "account/unsafe"],
)
def test_malformed_required_account_reference_is_rejected_before_authorization(
    account_ref: object,
) -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()

    with pytest.raises(ControlRejected, match="account reference"):
        service.execute(
            ACTOR,
            command(CommandKind.QUARANTINE, account_ref=account_ref),  # type: ignore[arg-type]
        )

    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize("online", [1, "online", object()])
def test_run_rejects_malformed_scheduler_online_result(online: object) -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    scheduler.online = online  # type: ignore[assignment]

    with pytest.raises(ControlRejected, match="online status"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert desired.persisted == []
    assert scheduler.accepted == []


class DesiredStateSubclass(DesiredState):
    pass


@pytest.mark.parametrize(
    "current",
    [
        object(),
        DesiredStateSubclass(TEAM, ControlMode.STOPPED, 3),
        DesiredState(StringSubclass(TEAM), ControlMode.STOPPED, 3),
        DesiredState("other-team", ControlMode.STOPPED, 3),
        DesiredState(TEAM, "STOPPED", 3),
        DesiredState(TEAM, ControlMode.STOPPED, True),
        DesiredState(TEAM, ControlMode.STOPPED, -1),
        DesiredState(TEAM, ControlMode.STOPPED, 2**63),
    ],
)
def test_malformed_desired_state_is_rejected_before_scheduler(
    current: object,
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.current = current  # type: ignore[assignment]

    with pytest.raises(ControlRejected, match="desired state"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert desired.persisted == []
    assert scheduler.accepted == []


def test_structurally_corrupted_desired_state_fails_closed() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.current = without_field(desired.current, "team_ref")  # type: ignore[assignment]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.RUN))

    assert str(caught.value) == "desired state is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert desired.persisted == []
    assert scheduler.accepted == []


def test_desired_state_exception_is_rejected_before_scheduler() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()

    def fail_load(team_ref: str) -> DesiredState:
        raise RuntimeError("private failure")

    desired.load = fail_load  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="desired state"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    "persisted",
    [
        object(),
        DurableIntent(StringSubclass("command-run-synthetic"), TEAM, CommandKind.RUN, ControlMode.RUNNING, 4),
        DurableIntent("command-run-synthetic", StringSubclass(TEAM), CommandKind.RUN, ControlMode.RUNNING, 4),
        DurableIntent("wrong-command", TEAM, CommandKind.RUN, ControlMode.RUNNING, 4),
        DurableIntent("command-run-synthetic", "other-team", CommandKind.RUN, ControlMode.RUNNING, 4),
        DurableIntent("command-run-synthetic", TEAM, CommandKind.STOP, ControlMode.RUNNING, 4),
        DurableIntent("command-run-synthetic", TEAM, CommandKind.RUN, ControlMode.PAUSED, 4),
        DurableIntent("command-run-synthetic", TEAM, CommandKind.RUN, ControlMode.RUNNING, 0),
        DurableIntent("command-run-synthetic", TEAM, CommandKind.RUN, ControlMode.RUNNING, True),
        DurableIntent(
            "command-run-synthetic",
            TEAM,
            CommandKind.RUN,
            ControlMode.RUNNING,
            4,
            account_ref="account-synthetic-a",
        ),
        DurableIntent(
            "command-run-synthetic",
            TEAM,
            CommandKind.RUN,
            ControlMode.RUNNING,
            4,
            debug_minutes=2,
        ),
    ],
)
def test_forged_persist_result_is_rejected_before_scheduler(
    persisted: object,
) -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    desired.persist = lambda requested: persisted  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert scheduler.accepted == []


def test_structurally_corrupted_persisted_intent_fails_closed() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    persisted = DurableIntent(
        "command-run-synthetic",
        TEAM,
        CommandKind.RUN,
        ControlMode.RUNNING,
        4,
    )
    desired.persist = lambda requested: without_field(persisted, "epoch")  # type: ignore[method-assign]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.RUN))

    assert str(caught.value) == "persisted intent is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert scheduler.accepted == []


def test_persist_exception_is_rejected_before_scheduler() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()

    def fail_persist(requested: Command) -> DurableIntent:
        raise RuntimeError("private failure")

    desired.persist = fail_persist  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert scheduler.accepted == []


def test_persisted_account_reference_must_be_an_exact_string() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    requested = command(CommandKind.QUARANTINE, account_ref="account-synthetic-a")
    desired.persist = lambda command: DurableIntent(  # type: ignore[method-assign]
        command.command_ref,
        command.team_ref,
        command.kind,
        ControlMode.STOPPED,
        4,
        account_ref=StringSubclass("account-synthetic-a"),
    )

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, requested)

    assert scheduler.accepted == []


def test_persisted_debug_duration_must_be_an_exact_integer() -> None:
    service, _, desired, scheduler, _, _, diagnostics = make_service(
        mode=ControlMode.RUNNING
    )
    requested = command(CommandKind.DEBUG_START, debug_minutes=2)
    desired.persist = lambda command: DurableIntent(  # type: ignore[method-assign]
        command.command_ref,
        command.team_ref,
        command.kind,
        ControlMode.RUNNING,
        4,
        debug_minutes=2.0,
    )

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, requested)

    assert scheduler.accepted == []
    assert diagnostics.accepted == []


def test_status_reason_and_outcome_have_closed_privacy_safe_vocabularies() -> None:
    assert {reason.value for reason in StatusBlockedReason} == {
        "OFFLINE",
        "NO_ELIGIBLE_ACCOUNT",
        "QUARANTINED",
        "LIFECYCLE_BLOCKED",
        "CONFIGURATION_BLOCKED",
    }
    assert {outcome.value for outcome in StatusOutcome} == {
        "IDLE",
        "SUCCEEDED",
        "FAILED",
        "DEFERRED",
        "STOPPED",
    }


class SchedulerStatusSubclass(SchedulerStatus):
    pass


@pytest.mark.parametrize(
    "snapshot",
    [
        object(),
        SchedulerStatusSubclass(0, None, None, None, None),
        SchedulerStatus(True, None, None, None, None),
        SchedulerStatus(-1, None, None, None, None),
        SchedulerStatus(1_000_001, None, None, None, None),
        SchedulerStatus(0, "", None, None, None),
        SchedulerStatus(0, "2030-02-30T00:00:00Z", None, None, None),
        SchedulerStatus(0, "2030-01-01T00:00:00", None, None, None),
        SchedulerStatus(0, None, "private raw reason", None, None),
        SchedulerStatus(0, None, None, "account/unsafe", None),
        SchedulerStatus(0, None, None, None, "private raw outcome"),
    ],
)
def test_status_rejects_malformed_scheduler_snapshot(snapshot: object) -> None:
    service, _, _, scheduler, _, _, _ = make_service()
    scheduler.status = lambda team_ref: snapshot  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="scheduler status"):
        service.execute(ACTOR, command(CommandKind.STATUS))


def test_structurally_corrupted_scheduler_status_fails_closed() -> None:
    service, _, _, scheduler, _, _, _ = make_service()
    snapshot = SchedulerStatus(0, None, None, None, None)
    scheduler.status = lambda team_ref: without_field(snapshot, "queue_depth")  # type: ignore[method-assign]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.STATUS))

    assert str(caught.value) == "scheduler status is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class LifecycleStatusSubclass(LifecycleStatus):
    pass


@pytest.mark.parametrize(
    "snapshot",
    [
        object(),
        LifecycleStatusSubclass(False, None),
        LifecycleStatus(1, None),
        LifecycleStatus(False, True),
        LifecycleStatus(False, -1),
        LifecycleStatus(False, 1024),
    ],
)
def test_status_rejects_malformed_lifecycle_snapshot(snapshot: object) -> None:
    service, _, _, _, _, _, _ = make_service()
    service._lifecycle.status = lambda team_ref: snapshot  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="lifecycle status"):
        service.execute(ACTOR, command(CommandKind.STATUS))


def test_structurally_corrupted_lifecycle_status_fails_closed() -> None:
    service, _, _, _, _, _, _ = make_service()
    snapshot = LifecycleStatus(False, None)
    service._lifecycle.status = lambda team_ref: without_field(snapshot, "active")  # type: ignore[method-assign]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.STATUS))

    assert str(caught.value) == "lifecycle status is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "enabled",
    [set(), frozenset({"HOME_GOLD"}), frozenset({StringSubclass("HOME_GOLD")})],
)
def test_status_rejects_malformed_resource_authority(enabled: object) -> None:
    service, _, _, _, _, resources, _ = make_service()
    resources.enabled = enabled  # type: ignore[assignment]

    with pytest.raises(ControlRejected, match="resource authority"):
        service.execute(ACTOR, command(CommandKind.STATUS))


class DiagnosticStatusSubclass(DiagnosticStatus):
    pass


@pytest.mark.parametrize(
    "snapshot",
    [
        object(),
        DiagnosticStatusSubclass(False, 0),
        DiagnosticStatus(1, 0),
        DiagnosticStatus(False, True),
        DiagnosticStatus(False, -1),
        DiagnosticStatus(True, 301),
    ],
)
def test_debug_status_rejects_malformed_snapshot(snapshot: object) -> None:
    service, _, _, _, _, _, diagnostics = make_service()
    diagnostics.status = lambda team_ref: snapshot  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="diagnostic status"):
        service.execute(ACTOR, command(CommandKind.DEBUG_STATUS))


def test_structurally_corrupted_diagnostic_status_fails_closed() -> None:
    service, _, _, _, _, _, diagnostics = make_service()
    snapshot = DiagnosticStatus(False, 0)
    diagnostics.status = lambda team_ref: without_field(snapshot, "remaining_seconds")  # type: ignore[method-assign]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.DEBUG_STATUS))

    assert str(caught.value) == "diagnostic status is malformed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "result",
    ["", "   ", True, StringSubclass("generation-synthetic-a"), "generation/unsafe"],
)
def test_setup_rejects_malformed_generation_result(result: object) -> None:
    service, _, _, _, setup, _, _ = make_service()
    setup.configure = lambda team_ref, resources: result  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="setup result"):
        service.execute(ACTOR, command(CommandKind.SETUP))


def test_authorization_exception_fails_closed_before_other_ports() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()

    def fail_authorize(actor: Actor, team_ref: str, action: CommandKind) -> bool:
        raise RuntimeError("private failure")

    authorization.authorize = fail_authorize  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="authorization unavailable"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert desired.persisted == []
    assert scheduler.accepted == []


def test_wrapped_port_exception_retains_no_private_payload() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    sentinel = "private-exception-sentinel"

    def fail_authorize(actor: Actor, team_ref: str, action: CommandKind) -> bool:
        raise RuntimeError(sentinel)

    authorization.authorize = fail_authorize  # type: ignore[method-assign]

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, command(CommandKind.RUN))

    assert str(caught.value) == "authorization unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sentinel not in repr(caught.value)
    assert desired.persisted == []
    assert scheduler.accepted == []


@pytest.mark.parametrize(
    ("kind", "mode", "admission_attribute", "message"),
    [
        (CommandKind.RUN, ControlMode.STOPPED, "_scheduler", "scheduler admission unavailable"),
        (
            CommandKind.DEBUG_START,
            ControlMode.RUNNING,
            "_diagnostics",
            "diagnostic admission unavailable",
        ),
    ],
)
def test_admission_exception_fails_closed_without_private_payload(
    kind: CommandKind,
    mode: ControlMode,
    admission_attribute: str,
    message: str,
) -> None:
    service, _, _, _, _, _, _ = make_service(mode=mode)
    sentinel = "private-admission-sentinel"

    def fail_admission(intent: DurableIntent) -> None:
        raise RuntimeError(sentinel)

    admission = getattr(service, admission_attribute)
    admission.accept_desired = fail_admission
    requested = (
        command(kind, debug_minutes=2)
        if kind is CommandKind.DEBUG_START
        else command(kind)
    )

    with pytest.raises(ControlRejected) as caught:
        service.execute(ACTOR, requested)

    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sentinel not in repr(caught.value)


def test_status_snapshots_are_copied_before_later_port_callbacks() -> None:
    service, _, desired, scheduler, _, resources, _ = make_service(
        mode=ControlMode.PAUSED
    )
    scheduler_snapshot = scheduler.status(TEAM)
    lifecycle_snapshot = LifecycleStatus(active=False, sanitized_slot_index=7)

    def scheduler_status(team_ref: str) -> SchedulerStatus:
        object.__setattr__(desired.current, "mode", "private-mode")
        object.__setattr__(desired.current, "epoch", True)
        return scheduler_snapshot

    def lifecycle_status(team_ref: str) -> LifecycleStatus:
        object.__setattr__(scheduler_snapshot, "queue_depth", True)
        object.__setattr__(scheduler_snapshot, "current_account_ref", "private/unsafe")
        return lifecycle_snapshot

    def enabled_for(team_ref: str) -> frozenset[Resource]:
        object.__setattr__(lifecycle_snapshot, "active", "private-active")
        object.__setattr__(lifecycle_snapshot, "sanitized_slot_index", True)
        return frozenset({Resource.HOME_GOLD})

    scheduler.status = scheduler_status  # type: ignore[method-assign]
    service._lifecycle.status = lifecycle_status  # type: ignore[method-assign]
    resources.enabled_for = enabled_for  # type: ignore[method-assign]

    result = service.execute(ACTOR, command(CommandKind.STATUS))

    assert result.mode is ControlMode.PAUSED
    assert result.epoch == 3
    assert result.queue_depth == 2
    assert result.current_account_ref == "account-synthetic-a"
    assert result.lifecycle_active is False
    assert result.sanitized_slot_index == 7


def test_debug_snapshot_is_copied_before_return() -> None:
    service, _, _, _, _, _, diagnostics = make_service()
    port_snapshot = DiagnosticStatus(active=True, remaining_seconds=120)
    diagnostics.status = lambda team_ref: port_snapshot  # type: ignore[method-assign]

    result = service.execute(ACTOR, command(CommandKind.DEBUG_STATUS))
    object.__setattr__(port_snapshot, "active", "private-active")
    object.__setattr__(port_snapshot, "remaining_seconds", True)

    assert result == DiagnosticStatus(active=True, remaining_seconds=120)
    assert result is not port_snapshot


def test_debug_stop_preserves_control_mode_and_avoids_scheduler() -> None:
    service, _, desired, scheduler, _, _, diagnostics = make_service(
        mode=ControlMode.RUNNING
    )
    requested = command(CommandKind.DEBUG_STOP)

    result = service.execute(ACTOR, requested)

    assert desired.persisted == [requested]
    assert diagnostics.accepted == [result]
    assert scheduler.accepted == []
    assert result.mode is ControlMode.RUNNING


def test_authorization_cannot_mutate_caller_owned_request_used_by_service() -> None:
    service, authorization, desired, scheduler, _, _, _ = make_service()
    requested_actor = Actor("operator-synthetic-mutation")
    requested = command(CommandKind.RUN)

    def mutate_request(
        actor: Actor, team_ref: str, action: CommandKind
    ) -> bool:
        object.__setattr__(actor, "actor_ref", "mutated-actor")
        object.__setattr__(requested, "team_ref", "mutated-team")
        object.__setattr__(requested, "kind", CommandKind.STOP)
        object.__setattr__(requested, "account_ref", "mutated-account")
        return True

    authorization.authorize = mutate_request  # type: ignore[method-assign]

    result = service.execute(requested_actor, requested)

    assert requested_actor == Actor("operator-synthetic-mutation")
    assert desired.persisted == [command(CommandKind.RUN)]
    assert scheduler.accepted == [result]
    assert result.kind is CommandKind.RUN
    assert result.team_ref == TEAM
    assert result.account_ref is None


def test_later_callback_cannot_mutate_caller_owned_request_used_by_service() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()
    requested = command(CommandKind.RUN)
    original_load = desired.load

    def mutate_request(team_ref: str) -> DesiredState:
        object.__setattr__(requested, "team_ref", "mutated-team")
        object.__setattr__(requested, "kind", CommandKind.STOP)
        return original_load(team_ref)

    desired.load = mutate_request  # type: ignore[method-assign]

    result = service.execute(ACTOR, requested)

    assert desired.persisted == [command(CommandKind.RUN)]
    assert scheduler.accepted == [result]
    assert result.kind is CommandKind.RUN
    assert result.team_ref == TEAM


def test_persist_mutation_cannot_forge_intent_against_changed_command() -> None:
    service, _, desired, scheduler, _, _, _ = make_service()

    def mutate_and_forge(received: Command) -> DurableIntent:
        object.__setattr__(received, "command_ref", "forged-command")
        object.__setattr__(received, "team_ref", "forged-team")
        object.__setattr__(received, "kind", CommandKind.STOP)
        return DurableIntent(
            "forged-command",
            "forged-team",
            CommandKind.STOP,
            ControlMode.RUNNING,
            4,
        )

    desired.persist = mutate_and_forge  # type: ignore[method-assign]

    with pytest.raises(ControlRejected, match="persisted intent"):
        service.execute(ACTOR, command(CommandKind.RUN))

    assert scheduler.accepted == []


@pytest.mark.parametrize(
    ("kind", "mode", "admission_attribute"),
    [
        (CommandKind.RUN, ControlMode.STOPPED, "_scheduler"),
        (CommandKind.DEBUG_START, ControlMode.RUNNING, "_diagnostics"),
    ],
)
def test_admission_requires_exact_none_result(
    kind: CommandKind, mode: ControlMode, admission_attribute: str
) -> None:
    service, _, _, _, _, _, _ = make_service(mode=mode)
    admission = getattr(service, admission_attribute)
    admission.accept_desired = lambda intent: False
    requested = (
        command(kind, debug_minutes=2)
        if kind is CommandKind.DEBUG_START
        else command(kind)
    )

    with pytest.raises(ControlRejected, match="admission result"):
        service.execute(ACTOR, requested)


@pytest.mark.parametrize(
    ("kind", "mode", "admission_attribute"),
    [
        (CommandKind.RUN, ControlMode.STOPPED, "_scheduler"),
        (CommandKind.DEBUG_START, ControlMode.RUNNING, "_diagnostics"),
    ],
)
def test_admission_mutation_cannot_change_returned_intent(
    kind: CommandKind, mode: ControlMode, admission_attribute: str
) -> None:
    service, _, _, _, _, _, _ = make_service(mode=mode)
    admission = getattr(service, admission_attribute)

    def mutate_intent(intent: DurableIntent) -> None:
        object.__setattr__(intent, "command_ref", "mutated-command")
        object.__setattr__(intent, "team_ref", "mutated-team")
        object.__setattr__(intent, "kind", CommandKind.STOP)

    admission.accept_desired = mutate_intent
    requested = (
        command(kind, debug_minutes=2)
        if kind is CommandKind.DEBUG_START
        else command(kind)
    )

    result = service.execute(ACTOR, requested)

    assert result.command_ref == requested.command_ref
    assert result.team_ref == TEAM
    assert result.kind is kind


@pytest.mark.parametrize(
    ("kind", "mode", "kwargs"),
    [
        *[
            (kind, mode, {field_name: field_value})
            for kind, mode in [
                (CommandKind.RUN, ControlMode.STOPPED),
                (CommandKind.PAUSE, ControlMode.RUNNING),
                (CommandKind.RESUME, ControlMode.PAUSED),
                (CommandKind.STOP, ControlMode.RUNNING),
                (CommandKind.STATUS, ControlMode.STOPPED),
                (CommandKind.DEBUG_STATUS, ControlMode.STOPPED),
                (CommandKind.DEBUG_STOP, ControlMode.RUNNING),
            ]
            for field_name, field_value in [
                ("account_ref", "account-synthetic-extra"),
                ("debug_minutes", 2),
                ("resources", frozenset({Resource.HOME_GOLD})),
            ]
        ],
        (CommandKind.SETUP, ControlMode.STOPPED, {"account_ref": "account-synthetic-extra"}),
        (CommandKind.SETUP, ControlMode.STOPPED, {"debug_minutes": 2}),
        (
            CommandKind.QUARANTINE,
            ControlMode.STOPPED,
            {"account_ref": "account-synthetic-a", "debug_minutes": 2},
        ),
        (
            CommandKind.UNQUARANTINE,
            ControlMode.STOPPED,
            {
                "account_ref": "account-synthetic-a",
                "resources": frozenset({Resource.HOME_GOLD}),
            },
        ),
        (
            CommandKind.DEBUG_START,
            ControlMode.RUNNING,
            {"debug_minutes": 2, "account_ref": "account-synthetic-extra"},
        ),
        (
            CommandKind.DEBUG_START,
            ControlMode.RUNNING,
            {
                "debug_minutes": 2,
                "resources": frozenset({Resource.HOME_GOLD}),
            },
        ),
    ],
)
def test_command_kind_rejects_irrelevant_fields_before_authorization(
    kind: CommandKind, mode: ControlMode, kwargs: dict[str, object]
) -> None:
    service, authorization, desired, scheduler, _, _, diagnostics = make_service(
        mode=mode
    )

    with pytest.raises(ControlRejected, match="fields"):
        service.execute(ACTOR, command(kind, **kwargs))

    assert authorization.calls == []
    assert desired.persisted == []
    assert scheduler.accepted == []
    assert diagnostics.accepted == []


@pytest.mark.parametrize(
    "resources",
    [set(), frozenset({"HOME_GOLD"}), frozenset({StringSubclass("HOME_GOLD")})],
)
def test_setup_validates_resources_before_authorization(resources: object) -> None:
    service, authorization, _, _, setup, _, _ = make_service()

    with pytest.raises(ControlRejected, match="resource authority"):
        service.execute(
            ACTOR,
            command(CommandKind.SETUP, resources=resources),  # type: ignore[arg-type]
        )

    assert authorization.calls == []
    assert setup.configured == []


class ApplicationServiceSubclass(ApplicationService):
    pass


class AttributeCompatibleService:
    def execute(self, actor: Actor, requested: Command) -> str:
        return "authorization-bypassed"


@pytest.mark.parametrize(
    "service",
    [object.__new__(ApplicationServiceSubclass), AttributeCompatibleService()],
)
def test_local_adapter_requires_exact_application_service(service: object) -> None:
    with pytest.raises(TypeError, match="exact ApplicationService"):
        LocalControlAdapter(service)  # type: ignore[arg-type]
