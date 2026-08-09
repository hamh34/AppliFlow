# AppliFlow

Personal job-hunting tool. Two halves that share one Google Sheet:

- **`scan`** — reads Gmail for replies to jobs you already applied to, and keeps
  a status per application (tracker; runs *after* you apply).
- **`find`** — pulls openings you have not applied to yet, from public job-board
  APIs and from job-alert emails (finder; runs *before* you apply).

## Conventions

- Python 3.11+, standard library first. Dependencies only where they earn their
  place — `find` and the alert parser add none.
- Pure logic stays pure and unit tested; API calls live in thin, isolated
  modules (`sheet.py`, `gmail.py`, the fetch half of `sources.py`).
- Parsers never raise on bad input. One malformed posting or one board being
  down costs that item, never the whole run.
- Credentials and local config are gitignored, never committed.

## Layout

| File | Role |
| --- | --- |
| `records.py` | `Application` + status model (used by `scan`) |
| `classify.py` | Decides status from email text |
| `openings.py` | `Opening` + filtering, dedupe, sorting (used by `find`) |
| `sources.py` | Job-board APIs: fetch split from parse, so parsers are testable |
| `alerts.py` | Job-alert email parsing |
| `gmail.py` | Gmail reads. `_extract_body` for prose, `extract_html` for links |
| `sheet.py` | `Tracker` (Applications tab), `OpeningsSheet` (Openings tab) |
| `diagnose.py` | Setup checks behind `doctor` |

## Design decisions worth not re-litigating

**No scraping of LinkedIn / JobStreet / Glints.** It violates their terms, needs
real account credentials in a config file, and risks getting the user's own
account banned — a serious cost for someone job hunting. Playwright does not
change this: it solves rendering, not authorization. Those boards will email
their postings via job alerts, which is the path this tool takes.

**Job-board sources are official public APIs** (Greenhouse, Lever, Ashby,
Arbeitnow, RemoteOK). No API key, no login, and they do not break on redesigns.
Coverage is honestly thin for Indonesian employers, which is why alerts matter.

**Alert emails are parsed by link pattern, not email template.** `alerts.py`
finds links matching a job-URL shape (`linkedin.com/jobs/view/<id>`) and reads
the anchor text as the title. A board can redesign its email freely and this
keeps working. Job IDs come from the URL, so click-tracking parameters cannot
create duplicate rows.

**`_extract_body` is not reusable for alerts.** It strips tags — including the
`href`s the links live in — and truncates at 4000 chars, which would silently
drop most of an email listing ten jobs. `extract_html` is the separate path,
opt-in via `include_html` so `scan` keeps its shape. There is a regression test
for this.

## Current state (Aug 2026)

Code is complete and pushed on branch `AppliFlow`; **193 tests pass**. Setup on
the user's machine is not finished.

Remaining steps, in order:

1. Turn on job alerts at LinkedIn, JobStreet, Glints, Kalibrr, Glassdoor —
   frequency **Daily**, email delivery on (not just in-app).
2. Put `config.toml` and `credentials.json` in the repo root.
   `credentials.json` is the OAuth client JSON downloaded from Google Cloud,
   renamed from its `client_secret_*.json` download name. It must be a
   **Desktop app** client; a Web client cannot complete the local browser flow.
3. `python -m appliflow doctor` — fixes anything marked `[--]`.
4. `python -m appliflow init` — browser OAuth. Sign in as the account that owns
   the spreadsheet, or writes will fail confusingly.
5. Once the first alert email arrives (usually within 24h):
   `python -m appliflow find --explain --no-sheet`

### Known untested risk

The link patterns in `alerts.py` were written from knowledge of these sites' URL
structures and have **never been checked against a real alert email** — the
environment they were built in blocks those hosts. Titles are likely correct
(they come from anchor text). **Company and location are the weak spot**: they
are read from loose text beside the link by `_split_details`, and each board
arranges it differently.

`find --explain` prints what was extracted from each email so misreads can be
spotted and the per-board heuristics corrected. That is the intended next
iteration. The user's mail never needs to leave their machine — the `--explain`
output is enough to fix the patterns.
