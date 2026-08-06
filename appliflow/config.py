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
class Config:
    spreadsheet_id: str
    worksheet: str = "Applications"
    lookback_days: int = 90
    followup_days: int = 14
    gmail_query: str = ""
    credentials_file: Path = field(default_factory=lambda: PROJECT_DIR / "credentials.json")
    token_file: Path = field(default_factory=lambda: PROJECT_DIR / "token.json")

    def query(self, lookback_days: int | None = None) -> str:
        if self.gmail_query:
            return self.gmail_query
        return build_default_query(lookback_days or self.lookback_days)


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
        spreadsheet_id=spreadsheet_id,
        worksheet=str(data.get("worksheet", "Applications")),
        lookback_days=int(data.get("lookback_days", 90)),
        followup_days=int(data.get("followup_days", 14)),
        gmail_query=str(data.get("gmail_query", "")),
        credentials_file=(base / credentials) if credentials else PROJECT_DIR / "credentials.json",
        token_file=(base / token) if token else PROJECT_DIR / "token.json",
    )
