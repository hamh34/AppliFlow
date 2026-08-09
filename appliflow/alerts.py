"""Reading job openings out of job-alert emails.

Job boards will happily email you new postings if you set up an alert. Those
emails are in your own inbox, so reading them needs no scraping, no login, and
no terms-of-service gymnastics.

The parsing strategy is deliberate: **match link patterns, not email templates.**
A board is free to redesign its email, change class names, or reshuffle its
layout. As long as its links still point at the same job-URL shape, this keeps
working. Template-matching would break on the first redesign.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

from .openings import Opening

# Alert emails wrap the real link in click-tracking redirects, so the useful URL
# often sits url-encoded inside a query string. Unwrap before matching.
_MAX_UNWRAP_DEPTH = 3

# Anchor text that is chrome, not a job title.
_CHROME = {
    "view job", "see job", "apply", "apply now", "view", "see all jobs",
    "view all", "unsubscribe", "see more jobs", "view all jobs", "learn more",
    "settings", "manage alerts", "see more",
}


@dataclass(frozen=True)
class Board:
    name: str
    # Captures a stable job id, so tracking parameters cannot create duplicates.
    pattern: re.Pattern
    # Domains these alerts are sent from, used to build the Gmail query.
    senders: tuple[str, ...]
    canonical: str  # Rebuilds a clean URL from the captured id.


BOARDS: tuple[Board, ...] = (
    Board(
        "linkedin",
        re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I),
        ("linkedin.com",),
        "https://www.linkedin.com/jobs/view/{id}",
    ),
    Board(
        "jobstreet",
        re.compile(r"jobstreet\.co\.id/(?:[a-z-]+/)*job/(\d+)", re.I),
        ("jobstreet.co.id", "jobstreet.com"),
        "https://www.jobstreet.co.id/job/{id}",
    ),
    Board(
        "glints",
        re.compile(r"glints\.com/(?:[a-z-]+/)*opportunities/jobs/([0-9a-zA-Z-]+)", re.I),
        ("glints.com",),
        "https://glints.com/opportunities/jobs/{id}",
    ),
    Board(
        "kalibrr",
        re.compile(r"kalibrr\.com/c/([^/\s\"']+)/jobs/(\d+)", re.I),
        ("kalibrr.com",),
        "https://www.kalibrr.com/c/{company}/jobs/{id}",
    ),
    Board(
        "glassdoor",
        re.compile(r"glassdoor\.[a-z.]+/(?:job-listing|partner/jobListing)[^\s\"']*?"
                   r"jobListingId=(\d+)", re.I),
        ("glassdoor.com",),
        "https://www.glassdoor.com/job-listing/?jobListingId={id}",
    ),
)

_BY_NAME = {board.name: board for board in BOARDS}


class _LinkCollector(HTMLParser):
    """Collects (href, anchor text) pairs plus the text that follows each link.

    Company and location are rarely inside the anchor; they sit in the markup
    just after it. Capturing the trailing text gives the heuristics something
    to work with.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._current: dict | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        # Close any anchor left open by malformed markup.
        if self._current is not None:
            self.links.append(self._current)
        href = ""
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value.strip()
                break
        self._current = {"href": href, "text": [], "after": []}
        self._depth = 0

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._current is not None:
            self._current["text"].append(text)
        elif self.links:
            # Text between this link and the next belongs to the previous one.
            self.links[-1]["after"].append(text)

    def close(self):
        super().close()
        if self._current is not None:
            self.links.append(self._current)
            self._current = None


def unwrap_url(url: str) -> str:
    """Follow click-tracking wrappers to the real destination.

    Tracking links carry the true target url-encoded in a query parameter. The
    job-id patterns cannot see through that encoding, so decode first.
    """
    seen = url
    for _ in range(_MAX_UNWRAP_DEPTH):
        decoded = urllib.parse.unquote(seen)
        if decoded == seen:
            break
        seen = decoded
    return seen


def identify(url: str) -> tuple[Board, str, str] | None:
    """Return (board, job id, canonical url) for a job link, else None."""
    target = unwrap_url(url)
    for board in BOARDS:
        match = board.pattern.search(target)
        if not match:
            continue
        if board.name == "kalibrr":
            company, job_id = match.group(1), match.group(2)
            return board, job_id, board.canonical.format(company=company, id=job_id)
        job_id = match.group(1)
        return board, job_id, board.canonical.format(id=job_id)
    return None


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(" ·-|•,")


def _looks_like_chrome(text: str) -> bool:
    return _clean(text).lower() in _CHROME


def _split_details(after: str, board_name: str) -> tuple[str, str]:
    """Guess (company, location) from the text following a job link.

    Boards separate these with a bullet, dash, pipe, or line break. This is the
    softest part of the parser by a wide margin -- `--explain` exists so the
    guesses can be checked against real mail and corrected.
    """
    text = _clean(after)
    if not text:
        return "", ""
    parts = [p.strip() for p in re.split(r"\s*[·•|]\s*|\s+[-–]\s+", text) if p.strip()]
    parts = [p for p in parts if not _looks_like_chrome(p)]
    if not parts:
        return "", ""
    if len(parts) == 1:
        # A lone fragment is far more often the company than the location.
        return parts[0], ""
    return parts[0], parts[1]


def openings_from_html(html: str, *, source_label: str = "alert") -> list[Opening]:
    """Extract every job posting linked from one alert email."""
    parser = _LinkCollector()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A malformed email should cost one message, never the whole run.
        return []

    found: list[Opening] = []
    for link in parser.links:
        identified = identify(link["href"])
        if not identified:
            continue
        board, job_id, canonical = identified

        title = _clean(" ".join(link["text"]))
        if not title or _looks_like_chrome(title):
            continue

        company, location = _split_details(" ".join(link["after"]), board.name)
        if board.name == "kalibrr" and not company:
            # Kalibrr puts the employer slug in the URL itself.
            slug = re.search(r"/c/([^/]+)/", canonical)
            if slug:
                company = slug.group(1).replace("-", " ").title()

        found.append(
            Opening(
                company=company,
                title=title,
                location=location,
                url=canonical,
                source=f"{source_label}:{board.name}",
            )
        )
    return found


def openings_from_messages(messages) -> list[Opening]:
    """Parse a batch of alert emails, dating each posting by its email."""
    found: list[Opening] = []
    for message in messages:
        for opening in openings_from_html(message.html):
            # The email's arrival is the best available "posted" signal; the
            # alert itself carries no posting date.
            opening.posted = message.received
            found.append(opening)
    return found


def gmail_query(lookback_days: int, boards: list[str] | None = None) -> str:
    """Build a Gmail search limited to alert senders and a time window."""
    selected = [
        _BY_NAME[name] for name in (boards or list(_BY_NAME)) if name in _BY_NAME
    ]
    if not selected:
        selected = list(BOARDS)
    senders = sorted({domain for board in selected for domain in board.senders})
    clause = " OR ".join(f"from:{domain}" for domain in senders)
    return f"({clause}) newer_than:{max(1, lookback_days)}d"
