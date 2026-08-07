"""Fetching job postings from public job-board APIs.

Every source here is an official, documented, public endpoint that needs no API
key and no scraping. Parsing is split from fetching so the parsers can be tested
against recorded payloads without network access.

Payload shapes are read defensively throughout: a board that adds, renames, or
drops a field should cost us one posting, not the whole run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

from .openings import Opening

USER_AGENT = "AppliFlow/1.0 (personal job tracker)"
TIMEOUT_SECONDS = 30


class SourceError(RuntimeError):
    pass


def _parse_timestamp(value) -> date | None:
    """Accept ISO 8601, epoch seconds, or epoch milliseconds."""
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None

    text = str(value).strip()
    if not text:
        return None

    if text.isdigit():
        return _parse_timestamp(int(text))

    # fromisoformat on 3.11 handles offsets but not a trailing 'Z'.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _looks_remote(*fields) -> bool:
    return any("remote" in str(field).lower() for field in fields if field)


# --- Parsers -----------------------------------------------------------------
# Each takes a decoded payload and returns Openings. Malformed entries are
# skipped rather than raising, so one bad row cannot abort a whole board.


def parse_greenhouse(payload: dict, company: str) -> list[Opening]:
    openings = []
    for job in (payload or {}).get("jobs") or []:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        location = ((job.get("location") or {}).get("name") or "").strip()
        openings.append(
            Opening(
                company=company,
                title=title,
                location=location,
                url=(job.get("absolute_url") or "").strip(),
                source="greenhouse",
                posted=_parse_timestamp(job.get("updated_at")),
                remote=_looks_remote(location, title),
            )
        )
    return openings


def parse_lever(payload: list, company: str) -> list[Opening]:
    openings = []
    for job in payload or []:
        title = (job.get("text") or "").strip()
        if not title:
            continue
        categories = job.get("categories") or {}
        location = (categories.get("location") or "").strip()
        workplace = job.get("workplaceType") or ""
        openings.append(
            Opening(
                company=company,
                title=title,
                location=location,
                url=(job.get("hostedUrl") or job.get("applyUrl") or "").strip(),
                source="lever",
                posted=_parse_timestamp(job.get("createdAt")),
                remote=_looks_remote(location, workplace, title),
            )
        )
    return openings


def parse_ashby(payload: dict, company: str) -> list[Opening]:
    openings = []
    for job in (payload or {}).get("jobs") or []:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        location = (job.get("location") or "").strip()
        openings.append(
            Opening(
                company=company,
                title=title,
                location=location,
                url=(job.get("jobUrl") or job.get("applyUrl") or "").strip(),
                source="ashby",
                posted=_parse_timestamp(job.get("publishedAt")),
                remote=bool(job.get("isRemote")) or _looks_remote(location),
            )
        )
    return openings


def parse_arbeitnow(payload: dict) -> list[Opening]:
    openings = []
    for job in (payload or {}).get("data") or []:
        title = (job.get("title") or "").strip()
        company = (job.get("company_name") or "").strip()
        if not title or not company:
            continue
        location = (job.get("location") or "").strip()
        openings.append(
            Opening(
                company=company,
                title=title,
                location=location,
                url=(job.get("url") or "").strip(),
                source="arbeitnow",
                posted=_parse_timestamp(job.get("created_at")),
                remote=bool(job.get("remote")) or _looks_remote(location),
            )
        )
    return openings


def parse_remoteok(payload: list) -> list[Opening]:
    openings = []
    for job in payload or []:
        # The first element of the RemoteOK feed is a legal notice, not a job.
        if not isinstance(job, dict) or "legal" in job:
            continue
        title = (job.get("position") or "").strip()
        company = (job.get("company") or "").strip()
        if not title or not company:
            continue
        openings.append(
            Opening(
                company=company,
                title=title,
                location=(job.get("location") or "Remote").strip(),
                url=(job.get("url") or "").strip(),
                source="remoteok",
                posted=_parse_timestamp(job.get("epoch") or job.get("date")),
                remote=True,
            )
        )
    return openings


# --- Fetching ----------------------------------------------------------------

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
REMOTEOK_URL = "https://remoteok.com/api"


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise SourceError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"could not reach {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"{url} did not return valid JSON") from exc


def collect(config, on_error=None) -> list[Opening]:
    """Pull every configured source. A failing board is reported, not fatal.

    `on_error` is called with (label, message) so the CLI can print warnings
    while the rest of the run continues.
    """
    found: list[Opening] = []

    def attempt(label, url, parse):
        try:
            found.extend(parse(fetch_json(url)))
        except SourceError as exc:
            if on_error:
                on_error(label, str(exc))

    for token in config.greenhouse:
        attempt(
            f"greenhouse/{token}",
            GREENHOUSE_URL.format(token=token),
            lambda payload, t=token: parse_greenhouse(payload, t),
        )
    for token in config.lever:
        attempt(
            f"lever/{token}",
            LEVER_URL.format(token=token),
            lambda payload, t=token: parse_lever(payload, t),
        )
    for token in config.ashby:
        attempt(
            f"ashby/{token}",
            ASHBY_URL.format(token=token),
            lambda payload, t=token: parse_ashby(payload, t),
        )
    if config.arbeitnow:
        attempt("arbeitnow", ARBEITNOW_URL, parse_arbeitnow)
    if config.remoteok:
        attempt("remoteok", REMOTEOK_URL, parse_remoteok)

    return found
