"""Setup checks, so a broken install says what is wrong instead of raising.

First-time setup has several independent moving parts -- an OAuth client of the
right type, a config file, a reachable spreadsheet, alerts actually arriving --
and a failure in any one of them surfaces late and vaguely. Each check here
reports its own verdict and the specific fix, so problems can be read off
rather than inferred from a traceback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

OK = "ok"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""

    @property
    def marker(self) -> str:
        return {OK: "[ok]", FAIL: "[--]", SKIP: "[..]"}[self.state]


def check_config(path: Path) -> Check:
    if not path.exists():
        return Check(
            "config.toml", FAIL,
            f"not found at {path}",
            "cp config.example.toml config.toml, then fill in spreadsheet_id",
        )
    try:
        import tomllib

        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:
        return Check("config.toml", FAIL, f"could not parse: {exc}",
                     "check for an unclosed quote or bracket")

    sheet_id = str(data.get("spreadsheet_id", "")).strip()
    if not sheet_id:
        return Check("config.toml", FAIL, "spreadsheet_id is empty",
                     "paste the ID from your sheet URL, between /d/ and /edit")
    return Check("config.toml", OK, f"spreadsheet_id set ({sheet_id[:12]}...)")


def check_credentials(path: Path) -> Check:
    """Catch the Web-vs-Desktop client mistake before it fails confusingly."""
    if not path.exists():
        return Check(
            "credentials.json", FAIL,
            f"not found at {path}",
            "Google Cloud Console > Credentials > Create OAuth client ID > "
            "Desktop app, then download the JSON here",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return Check("credentials.json", FAIL, f"unreadable: {exc}",
                     "re-download the client JSON")

    if "installed" in data:
        return Check("credentials.json", OK, "Desktop app client")
    if "web" in data:
        return Check(
            "credentials.json", FAIL,
            "this is a Web application client",
            "create a new OAuth client with type 'Desktop app' -- a web client "
            "cannot complete the local browser flow",
        )
    return Check("credentials.json", FAIL, "unrecognized client format",
                 "re-download the JSON for a Desktop app OAuth client")


def check_token(path: Path) -> Check:
    if not path.exists():
        return Check("authorization", FAIL, "not authorized yet",
                     "run: python -m appliflow init")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Check("authorization", FAIL, "token.json is corrupt",
                     "delete token.json and run: python -m appliflow init")

    if not data.get("refresh_token"):
        return Check("authorization", FAIL, "no refresh token stored",
                     "delete token.json and run init again, approving all scopes")
    return Check("authorization", OK, "token present")


def check_alert_config(alerts) -> Check:
    if not alerts.enabled:
        return Check("alert reading", SKIP, "disabled",
                     "set enabled = true under [find.alerts] to read job alerts")
    return Check("alert reading", OK, f"enabled, looking back {alerts.lookback_days}d")


def render(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        line = f"  {check.marker} {check.name}"
        if check.detail:
            line += f": {check.detail}"
        lines.append(line)
        if check.state == FAIL and check.fix:
            lines.append(f"       fix: {check.fix}")
    return "\n".join(lines)
