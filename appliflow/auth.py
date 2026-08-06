"""Google OAuth for a desktop app.

Gmail is requested read-only: this tool never sends, deletes, or modifies mail.
"""

from __future__ import annotations

from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


class AuthError(RuntimeError):
    pass


def get_credentials(credentials_file: Path, token_file: Path):
    """Return usable OAuth credentials, prompting in a browser on first run.

    The refresh token is cached in `token_file`, so the browser prompt only
    appears once (or again if you revoke access or change scopes).
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise AuthError(
            "Google client libraries are missing. Run: pip install -r requirements.txt"
        ) from exc

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not credentials_file.exists():
            raise AuthError(
                f"No OAuth client file at {credentials_file}.\n"
                "Create a Desktop app OAuth client in Google Cloud Console, "
                "download the JSON, and save it there. See the README for steps."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    # The refresh token is a long-lived credential; keep it owner-readable only.
    try:
        token_file.chmod(0o600)
    except OSError:  # pragma: no cover - not supported on every filesystem
        pass
    return creds
