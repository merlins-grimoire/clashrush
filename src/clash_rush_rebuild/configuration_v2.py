"""Immutable schema-v2 configuration values."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass


_ACCOUNT_KEY_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_GENERATION_PATTERN = re.compile(r"[0-9a-f]{32}")
_CONTENT_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class ConfigurationValidationError(ValueError):
    """A schema-v2 configuration value is invalid."""


@dataclass(frozen=True, slots=True)
class AccountKey:
    """Stable account identity."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _ACCOUNT_KEY_PATTERN.fullmatch(self.value) is None:
            raise ConfigurationValidationError("account key must be an exact lower-ASCII slug")


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """One account in configuration order."""

    key: AccountKey

    def __post_init__(self) -> None:
        if type(self.key) is not AccountKey:
            raise ConfigurationValidationError("account record key is invalid")


@dataclass(frozen=True, slots=True)
class ConfigurationGeneration:
    """Opaque identity for one immutable configuration."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _GENERATION_PATTERN.fullmatch(self.value) is None:
            raise ConfigurationValidationError(
                "configuration generation must be exactly 32 lowercase hex characters"
            )

    @classmethod
    def new(cls) -> ConfigurationGeneration:
        """Create a random opaque 128-bit generation identity."""
        return cls(secrets.token_hex(16))


@dataclass(frozen=True, slots=True)
class ConfigurationContentHash:
    """SHA-256 identity of immutable schema-v2 configuration content."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CONTENT_HASH_PATTERN.fullmatch(self.value) is None:
            raise ConfigurationValidationError(
                "configuration content hash must be exactly 64 lowercase hex characters"
            )


@dataclass(frozen=True, slots=True)
class SchemaV2Configuration:
    """An immutable ordered schema-v2 account configuration."""

    generation: ConfigurationGeneration
    accounts: tuple[AccountRecord, ...]

    def __post_init__(self) -> None:
        if type(self.generation) is not ConfigurationGeneration:
            raise ConfigurationValidationError("configuration generation is invalid")
        if type(self.accounts) is not tuple:
            raise ConfigurationValidationError("configuration accounts must be a tuple")
        if not 1 <= len(self.accounts) <= 10:
            raise ConfigurationValidationError(
                "configuration must contain 1 to 10 accounts"
            )
        if any(type(account) is not AccountRecord for account in self.accounts):
            raise ConfigurationValidationError(
                "configuration accounts must contain account records"
            )
        keys = tuple(account.key for account in self.accounts)
        if len(set(keys)) != len(keys):
            raise ConfigurationValidationError("configuration account keys must be unique")

    @property
    def cardinality(self) -> int:
        """Derive account count solely from the account tuple."""
        return len(self.accounts)

    @property
    def content_hash(self) -> ConfigurationContentHash:
        """Hash ordered content separately from the opaque generation identity."""
        canonical = json.dumps(
            {"schema": 2, "accounts": [account.key.value for account in self.accounts]},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return ConfigurationContentHash(hashlib.sha256(canonical).hexdigest())

    def reconfigured(
        self,
        accounts: tuple[AccountRecord, ...],
        *,
        generation: ConfigurationGeneration,
    ) -> SchemaV2Configuration:
        """Validate replacement content and require a fresh identity when it changes."""
        replacement = type(self)(generation=generation, accounts=accounts)
        if replacement.accounts != self.accounts and generation == self.generation:
            raise ConfigurationValidationError(
                "changed configuration requires a fresh generation"
            )
        return replacement
