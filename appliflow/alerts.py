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
from collections import Counter
from dataclasses import dataclass, field
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

# Recommendation links point at a real job URL but are not a posting in this
# alert -- their anchor names a *different* job. Matched by prefix because the
# rest of the text varies with whatever job is being recommended.
_CHROME_PREFIXES = (
    "jobs similar to",
    "similar jobs",
    "more jobs at",
    "see jobs at",
)

# Card decoration LinkedIn places after the location. It arrives in its own
# elements, so joining the text runs it straight into the location instead of
# leaving it as a separate field to split off. Every alternative below was
# taken from real alert mail, not guessed.
_CARD_NOISE = re.compile(
    r"\s*(?:"
    r"actively\s+recruiting"
    r"|easy\s+apply"
    r"|be\s+an\s+early\s+applicant"
    r"|\d+\s+connections?"
    r"|\d+\s+school\s+alum(?:ni)?"
    r"|\d+\s+alum(?:ni)?"
    r")\b",
    re.I,
)

# LinkedIn marks the work arrangement in parentheses after the location.
_ARRANGEMENT = re.compile(r"\((remote|hybrid|on-?site)\)", re.I)

# --- Glassdoor ------------------------------------------------------------
# Glassdoor puts the whole card inside the anchor and leaves nothing after the
# link, so `_split_details` has no text to work with and every field but the
# title came back empty. The card reads:
#
#   <Company> [<rating> ★] <Title> <Location> [badge] [salary] [Easy Apply] <age>
#
# The rating is the only reliable delimiter, and it is present on most rows.
_GD_RATING = re.compile(r"^(?P<company>.+?)\s+\d\.\d\s*★\s*(?P<rest>.+)$")
_GD_AGE = re.compile(r"\s+(?:\d+[dhm]|just\s+posted)$", re.I)
_GD_NOISE = re.compile(
    r"\s*(?:"
    r"easy\s+apply"
    r"|\(\s*(?:employer|glassdoor)\s+est\.?\s*\)"
    r"|best\s+places?\s+to\s+work"
    r"|best-?led\s+compan(?:y|ies)"
    r"|top\s+compan(?:y|ies)"
    r"|idr\s*[\d.,]+\s*[mkb]?(?:\s*-\s*idr\s*[\d.,]+\s*[mkb]?)?"
    r")\s*",
    re.I,
)

# Where the location ends and the title begins is not marked, so the tail is
# matched against places instead of guessed. An unrecognised tail leaves the
# location empty, which now passes the filters rather than dropping the row --
# so a missing entry here costs a blank cell, never a posting.
_PLACES = (
    "kuala lumpur", "ho chi minh city", "jakarta selatan", "jakarta utara",
    "jakarta barat", "jakarta timur", "jakarta pusat", "south jakarta",
    "north jakarta", "west jakarta", "east jakarta", "central jakarta",
    "jakarta", "indonesia", "tangerang", "karawang", "cikarang", "bekasi",
    "depok", "bogor", "bandung", "surabaya", "semarang", "yogyakarta",
    "denpasar", "makassar", "palembang", "medan", "batam", "malang",
    "ciracas", "bali", "singapore", "bangkok", "manila", "hanoi", "remote",
)


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
        # Both domains: JobStreet Indonesia serves id.jobstreet.com as well as
        # the older jobstreet.co.id, and `senders` below accepts mail from both.
        # Job ids are shared, so one canonical form still collapses duplicates.
        re.compile(r"jobstreet\.(?:co\.id|com)/(?:[a-z-]+/)*job/(\d+)", re.I),
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


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# A path segment this long with no punctuation is a tracking blob, not a slug.
_OPAQUE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{32,}$")


def _redact_value(value: str) -> str:
    """Keep the parts of a query value a pattern could key on, drop the rest.

    Numeric ids are kept because that is what the job-id patterns capture, and
    nested URLs are kept (redacted in turn) because tracking wrappers hide the
    real destination in one. Anything else is assumed to be a per-recipient
    token. The parameter *name* always survives, so a board that hides its job
    id somewhere new still shows up as `?vacancyId=<redacted>` -- enough to know
    what to ask for without leaking the token itself.
    """
    if not value:
        return value
    if value.isdigit():
        return value
    if "://" in value:
        return redact_url(value)
    return "<redacted>"


def redact_url(url: str) -> str:
    """Strip per-recipient tokens from a URL so it is safe to share.

    `--explain` exists to be read by someone other than the mailbox's owner, so
    what it prints must not carry the mailbox with it. Alert links are stuffed
    with tracking parameters tied to the recipient's account, and footer links
    often carry the address in the clear.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable url>"

    segments = [
        "<token>" if _OPAQUE_SEGMENT.match(segment) else segment
        for segment in parts.path.split("/")
    ]
    path = "/".join(segments)

    query = ""
    if parts.query:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if pairs:
            query = "&".join(f"{key}={_redact_value(value)}" for key, value in pairs)
        else:
            query = "<redacted>"

    cleaned = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))
    return _EMAIL.sub("<email>", cleaned)


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
    cleaned = _clean(text).lower()
    if cleaned in _CHROME:
        return True
    return any(cleaned.startswith(prefix) for prefix in _CHROME_PREFIXES)


def _strip_card_noise(text: str) -> str:
    """Remove the recruiting-card decoration that runs into the location."""
    return _clean(_CARD_NOISE.sub(" ", text))


def _trailing_place(text: str) -> tuple[str, str]:
    """Split a trailing place name off the end. Returns (remainder, place)."""
    lowered = text.lower()
    for place in _PLACES:
        if not lowered.endswith(place):
            continue
        head = text[: len(text) - len(place)].rstrip(" ,-·")
        if head:  # A row that is only a place name has no title; leave it be.
            return head, text[len(text) - len(place):]
    return text, ""


def glassdoor_card(anchor: str) -> tuple[str, str, str]:
    """Split Glassdoor's single-anchor card into (title, company, location).

    Company is taken from before the star rating, the only delimiter the card
    offers. Rows without a rating keep the company inside the title -- there is
    nothing to split on, and inventing a boundary would be worse than leaving
    it, since the title still matches keywords either way.
    """
    text = _clean(anchor)
    company = ""
    rated = _GD_RATING.match(text)
    if rated:
        company = rated.group("company").strip()
        text = rated.group("rest").strip()

    # Age and badges can stack in either order, so strip until nothing changes.
    previous = None
    while previous != text:
        previous = text
        text = _clean(_GD_NOISE.sub(" ", _GD_AGE.sub("", text)))

    title, location = _trailing_place(text)
    return title, company, location


def _split_details(after: str, board_name: str) -> tuple[str, str]:
    """Guess (company, location) from the text following a job link.

    Boards separate these with a bullet, dash, pipe, or line break. Real mail
    showed the separators work: company comes out clean. What did not work was
    the tail -- "Jakarta, Indonesia (On-site) 17 connections Easy Apply" -- so
    the card decoration is stripped before splitting, not after.
    """
    text = _strip_card_noise(after)
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


@dataclass
class LinkReport:
    """One `<a>` from an alert email, with the parser's working shown.

    `--explain` prints these. The fields that matter for correcting a misread
    are `trailing` -- the exact text `_split_details` was handed -- and `href`,
    which is what `identify` actually matched against.
    """

    href: str  # Unwrapped and redacted; safe to paste somewhere else.
    anchor: str
    trailing: str
    board: str = ""  # Empty when the URL matched no board pattern.
    opening: Opening | None = None
    skipped: str = ""  # Why no Opening came out: see the reasons below.


NOT_A_JOB = "not a job link"
CHROME_ANCHOR = "anchor text is boilerplate, not a title"
NO_ANCHOR = "no anchor text, so the title would be blank"


def analyze_html(html: str, *, source_label: str = "alert") -> list[LinkReport]:
    """Walk every link in one alert email and report what came of it.

    This is the single parsing pass; `openings_from_html` is a filter over its
    output. Keeping one implementation means `--explain` cannot drift into
    describing something the real run does not do.
    """
    parser = _LinkCollector()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A malformed email should cost one message, never the whole run.
        return []

    reports: list[LinkReport] = []
    for link in parser.links:
        raw_href = link["href"]
        anchor = _clean(" ".join(link["text"]))
        trailing = _clean(" ".join(link["after"]))

        identified = identify(raw_href)
        href = redact_url(unwrap_url(raw_href))
        if not identified:
            reports.append(
                LinkReport(href=href, anchor=anchor, trailing=trailing, skipped=NOT_A_JOB)
            )
            continue
        board, _job_id, canonical = identified

        if not anchor:
            reports.append(
                LinkReport(href=href, anchor=anchor, trailing=trailing,
                           board=board.name, skipped=NO_ANCHOR)
            )
            continue
        if _looks_like_chrome(anchor):
            reports.append(
                LinkReport(href=href, anchor=anchor, trailing=trailing,
                           board=board.name, skipped=CHROME_ANCHOR)
            )
            continue

        if board.name == "glassdoor":
            # The card is the anchor, not the text beside it.
            title, company, location = glassdoor_card(anchor)
        else:
            title = anchor
            company, location = _split_details(trailing, board.name)

        if board.name == "kalibrr" and not company:
            # Kalibrr puts the employer slug in the URL itself.
            slug = re.search(r"/c/([^/]+)/", canonical)
            if slug:
                company = slug.group(1).replace("-", " ").title()

        # "(Remote)" beside the location is the only remote signal an alert
        # carries; without this the flag stays False and a `locations =
        # ["Remote"]` filter can never match an alert posting.
        arrangement = _ARRANGEMENT.search(trailing) or _ARRANGEMENT.search(anchor)
        remote = (bool(arrangement) and arrangement.group(1).lower() == "remote") or (
            location.lower() == "remote"
        )

        reports.append(
            LinkReport(
                href=href,
                anchor=anchor,
                trailing=trailing,
                board=board.name,
                opening=Opening(
                    company=company,
                    title=title,
                    location=location,
                    url=canonical,
                    source=f"{source_label}:{board.name}",
                    remote=remote,
                ),
            )
        )
    return reports


def openings_from_html(html: str, *, source_label: str = "alert") -> list[Opening]:
    """Extract every job posting linked from one alert email."""
    return [
        report.opening
        for report in analyze_html(html, source_label=source_label)
        if report.opening is not None
    ]


@dataclass
class MessageReport:
    """`--explain` detail for one alert email."""

    subject: str
    sender: str
    received: object = None
    links: list[LinkReport] = field(default_factory=list)
    has_html: bool = True

    @property
    def matched(self) -> list[LinkReport]:
        return [link for link in self.links if link.opening is not None]

    @property
    def skipped_jobs(self) -> list[LinkReport]:
        """Job-shaped links that yielded nothing -- the interesting failures."""
        return [link for link in self.links if link.board and link.opening is None]

    def unmatched_hosts(
        self, per_host: int = 3
    ) -> list[tuple[str, int, list[tuple[str, str]]]]:
        """Distinct link shapes that matched no board, worst-case diagnosis.

        Each sample is (url, anchor text). The anchor matters as much as the
        URL: when a board hides its destination behind an opaque click-tracking
        redirect, no pattern can recover the job id, and the only question left
        is whether the anchor carries enough to build a posting without one.
        """
        counts: Counter[str] = Counter()
        samples: dict[str, list[tuple[str, str]]] = {}
        for link in self.links:
            if link.board or not link.href:
                continue
            host = urllib.parse.urlsplit(link.href).netloc or "(no host)"
            counts[host] += 1
            seen = samples.setdefault(host, [])
            if len(seen) < per_host and all(link.href != url for url, _ in seen):
                seen.append((link.href, link.anchor))
        return [(host, count, samples[host]) for host, count in counts.most_common()]


def explain_messages(messages) -> list[MessageReport]:
    """Build `--explain` detail for a batch of alert emails."""
    return [
        MessageReport(
            subject=message.subject,
            sender=message.sender_email,
            received=message.received,
            links=analyze_html(message.html),
            has_html=bool(message.html),
        )
        for message in messages
    ]


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
