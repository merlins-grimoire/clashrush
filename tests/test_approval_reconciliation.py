from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import pytest

from clash_rush_rebuild.approval_reconciliation import (
    ApprovalAction,
    ApprovalError,
    DurableReconciliationAudit,
    OneShotApprovalService,
    PrivateApprovalStorage,
    ReconciliationError,
    StaleStateApprovalIssuer,
    StaleStateReconciler,
)
from clash_rush_rebuild.lifecycle_state import Active, BlockReason, Ready, encode_state


TREE = "1" * 40
NONCE = "2" * 32
STATE = Active(0, NONCE, BlockReason.WINDOW_BINDING)
STATE_BYTES = encode_state(STATE)


class FakeApprovalStorage:
    def __init__(self) -> None:
        self.artifacts: dict[ApprovalAction, bytes] = {}
        self.events: list[str] = []
        self.fail_consume = False

    def replace_artifact(self, action: ApprovalAction, payload: bytes) -> bytes:
        self.events.append(f"replace:{action.value}")
        self.artifacts[action] = payload
        return payload

    def read_artifact(self, action: ApprovalAction) -> bytes:
        self.events.append(f"read:{action.value}")
        try:
            return self.artifacts[action]
        except KeyError as exc:
            raise FileNotFoundError from exc

    def consume_artifact(
        self, action: ApprovalAction, expected: bytes, consumed: bytes
    ) -> bytes:
        self.events.append(f"consume:{action.value}")
        if self.fail_consume:
            raise OSError("consume failed")
        if self.artifacts.get(action) != expected:
            raise OSError("artifact changed")
        self.artifacts[action] = consumed
        return consumed


def service(storage: FakeApprovalStorage, *, now: int = 1_000) -> OneShotApprovalService:
    return OneShotApprovalService(
        storage,
        utc_now=lambda: now,
        approval_id_factory=lambda: "a" * 32,
    )


def test_grant_binds_one_action_tree_expiry_and_exact_state_digest() -> None:
    storage = FakeApprovalStorage()

    granted = service(storage).grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING,
        TREE,
        STATE,
        STATE_BYTES,
        lifetime_seconds=600,
    )

    assert granted.action is ApprovalAction.RECONCILE_WINDOW_BINDING
    assert granted.approval_id == "a" * 32
    assert granted.candidate_tree == TREE
    assert granted.issued_at == 1_000
    assert granted.expires_at == 1_600
    assert granted.state_sha256 == hashlib.sha256(STATE_BYTES).hexdigest()
    assert granted.consumed_at is None
    assert storage.events == ["replace:RECONCILE_WINDOW_BINDING"]


@pytest.mark.parametrize("lifetime", [0, 601, True, 1.5])
def test_grant_rejects_expiry_outside_one_to_ten_minutes(lifetime: object) -> None:
    storage = FakeApprovalStorage()

    with pytest.raises(ApprovalError, match="lifetime"):
        service(storage).grant(
            ApprovalAction.RECONCILE_WINDOW_BINDING,
            TREE,
            STATE,
            STATE_BYTES,
            lifetime_seconds=lifetime,  # type: ignore[arg-type]
        )

    assert storage.events == []


def test_grant_rejects_noncanonical_or_wrong_state_bytes() -> None:
    storage = FakeApprovalStorage()

    with pytest.raises(ApprovalError, match="canonical"):
        service(storage).grant(
            ApprovalAction.RECONCILE_WINDOW_BINDING,
            TREE,
            STATE,
            STATE_BYTES.replace(b"\n", b" \n"),
            lifetime_seconds=600,
        )

    assert storage.events == []


def test_validate_then_consume_is_action_tree_state_bound_and_nonreplayable() -> None:
    storage = FakeApprovalStorage()
    approval = service(storage)
    approval.grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES, lifetime_seconds=600
    )

    consumed = approval.validate_then_consume(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
    )

    assert consumed.consumed_at == 1_000
    with pytest.raises(ApprovalError, match="consumed"):
        approval.validate_then_consume(
            ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
        )


@pytest.mark.parametrize(
    ("action", "tree", "state_bytes", "now", "message"),
    [
        (ApprovalAction.LAUNCH_WINDOW_BINDING_DIAGNOSTIC, TREE, STATE_BYTES, 1_000, "missing"),
        (ApprovalAction.RECONCILE_WINDOW_BINDING, "3" * 40, STATE_BYTES, 1_000, "tree"),
        (
            ApprovalAction.RECONCILE_WINDOW_BINDING,
            TREE,
            encode_state(Active(0, "4" * 32, BlockReason.WINDOW_BINDING)),
            1_000,
            "state",
        ),
        (ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE_BYTES, 1_601, "expired"),
    ],
)
def test_mismatch_before_consumption_leaves_approval_pending(
    action: ApprovalAction, tree: str, state_bytes: bytes, now: int, message: str
) -> None:
    storage = FakeApprovalStorage()
    service(storage).grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES, lifetime_seconds=600
    )
    before = storage.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ApprovalError, match=message):
        service(storage, now=now).validate_then_consume(action, tree, STATE, state_bytes)

    assert storage.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before


def test_boolean_fields_in_artifact_are_rejected() -> None:
    storage = FakeApprovalStorage()
    service(storage).grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES, lifetime_seconds=600
    )
    payload = storage.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]
    storage.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] = payload.replace(
        b'"issued_at":1000', b'"issued_at":true'
    )

    with pytest.raises(ApprovalError, match="malformed"):
        service(storage).validate(
            ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
        )


def test_failed_durable_consumption_does_not_report_consumed() -> None:
    storage = FakeApprovalStorage()
    approval = service(storage)
    approval.grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES, lifetime_seconds=600
    )
    storage.fail_consume = True

    with pytest.raises(ApprovalError, match="consumption"):
        approval.validate_then_consume(
            ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
        )


@dataclass
class FakeLease:
    events: list[str]
    abandoned: bool = False
    usable: bool = True

    def require_usable(self) -> None:
        self.events.append("mutex:usable")
        if not self.usable:
            raise OSError("unusable")

    def release(self) -> None:
        self.events.append("mutex:release")


class FakeRuntime:
    def __init__(
        self, events: list[str], *, abandoned: bool = False, usable: bool = True
    ) -> None:
        self.events = events
        self.abandoned = abandoned
        self.usable = usable

    def acquire_mutex(self) -> FakeLease:
        self.events.append("mutex:acquire")
        return FakeLease(self.events, self.abandoned, self.usable)


class FakeStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.state = STATE
        self.payload = STATE_BYTES
        self.fail_commit = False
        self.fail_readback = False
        self.loads = 0
        self.unresolved_guard = False

    def load_with_bytes(self):
        self.loads += 1
        self.events.append("state:load")
        if self.fail_readback and self.loads >= 3:
            return Active(0, "9" * 32, BlockReason.WINDOW_BINDING), encode_state(
                Active(0, "9" * 32, BlockReason.WINDOW_BINDING)
            )
        return self.state, self.payload

    def commit(self, state: Ready):
        self.events.append("state:commit")
        if self.fail_commit:
            raise OSError("write failure")
        self.state = state
        self.payload = encode_state(state)
        return state

    def commit_with_postcondition(self, state: Ready, postcondition):
        committed = self.commit(state)
        self.unresolved_guard = True
        if self.fail_readback:
            raise OSError("read-back failure")
        postcondition()
        self.unresolved_guard = False
        return committed


class FakeAudit:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.fail_intent = False
        self.fail_outcome = False

    def append_intent(self) -> None:
        self.events.append("audit:intent")
        if self.fail_intent:
            raise OSError("intent failure")

    def append_outcome(self) -> None:
        self.events.append("audit:outcome")
        if self.fail_outcome:
            raise OSError("outcome failure")


def reconciler(*, abandoned: bool = False, usable: bool = True):
    events: list[str] = []
    approvals = FakeApprovalStorage()
    service(approvals).grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES, lifetime_seconds=600
    )
    events.clear()
    store = FakeStore(events)
    audit = FakeAudit(events)
    observations = [(0, 0), (0, 0), (0, 0)]
    subject = StaleStateReconciler(
        FakeRuntime(events, abandoned=abandoned, usable=usable),
        make_state_store=lambda: store,
        approvals=service(approvals),
        candidate_tree=lambda: TREE,
        observe_absence=lambda: observations.pop(0),
        audit=audit,
    )
    return subject, events, approvals, store, audit, observations


def test_reconciliation_orders_double_absence_consumption_intent_mutation_readback_outcome() -> None:
    subject, events, approvals, store, _audit, _observations = reconciler()

    assert subject.reconcile() == Ready(0)

    assert events == [
        "mutex:acquire",
        "mutex:usable",
        "state:load",
        "state:load",
        "audit:intent",
        "state:commit",
        "audit:outcome",
        "mutex:release",
    ]
    assert b'"consumed_at":1000' in approvals.artifacts[
        ApprovalAction.RECONCILE_WINDOW_BINDING
    ]
    assert store.state == Ready(0)


@pytest.mark.parametrize("bad_state", [Ready(0), Active(1, NONCE, BlockReason.WINDOW_BINDING), Active(0, NONCE, BlockReason.STATE)])
def test_wrong_exact_state_is_preserved_without_consuming_approval(bad_state: object) -> None:
    subject, _events, approvals, store, _audit, _observations = reconciler()
    store.state = bad_state
    store.payload = encode_state(bad_state)  # type: ignore[arg-type]
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ReconciliationError, match="exact blocked state"):
        subject.reconcile()

    assert store.state == bad_state
    assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before


def test_abandoned_mutex_preserves_state_and_approval() -> None:
    subject, events, approvals, store, _audit, _observations = reconciler(abandoned=True)
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ReconciliationError, match="abandoned"):
        subject.reconcile()

    assert store.state == STATE
    assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before
    assert events == ["mutex:acquire", "mutex:release"]


def test_unusable_mutex_preserves_state_and_approval() -> None:
    subject, events, approvals, store, _audit, _observations = reconciler(usable=False)
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(OSError, match="unusable"):
        subject.reconcile()

    assert store.state == STATE
    assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before
    assert events == ["mutex:acquire", "mutex:usable", "mutex:release"]


@pytest.mark.parametrize(
    "appearance",
    [
        [(1, 0)],
        [(0, 1)],
        [(True, 0)],
        [(0, 0), (1, 0)],
        [(0, 0), (0, 1)],
        [(0, 0), (0, 0), (1, 0)],
        [(0, 0), (0, 0), (0, 1)],
    ],
)
def test_process_or_window_presence_at_either_check_preserves_active(appearance) -> None:
    subject, _events, approvals, store, _audit, observations = reconciler()
    observations[:] = appearance
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ReconciliationError, match="absence"):
        subject.reconcile()

    assert store.state == STATE
    if len(appearance) == 1:
        assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before
    else:
        assert b'"consumed_at":1000' in approvals.artifacts[
            ApprovalAction.RECONCILE_WINDOW_BINDING
        ]


def test_state_or_tree_change_between_checks_preserves_active() -> None:
    subject, _events, approvals, store, _audit, _observations = reconciler()
    original_load = store.load_with_bytes

    def changed_load():
        state, payload = original_load()
        if store.loads == 2:
            changed = Active(0, "8" * 32, BlockReason.WINDOW_BINDING)
            return changed, encode_state(changed)
        return state, payload

    store.load_with_bytes = changed_load  # type: ignore[method-assign]
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ReconciliationError, match="changed"):
        subject.reconcile()

    assert store.state == STATE
    assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before


def test_candidate_tree_change_between_checks_preserves_active() -> None:
    subject, _events, approvals, store, _audit, _observations = reconciler()
    trees = iter((TREE, "3" * 40))
    subject._candidate_tree = lambda: next(trees)
    before = approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING]

    with pytest.raises(ReconciliationError, match="changed"):
        subject.reconcile()

    assert store.state == STATE
    assert approvals.artifacts[ApprovalAction.RECONCILE_WINDOW_BINDING] == before


def test_intent_failure_preserves_active_after_consuming_approval() -> None:
    subject, _events, approvals, store, audit, _observations = reconciler()
    audit.fail_intent = True

    with pytest.raises(ReconciliationError, match="intent"):
        subject.reconcile()

    assert store.state == STATE
    assert b'"consumed_at":1000' in approvals.artifacts[
        ApprovalAction.RECONCILE_WINDOW_BINDING
    ]


def test_write_failure_preserves_active_or_transition_guard_and_consumes_approval() -> None:
    subject, _events, approvals, store, _audit, _observations = reconciler()
    store.fail_commit = True

    with pytest.raises(ReconciliationError, match="write"):
        subject.reconcile()

    assert store.state == STATE
    assert b'"consumed_at":1000' in approvals.artifacts[
        ApprovalAction.RECONCILE_WINDOW_BINDING
    ]


def test_readback_failure_reports_unresolved_and_never_audits_success() -> None:
    subject, events, _approvals, store, _audit, _observations = reconciler()
    store.fail_readback = True

    with pytest.raises(ReconciliationError, match="read-back"):
        subject.reconcile()

    assert "audit:outcome" not in events
    assert store.unresolved_guard is True


def test_outcome_audit_failure_reports_ready_but_unresolved() -> None:
    subject, _events, _approvals, store, audit, _observations = reconciler()
    audit.fail_outcome = True

    with pytest.raises(ReconciliationError, match="outcome"):
        subject.reconcile()

    assert store.state == Ready(0)
    assert store.unresolved_guard is True


def test_issuer_holds_usable_mutex_and_grants_only_exact_blocked_state() -> None:
    events: list[str] = []
    storage = FakeApprovalStorage()
    store = FakeStore(events)
    issuer = StaleStateApprovalIssuer(
        FakeRuntime(events),
        make_state_store=lambda: store,
        approvals=service(storage),
        candidate_tree=lambda: TREE,
    )

    issued = issuer.issue(lifetime_seconds=300)

    assert issued.action is ApprovalAction.RECONCILE_WINDOW_BINDING
    assert events == ["mutex:acquire", "mutex:usable", "state:load", "mutex:release"]


def test_private_storage_atomically_reads_back_and_marks_consumed(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "var").mkdir(parents=True)
    sealed: list[tuple[str, bool]] = []
    storage = PrivateApprovalStorage(
        project,
        permission_sealer=lambda path, directory: sealed.append((path.name, directory)),
    )
    approval = OneShotApprovalService(
        storage, utc_now=lambda: 1_000, approval_id_factory=lambda: "a" * 32
    )

    approval.grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING,
        TREE,
        STATE,
        STATE_BYTES,
        lifetime_seconds=300,
    )
    approval.validate_then_consume(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
    )

    with pytest.raises(ApprovalError, match="consumed"):
        approval.validate_then_consume(
            ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
        )
    assert ("approvals", True) in sealed
    assert any(name.endswith(".json") and not directory for name, directory in sealed)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL verification")
def test_private_storage_sets_and_reads_back_exact_operator_system_dacl(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "var").mkdir(parents=True)
    approval = OneShotApprovalService(
        PrivateApprovalStorage(project),
        utc_now=lambda: 1_000,
        approval_id_factory=lambda: "a" * 32,
    )

    approval.grant(
        ApprovalAction.RECONCILE_WINDOW_BINDING,
        TREE,
        STATE,
        STATE_BYTES,
        lifetime_seconds=300,
    )

    assert approval.validate(
        ApprovalAction.RECONCILE_WINDOW_BINDING, TREE, STATE, STATE_BYTES
    ).consumed_at is None


def test_reconciliation_audit_is_sanitized_durable_and_ordered(tmp_path) -> None:
    path = tmp_path / "reconciliation-audit.jsonl"
    sealed: list[tuple[str, bool]] = []
    audit = DurableReconciliationAudit(
        path,
        utc_now=lambda: 1_000,
        permission_sealer=lambda item, directory: sealed.append((item.name, directory)),
    )

    audit.append_intent()
    audit.append_outcome()

    assert path.read_bytes() == (
        b'{"event":"RECONCILIATION_INTENT","schema":1,"timestamp":1000}\n'
        b'{"event":"RECONCILIATION_OUTCOME_READY","schema":1,"timestamp":1000}\n'
    )
    assert sealed == [("reconciliation-audit.jsonl", False)] * 2
