from __future__ import annotations

import re
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_rush_rebuild.guided_setup as guided_setup

from clash_rush_rebuild.guided_setup import (
    ACCOUNT_REF_PATTERN,
    Installation,
    InstallationAccount,
    InstallationTeam,
    InstallationValidationError,
    ResourceChoices,
    SetupAnswers,
    build_installation,
    decode_installation,
    encode_installation,
    export_synthetic_installation,
    run_guided_setup,
)


def answers() -> SetupAnswers:
    return SetupAnswers(
        installation_name="Synthetic Installation",
        central_url="wss://control.example.invalid/runner",
        discord_bot_secret_ref="credential://clash-rush/discord-bot",
        clash_api_secret_ref="credential://clash-rush/supercell-api",
        guild_id="100000000000000001",
        captain_role_id="100000000000000002",
        fleet_captain_role_id="100000000000000003",
        fleet_captain_user_ids=("100000000000000004",),
        team_key="synthetic-team",
        team_display_name="Synthetic Team",
        category_id="100000000000000005",
        operations_channel_id="100000000000000006",
        runner_secret_ref="credential://clash-rush/runner/synthetic-team",
        captain_user_ids=("100000000000000007",),
        resources=ResourceChoices(
            home_gold=True,
            home_elixir=False,
            home_dark_elixir=True,
            builder_gold=False,
            builder_elixir=True,
        ),
        accounts=(
            (
                "account-one",
                "Synthetic Account One",
                "#SYNTH001",
                "Synthetic Instance One",
                "100000000000000008",
            ),
            (
                "account-two",
                "Synthetic Account Two",
                "#SYNTH002",
                "Synthetic Instance Two",
                "100000000000000009",
            ),
        ),
    )


def guided_responses(account_count: str, count: int) -> tuple[str, ...]:
    prefix = (
        "Private Installation",
        "wss://control.example.invalid/runner",
        "credential://clash-rush/discord-bot",
        "credential://clash-rush/supercell-api",
        "100000000000000001",
        "100000000000000002",
        "100000000000000003",
        "100000000000000004",
        "private-team",
        "Private Team",
        "100000000000000005",
        "100000000000000006",
        "credential://clash-rush/runner/private-team",
        "100000000000000007",
        account_count,
        "no",
        "no",
        "no",
        "no",
        "no",
    )
    accounts = tuple(
        value
        for index in range(1, count + 1)
        for value in (
            f"account-{index:02d}",
            f"Private Account {index:02d}",
            f"#PRIVATE{index:02d}",
            f"Private Instance {index:02d}",
            str(100000000000000007 + index),
        )
    )
    return prefix + accounts


def test_setup_generates_distinct_opaque_account_refs_and_explicit_resources() -> None:
    generated = iter(("0" * 32, "1" * 32))

    installation = build_installation(answers(), account_ref_factory=lambda: next(generated))

    team = installation.teams[0]
    assert tuple(account.account_ref for account in team.accounts) == ("0" * 32, "1" * 32)
    assert all(ACCOUNT_REF_PATTERN.fullmatch(account.account_ref) for account in team.accounts)
    assert team.resources == ResourceChoices(True, False, True, False, True)
    assert not hasattr(installation, "team_size")


def test_closed_toml_round_trip_rejects_unknown_or_implicit_resource_fields() -> None:
    generated = iter(("2" * 32, "3" * 32))
    payload = encode_installation(
        build_installation(answers(), account_ref_factory=lambda: next(generated))
    )

    decoded = decode_installation(payload)
    assert encode_installation(decoded) == payload

    with pytest.raises(InstallationValidationError, match="unknown field"):
        decode_installation(payload + "\nunknown = true\n")
    with pytest.raises(InstallationValidationError, match="builder_elixir_enabled"):
        decode_installation(payload.replace("builder_elixir_enabled = true\n", ""))
    with pytest.raises(InstallationValidationError, match="exact boolean"):
        decode_installation(payload.replace("home_gold_enabled = true", 'home_gold_enabled = "true"'))


def test_closed_toml_round_trip_preserves_non_bmp_operator_labels() -> None:
    supplied = answers()
    unicode_accounts = (
        (
            supplied.accounts[0][0],
            "Synthetic Account 🚀",
            supplied.accounts[0][2],
            supplied.accounts[0][3],
            supplied.accounts[0][4],
        ),
        supplied.accounts[1],
    )
    unicode_answers = SetupAnswers(
        supplied.installation_name,
        supplied.central_url,
        supplied.discord_bot_secret_ref,
        supplied.clash_api_secret_ref,
        supplied.guild_id,
        supplied.captain_role_id,
        supplied.fleet_captain_role_id,
        supplied.fleet_captain_user_ids,
        supplied.team_key,
        supplied.team_display_name,
        supplied.category_id,
        supplied.operations_channel_id,
        supplied.runner_secret_ref,
        supplied.captain_user_ids,
        supplied.resources,
        unicode_accounts,
    )
    generated = iter(("a" * 32, "b" * 32))

    installation = build_installation(
        unicode_answers, account_ref_factory=generated.__next__
    )
    payload = encode_installation(installation)

    assert decode_installation(payload) == installation
    assert "🚀" in payload


@pytest.mark.parametrize("invalid_label", ["label\x7f", "label\ud800"])
def test_build_rejects_labels_that_toml_cannot_represent(invalid_label: str) -> None:
    supplied = answers()
    malformed = SetupAnswers(
        invalid_label,
        supplied.central_url,
        supplied.discord_bot_secret_ref,
        supplied.clash_api_secret_ref,
        supplied.guild_id,
        supplied.captain_role_id,
        supplied.fleet_captain_role_id,
        supplied.fleet_captain_user_ids,
        supplied.team_key,
        supplied.team_display_name,
        supplied.category_id,
        supplied.operations_channel_id,
        supplied.runner_secret_ref,
        supplied.captain_user_ids,
        supplied.resources,
        supplied.accounts,
    )

    with pytest.raises(InstallationValidationError, match="installation name"):
        build_installation(
            malformed,
            account_ref_factory=iter(("a" * 32, "b" * 32)).__next__,
        )


def test_generated_account_refs_are_not_accepted_from_setup_answers() -> None:
    supplied = answers()
    assert all(len(account) == 5 for account in supplied.accounts)

    with pytest.raises(InstallationValidationError, match="account reference"):
        build_installation(supplied, account_ref_factory=lambda: "account-one")


def test_build_rejects_duplicate_fleet_captains_before_encoding() -> None:
    supplied = answers()
    duplicated = SetupAnswers(
        supplied.installation_name,
        supplied.central_url,
        supplied.discord_bot_secret_ref,
        supplied.clash_api_secret_ref,
        supplied.guild_id,
        supplied.captain_role_id,
        supplied.fleet_captain_role_id,
        (supplied.fleet_captain_user_ids[0],) * 2,
        supplied.team_key,
        supplied.team_display_name,
        supplied.category_id,
        supplied.operations_channel_id,
        supplied.runner_secret_ref,
        supplied.captain_user_ids,
        supplied.resources,
        supplied.accounts,
    )

    with pytest.raises(InstallationValidationError, match="fleet captain user IDs must be unique"):
        build_installation(
            duplicated,
            account_ref_factory=iter(("7" * 32, "8" * 32)).__next__,
        )


@pytest.mark.parametrize(
    "central_url",
    [
        "https://",
        "https://bad host.invalid",
        "https://example.invalid:/runner",
        "https://example.invalid\\evil",
    ],
)
def test_build_rejects_a_malformed_central_url(central_url: str) -> None:
    supplied = answers()
    malformed = SetupAnswers(
        supplied.installation_name,
        central_url,
        supplied.discord_bot_secret_ref,
        supplied.clash_api_secret_ref,
        supplied.guild_id,
        supplied.captain_role_id,
        supplied.fleet_captain_role_id,
        supplied.fleet_captain_user_ids,
        supplied.team_key,
        supplied.team_display_name,
        supplied.category_id,
        supplied.operations_channel_id,
        supplied.runner_secret_ref,
        supplied.captain_user_ids,
        supplied.resources,
        supplied.accounts,
    )

    with pytest.raises(InstallationValidationError, match="central URL"):
        build_installation(
            malformed,
            account_ref_factory=iter(("7" * 32, "8" * 32)).__next__,
        )


def test_build_rejects_duplicate_player_tags() -> None:
    supplied = answers()
    duplicate_tag_accounts = (
        supplied.accounts[0],
        (
            supplied.accounts[1][0],
            supplied.accounts[1][1],
            supplied.accounts[0][2],
            supplied.accounts[1][3],
            supplied.accounts[1][4],
        ),
    )
    malformed = replace(supplied, accounts=duplicate_tag_accounts)

    with pytest.raises(InstallationValidationError, match="player tags must be unique"):
        build_installation(
            malformed,
            account_ref_factory=iter(("7" * 32, "8" * 32)).__next__,
        )


def test_build_rejects_an_account_channel_that_is_not_private_to_the_account() -> None:
    supplied = answers()
    shared_channel_accounts = (
        (
            supplied.accounts[0][0],
            supplied.accounts[0][1],
            supplied.accounts[0][2],
            supplied.accounts[0][3],
            supplied.operations_channel_id,
        ),
        supplied.accounts[1],
    )
    malformed = replace(supplied, accounts=shared_channel_accounts)

    with pytest.raises(InstallationValidationError, match="channel IDs must be distinct"):
        build_installation(
            malformed,
            account_ref_factory=iter(("7" * 32, "8" * 32)).__next__,
        )


def test_installation_rejects_account_refs_reused_across_teams() -> None:
    generated = iter(("7" * 32, "8" * 32))
    installation = build_installation(
        answers(), account_ref_factory=generated.__next__
    )
    first_team = installation.teams[0]
    first_account = first_team.accounts[0]
    second_account = InstallationAccount(
        "account-three",
        first_account.account_ref,
        "Synthetic Account Three",
        "#SYNTH003",
        "Synthetic Instance Three",
        "100000000000000010",
    )
    second_team = InstallationTeam(
        "synthetic-team-two",
        "Synthetic Team Two",
        "100000000000000011",
        "100000000000000012",
        "credential://clash-rush/runner/synthetic-team-two",
        ("100000000000000013",),
        first_team.resources,
        (second_account,),
    )

    with pytest.raises(InstallationValidationError, match="account references must be globally unique"):
        Installation(
            installation.schema,
            installation.installation_name,
            installation.central_url,
            installation.discord_bot_secret_ref,
            installation.clash_api_secret_ref,
            installation.guild_id,
            installation.captain_role_id,
            installation.fleet_captain_role_id,
            installation.fleet_captain_user_ids,
            (first_team, second_team),
        )


def test_installation_rejects_channel_ids_reused_across_teams() -> None:
    generated = iter(("7" * 32, "8" * 32))
    installation = build_installation(
        answers(), account_ref_factory=generated.__next__
    )
    first_team = installation.teams[0]
    second_team = InstallationTeam(
        "synthetic-team-two",
        "Synthetic Team Two",
        "100000000000000011",
        "100000000000000012",
        "credential://clash-rush/runner/synthetic-team-two",
        ("100000000000000013",),
        first_team.resources,
        (
            InstallationAccount(
                "account-three",
                "9" * 32,
                "Synthetic Account Three",
                "#SYNTH003",
                "Synthetic Instance Three",
                first_team.category_id,
            ),
        ),
    )

    with pytest.raises(InstallationValidationError, match="channel IDs must be globally distinct"):
        replace(installation, teams=(first_team, second_team))


def test_synthetic_export_is_valid_closed_configuration_without_placeholders() -> None:
    payload = export_synthetic_installation()
    installation = decode_installation(payload)

    assert installation.schema == 2
    assert len(installation.teams) == 1
    assert len(installation.teams[0].accounts) == 5
    assert "HERE" not in payload
    assert "GENERATED" not in payload
    assert re.search(r'account_ref = "[0-9a-f]{32}"', payload)


def test_guided_setup_writes_only_the_ignored_private_installation_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    responses = iter(
        (
            "Private Installation",
            "wss://control.example.invalid/runner",
            "credential://clash-rush/discord-bot",
            "credential://clash-rush/supercell-api",
            "100000000000000001",
            "100000000000000002",
            "100000000000000003",
            "100000000000000004",
            "private-team",
            "Private Team",
            "100000000000000005",
            "100000000000000006",
            "credential://clash-rush/runner/private-team",
            "100000000000000007",
            "2",
            "yes",
            "no",
            "yes",
            "no",
            "yes",
            "account-one",
            "Private Account One",
            "#PRIVATE01",
            "Private Instance One",
            "100000000000000008",
            "account-two",
            "Private Account Two",
            "#PRIVATE02",
            "Private Instance Two",
            "100000000000000009",
        )
    )

    result = run_guided_setup(
        project,
        prompt=lambda _label: next(responses),
        account_ref_factory=iter(("4" * 32, "5" * 32)).__next__,
    )

    expected = project / "private" / "installation.toml"
    assert result == expected
    assert expected.is_file()
    assert decode_installation(expected.read_text(encoding="utf-8")).teams[0].accounts[0].account_ref == "4" * 32
    assert sorted(path.relative_to(project).as_posix() for path in project.rglob("*")) == [
        "private",
        "private/installation.toml",
    ]

    with pytest.raises(InstallationValidationError, match="already exists"):
        run_guided_setup(project, prompt=lambda _label: "unused")


def test_guided_setup_defaults_to_five_accounts_without_source_edits(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    responses = iter(guided_responses("", 5))

    result = run_guided_setup(
        project,
        prompt=lambda _label: next(responses),
        account_ref_factory=iter(f"{index:032x}" for index in range(1, 6)).__next__,
    )

    installation = decode_installation(result.read_text(encoding="utf-8"))
    assert len(installation.teams[0].accounts) == 5


def test_guided_setup_accepts_an_existing_safe_private_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    existing = private / "other-private-state"
    existing.write_text("keep", encoding="utf-8")
    responses = iter(guided_responses("1", 1))

    result = run_guided_setup(
        project,
        prompt=lambda _label: next(responses),
        account_ref_factory=lambda: "6" * 32,
    )

    assert result == private / "installation.toml"
    assert existing.read_text(encoding="utf-8") == "keep"


def test_guided_setup_rejects_a_destination_reported_as_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    destination = private / "installation.toml"
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: (
            SimpleNamespace(
                st_mode=stat.S_IFLNK,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
            if self == destination
            else original_lstat(self)
        ),
    )

    with pytest.raises(InstallationValidationError, match="unsafe"):
        run_guided_setup(project, prompt=lambda _label: "unused")


def test_guided_setup_rejects_a_private_directory_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    private = project / "private"
    private.mkdir(parents=True)
    original_lstat = Path.lstat
    private_stat = original_lstat(private)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: (
            SimpleNamespace(
                st_mode=private_stat.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
            if self == private
            else original_lstat(self)
        ),
    )

    with pytest.raises(InstallationValidationError, match="unsafe"):
        run_guided_setup(project, prompt=lambda _label: "unused")


def test_guided_setup_does_not_delete_a_file_created_during_prompting(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    destination = project / "private" / "installation.toml"
    responses = iter(guided_responses("1", 1))

    def racing_factory() -> str:
        destination.parent.mkdir()
        destination.write_text("racer-owned sentinel", encoding="utf-8")
        return "9" * 32

    with pytest.raises(FileExistsError):
        run_guided_setup(
            project,
            prompt=lambda _label: next(responses),
            account_ref_factory=racing_factory,
        )

    assert destination.read_text(encoding="utf-8") == "racer-owned sentinel"
    assert destination.parent.is_dir()


def test_guided_setup_does_not_delete_a_same_path_replacement_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    destination = project / "private" / "installation.toml"
    responses = iter(guided_responses("1", 1))
    original_open = Path.open

    class FaultingStream:
        def __enter__(self):
            return self

        def write(self, _payload: str) -> None:
            raise RuntimeError("forced write failure")

        def __exit__(self, _exc_type, _exc, _traceback) -> bool:
            destination.unlink()
            destination.write_text("racer replacement", encoding="utf-8")
            return False

    def fake_open(self, *args, **kwargs):
        if self == destination and args and args[0] == "x":
            with original_open(self, *args, **kwargs):
                pass
            return FaultingStream()
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(RuntimeError, match="forced write failure"):
        run_guided_setup(
            project,
            prompt=lambda _label: next(responses),
            account_ref_factory=lambda: "c" * 32,
        )

    assert destination.read_text(encoding="utf-8") == "racer replacement"


def test_guided_setup_holds_the_private_directory_against_a_junction_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    private = project / "private"
    destination = private / "installation.toml"
    outside = tmp_path / "outside"
    outside.mkdir()
    responses = iter(guided_responses("1", 1))
    original_check = guided_setup._is_reparse_point
    swap_blocked = False

    def swap_before_destination_check(path: Path) -> bool:
        nonlocal swap_blocked
        if path == destination and private.is_dir() and not destination.exists():
            try:
                private.rmdir()
            except OSError:
                swap_blocked = True
            else:
                created = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(private), str(outside)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                assert created.returncode == 0
        return original_check(path)

    monkeypatch.setattr(guided_setup, "_is_reparse_point", swap_before_destination_check)

    result = run_guided_setup(
        project,
        prompt=lambda _label: next(responses),
        account_ref_factory=lambda: "d" * 32,
    )

    assert swap_blocked is True
    assert result.is_file()
    assert not (outside / "installation.toml").exists()


def test_guided_setup_does_not_revisit_the_private_path_after_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    private = project / "private"
    destination = private / "installation.toml"
    responses = iter(guided_responses("1", 1))
    original_open = Path.open

    def fail_destination_open(self, *args, **kwargs):
        if self == destination and args and args[0] == "x":
            raise PermissionError("forced destination-open failure")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_destination_open)

    with pytest.raises(PermissionError, match="forced destination-open failure"):
        run_guided_setup(
            project,
            prompt=lambda _label: next(responses),
            account_ref_factory=lambda: "e" * 32,
        )

    assert private.is_dir()
    assert not destination.exists()
