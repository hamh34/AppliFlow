"""Read-only views over the tracked applications: follow-ups and summary stats."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .records import OPEN_STATUSES, Application, Status


def stale_applications(
    applications: list[Application], as_of: date, min_days: int
) -> list[tuple[Application, int]]:
    """Open applications with no movement for at least `min_days`, stalest first.

    Age is measured from the last update we saw, falling back to the applied
    date. Closed applications (offer, rejection) are never chased.
    """
    aged: list[tuple[Application, int]] = []
    for app in applications:
        if app.status not in OPEN_STATUSES:
            continue
        reference = app.last_update or app.applied_date
        if reference is None:
            continue
        age = (as_of - reference).days
        if age >= min_days:
            aged.append((app, age))

    aged.sort(key=lambda pair: (-pair[1], pair[0].company.lower()))
    return aged


@dataclass
class Summary:
    total: int = 0
    by_status: dict[Status, int] = field(default_factory=dict)
    responses: int = 0
    response_rate: float = 0.0
    per_week: float = 0.0
    first_applied: date | None = None
    last_applied: date | None = None


def summarize(applications: list[Application]) -> Summary:
    """Counts by status, response rate, and application pace."""
    summary = Summary(total=len(applications))
    summary.by_status = {status: 0 for status in Status}
    for app in applications:
        summary.by_status[app.status] += 1

    # A response is any movement past the automated "we got it" acknowledgement.
    # A rejection counts: it is still the company answering.
    summary.responses = sum(
        1
        for app in applications
        if app.status not in {Status.APPLIED, Status.UNKNOWN}
    )
    if summary.total:
        summary.response_rate = summary.responses / summary.total

    dates = sorted(app.applied_date for app in applications if app.applied_date)
    if dates:
        summary.first_applied = dates[0]
        summary.last_applied = dates[-1]
        span_days = (dates[-1] - dates[0]).days
        # A single day of applications still counts as one week of activity.
        weeks = max(span_days / 7, 1.0)
        summary.per_week = len(dates) / weeks

    return summary
