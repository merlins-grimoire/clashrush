"""Purpose-specific durable authority for one local MVP account session.

This store deliberately does not reuse the scheduler database.  It owns only local
Setup/Run/Pause/Resume/Stop/Status, one-use admission, one action phase chain, and
the final game/retirement receipt.  SQLite transactions are short and never wrap
capture, native input, process launch, or process retirement.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .mvp_local_gameplay import MvpConfiguration


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SLOT = re.compile(r"slot-[0-4]")
_TRANSACTION = _REFERENCE
_MAX_DEADLINE_SECONDS = 300


class SessionAuthorityError(RuntimeError):
    """The durable single-account authority failed closed."""


class ControlMode(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class ActionPhase(StrEnum):
    PLANNED = "PLANNED"
    INTENT_RECORDED = "INTENT_RECORDED"
    INPUT_STARTED = "INPUT_STARTED"
    INPUT_COMPLETED = "INPUT_COMPLETED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class SessionGrant:
    run_nonce: str
    configuration_revision: int
    control_revision: int
    deadline: int


@dataclass(frozen=True, slots=True)
class DurableTransaction:
    transaction_ref: str
    run_nonce: str
    phase: ActionPhase
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    mode: ControlMode
    configured: bool
    run_nonce: str | None
    configuration_revision: int
    control_revision: int
    admission_consumed: bool
    action_phase: ActionPhase | None
    game_outcome: str | None
    retirement_outcome: str | None
    final_acknowledged: bool


_SCHEMA = """
CREATE TABLE configuration (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    team_ref TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    instance_ref TEXT NOT NULL,
    player_tag_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE control (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    mode TEXT NOT NULL CHECK (mode IN ('STOPPED','RUNNING','PAUSED')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    current_run_nonce TEXT
) STRICT;
CREATE TABLE runs (
    run_nonce TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    candidate_tree TEXT NOT NULL,
    ready_sha256 TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL,
    control_revision INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    admission_consumed INTEGER NOT NULL DEFAULT 0 CHECK (admission_consumed IN (0,1)),
    game_outcome TEXT CHECK (game_outcome IN ('CONFIRMED','FAILED','UNCERTAIN')),
    retirement_outcome TEXT CHECK (retirement_outcome IN ('SUCCEEDED','FAILED')),
    final_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (final_acknowledged IN (0,1))
) STRICT;
CREATE TABLE transactions (
    transaction_ref TEXT PRIMARY KEY,
    run_nonce TEXT NOT NULL UNIQUE REFERENCES runs(run_nonce),
    phase TEXT NOT NULL CHECK (phase IN ('PLANNED','INTENT_RECORDED','INPUT_STARTED','INPUT_COMPLETED','CONFIRMED','FAILED','UNCERTAIN')),
    reason_code TEXT
) STRICT;
CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    target TEXT NOT NULL CHECK (target IN ('PAUSED','STOPPED'))
) STRICT;
"""


class DurableSessionAuthority:
    """Version-one SQLite authority for exactly one configured local account."""

    def __init__(
        self, path: Path, *, nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16)
    ) -> None:
        self._path = Path(path)
        self._nonce_factory = nonce_factory
        if not callable(nonce_factory):
            raise SessionAuthorityError("nonce factory is required")
        if self._path.exists() and self._path.is_symlink():
            raise SessionAuthorityError("session database path must not redirect")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(
                self._path, timeout=2.0, isolation_level=None, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 2000")
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            if str(mode).lower() != "wal":
                raise SessionAuthorityError("session database WAL mode is unavailable")
            self._initialize()
        except BaseException as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            if isinstance(exc, SessionAuthorityError):
                raise
            raise SessionAuthorityError("session database is unavailable") from exc

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _open(self) -> sqlite3.Connection:
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise SessionAuthorityError("session database is closed")
        return connection

    def _initialize(self) -> None:
        connection = self._open()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        manifest = connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        if type(version) is not int or version not in (0, 1, 2):
            raise SessionAuthorityError("session database version is unsupported")
        if version == 0:
            if manifest:
                raise SessionAuthorityError("session database schema is noncanonical")
            connection.executescript("BEGIN IMMEDIATE;" + _SCHEMA + "PRAGMA user_version = 1; COMMIT;")
        expected = [
            ("table", "commands", "commands"),
            ("table", "configuration", "configuration"),
            ("table", "control", "control"),
            ("table", "runs", "runs"),
            ("table", "transactions", "transactions"),
        ]
        if version == 2:
            expected.insert(3, ("table", "deployment_receipts", "deployment_receipts"))
        actual = connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        if [tuple(row) for row in actual] != expected:
            raise SessionAuthorityError("session database schema is noncanonical")

        from .mvp_deployment_receipt import validate_schema
        validate_schema(connection, _SCHEMA)

    @staticmethod
    def _configuration(configuration: object) -> MvpConfiguration:
        if type(configuration) is not MvpConfiguration:
            raise SessionAuthorityError("exact local MVP configuration required")
        values = (configuration.team_ref, configuration.account_ref)
        if (
            any(type(value) is not str or _REFERENCE.fullmatch(value) is None for value in values)
            or type(configuration.instance_ref) is not str
            or _SLOT.fullmatch(configuration.instance_ref) is None
            or type(configuration.player_tag_sha256) is not str
            or _HEX_64.fullmatch(configuration.player_tag_sha256) is None
        ):
            raise SessionAuthorityError("one exact Team, account, and instance are required")
        return configuration

    def setup(self, configuration: MvpConfiguration) -> SessionStatus:
        config = self._configuration(configuration)
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM configuration").fetchone() is not None:
                raise SessionAuthorityError("local MVP is already configured")
            connection.execute(
                "INSERT INTO configuration VALUES (1,1,?,?,?,?)",
                (config.team_ref, config.account_ref, config.instance_ref, config.player_tag_sha256),
            )
            connection.execute("INSERT INTO control VALUES (1,'STOPPED',1,NULL)")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def rebind_stopped_instance(
        self, *, expected_instance_ref: str, instance_ref: str
    ) -> SessionStatus:
        if (
            type(expected_instance_ref) is not str
            or _SLOT.fullmatch(expected_instance_ref) is None
            or type(instance_ref) is not str
            or _SLOT.fullmatch(instance_ref) is None
        ):
            raise SessionAuthorityError("exact instance references are required")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute(
                "SELECT mode FROM control WHERE singleton=1"
            ).fetchone()
            if (
                control is None
                or control["mode"] != ControlMode.STOPPED.value
            ):
                raise SessionAuthorityError("instance rebind requires STOPPED mode")
            configuration = connection.execute(
                "SELECT instance_ref FROM configuration WHERE singleton=1"
            ).fetchone()
            if configuration is None:
                raise SessionAuthorityError("local MVP is not configured")
            if configuration["instance_ref"] != expected_instance_ref:
                raise SessionAuthorityError("configured instance changed")
            if instance_ref == expected_instance_ref:
                raise SessionAuthorityError("configured instance is already selected")
            connection.execute(
                "UPDATE configuration SET revision=revision+1,instance_ref=? WHERE singleton=1",
                (instance_ref,),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def import_stopped_legacy(self, path: Path) -> SessionStatus:
        if self._open().execute("SELECT 1 FROM configuration").fetchone() is not None:
            raise SessionAuthorityError("local MVP is already configured")
        try:
            payload = Path(path).read_bytes()
            raw = json.loads(payload.decode("utf-8"))
            if (
                type(raw) is not dict
                or set(raw) != {"schema", "mode", "configuration"}
                or type(raw["schema"]) is not int
                or raw["schema"] != 2
                or raw["mode"] != "STOPPED"
                or type(raw["configuration"]) is not dict
                or set(raw["configuration"]) != set(asdict(MvpConfiguration("a", "a", "slot-0", "a" * 64)))
            ):
                raise SessionAuthorityError("legacy control is not an exact stopped state")
            canonical = (
                json.dumps(raw, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
                + b"\n"
            )
            if canonical != payload:
                raise SessionAuthorityError("legacy control is not canonical")
            configuration = MvpConfiguration(**raw["configuration"])
        except SessionAuthorityError:
            raise
        except BaseException as exc:
            raise SessionAuthorityError("legacy control is unavailable") from exc
        return self.setup(configuration)

    @staticmethod
    def _binding(
        purpose: object, candidate_tree: object, ready_bytes: object, deadline: object, now: object
    ) -> tuple[str, str, str, int, int]:
        if (
            type(purpose) is not str
            or _REFERENCE.fullmatch(purpose) is None
            or type(candidate_tree) is not str
            or _HEX_40.fullmatch(candidate_tree) is None
            or type(ready_bytes) is not bytes
            or not ready_bytes
            or type(deadline) is not int
            or type(now) is not int
            or not 0 <= now < deadline <= now + _MAX_DEADLINE_SECONDS
        ):
            raise SessionAuthorityError("run admission binding is malformed")
        return purpose, candidate_tree, hashlib.sha256(ready_bytes).hexdigest(), deadline, now

    def _begin_run(
        self,
        *,
        expected_mode: ControlMode,
        purpose: str,
        candidate_tree: str,
        ready_bytes: bytes,
        deadline: int,
        now: int,
        nonce: str | None = None,
        deployment_sha256: str | None = None,
    ) -> SessionGrant:
        purpose, tree, ready_digest, deadline, _ = self._binding(
            purpose, candidate_tree, ready_bytes, deadline, now
        )
        run_nonce = self._nonce_factory() if nonce is None else nonce
        if type(run_nonce) is not str or _HEX_32.fullmatch(run_nonce) is None:
            raise SessionAuthorityError("run nonce is malformed")
        connection = self._open()
        from .mvp_deployment_receipt import upgrade_schema
        upgrade_schema(connection, _SCHEMA)
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            configuration = connection.execute(
                "SELECT revision FROM configuration WHERE singleton=1"
            ).fetchone()
            if control is None or configuration is None:
                raise SessionAuthorityError("local MVP is not configured")
            unresolved = connection.execute(
                "SELECT 1 FROM runs WHERE admission_consumed=1 AND (game_outcome IS NULL OR game_outcome='UNCERTAIN' OR retirement_outcome IS NULL OR retirement_outcome='FAILED') LIMIT 1"
            ).fetchone()
            if unresolved is not None:
                raise SessionAuthorityError("an unresolved run blocks new attack authority")
            if control["mode"] != expected_mode.value:
                raise SessionAuthorityError(f"run requires {expected_mode.value} mode")
            revision = control["revision"] + 1
            connection.execute(
                "INSERT INTO runs (run_nonce,purpose,candidate_tree,ready_sha256,configuration_revision,control_revision,deadline) VALUES (?,?,?,?,?,?,?)",
                (run_nonce, purpose, tree, ready_digest, configuration["revision"], revision, deadline),
            )
            from .mvp_deployment_receipt import bind_profile
            bind_profile(connection, run_nonce, purpose, deployment_sha256)
            connection.execute(
                "UPDATE control SET mode='RUNNING',revision=?,current_run_nonce=? WHERE singleton=1",
                (revision, run_nonce),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return SessionGrant(run_nonce, configuration["revision"], revision, deadline)

    def run(self, **binding: object) -> SessionGrant:
        return self._begin_run(expected_mode=ControlMode.STOPPED, **binding)  # type: ignore[arg-type]

    def resume(self, **binding: object) -> SessionGrant:
        return self._begin_run(expected_mode=ControlMode.PAUSED, **binding)  # type: ignore[arg-type]

    def command(self, target: ControlMode, *, command_id: str) -> SessionStatus:
        if type(target) is not ControlMode or target not in (
            ControlMode.PAUSED,
            ControlMode.STOPPED,
        ):
            raise SessionAuthorityError("invalid control command")
        if type(command_id) is not str or _REFERENCE.fullmatch(command_id) is None:
            raise SessionAuthorityError("control command id is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT target FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if existing is not None:
                if existing["target"] != target.value:
                    raise SessionAuthorityError("control command id conflicts")
                connection.execute("COMMIT")
                return self.status()
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            if control is None:
                raise SessionAuthorityError("local MVP is not configured")
            current = ControlMode(control["mode"])
            effective = (
                ControlMode.STOPPED
                if current is ControlMode.STOPPED or target is ControlMode.STOPPED
                else ControlMode.PAUSED
            )
            if current is not ControlMode.RUNNING and not (
                current is ControlMode.PAUSED and target is ControlMode.STOPPED
            ) and effective is not current:
                raise SessionAuthorityError("control command requires an active run")
            connection.execute(
                "INSERT INTO commands VALUES (?,?)", (command_id, target.value)
            )
            connection.execute(
                "UPDATE control SET mode=?,revision=revision+1 WHERE singleton=1",
                (effective.value,),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def admit(
        self,
        *,
        run_nonce: str,
        transaction_ref: str,
        purpose: str,
        candidate_tree: str,
        ready_bytes: bytes,
        configuration_revision: int,
        control_revision: int,
        now: int,
    ) -> DurableTransaction:
        if type(transaction_ref) is not str or _TRANSACTION.fullmatch(transaction_ref) is None:
            raise SessionAuthorityError("transaction reference is malformed")
        if type(now) is not int or type(configuration_revision) is not int or type(control_revision) is not int:
            raise SessionAuthorityError("admission values are malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            run = connection.execute("SELECT * FROM runs WHERE run_nonce=?", (run_nonce,)).fetchone()
            if control is None or run is None or control["mode"] != "RUNNING" or control["current_run_nonce"] != run_nonce:
                raise SessionAuthorityError("run nonce is not current")
            if run["admission_consumed"]:
                raise SessionAuthorityError("run admission was already consumed")
            if run["purpose"] != purpose or run["candidate_tree"] != candidate_tree:
                raise SessionAuthorityError("run purpose or candidate tree mismatch")
            if hashlib.sha256(ready_bytes).hexdigest() != run["ready_sha256"]:
                raise SessionAuthorityError("READY bytes mismatch")
            if (
                run["configuration_revision"] != configuration_revision
                or run["control_revision"] != control_revision
                or control["revision"] != control_revision
            ):
                raise SessionAuthorityError("configuration or control revision mismatch")
            if not 0 <= now < run["deadline"]:
                raise SessionAuthorityError("run admission deadline expired")
            connection.execute(
                "INSERT INTO transactions VALUES (?,?,?,NULL)",
                (transaction_ref, run_nonce, ActionPhase.PLANNED.value),
            )
            connection.execute(
                "UPDATE runs SET admission_consumed=1 WHERE run_nonce=?", (run_nonce,)
            )
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise SessionAuthorityError("transaction identity was already used") from exc
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.transaction(transaction_ref)

    def transaction(self, transaction_ref: str) -> DurableTransaction:
        row = self._open().execute(
            "SELECT * FROM transactions WHERE transaction_ref=?", (transaction_ref,)
        ).fetchone()
        if row is None:
            raise SessionAuthorityError("transaction is unavailable")
        return DurableTransaction(
            row["transaction_ref"], row["run_nonce"], ActionPhase(row["phase"]), row["reason_code"]
        )

    def transition_action(
        self,
        transaction_ref: str,
        *,
        expected: ActionPhase,
        target: ActionPhase,
        reason_code: str | None = None,
    ) -> DurableTransaction:
        allowed = {
            ActionPhase.PLANNED: {ActionPhase.INTENT_RECORDED, ActionPhase.FAILED},
            ActionPhase.INTENT_RECORDED: {ActionPhase.INPUT_STARTED, ActionPhase.FAILED},
            ActionPhase.INPUT_STARTED: {ActionPhase.INPUT_COMPLETED, ActionPhase.FAILED, ActionPhase.UNCERTAIN},
            ActionPhase.INPUT_COMPLETED: {ActionPhase.CONFIRMED, ActionPhase.FAILED, ActionPhase.UNCERTAIN},
        }
        if type(expected) is not ActionPhase or type(target) is not ActionPhase or target not in allowed.get(expected, set()):
            raise SessionAuthorityError("invalid action phase transition")
        if reason_code is not None and (type(reason_code) is not str or _REFERENCE.fullmatch(reason_code) is None):
            raise SessionAuthorityError("action reason is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transactions WHERE transaction_ref=?", (transaction_ref,)
            ).fetchone()
            if row is None or row["phase"] != expected.value:
                raise SessionAuthorityError(f"action is not {expected.value}")
            connection.execute(
                "UPDATE transactions SET phase=?,reason_code=? WHERE transaction_ref=?",
                (target.value, reason_code, transaction_ref),
            )
            if target in (ActionPhase.CONFIRMED, ActionPhase.FAILED, ActionPhase.UNCERTAIN):
                connection.execute(
                    "UPDATE runs SET game_outcome=? WHERE run_nonce=?",
                    (target.value, row["run_nonce"]),
                )
                self._finalize_ack(connection, row["run_nonce"])
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.transaction(transaction_ref)

    @staticmethod
    def _finalize_ack(connection: sqlite3.Connection, run_nonce: str) -> None:
        connection.execute(
            "UPDATE runs SET final_acknowledged=1 WHERE run_nonce=? AND game_outcome IS NOT NULL AND retirement_outcome IS NOT NULL",
            (run_nonce,),
        )

    def record_retirement(self, run_nonce: str, *, succeeded: bool) -> SessionStatus:
        if type(succeeded) is not bool:
            raise SessionAuthorityError("retirement outcome is malformed")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_nonce=?", (run_nonce,)).fetchone()
            if row is None:
                raise SessionAuthorityError("run is unavailable")
            outcome = "SUCCEEDED" if succeeded else "FAILED"
            if row["retirement_outcome"] not in (None, outcome):
                raise SessionAuthorityError("retirement outcome conflicts")
            connection.execute(
                "UPDATE runs SET retirement_outcome=? WHERE run_nonce=?", (outcome, run_nonce)
            )
            self._finalize_ack(connection, run_nonce)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return self.status()

    def status(self) -> SessionStatus:
        connection = self._open()
        configuration = connection.execute(
            "SELECT revision FROM configuration WHERE singleton=1"
        ).fetchone()
        control = connection.execute("SELECT * FROM control WHERE singleton=1").fetchone()
        if configuration is None or control is None:
            raise SessionAuthorityError("local MVP is not configured")
        run = None
        transaction = None
        if control["current_run_nonce"] is not None:
            run = connection.execute(
                "SELECT * FROM runs WHERE run_nonce=?", (control["current_run_nonce"],)
            ).fetchone()
            transaction = connection.execute(
                "SELECT * FROM transactions WHERE run_nonce=?", (control["current_run_nonce"],)
            ).fetchone()
        return SessionStatus(
            ControlMode(control["mode"]),
            True,
            control["current_run_nonce"],
            configuration["revision"],
            control["revision"],
            bool(run["admission_consumed"]) if run is not None else False,
            ActionPhase(transaction["phase"]) if transaction is not None else None,
            run["game_outcome"] if run is not None else None,
            run["retirement_outcome"] if run is not None else None,
            bool(run["final_acknowledged"]) if run is not None else False,
        )

    def configuration(self) -> MvpConfiguration:
        row = self._open().execute(
            "SELECT team_ref,account_ref,instance_ref,player_tag_sha256 FROM configuration WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise SessionAuthorityError("local MVP is not configured")
        return self._configuration(
            MvpConfiguration(
                row["team_ref"],
                row["account_ref"],
                row["instance_ref"],
                row["player_tag_sha256"],
            )
        )


def run_reversible_transaction(
    authority: DurableSessionAuthority,
    transaction_ref: str,
    *,
    execute: Callable[[], bool],
    confirm: Callable[[], bool],
) -> DurableTransaction:
    """Exercise one injected transaction without holding SQLite across callbacks."""
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.PLANNED,
        target=ActionPhase.INTENT_RECORDED,
    )
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INTENT_RECORDED,
        target=ActionPhase.INPUT_STARTED,
    )
    try:
        executed = execute()
    except BaseException:
        authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_STARTED,
            target=ActionPhase.UNCERTAIN,
            reason_code="EXECUTOR_EXCEPTION",
        )
        raise
    if executed is not True:
        return authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_STARTED,
            target=ActionPhase.FAILED,
            reason_code="INPUT_FAILED",
        )
    authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INPUT_STARTED,
        target=ActionPhase.INPUT_COMPLETED,
    )
    try:
        confirmed = confirm()
    except BaseException:
        return authority.transition_action(
            transaction_ref,
            expected=ActionPhase.INPUT_COMPLETED,
            target=ActionPhase.UNCERTAIN,
            reason_code="CONFIRMATION_UNAVAILABLE",
        )
    return authority.transition_action(
        transaction_ref,
        expected=ActionPhase.INPUT_COMPLETED,
        target=(
            ActionPhase.CONFIRMED if confirmed is True else ActionPhase.UNCERTAIN
        ),
        reason_code=None if confirmed is True else "POSTCONDITION_UNCONFIRMED",
    )


__all__ = [
    "ActionPhase",
    "ControlMode",
    "DurableSessionAuthority",
    "DurableTransaction",
    "SessionAuthorityError",
    "SessionGrant",
    "SessionStatus",
    "run_reversible_transaction",
]
