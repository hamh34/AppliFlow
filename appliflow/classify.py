"""Turn a recruiting email into a status, a company, and a role.

Everything here is heuristic pattern matching over the subject and body text.
It is deliberately pure and dependency-free so the rules can be unit tested
without touching Gmail.
"""

from __future__ import annotations

import re

from .records import Status

# (pattern, weight). Weight reflects how strongly a phrase implies the status;
# vague phrases score low so a single specific phrase elsewhere can outrank them.
_PATTERNS: dict[Status, list[tuple[str, int]]] = {
    Status.REJECTED: [
        (r"regret to inform", 10),
        (r"we (?:have |'ve )?decided (?:not to|to not) (?:move|proceed|progress|continue)", 10),
        (r"not (?:be )?(?:moving|proceeding|progressing|continuing) (?:forward|ahead|with)", 10),
        (r"will not be (?:moving|proceeding|progressing)", 10),
        (r"(?:move|moving|proceed|proceeding) forward with (?:other|another|different)", 10),
        (r"pursuing other (?:candidates|applicants)", 10),
        (r"no longer (?:under consideration|being considered)", 10),
        (r"we (?:are|'re) unable to (?:move|offer|proceed)", 10),
        (r"other candidates whose", 9),
        (r"(?:was|were) not selected", 9),
        (r"application (?:was |is |has been )?unsuccessful", 9),
        (r"(?:position|role|vacancy) has been filled", 9),
        (r"decided to (?:move|proceed) with", 9),
        (r"not (?:a |the )?(?:right |best |good )?(?:fit|match) (?:for|at) (?:this|the)", 8),
        (r"unfortunately", 8),
        (r"keep your (?:resume|cv|details|profile) on file", 5),
    ],
    Status.OFFER: [
        (r"offer of employment", 10),
        (r"(?:pleased|delighted|happy|excited) to (?:extend|offer|make you)", 10),
        (r"extend(?:ing)? (?:you )?an offer", 10),
        (r"offer letter", 9),
        (r"job offer", 9),
    ],
    Status.INTERVIEW: [
        (r"invit(?:e|ation|ing) (?:you )?(?:to|for) (?:an? )?(?:interview|conversation)", 10),
        (r"interview (?:invitation|request|invite)", 10),
        (r"schedule (?:an?|your|the) (?:interview|call|chat|conversation|time)", 9),
        (r"(?:phone|initial|recruiter) screen", 9),
        (r"set up (?:an?|some) time", 8),
        (r"would (?:like|love) to (?:speak|chat|talk|meet) with you", 8),
        (r"(?:book|pick|choose|select) a time", 7),
        (r"next steps? in (?:the|our)", 7),
        (r"your availability", 6),
    ],
    Status.ASSESSMENT: [
        (r"(?:online|technical|skills?|coding) assessment", 10),
        (r"coding (?:challenge|test|exercise)", 10),
        (r"take[- ]?home (?:assignment|test|exercise|challenge|project)", 10),
        (r"hackerrank|codility|codesignal|karat|woven|testgorilla|coderbyte", 9),
        (r"complete (?:the|a|this) (?:assessment|test|challenge|exercise)", 8),
    ],
    Status.APPLIED: [
        (r"thank(?:s| you) for (?:applying|your application)", 10),
        (r"(?:we|we've|we have) (?:received|got) your application", 10),
        (r"application (?:has been )?received", 9),
        (r"application (?:was |has been )?submitted", 9),
        (r"successfully applied", 9),
        (r"thank(?:s| you) for your interest in", 7),
        (r"your application (?:to|for)", 6),
    ],
}

# Applied is excluded: a confirmation phrase like "thank you for applying" shows
# up in rejections and interview invites too, so it only wins by default.
_PROGRESS_STATUSES = [
    Status.REJECTED,
    Status.OFFER,
    Status.INTERVIEW,
    Status.ASSESSMENT,
]


def _score(text: str, patterns: list[tuple[str, int]]) -> float:
    weights = [w for pattern, w in patterns if re.search(pattern, text)]
    if not weights:
        return 0.0
    # The strongest phrase dominates; extra corroborating phrases nudge it up.
    return max(weights) + 0.1 * (len(weights) - 1)


def classify(subject: str, body: str = "") -> Status:
    """Classify an email into a pipeline status."""
    text = f"{subject}\n{body}".lower()

    best_status = Status.UNKNOWN
    best_score = 0.0
    for status in _PROGRESS_STATUSES:
        score = _score(text, _PATTERNS[status])
        if score > best_score:
            best_status, best_score = status, score

    if best_score > 0:
        return best_status
    if _score(text, _PATTERNS[Status.APPLIED]) > 0:
        return Status.APPLIED
    return Status.UNKNOWN


# Domains that tell us nothing about the employer: applicant tracking systems,
# job boards, and consumer mail providers.
_OPAQUE_DOMAINS = {
    "greenhouse.io", "us.greenhouse-mail.io", "greenhouse-mail.io", "lever.co",
    "hire.lever.co", "myworkday.com", "workday.com", "workdaysuite.com",
    "taleo.net", "icims.com", "smartrecruiters.com", "ashbyhq.com",
    "jobvite.com", "bamboohr.com", "breezy.hr", "recruitee.com",
    "workable.com", "workablemail.com", "teamtailor.com", "successfactors.com",
    "avature.net", "jazzhr.com", "applytojob.com", "rippling.com",
    "indeed.com", "indeedemail.com", "linkedin.com", "glassdoor.com",
    "ziprecruiter.com", "dice.com", "monster.com", "seek.com.au", "jobstreet.com",
    "google.com", "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
    "yahoo.com", "icloud.com", "proton.me", "sendgrid.net", "mailgun.org",
}

_NAME_NOISE = re.compile(
    r"\b(careers?|recruit(?:ing|ment|er)?|talent(?: acquisition)?|hiring(?: team)?"
    r"|hr|people(?: team)?|jobs?|team|notifications?|no[- ]?reply|donotreply"
    r"|do[- ]?not[- ]?reply|via|support|info|admin|mailer)\b",
    re.IGNORECASE,
)

_TWO_PART_TLDS = {
    "co.uk", "com.au", "co.id", "co.jp", "com.br", "co.nz", "co.in",
    "com.sg", "co.za", "com.mx", "com.tr", "co.kr", "com.hk",
}


def _clean_name(raw: str) -> str | None:
    without_noise = _NAME_NOISE.sub(" ", raw or "")
    cleaned = re.sub(r"[^\w&'\- ]+", " ", without_noise)
    cleaned = " ".join(cleaned.split()).strip(" -&")
    return cleaned or None


def _company_from_domain(email: str) -> str | None:
    match = re.search(r"@([\w.\-]+)", email or "")
    if not match:
        return None
    domain = match.group(1).lower().rstrip(".")
    if domain in _OPAQUE_DOMAINS:
        return None

    parts = domain.split(".")
    if len(parts) < 2:
        return None
    # Registered name sits before the public suffix, which may be two labels.
    suffix_len = 2 if ".".join(parts[-2:]) in _TWO_PART_TLDS else 1
    label_index = -(suffix_len + 1)
    if len(parts) < suffix_len + 1:
        return None
    label = parts[label_index]

    # A parent domain in the opaque list means the subdomain is not an employer.
    if ".".join(parts[label_index:]) in _OPAQUE_DOMAINS:
        return None

    label = label.replace("-", " ").replace("_", " ").strip()
    if not label or label in {"mail", "email", "smtp", "mx"}:
        return None
    return " ".join(word.capitalize() for word in label.split())


_SUBJECT_COMPANY_PATTERNS = [
    r"(?:applying|application) (?:to|at|with) (?P<company>[\w&.\-' ]+)",
    r"(?:your |the )?application (?:for|to) .+? at (?P<company>[\w&.\-' ]+)",
    r"(?:your interest in) (?:a (?:role|position) at )?(?P<company>[\w&.\-' ]+)",
]


def extract_company(sender_name: str, sender_email: str, subject: str = "") -> str:
    """Best guess at the employer behind an email."""
    from_domain = _company_from_domain(sender_email)
    if from_domain:
        return from_domain

    cleaned_name = _clean_name(sender_name)
    if cleaned_name and cleaned_name.lower() not in {
        d.split(".")[0] for d in _OPAQUE_DOMAINS
    }:
        return cleaned_name

    for pattern in _SUBJECT_COMPANY_PATTERNS:
        match = re.search(pattern, subject or "", re.IGNORECASE)
        if match:
            candidate = _clean_name(match.group("company"))
            if candidate:
                return candidate

    return "Unknown"


_ROLE_PATTERNS = [
    r"application for (?:the )?(?P<role>.+?)(?:\s+(?:position|role|opening|job|vacancy))?(?:\s+(?:at|with)\s|[-–—|]|$)",
    r"applying (?:to|for) (?:the )?(?P<role>.+?)(?:\s+(?:position|role|opening|job))?(?:\s+(?:at|with)\s|[-–—|]|$)",
    r"your application (?:to|for) (?:the )?(?P<role>.+?)(?:\s+(?:position|role|opening))?(?:\s+(?:at|with)\s|[-–—|]|$)",
    r"(?:interest in|regarding) (?:the )?(?P<role>.+?)\s+(?:position|role|opening)",
    r"^(?P<role>[^-–—|]{3,60}?)\s*[-–—|]\s*(?:application|thank you|we received|interview)",
]

_ROLE_JUNK = re.compile(
    r"^(?:your|our|a|an|the|this|that|us|we|it|job|position|role)$", re.IGNORECASE
)


def extract_role(subject: str) -> str | None:
    """Pull a job title out of a subject line, or None when there isn't one."""
    for pattern in _ROLE_PATTERNS:
        match = re.search(pattern, subject or "", re.IGNORECASE)
        if not match:
            continue
        role = " ".join(match.group("role").split()).strip(" -–—|:,.")
        # Strip a leading article the pattern may have left behind.
        role = re.sub(r"^(?:the|a|an)\s+", "", role, flags=re.IGNORECASE)
        if len(role) < 3 or _ROLE_JUNK.match(role):
            continue
        return role
    return None
