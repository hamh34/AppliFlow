"""Reading and parsing Gmail messages.

`parse_message` is pure dict-and-bytes work so it can be tested without the API.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime, parseaddr

# Enough text to classify on; recruiting emails put the verdict near the top,
# and long HTML signatures add noise without adding signal.
BODY_CHAR_LIMIT = 4000


@dataclass
class Message:
    message_id: str
    thread_id: str
    subject: str
    sender_name: str
    sender_email: str
    received: date | None
    body: str


def _decode(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError):
        return ""
    return raw.decode("utf-8", errors="replace")


_TAG = re.compile(r"<[^>]+>")
_STYLE_BLOCK = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def _strip_html(html: str) -> str:
    without_blocks = _STYLE_BLOCK.sub(" ", html)
    text = _TAG.sub(" ", without_blocks)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return " ".join(text.split())


def _extract_body(payload: dict) -> str:
    """Depth-first search for readable text, preferring text/plain over HTML."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = (part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data", "")
        if data:
            if mime.startswith("text/plain"):
                plain.append(_decode(data))
            elif mime.startswith("text/html"):
                html.append(_decode(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})

    if plain:
        return " ".join(" ".join(plain).split())
    if html:
        return _strip_html(" ".join(html))
    return ""


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return header.get("value") or ""
    return ""


def _received_date(raw: dict, headers: list[dict]) -> date | None:
    # internalDate is Gmail's own receipt timestamp in epoch milliseconds and is
    # more reliable than the Date header, which senders can set to anything.
    internal = raw.get("internalDate")
    if internal:
        try:
            return datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            pass

    header_date = _header(headers, "Date")
    if header_date:
        try:
            return parsedate_to_datetime(header_date).date()
        except (TypeError, ValueError):
            pass
    return None


def parse_message(raw: dict) -> Message:
    """Turn a Gmail API message resource into a flat record."""
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []

    sender_name, sender_email = parseaddr(_header(headers, "From"))
    body = _extract_body(payload) or (raw.get("snippet") or "")

    return Message(
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        subject=_header(headers, "Subject").strip(),
        sender_name=sender_name.strip(),
        sender_email=sender_email.strip(),
        received=_received_date(raw, headers),
        body=body[:BODY_CHAR_LIMIT],
    )


def fetch_messages(creds, query: str, max_results: int = 500) -> list[Message]:
    """Search Gmail and return parsed messages, oldest first."""
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    messages = service.users().messages()

    ids: list[str] = []
    request = messages.list(userId="me", q=query, maxResults=min(max_results, 500))
    while request is not None and len(ids) < max_results:
        response = request.execute()
        ids.extend(item["id"] for item in response.get("messages", []))
        request = messages.list_next(request, response)

    parsed: list[Message] = []
    for message_id in ids[:max_results]:
        raw = messages.get(userId="me", id=message_id, format="full").execute()
        parsed.append(parse_message(raw))

    # Oldest first so a later email can advance the status a earlier one set.
    parsed.sort(key=lambda m: (m.received or date.min, m.message_id))
    return parsed
