"""Job openings discovered from public job-board APIs.

Separate from `records.py`, which tracks applications you have already sent.
This module is the "before you apply" half: what is out there right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

COLUMNS = [
    "Company",
    "Title",
    "Location",
    "Posted",
    "Remote",
    "Source",
    "URL",
    "Status",
]


@dataclass
class Opening:
    company: str
    title: str
    location: str = ""
    url: str = ""
    source: str = ""
    posted: date | None = None
    remote: bool = False

    def to_row(self) -> list[str]:
        return [
            self.company,
            self.title,
            self.location,
            self.posted.isoformat() if self.posted else "",
            "Yes" if self.remote else "",
            self.source,
            self.url,
            "",  # Status is yours to fill in: Interested, Applied, Skip.
        ]

    def key(self) -> str:
        """Identity for de-duplication."""
        if self.url:
            # Strip tracking noise so the same posting shared two ways collapses.
            return self.url.split("?")[0].rstrip("/").lower()
        return "|".join(
            part.lower().strip()
            for part in (self.company, self.title, self.location)
        )


def matches_keywords(opening: Opening, keywords: list[str]) -> bool:
    """True when the title contains any keyword. Empty keyword list matches all."""
    if not keywords:
        return True
    title = opening.title.lower()
    return any(keyword.lower().strip() in title for keyword in keywords if keyword.strip())


def matches_locations(opening: Opening, locations: list[str]) -> bool:
    """True when the location matches any entry. 'remote' also matches remote roles."""
    if not locations:
        return True
    haystack = opening.location.lower()
    for location in locations:
        needle = location.lower().strip()
        if not needle:
            continue
        if needle in haystack:
            return True
        if needle == "remote" and opening.remote:
            return True
    return False


def is_recent(opening: Opening, as_of: date, max_age_days: int | None) -> bool:
    """Postings with no date are kept: an unknown date is not evidence of staleness."""
    if max_age_days is None or opening.posted is None:
        return True
    return (as_of - opening.posted).days <= max_age_days


def filter_openings(
    openings: list[Opening],
    *,
    keywords: list[str],
    locations: list[str],
    max_age_days: int | None,
    as_of: date,
) -> list[Opening]:
    return [
        opening
        for opening in openings
        if matches_keywords(opening, keywords)
        and matches_locations(opening, locations)
        and is_recent(opening, as_of, max_age_days)
    ]


def dedupe(openings: list[Opening]) -> list[Opening]:
    """Drop repeats, keeping the first occurrence so source order is respected."""
    seen: set[str] = set()
    unique: list[Opening] = []
    for opening in openings:
        identity = opening.key()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(opening)
    return unique


def exclude_known(openings: list[Opening], known_urls: set[str]) -> list[Opening]:
    """Drop postings already written to the sheet on an earlier run."""
    normalized = {url.split("?")[0].rstrip("/").lower() for url in known_urls if url}
    return [o for o in openings if o.key() not in normalized]


def sort_openings(openings: list[Opening]) -> list[Opening]:
    """Newest first; undated postings sink to the bottom."""
    return sorted(
        openings,
        key=lambda o: (o.posted is not None, o.posted or date.min),
        reverse=True,
    )
