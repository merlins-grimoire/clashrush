"""Admission-bound schema-v2 lifecycle supervisor."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .configuration_v2 import (
    AccountKey,
    ConfigurationGeneration,
    SchemaV2Configuration,
)
from .lifecycle import (
    AcquiredMutexLease,
    CreatedProcess,
    LifecycleError,
    LifecycleHostPort,
    MemberSnapshot,
    PlayerBinding,
    ProcessIdentity,
    StopRecord,
)
from .lifecycle_state import BlockReason
from .lifecycle_state_v2 import ActiveV2, ReadyV2, SchemaV2LifecycleStateStore
from .registry import Slot


_VISIT_NONCE_PATTERN = re.compile(r"[0-9a-f]{32}")


class StateV2Port(Protocol):
    def load(self) -> ReadyV2 | ActiveV2: ...

    def commit(self, state: ReadyV2 | ActiveV2) -> ReadyV2 | ActiveV2: ...


@dataclass(slots=True)
class _OwnedRunV2:
    account_key: AccountKey
    configuration_generation: ConfigurationGeneration
    slot: Slot
    run_nonce: str
    job: object
    process: object
    members: MemberSnapshot
    binding: PlayerBinding


class SchemaV2LifecycleSupervisor:
    """Launch only an account already admitted by the schema-v2 scheduler."""

    def __init__(
        self,
        host: LifecycleHostPort,
        state: StateV2Port | SchemaV2LifecycleStateStore,
        mutex: AcquiredMutexLease,
        *,
        configuration: SchemaV2Configuration,
        slots: tuple[Slot, ...],
        readiness_wait: Callable[[int], None] | None = None,
        readiness_attempts: int = 180,
    ) -> None:
        if type(mutex) is not AcquiredMutexLease or mutex.abandoned:
            raise LifecycleError("non-abandoned acquired lifecycle mutex required")
        if type(configuration) is not SchemaV2Configuration:
            raise LifecycleError("exact schema-v2 configuration required")
        if (
            type(slots) is not tuple
            or len(slots) != configuration.cardinality
            or any(type(slot) is not Slot for slot in slots)
            or tuple(slot.index for slot in slots) != tuple(range(len(slots)))
        ):
            raise LifecycleError("ordered configuration-sized slot registry required")
        if (
            len({slot.internal_name for slot in slots}) != len(slots)
            or len({slot.display_name for slot in slots}) != len(slots)
        ):
            raise LifecycleError("schema-v2 slot bindings must be distinct")
        if (
            (readiness_wait is not None and not callable(readiness_wait))
            or type(readiness_attempts) is not int
            or not 1 <= readiness_attempts <= 240
        ):
            raise LifecycleError("bounded readiness policy required")
        self._host = host
        self._state = state
        self._mutex = mutex
        self._configuration = configuration
        self._account_slots = {
            account.key: slots[index]
            for index, account in enumerate(configuration.accounts)
        }
        self._readiness_wait = (
            readiness_wait
            if readiness_wait is not None
            else lambda milliseconds: time.sleep(milliseconds / 1000)
        )
        self._readiness_attempts = readiness_attempts
        self._owned: _OwnedRunV2 | None = None

    def start_admitted(
        self,
        account_key: AccountKey,
        configuration_generation: ConfigurationGeneration,
        visit_nonce: str,
    ) -> PlayerBinding:
        """Start one externally admitted visit without consulting the tie cursor."""
        if type(account_key) is not AccountKey:
            raise LifecycleError("exact admitted account key required")
        if type(configuration_generation) is not ConfigurationGeneration:
            raise LifecycleError("exact admitted configuration generation required")
        if configuration_generation != self._configuration.generation:
            raise LifecycleError("admitted configuration generation mismatch")
        slot = self._account_slots.get(account_key)
        if slot is None:
            raise LifecycleError("admitted account is not configured")
        if type(visit_nonce) is not str or _VISIT_NONCE_PATTERN.fullmatch(
            visit_nonce
        ) is None:
            raise LifecycleError("admitted visit nonce must be 32 lowercase hex characters")
        if self._owned is not None:
            raise LifecycleError("owned run must stop before another launch")

        active = ActiveV2(
            configuration_generation,
            account_key,
            slot.index,
            visit_nonce,
            None,
        )
        current = self._state.load()
        if (
            type(current) is not ReadyV2
            or current.configuration_generation != configuration_generation
        ):
            raise LifecycleError("lifecycle state does not authorize admitted start")

        players = self._host.complete_player_snapshot()
        try:
            if players.identities:
                raise LifecycleError("another BlueStacks player is already running")
        finally:
            players.close()

        committed = self._state.commit(active)
        if type(committed) is not ActiveV2 or committed != active:
            raise LifecycleError("ACTIVE lifecycle state was not durably committed")

        job: object | None = None
        created: CreatedProcess | None = None
        identity: ProcessIdentity | None = None
        members: MemberSnapshot | None = None
        binding: PlayerBinding | None = None
        assignment_uncertain = False
        assigned = False
        thread_closed = False
        reason = BlockReason.JOB_SETUP
        try:
            job = self._host.create_job()
            self._host.set_kill_on_close(job)
            reason = BlockReason.PROCESS_CREATE
            created = self._host.create_suspended(slot.internal_name)
            reason = BlockReason.IDENTITY
            identity = self._host.identity_from_handle(
                created.process_handle, created.pid
            )
            reason = BlockReason.ASSIGNMENT
            assignment_uncertain = True
            self._host.assign_to_job(job, created.process_handle)
            if self._host.is_process_in_job(created.process_handle, job) is not True:
                raise LifecycleError("created process Job membership was not proved")
            assigned = True
            assignment_uncertain = False
            reason = BlockReason.RESUME
            if self._host.resume_thread(created.thread_handle) != 1:
                raise LifecycleError("unexpected suspended-thread resume count")
            reason = BlockReason.THREAD_CLOSE
            self._host.close_thread(created.thread_handle)
            thread_closed = True

            reason = BlockReason.PLAYER_ENUMERATION
            exact_player_seen = False
            for attempt in range(self._readiness_attempts):
                post_launch = self._host.complete_player_snapshot()
                try:
                    observed = post_launch.identities
                finally:
                    post_launch.close()
                if observed == (identity,):
                    exact_player_seen = True
                    break
                if observed:
                    raise LifecycleError("post-launch player identity is not exact")
                if attempt + 1 < self._readiness_attempts:
                    self._readiness_wait(500)
            if not exact_player_seen:
                raise LifecycleError("exact post-launch player did not become ready")

            readiness_complete = False
            for attempt in range(self._readiness_attempts):
                candidate_members: MemberSnapshot | None = None
                candidate_binding: PlayerBinding | None = None
                attempt_failed = False
                try:
                    reason = BlockReason.MEMBERSHIP
                    candidate_members = self._host.stable_job_members(job)
                    if identity not in candidate_members.identities:
                        attempt_failed = True
                    else:
                        reason = BlockReason.WINDOW_BINDING
                        candidate_binding = self._host.bind_exact(
                            identity, slot.display_name, candidate_members
                        )
                        if (
                            type(candidate_binding) is not PlayerBinding
                            or candidate_binding.identity != identity
                        ):
                            attempt_failed = True
                        else:
                            binding = candidate_binding
                            reason = BlockReason.CAPTURE_READINESS
                            self._host.capture_ready(candidate_binding, job)
                except BaseException:  # noqa: BLE001 - bounded transient readiness retry
                    attempt_failed = True
                if not attempt_failed:
                    members = candidate_members
                    binding = candidate_binding
                    readiness_complete = True
                    break
                if candidate_members is not None:
                    candidate_members.close()
                if attempt + 1 < self._readiness_attempts:
                    self._readiness_wait(500)
            if not readiness_complete:
                raise LifecycleError("exact window and capture did not become ready")
        except BaseException as exc:
            self._rollback_start(
                active=active,
                reason=reason,
                job=job,
                created=created,
                assignment_uncertain=assignment_uncertain,
                assigned=assigned,
                thread_closed=thread_closed,
                members=members,
                binding=binding,
            )
            if isinstance(exc, LifecycleError):
                raise
            message = reason.value.replace("_", " ").lower()
            raise LifecycleError(f"{message} failed") from exc

        if job is None or created is None or members is None or binding is None:
            raise LifecycleError("owned launch result is incomplete")
        self._owned = _OwnedRunV2(
            account_key,
            configuration_generation,
            slot,
            visit_nonce,
            job,
            created.process_handle,
            members,
            binding,
        )
        return binding

    def stop_admitted(self, binding: PlayerBinding, proof: None) -> StopRecord:
        """Stop one owned admitted visit and advance only the stable tie cursor."""
        if proof is not None:
            raise LifecycleError("schema-v2 lifecycle accepts no graceful-close proof")
        owned = self._owned
        if (
            owned is None
            or type(binding) is not PlayerBinding
            or binding != owned.binding
        ):
            raise LifecycleError("stop binding does not match the owned admitted run")

        try:
            member_count = SchemaV2LifecycleSupervisor._prove_stopped_and_close_owned(
                self, owned, binding
            )
        except BaseException as exc:
            blocked = ActiveV2(
                owned.configuration_generation,
                owned.account_key,
                owned.slot.index,
                owned.run_nonce,
                BlockReason.STOP_PROOF,
            )
            try:
                self._state.commit(blocked)
            except BaseException:  # noqa: BLE001 - previous ACTIVE remains fail-closed
                pass
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError("owned admitted stop proof failed") from exc

        ready = ReadyV2(owned.configuration_generation, owned.account_key)
        committed = self._state.commit(ready)
        if type(committed) is not ReadyV2 or committed != ready:
            raise LifecycleError("schema-v2 READY state was not durably committed")
        self._owned = None
        return StopRecord(
            owned.slot.index,
            owned.run_nonce,
            member_count,
            2,
            True,
        )

    def emergency_stop_admitted(self, binding: PlayerBinding) -> StopRecord:
        """Stop an owned run without exposing READY after successor-plan failure."""
        owned = self._owned
        if (
            owned is None
            or type(binding) is not PlayerBinding
            or binding != owned.binding
        ):
            raise LifecycleError("emergency stop binding does not match the owned admitted run")

        try:
            member_count = SchemaV2LifecycleSupervisor._prove_stopped_and_close_owned(
                self, owned, binding
            )
        except BaseException as exc:
            try:
                self._state.commit(
                    ActiveV2(
                        owned.configuration_generation,
                        owned.account_key,
                        owned.slot.index,
                        owned.run_nonce,
                        BlockReason.STOP_PROOF,
                    )
                )
            except BaseException:  # noqa: BLE001 - prior ACTIVE remains fail-closed
                pass
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError("emergency owned stop proof failed") from exc

        blocked = ActiveV2(
            owned.configuration_generation,
            owned.account_key,
            owned.slot.index,
            owned.run_nonce,
            BlockReason.SUCCESSOR_PLAN,
        )
        committed = self._state.commit(blocked)
        if type(committed) is not ActiveV2 or committed != blocked:
            raise LifecycleError("emergency blocked ACTIVE was not durably committed")
        self._owned = None
        return StopRecord(
            owned.slot.index,
            owned.run_nonce,
            member_count,
            2,
            True,
        )

    def _prove_stopped_and_close_owned(
        self, owned: _OwnedRunV2, binding: PlayerBinding
    ) -> int:
        members: MemberSnapshot | None = None
        membership_proved = False
        member_count = 0
        try:
            members = self._host.stable_job_members(owned.job)
            member_count = len(members.identities)
            membership_proved = (
                member_count >= 1 and owned.binding.identity in members.identities
            )
        except BaseException:  # noqa: BLE001 - termination remains authoritative
            membership_proved = False

        self._host.terminate_job(owned.job)
        if self._host.wait_process(owned.process, 30_000) is not True:
            raise LifecycleError("owned admitted process did not signal")
        if self._host.job_active_count(owned.job) != 0:
            raise LifecycleError("owned admitted Job did not become empty")
        if self._host.is_window(binding.root_hwnd) or self._host.is_window(
            binding.render_hwnd
        ):
            raise LifecycleError("owned admitted HWND remained after stop")
        players = self._host.complete_player_snapshot()
        try:
            if players.identities:
                raise LifecycleError("a BlueStacks player remained after admitted stop")
        finally:
            players.close()
        if members is not None:
            members.close()
        owned.members.close()
        self._host.close_handle(owned.process)
        self._host.close_handle(owned.job)
        if not membership_proved:
            raise LifecycleError("fresh owned Job membership was not proved")
        return member_count

    def _commit_blocked(self, active: ActiveV2, reason: BlockReason) -> None:
        blocked = ActiveV2(
            active.configuration_generation,
            active.account_key,
            active.slot_index,
            active.run_nonce,
            reason,
        )
        try:
            committed = self._state.commit(blocked)
        except BaseException as exc:
            raise LifecycleError(
                "blocked lifecycle state could not be committed"
            ) from exc
        if type(committed) is not ActiveV2 or committed != blocked:
            raise LifecycleError("blocked lifecycle state was not committed exactly")

    def _rollback_start(
        self,
        *,
        active: ActiveV2,
        reason: BlockReason,
        job: object | None,
        created: CreatedProcess | None,
        assignment_uncertain: bool,
        assigned: bool,
        thread_closed: bool,
        members: MemberSnapshot | None,
        binding: PlayerBinding | None,
    ) -> None:
        try:
            if created is not None:
                if assigned:
                    if job is None:
                        raise LifecycleError("assigned rollback lost its Job handle")
                    self._host.terminate_job(job)
                elif assignment_uncertain:
                    if job is not None:
                        try:
                            self._host.terminate_job(job)
                        except BaseException:  # noqa: BLE001 - retained handle remains authoritative
                            pass
                    try:
                        self._host.terminate_retained_process(created.process_handle)
                    except BaseException:  # noqa: BLE001 - proofs below decide success
                        pass
                else:
                    self._host.terminate_retained_process(created.process_handle)
                if self._host.wait_process(created.process_handle, 30_000) is not True:
                    raise LifecycleError("rollback process wait failed")
                if (assigned or assignment_uncertain) and (
                    job is None or self._host.job_active_count(job) != 0
                ):
                    raise LifecycleError("rollback Job did not become empty")
                if binding is not None and (
                    self._host.is_window(binding.root_hwnd)
                    or self._host.is_window(binding.render_hwnd)
                ):
                    raise LifecycleError("rollback HWND invalidation failed")
                players = self._host.complete_player_snapshot()
                try:
                    if players.identities:
                        raise LifecycleError("rollback player absence was not proved")
                finally:
                    players.close()
                if not thread_closed:
                    self._host.close_thread(created.thread_handle)
            if members is not None:
                members.close()
            if created is not None:
                self._host.close_handle(created.process_handle)
            if job is not None:
                self._host.close_handle(job)
            self._commit_blocked(active, reason)
        except BaseException as exc:
            try:
                self._state.commit(
                    ActiveV2(
                        active.configuration_generation,
                        active.account_key,
                        active.slot_index,
                        active.run_nonce,
                        BlockReason.ROLLBACK_UNPROVED,
                    )
                )
            except BaseException:  # noqa: BLE001 - previous ACTIVE remains fail-closed
                pass
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError("rollback proof failed") from exc
