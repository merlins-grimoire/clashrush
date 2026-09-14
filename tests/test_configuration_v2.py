from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from clash_rush_rebuild.configuration_v2 import (
    AccountKey,
    AccountRecord,
    ConfigurationValidationError,
    ConfigurationGeneration,
    SchemaV2Configuration,
)


def _account(index: int) -> AccountRecord:
    return AccountRecord(key=AccountKey(f"account-{index}"))


def _configuration(size: int) -> SchemaV2Configuration:
    return SchemaV2Configuration(
        generation=ConfigurationGeneration("0123456789abcdef0123456789abcdef"),
        accounts=tuple(_account(index) for index in range(size)),
    )


@pytest.mark.parametrize("size", range(1, 11))
def test_configuration_derives_supported_cardinality_from_accounts(size: int) -> None:
    configuration = _configuration(size)

    assert configuration.cardinality == size
    assert tuple(account.key.value for account in configuration.accounts) == tuple(
        f"account-{index}" for index in range(size)
    )


def test_configuration_and_nested_values_are_immutable() -> None:
    configuration = _configuration(1)

    with pytest.raises(FrozenInstanceError):
        configuration.accounts = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        configuration.accounts[0].key = AccountKey("replacement")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        configuration.accounts[0].key.value = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "0",
        "account-1",
        "a" * 64,
        "a" + ("-" * 62) + "z",
    ],
)
def test_account_key_accepts_exact_lower_ascii_slug(value: str) -> None:
    assert AccountKey(value).value == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A",
        " account",
        "account ",
        "account_name",
        "account--",
        "-account",
        "account-",
        "áccount",
        "a" * 65,
        1,
        None,
    ],
)
def test_account_key_rejects_malformed_values(value: object) -> None:
    with pytest.raises(ConfigurationValidationError, match="account key"):
        AccountKey(value)  # type: ignore[arg-type]


def test_generation_can_be_created_as_opaque_random_identity() -> None:
    first = ConfigurationGeneration.new()
    second = ConfigurationGeneration.new()

    assert len(first.value) == 32
    assert first.value != second.value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0123456789abcdef0123456789abcde",
        "0123456789abcdef0123456789abcdef0",
        "0123456789ABCDEF0123456789ABCDEF",
        "g123456789abcdef0123456789abcdef",
        1,
        None,
    ],
)
def test_generation_rejects_malformed_values(value: object) -> None:
    with pytest.raises(ConfigurationValidationError, match="generation"):
        ConfigurationGeneration(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("size", [0, 11])
def test_configuration_rejects_unsupported_cardinality(size: int) -> None:
    with pytest.raises(ConfigurationValidationError, match="1 to 10"):
        _configuration(size)


def test_configuration_rejects_duplicate_account_keys() -> None:
    duplicate = _account(0)

    with pytest.raises(ConfigurationValidationError, match="unique"):
        SchemaV2Configuration(
            generation=ConfigurationGeneration("0123456789abcdef0123456789abcdef"),
            accounts=(duplicate, duplicate),
        )


def test_configuration_rejects_mutable_or_untyped_account_collections() -> None:
    generation = ConfigurationGeneration("0123456789abcdef0123456789abcdef")

    with pytest.raises(ConfigurationValidationError, match="tuple"):
        SchemaV2Configuration(generation=generation, accounts=[_account(0)])  # type: ignore[arg-type]
    with pytest.raises(ConfigurationValidationError, match="record"):
        SchemaV2Configuration(generation=generation, accounts=(AccountKey("account-0"),))  # type: ignore[arg-type]


def test_configuration_has_no_independent_count_field() -> None:
    configuration = _configuration(2)

    assert not hasattr(configuration, "count")
    assert not hasattr(configuration, "team_size")


@pytest.mark.parametrize(
    "accounts",
    [
        (_account(1), _account(0)),
        (_account(0),),
        (_account(0), _account(1), _account(2)),
    ],
)
def test_reorder_or_resize_requires_fresh_generation(
    accounts: tuple[AccountRecord, ...],
) -> None:
    configuration = _configuration(2)

    with pytest.raises(ConfigurationValidationError, match="fresh generation"):
        configuration.reconfigured(accounts, generation=configuration.generation)


def test_reconfiguration_accepts_fresh_generation_and_changes_content_hash() -> None:
    configuration = _configuration(2)
    generation = ConfigurationGeneration("fedcba9876543210fedcba9876543210")

    reordered = configuration.reconfigured(
        tuple(reversed(configuration.accounts)), generation=generation
    )

    assert reordered.generation == generation
    assert reordered.content_hash != configuration.content_hash
    assert len(reordered.content_hash.value) == 64


def test_content_hash_is_independent_of_opaque_generation() -> None:
    configuration = _configuration(2)
    replacement = SchemaV2Configuration(
        generation=ConfigurationGeneration("fedcba9876543210fedcba9876543210"),
        accounts=configuration.accounts,
    )

    assert replacement.content_hash == configuration.content_hash
    assert replacement.generation != configuration.generation
