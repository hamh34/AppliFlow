"""Application records and the rules for merging new email evidence into them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Status(str, Enum):
    UNKNOWN = "Unknown"
    APPLIED = "Applied"
    ASSESSMENT = "Assessment"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"


# How far along the hiring pipeline each status sits. Rejected sits outside this
# ordering because it is terminal rather than a stage you advance through.
_RANK = {
    Status.UNKNOWN: 0,
    Status.APPLIED: 1,
    Status.ASSESSMENT: 2,
    Status.INTERVIEW: 3,
    Status.OFFER: 4,
    Status.REJECTED: 0,
}

OPEN_STATUSES = {Status.APPLIED, Status.ASSESSMENT, Status.INTERVIEW}
CLOSED_STATUSES = {Status.OFFER, Status.REJECTED}

COLUMNS = [
    "Company",
    "Role",
    "Status",
    "Applied Date",
    "Last Update",
    "Last Subject",
    "Thread ID",
    "Notes",
]


def parse_status(raw: str) -> Status:
    """Read a status back from the sheet, tolerating case and stray whitespace."""
    cleaned = (raw or "").strip().lower()
    for status in Status:
        if status.value.lower() == cleaned:
            return status
    return Status.UNKNOWN


def parse_date(raw: str) -> date | None:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _format_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def normalize(text: str | None) -> str:
    """Fold a company or role name down to a comparable key."""
    return " ".join((text or "").lower().split())


@dataclass
class Application:
    company: str
    role: str | None = None
    status: Status = Status.UNKNOWN
    applied_date: date | None = None
    last_update: date | None = None
    last_subject: str = ""
    thread_id: str = ""
    notes: str = ""
    # 1-indexed row in the worksheet; None until the record has been written.
    row: int | None = None

    def to_row(self) -> list[str]:
        return [
            self.company,
            self.role or "",
            self.status.value,
            _format_date(self.applied_date),
            _format_date(self.last_update),
            self.last_subject,
            self.thread_id,
            self.notes,
        ]

    @classmethod
    def from_row(cls, values: list[str], row: int | None = None) -> "Application":
        padded = list(values) + [""] * (len(COLUMNS) - len(values))
        return cls(
            company=padded[0].strip(),
            role=padded[1].strip() or None,
            status=parse_status(padded[2]),
            applied_date=parse_date(padded[3]),
            last_update=parse_date(padded[4]),
            last_subject=padded[5].strip(),
            thread_id=padded[6].strip(),
            notes=padded[7].strip(),
            row=row,
        )

    def absorb(self, incoming: "Application") -> bool:
        """Fold a newly seen email into this record. Returns True if anything changed.

        Status only moves forward through the pipeline, so an older confirmation
        email arriving after an interview invite cannot drag the record back to
        Applied. A rejection always wins, and once set it sticks.
        """
        changed = False

        new_status = self._resolved_status(incoming.status)
        if new_status != self.status:
            self.status = new_status
            changed = True

        # Applied date is the earliest evidence from either record. Reading
        # last_update here (before it advances below) means a hand-typed row
        # carrying only a last-update date still seeds a sensible applied date.
        known_dates = [
            value
            for value in (
                self.applied_date,
                self.last_update,
                incoming.applied_date,
                incoming.last_update,
            )
            if value is not None
        ]
        if known_dates and self.applied_date != min(known_dates):
            self.applied_date = min(known_dates)
            changed = True

        if incoming.last_update and (
            self.last_update is None or incoming.last_update > self.last_update
        ):
            self.last_update = incoming.last_update
            if incoming.last_subject:
                self.last_subject = incoming.last_subject
            changed = True

        if not self.role and incoming.role:
            self.role = incoming.role
            changed = True

        if not self.thread_id and incoming.thread_id:
            self.thread_id = incoming.thread_id
            changed = True

        return changed

    def _resolved_status(self, incoming: Status) -> Status:
        if incoming == Status.REJECTED:
            return Status.REJECTED
        if self.status == Status.REJECTED:
            return Status.REJECTED
        if _RANK[incoming] > _RANK[self.status]:
            return incoming
        return self.status


def match_application(
    existing: list[Application], incoming: Application
) -> Application | None:
    """Find the record an incoming email belongs to, or None if it is new.

    Thread ID is the strongest signal. Failing that we fall back to company plus
    role, and finally to company alone when the incoming email did not name a
    role and the company has exactly one application still open.
    """
    if incoming.thread_id:
        for app in existing:
            if app.thread_id and app.thread_id == incoming.thread_id:
                return app

    company_key = normalize(incoming.company)
    # An unidentified employer is not an identity: two "Unknown" records are not
    # the same application, so they can only ever be joined by thread ID.
    if company_key in {"", "unknown"}:
        return None

    same_company = [a for a in existing if normalize(a.company) == company_key]
    if not same_company:
        return None

    if incoming.role:
        role_key = normalize(incoming.role)
        for app in same_company:
            if normalize(app.role) == role_key:
                return app
        # A named role that matches nothing on file is a separate application,
        # unless the record on file has no role recorded at all.
        unroled = [a for a in same_company if not a.role]
        return unroled[0] if len(unroled) == 1 else None

    open_apps = [a for a in same_company if a.status not in CLOSED_STATUSES]
    if len(open_apps) == 1:
        return open_apps[0]
    if len(same_company) == 1:
        return same_company[0]
    return None


def merge_all(
    existing: list[Application], incoming: list[Application]
) -> tuple[list[Application], list[Application]]:
    """Fold a chronological run of email-derived records into the tracked set.

    `incoming` must be sorted oldest first so status progresses in the order the
    emails actually arrived. Returns (updated existing records, brand new ones).
    Records created mid-run are matchable by later emails in the same run.
    """
    working = list(existing)
    added: list[Application] = []
    changed: list[Application] = []
    added_ids: set[int] = set()
    changed_ids: set[int] = set()

    for record in incoming:
        match = match_application(working, record)
        if match is None:
            working.append(record)
            added.append(record)
            added_ids.add(id(record))
            continue
        if not match.absorb(record):
            continue
        # A record added during this run is written as a new row, not an update.
        if id(match) in added_ids or id(match) in changed_ids:
            continue
        changed.append(match)
        changed_ids.add(id(match))

    return changed, added
