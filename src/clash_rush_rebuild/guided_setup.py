"""Closed, guided installation configuration generation."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import stat
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .win32_state_io import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_OPEN_REPARSE_POINT,
    GENERIC_READ,
    OPEN_EXISTING,
    NativeWin32StateApi,
)


ACCOUNT_REF_PATTERN = re.compile(r"[0-9a-f]{32}")
_SLUG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_ID_PATTERN = re.compile(r"[0-9]{17,20}")
_SECRET_REF_PATTERN = re.compile(r"credential://[A-Za-z0-9][A-Za-z0-9._/-]{0,126}")
_PLAYER_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,15}")
_HOSTNAME_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


class InstallationValidationError(ValueError):
    """Installation input or persisted content is outside the closed schema."""


def _exact_text(value: object, label: str, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise InstallationValidationError(f"{label} must be exact bounded text")
    return value


def _pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise InstallationValidationError(f"{label} is invalid")
    return value


def _central_url(value: object) -> str:
    text = _exact_text(value, "central URL", maximum=2048)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        raise InstallationValidationError("central URL is invalid") from None
    host = parsed.hostname
    if host is None:
        raise InstallationValidationError("central URL is invalid")
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise InstallationValidationError("central URL is invalid") from None
        expected_authority = f"[{host}]"
    else:
        if _HOSTNAME_PATTERN.fullmatch(host) is None:
            raise InstallationValidationError("central URL is invalid")
        expected_authority = host
    if port is not None:
        expected_authority = f"{expected_authority}:{port}"
    if (
        parsed.scheme not in {"https", "wss"}
        or any(character.isspace() for character in text)
        or "\\" in text
        or "%" in parsed.netloc
        or parsed.netloc.lower() != expected_authority.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise InstallationValidationError("central URL is invalid")
    return text


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class _PrivateDirectoryLease:
    """Retain a no-delete-share directory handle across destination creation."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._api: NativeWin32StateApi | None = None
        self._handle: object | None = None

    def __enter__(self) -> None:
        try:
            api = NativeWin32StateApi()
            handle = api.create_file(
                str(self._path),
                GENERIC_READ,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                OPEN_EXISTING,
                _FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            )
            if handle is None or handle == 0:
                raise OSError
            self._api = api
            self._handle = handle
            if (
                api.handle_attributes(handle) & FILE_ATTRIBUTE_REPARSE_POINT
                or _path_key(api.final_path(handle)) != _path_key(self._path)
            ):
                raise OSError
        except BaseException:
            if self._api is not None and self._handle is not None:
                self._api.close_handle(self._handle)
                self._handle = None
            raise InstallationValidationError("private path locking failed") from None

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        api = self._api
        handle = self._handle
        self._handle = None
        if api is None or handle is None or api.close_handle(handle) is not True:
            raise InstallationValidationError("private path locking failed")


def _closed_table(value: object, keys: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise InstallationValidationError(f"{label} must be a table")
    unknown = set(value) - keys
    if unknown:
        raise InstallationValidationError(f"{label} contains unknown field")
    missing = keys - set(value)
    if missing:
        raise InstallationValidationError(f"{label} missing {sorted(missing)[0]}")
    return value


def _exact_string_tuple(value: object, label: str) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise InstallationValidationError(f"{label} must be a non-empty array")
    result = tuple(_pattern(item, _ID_PATTERN, label) for item in value)
    if len(set(result)) != len(result):
        raise InstallationValidationError(f"{label} must be unique")
    return result


@dataclass(frozen=True, slots=True)
class ResourceChoices:
    """Five independent resource-class choices; there is no fallback relation."""

    home_gold: bool
    home_elixir: bool
    home_dark_elixir: bool
    builder_gold: bool
    builder_elixir: bool

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if type(getattr(self, name)) is not bool:
                raise InstallationValidationError(f"{name}_enabled must be an exact boolean")


@dataclass(frozen=True, slots=True)
class InstallationAccount:
    key: str
    account_ref: str
    account_name: str
    player_tag: str
    bluestacks_display_name: str
    discord_channel_id: str

    def __post_init__(self) -> None:
        _pattern(self.key, _SLUG_PATTERN, "account key")
        _pattern(self.account_ref, ACCOUNT_REF_PATTERN, "account reference")
        _exact_text(self.account_name, "account name")
        _pattern(self.player_tag, _PLAYER_TAG_PATTERN, "player tag")
        _exact_text(self.bluestacks_display_name, "BlueStacks display name")
        _pattern(self.discord_channel_id, _ID_PATTERN, "account channel ID")


@dataclass(frozen=True, slots=True)
class InstallationTeam:
    key: str
    display_name: str
    category_id: str
    operations_channel_id: str
    runner_secret_ref: str
    captain_user_ids: tuple[str, ...]
    resources: ResourceChoices
    accounts: tuple[InstallationAccount, ...]

    def __post_init__(self) -> None:
        _pattern(self.key, _SLUG_PATTERN, "team key")
        _exact_text(self.display_name, "team display name")
        _pattern(self.category_id, _ID_PATTERN, "category ID")
        _pattern(self.operations_channel_id, _ID_PATTERN, "operations channel ID")
        _pattern(self.runner_secret_ref, _SECRET_REF_PATTERN, "runner secret reference")
        if type(self.captain_user_ids) is not tuple or not self.captain_user_ids:
            raise InstallationValidationError("captain user IDs must be a non-empty tuple")
        if any(type(value) is not str or _ID_PATTERN.fullmatch(value) is None for value in self.captain_user_ids):
            raise InstallationValidationError("captain user ID is invalid")
        if len(set(self.captain_user_ids)) != len(self.captain_user_ids):
            raise InstallationValidationError("captain user IDs must be unique")
        if type(self.resources) is not ResourceChoices:
            raise InstallationValidationError("resources are invalid")
        if type(self.accounts) is not tuple or not 1 <= len(self.accounts) <= 10:
            raise InstallationValidationError("accounts must contain 1 to 10 entries")
        if any(type(account) is not InstallationAccount for account in self.accounts):
            raise InstallationValidationError("accounts contain an invalid entry")
        for attribute, label in (
            ("key", "account keys"),
            ("account_ref", "account references"),
            ("player_tag", "player tags"),
            ("bluestacks_display_name", "BlueStacks display names"),
            ("discord_channel_id", "account channel IDs"),
        ):
            values = tuple(getattr(account, attribute) for account in self.accounts)
            if len(set(values)) != len(values):
                raise InstallationValidationError(f"{label} must be unique")
        channel_ids = (
            self.category_id,
            self.operations_channel_id,
            *(account.discord_channel_id for account in self.accounts),
        )
        if len(set(channel_ids)) != len(channel_ids):
            raise InstallationValidationError("channel IDs must be distinct")


@dataclass(frozen=True, slots=True)
class Installation:
    schema: int
    installation_name: str
    central_url: str
    discord_bot_secret_ref: str
    clash_api_secret_ref: str
    guild_id: str
    captain_role_id: str
    fleet_captain_role_id: str
    fleet_captain_user_ids: tuple[str, ...]
    teams: tuple[InstallationTeam, ...]

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 2:
            raise InstallationValidationError("schema must be exactly 2")
        _exact_text(self.installation_name, "installation name")
        _central_url(self.central_url)
        _pattern(self.discord_bot_secret_ref, _SECRET_REF_PATTERN, "Discord bot secret reference")
        _pattern(self.clash_api_secret_ref, _SECRET_REF_PATTERN, "Clash API secret reference")
        _pattern(self.guild_id, _ID_PATTERN, "guild ID")
        _pattern(self.captain_role_id, _ID_PATTERN, "captain role ID")
        _pattern(self.fleet_captain_role_id, _ID_PATTERN, "fleet captain role ID")
        if type(self.fleet_captain_user_ids) is not tuple or not self.fleet_captain_user_ids:
            raise InstallationValidationError("fleet captain user IDs must be a non-empty tuple")
        if any(type(value) is not str or _ID_PATTERN.fullmatch(value) is None for value in self.fleet_captain_user_ids):
            raise InstallationValidationError("fleet captain user ID is invalid")
        if len(set(self.fleet_captain_user_ids)) != len(self.fleet_captain_user_ids):
            raise InstallationValidationError("fleet captain user IDs must be unique")
        if type(self.teams) is not tuple or not self.teams:
            raise InstallationValidationError("teams must be a non-empty tuple")
        if any(type(team) is not InstallationTeam for team in self.teams):
            raise InstallationValidationError("teams contain an invalid entry")
        if len({team.key for team in self.teams}) != len(self.teams):
            raise InstallationValidationError("team keys must be unique")
        all_accounts = tuple(
            account for team in self.teams for account in team.accounts
        )
        for attribute, label in (
            ("account_ref", "account references"),
            ("bluestacks_display_name", "BlueStacks display names"),
            ("discord_channel_id", "account channel IDs"),
        ):
            values = tuple(getattr(account, attribute) for account in all_accounts)
            if len(set(values)) != len(values):
                raise InstallationValidationError(f"{label} must be globally unique")
        all_channel_ids = tuple(
            channel_id
            for team in self.teams
            for channel_id in (
                team.category_id,
                team.operations_channel_id,
                *(account.discord_channel_id for account in team.accounts),
            )
        )
        if len(set(all_channel_ids)) != len(all_channel_ids):
            raise InstallationValidationError("channel IDs must be globally distinct")


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """Validated guided answers before Setup creates opaque account references."""

    installation_name: str
    central_url: str
    discord_bot_secret_ref: str
    clash_api_secret_ref: str
    guild_id: str
    captain_role_id: str
    fleet_captain_role_id: str
    fleet_captain_user_ids: tuple[str, ...]
    team_key: str
    team_display_name: str
    category_id: str
    operations_channel_id: str
    runner_secret_ref: str
    captain_user_ids: tuple[str, ...]
    resources: ResourceChoices
    accounts: tuple[tuple[str, str, str, str, str], ...]


def build_installation(
    answers: SetupAnswers,
    *,
    account_ref_factory: Callable[[], str] = lambda: secrets.token_hex(16),
) -> Installation:
    """Build one closed draft and generate each account reference internally."""
    if type(answers) is not SetupAnswers:
        raise InstallationValidationError("setup answers are invalid")
    if type(answers.accounts) is not tuple or not 1 <= len(answers.accounts) <= 10:
        raise InstallationValidationError("setup requires 1 to 10 accounts")
    accounts: list[InstallationAccount] = []
    for fields in answers.accounts:
        if type(fields) is not tuple or len(fields) != 5:
            raise InstallationValidationError("setup account answers are invalid")
        reference = account_ref_factory()
        accounts.append(InstallationAccount(*fields[:1], reference, *fields[1:]))
    team = InstallationTeam(
        answers.team_key,
        answers.team_display_name,
        answers.category_id,
        answers.operations_channel_id,
        answers.runner_secret_ref,
        answers.captain_user_ids,
        answers.resources,
        tuple(accounts),
    )
    return Installation(
        2,
        answers.installation_name,
        answers.central_url,
        answers.discord_bot_secret_ref,
        answers.clash_api_secret_ref,
        answers.guild_id,
        answers.captain_role_id,
        answers.fleet_captain_role_id,
        answers.fleet_captain_user_ids,
        (team,),
    )


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_quoted(value) for value in values) + "]"


def encode_installation(installation: Installation) -> str:
    """Encode the closed schema as deterministic TOML."""
    if type(installation) is not Installation:
        raise InstallationValidationError("installation is invalid")
    lines = [
        "# Generated by clash-rush-rebuild guided Setup. Keep this file private.",
        f"schema = {installation.schema}",
        f"installation_name = {_quoted(installation.installation_name)}",
        "",
        "[central]",
        f"url = {_quoted(installation.central_url)}",
        f"discord_bot_secret_ref = {_quoted(installation.discord_bot_secret_ref)}",
        f"clash_api_secret_ref = {_quoted(installation.clash_api_secret_ref)}",
        "",
        "[central.discord]",
        f"guild_id = {_quoted(installation.guild_id)}",
        f"captain_role_id = {_quoted(installation.captain_role_id)}",
        f"fleet_captain_role_id = {_quoted(installation.fleet_captain_role_id)}",
        f"fleet_captain_user_ids = {_array(installation.fleet_captain_user_ids)}",
    ]
    for team in installation.teams:
        lines.extend(
            (
                "",
                "[[teams]]",
                f"key = {_quoted(team.key)}",
                f"display_name = {_quoted(team.display_name)}",
                f"category_id = {_quoted(team.category_id)}",
                f"operations_channel_id = {_quoted(team.operations_channel_id)}",
                f"runner_secret_ref = {_quoted(team.runner_secret_ref)}",
                f"captain_user_ids = {_array(team.captain_user_ids)}",
                "",
                "[teams.resources]",
                f"home_gold_enabled = {str(team.resources.home_gold).lower()}",
                f"home_elixir_enabled = {str(team.resources.home_elixir).lower()}",
                f"home_dark_elixir_enabled = {str(team.resources.home_dark_elixir).lower()}",
                f"builder_gold_enabled = {str(team.resources.builder_gold).lower()}",
                f"builder_elixir_enabled = {str(team.resources.builder_elixir).lower()}",
            )
        )
        for account in team.accounts:
            lines.extend(
                (
                    "",
                    "[[teams.accounts]]",
                    f"key = {_quoted(account.key)}",
                    f"account_ref = {_quoted(account.account_ref)}",
                    f"account_name = {_quoted(account.account_name)}",
                    f"player_tag = {_quoted(account.player_tag)}",
                    f"bluestacks_display_name = {_quoted(account.bluestacks_display_name)}",
                    f"discord_channel_id = {_quoted(account.discord_channel_id)}",
                )
            )
    return "\n".join(lines) + "\n"


def decode_installation(payload: str) -> Installation:
    """Decode TOML while rejecting every unknown, missing, or mistyped field."""
    if type(payload) is not str:
        raise InstallationValidationError("installation payload must be text")
    try:
        root = tomllib.loads(payload)
    except (tomllib.TOMLDecodeError, ValueError):
        raise InstallationValidationError("installation TOML is invalid") from None
    root = _closed_table(root, frozenset(("schema", "installation_name", "central", "teams")), "installation")
    central = _closed_table(root["central"], frozenset(("url", "discord_bot_secret_ref", "clash_api_secret_ref", "discord")), "central")
    discord = _closed_table(central["discord"], frozenset(("guild_id", "captain_role_id", "fleet_captain_role_id", "fleet_captain_user_ids")), "central.discord")
    raw_teams = root["teams"]
    if type(raw_teams) is not list or not raw_teams:
        raise InstallationValidationError("teams must be a non-empty array")
    teams: list[InstallationTeam] = []
    team_keys = frozenset(("key", "display_name", "category_id", "operations_channel_id", "runner_secret_ref", "captain_user_ids", "resources", "accounts"))
    account_keys = frozenset(("key", "account_ref", "account_name", "player_tag", "bluestacks_display_name", "discord_channel_id"))
    resource_keys = frozenset(("home_gold_enabled", "home_elixir_enabled", "home_dark_elixir_enabled", "builder_gold_enabled", "builder_elixir_enabled"))
    for raw_team in raw_teams:
        team = _closed_table(raw_team, team_keys, "team")
        resource = _closed_table(team["resources"], resource_keys, "resources")
        choices = ResourceChoices(
            resource["home_gold_enabled"],
            resource["home_elixir_enabled"],
            resource["home_dark_elixir_enabled"],
            resource["builder_gold_enabled"],
            resource["builder_elixir_enabled"],
        )
        raw_accounts = team["accounts"]
        if type(raw_accounts) is not list:
            raise InstallationValidationError("accounts must be an array")
        accounts = tuple(
            InstallationAccount(**_closed_table(raw_account, account_keys, "account"))
            for raw_account in raw_accounts
        )
        teams.append(
            InstallationTeam(
                team["key"],
                team["display_name"],
                team["category_id"],
                team["operations_channel_id"],
                team["runner_secret_ref"],
                _exact_string_tuple(team["captain_user_ids"], "captain user IDs"),
                choices,
                accounts,
            )
        )
    return Installation(
        root["schema"],
        root["installation_name"],
        central["url"],
        central["discord_bot_secret_ref"],
        central["clash_api_secret_ref"],
        discord["guild_id"],
        discord["captain_role_id"],
        discord["fleet_captain_role_id"],
        _exact_string_tuple(discord["fleet_captain_user_ids"], "fleet captain user IDs"),
        tuple(teams),
    )


def _csv_ids(value: str, label: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in value.split(",") if part.strip())
    if not values:
        raise InstallationValidationError(f"{label} must not be empty")
    return values


def _yes_no(value: str, label: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise InstallationValidationError(f"{label} requires explicit yes or no")


def _account_count(value: object) -> int:
    if value == "":
        return 5
    if type(value) is not str or re.fullmatch(r"(?:[1-9]|10)", value) is None:
        raise InstallationValidationError("account count must be an integer from 1 to 10")
    return int(value)


def run_guided_setup(
    project_root: Path,
    *,
    prompt: Callable[[str], str] = input,
    account_ref_factory: Callable[[], str] = lambda: secrets.token_hex(16),
) -> Path:
    """Prompt for one installation and exclusively create its ignored private draft."""
    project = Path(project_root).resolve(strict=True)
    private = project / "private"
    destination = private / "installation.toml"
    if _is_reparse_point(private) or (private.exists() and not private.is_dir()):
        raise InstallationValidationError("private path is unsafe")
    if _is_reparse_point(destination):
        raise InstallationValidationError("private installation path is unsafe")
    if destination.exists():
        raise InstallationValidationError("private installation already exists")
    installation_name = prompt("Installation name: ")
    central_url = prompt("Central service URL: ")
    discord_ref = prompt("Discord bot credential reference: ")
    api_ref = prompt("Clash API credential reference: ")
    guild_id = prompt("Discord guild ID: ")
    captain_role = prompt("Team Captain role ID: ")
    fleet_role = prompt("Fleet Captain role ID: ")
    fleet_users = _csv_ids(prompt("Fleet Captain user IDs (comma separated): "), "fleet captain user IDs")
    team_key = prompt("Team key: ")
    team_name = prompt("Team display name: ")
    category_id = prompt("Team category ID: ")
    operations_id = prompt("Operations channel ID: ")
    runner_ref = prompt("Runner credential reference: ")
    captain_users = _csv_ids(prompt("Team Captain user IDs (comma separated): "), "captain user IDs")
    account_count = _account_count(prompt("Account count (1-10, default 5): "))
    resources = ResourceChoices(
        _yes_no(prompt("Allow Home Gold? (yes/no): "), "Home Gold"),
        _yes_no(prompt("Allow Home Elixir? (yes/no): "), "Home Elixir"),
        _yes_no(prompt("Allow Home Dark Elixir? (yes/no): "), "Home Dark Elixir"),
        _yes_no(prompt("Allow Builder Gold? (yes/no): "), "Builder Gold"),
        _yes_no(prompt("Allow Builder Elixir? (yes/no): "), "Builder Elixir"),
    )
    accounts = tuple(
        (
            prompt(f"Account {index} key: "),
            prompt(f"Account {index} name: "),
            prompt(f"Account {index} player tag: "),
            prompt(f"Account {index} BlueStacks display name: "),
            prompt(f"Account {index} Discord channel ID: "),
        )
        for index in range(1, account_count + 1)
    )
    installation = build_installation(
        SetupAnswers(
            installation_name,
            central_url,
            discord_ref,
            api_ref,
            guild_id,
            captain_role,
            fleet_role,
            fleet_users,
            team_key,
            team_name,
            category_id,
            operations_id,
            runner_ref,
            captain_users,
            resources,
            accounts,
        ),
        account_ref_factory=account_ref_factory,
    )
    try:
        private.mkdir()
    except FileExistsError:
        pass
    with _PrivateDirectoryLease(private):
        if _is_reparse_point(destination):
            raise InstallationValidationError("private installation path is unsafe")
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encode_installation(installation))
    return destination


def install_private_startup_font(project_root: Path, source_path: Path) -> Path:
    """Copy one operator-provided font into the ignored private asset boundary."""
    project = Path(project_root).resolve(strict=True)
    source = Path(source_path)
    try:
        if source.is_symlink() or not source.is_file():
            raise OSError
        payload = source.read_bytes()
    except BaseException:
        raise InstallationValidationError("startup font is unavailable") from None
    if not payload or len(payload) > 16 * 1024 * 1024:
        raise InstallationValidationError("startup font is invalid") from None
    private = project / "private"
    assets = private / "assets"
    destination = assets / "CCBackBeat.ttf"
    if any(_is_reparse_point(path) for path in (private, assets, destination)):
        raise InstallationValidationError("private font path is unsafe")
    if destination.exists():
        raise InstallationValidationError("private startup font already exists")
    private.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise InstallationValidationError("private startup font already exists") from None
    return destination


def export_synthetic_installation() -> str:
    """Return a valid, deterministic, public-safe five-account example."""
    synthetic = SetupAnswers(
        "Synthetic Installation",
        "wss://control.example.invalid/runner",
        "credential://clash-rush/discord-bot",
        "credential://clash-rush/supercell-api",
        "100000000000000001",
        "100000000000000002",
        "100000000000000003",
        ("100000000000000004",),
        "synthetic-team",
        "Synthetic Team",
        "100000000000000005",
        "100000000000000006",
        "credential://clash-rush/runner/synthetic-team",
        ("100000000000000007",),
        ResourceChoices(False, False, False, False, False),
        tuple(
            (
                f"account-{index:02d}",
                f"Synthetic Account {index:02d}",
                f"#SYNTH{index:02d}",
                f"Synthetic Instance {index:02d}",
                str(100000000000000007 + index),
            )
            for index in range(1, 6)
        ),
    )
    references = iter(f"{index:032x}" for index in range(1, 6))
    return encode_installation(build_installation(synthetic, account_ref_factory=references.__next__))
