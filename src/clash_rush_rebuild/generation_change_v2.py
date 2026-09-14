"""Fail-closed schema-v2 configuration generation changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    ConfigurationGeneration,
    SchemaV2Configuration,
)


class GenerationChangeError(ValueError):
    """A schema-v2 configuration generation change is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class AccountGenerationState:
    """Open source-generation state that can make an account unsafe to move."""

    admitted_count: int = 0
    uncertain_count: int = 0
    blocked_count: int = 0
    unarchived_count: int = 0

    def __post_init__(self) -> None:
        values = (
            self.admitted_count,
            self.uncertain_count,
            self.blocked_count,
            self.unarchived_count,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise GenerationChangeError(
                "account generation state counts must be nonnegative integers"
            )

    @property
    def is_safe(self) -> bool:
        return (
            self.admitted_count == 0
            and self.uncertain_count == 0
            and self.blocked_count == 0
            and self.unarchived_count == 0
        )


class GenerationChangeProbePort(Protocol):
    """Read-only source-generation proofs while the lifecycle mutex is held."""

    def mutex_owned(self) -> bool: ...

    def account_generation_state(
        self,
        account_key: AccountKey,
        generation: ConfigurationGeneration,
    ) -> AccountGenerationState: ...


@dataclass(frozen=True, slots=True)
class ConfigurationGenerationChange:
    """One immutable replacement and the source accounts it affects."""

    source: SchemaV2Configuration
    target: SchemaV2Configuration
    affected_source_account_keys: tuple[AccountKey, ...]

    def __post_init__(self) -> None:
        if (
            type(self.source) is not SchemaV2Configuration
            or type(self.target) is not SchemaV2Configuration
        ):
            raise GenerationChangeError("exact schema-v2 configurations are required")
        if self.source.accounts == self.target.accounts:
            raise GenerationChangeError(
                "generation change requires changed account content"
            )
        if self.source.generation == self.target.generation:
            raise GenerationChangeError(
                "changed configuration requires a fresh generation"
            )
        if type(self.affected_source_account_keys) is not tuple:
            raise GenerationChangeError("affected account keys must be an exact tuple")
        target_indexes = {
            account.key: index for index, account in enumerate(self.target.accounts)
        }
        expected = tuple(
            account.key
            for index, account in enumerate(self.source.accounts)
            if target_indexes.get(account.key) != index
        )
        if self.affected_source_account_keys != expected:
            raise GenerationChangeError(
                "affected source account keys do not match the configuration change"
            )


def plan_generation_change(
    source: SchemaV2Configuration,
    target: SchemaV2Configuration,
) -> ConfigurationGenerationChange:
    """Validate a changed configuration and identify moved or removed accounts."""
    if type(source) is not SchemaV2Configuration or type(target) is not SchemaV2Configuration:
        raise GenerationChangeError("exact schema-v2 configurations are required")
    target_indexes = {account.key: index for index, account in enumerate(target.accounts)}
    affected = tuple(
        account.key
        for index, account in enumerate(source.accounts)
        if target_indexes.get(account.key) != index
    )
    return ConfigurationGenerationChange(source, target, affected)


def require_safe_generation_activation(
    change: ConfigurationGenerationChange,
    probes: GenerationChangeProbePort,
) -> ConfigurationGenerationChange:
    """Reject activation unless every affected source account is fully archived."""
    if type(change) is not ConfigurationGenerationChange:
        raise GenerationChangeError("exact generation change plan required")

    def require_mutex() -> None:
        try:
            mutex_owned = probes.mutex_owned()
        except BaseException as exc:
            raise GenerationChangeError(
                "generation activation mutex ownership is unprovable"
            ) from exc
        if type(mutex_owned) is not bool or mutex_owned is not True:
            raise GenerationChangeError(
                "generation activation mutex ownership is unprovable"
            )

    require_mutex()

    for account_key in change.affected_source_account_keys:
        try:
            state = probes.account_generation_state(
                account_key,
                change.source.generation,
            )
        except BaseException as exc:
            raise GenerationChangeError(
                "affected account generation state is unprovable"
            ) from exc
        if type(state) is not AccountGenerationState:
            raise GenerationChangeError(
                "affected account generation state is unprovable"
            )
        if not state.is_safe:
            raise GenerationChangeError(
                "affected account has unsafe account state for generation activation"
            )
    require_mutex()
    return change
