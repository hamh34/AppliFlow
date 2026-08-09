"""Configuration loading."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.toml"

# Phrases that appear in almost every application-related email. Kept broad on
# purpose: the classifier filters precisely, this only narrows what we download.
_QUERY_PHRASES = [
    '"your application"',
    '"thank you for applying"',
    '"application received"',
    '"we received your application"',
    '"application for"',
    '"regret to inform"',
    '"move forward with other"',
    '"interview"',
    '"offer of employment"',
]


def build_default_query(lookback_days: int) -> str:
    return f"newer_than:{lookback_days}d ({' OR '.join(_QUERY_PHRASES)})"


class ConfigError(RuntimeError):
    pass


@dataclass
class AlertConfig:
    """Reading openings out of job-alert emails already in your inbox."""

    enabled: bool = False
    lookback_days: int = 7
    # Empty means every board AppliFlow knows how to parse.
    boards: list[str] = field(default_factory=list)
    max_emails: int = 200


@dataclass
class FindConfig:
    """Settings for `find`, which discovers openings you have not applied to yet."""

    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    max_age_days: int | None = 30
    worksheet: str = "Openings"
    greenhouse: list[str] = field(default_factory=list)
    lever: list[str] = field(default_factory=list)
    ashby: list[str] = field(default_factory=list)
    arbeitnow: bool = False
    remoteok: bool = False
    alerts: AlertConfig = field(default_factory=AlertConfig)

    @property
    def has_sources(self) -> bool:
        return bool(
            self.greenhouse
            or self.lever
            or self.ashby
            or self.arbeitnow
            or self.remoteok
            or self.alerts.enabled
        )


@dataclass
class Config:
    spreadsheet_id: str
    worksheet: str = "Applications"
    lookback_days: int = 90
    followup_days: int = 14
    gmail_query: str = ""
    credentials_file: Path = field(default_factory=lambda: PROJECT_DIR / "credentials.json")
    token_file: Path = field(default_factory=lambda: PROJECT_DIR / "token.json")
    find: FindConfig = field(default_factory=FindConfig)

    def query(self, lookback_days: int | None = None) -> str:
        if self.gmail_query:
            return self.gmail_query
        return build_default_query(lookback_days or self.lookback_days)


def _string_list(value) -> list[str]:
    """Accept either a TOML array or a single string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _load_alerts(data: dict) -> AlertConfig:
    return AlertConfig(
        enabled=bool(data.get("enabled", False)),
        lookback_days=max(1, int(data.get("lookback_days", 7))),
        boards=_string_list(data.get("boards")),
        max_emails=max(1, int(data.get("max_emails", 200))),
    )


def _load_find(data: dict) -> FindConfig:
    max_age = data.get("max_age_days", 30)
    return FindConfig(
        alerts=_load_alerts(data.get("alerts") or {}),
        keywords=_string_list(data.get("keywords")),
        locations=_string_list(data.get("locations")),
        # 0 or a negative value means "no age limit at all".
        max_age_days=None if max_age in (None, 0) or int(max_age) < 0 else int(max_age),
        worksheet=str(data.get("worksheet", "Openings")),
        greenhouse=_string_list(data.get("greenhouse")),
        lever=_string_list(data.get("lever")),
        ashby=_string_list(data.get("ashby")),
        arbeitnow=bool(data.get("arbeitnow", False)),
        remoteok=bool(data.get("remoteok", False)),
    )


def load(path: Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"No config found at {config_path}.\n"
            "Copy config.example.toml to config.toml and fill in your spreadsheet ID."
        )

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    spreadsheet_id = str(data.get("spreadsheet_id", "")).strip()
    if not spreadsheet_id:
        raise ConfigError(f"'spreadsheet_id' is missing from {config_path}.")

    base = config_path.parent
    credentials = data.get("credentials_file")
    token = data.get("token_file")

    return Config(
        find=_load_find(data.get("find") or {}),
        spreadsheet_id=spreadsheet_id,
        worksheet=str(data.get("worksheet", "Applications")),
        lookback_days=int(data.get("lookback_days", 90)),
        followup_days=int(data.get("followup_days", 14)),
        gmail_query=str(data.get("gmail_query", "")),
        credentials_file=(base / credentials) if credentials else PROJECT_DIR / "credentials.json",
        token_file=(base / token) if token else PROJECT_DIR / "token.json",
    )
