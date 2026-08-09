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

Code is complete and pushed on `main`; **239 tests pass**. Setup on the user's
machine is **finished** — `doctor` is all green, and `find` and `scan` both run
end to end and write to the spreadsheet.

Remaining work is tuning, not setup:

- **The upstream alerts are the bottleneck, not the filters.** The most common
  words across 404 real postings were `consultant`, `analyst`, `intern`,
  `manager`, `accountant` — not one sustainability term. The board-side alert
  searches were set up before the ESG focus, so no amount of `keywords` tuning
  helps; this tool can only filter what the boards send.
- Glints and Kalibrr sent **zero** emails in a 7-day window. Alerts there are
  either off or in-app only.
- Automating a daily run (Windows Task Scheduler) is the last step.

### What real mail actually showed

The link patterns had never met a real alert email. When they did, the guesses
recorded here were half wrong, and worth keeping straight:

**Titles were right** (anchor text), as predicted. **Company was predicted to be
the weak spot and was not** — on LinkedIn the `·` separator works and company
came out right on every row. **Location was the field that broke**, for a reason
not anticipated: LinkedIn's card decoration (`Actively recruiting`, `Easy
Apply`, `17 connections`, `10 school alumni`) sits in its own elements, so
joining the trailing text runs it into the end of the location. Stripped before
the split now.

**Glassdoor needed a different parser entirely.** It puts the whole card inside
the anchor and leaves nothing after the link, so the generic path had no text to
read and all 200 of its postings came back with no company and no location. The
card is `<Company> [<rating> ★] <Title> <Location> [badge] [salary] [Easy Apply]
<age>`; the rating is the only delimiter. Derived from the 180 distinct cards in
one run: company fills on 149/180 (exactly the rated rows), location on 180/180.
Cards without a rating keep the employer in the title — no delimiter exists, and
a guessed boundary is worse than none.

**JobStreet cannot be parsed at all**, and this is not a bug to fix. Every link
goes through `url.jobstreet.com` with the destination inside encrypted path
segments, not a query parameter, so `unwrap_url` cannot recover a job id and no
pattern can. Recovering it would mean following redirects over HTTP from inside
the parser. 11 emails, 0 postings, by design on their side.

**Location filtering costs more than it saves here.** Measured on 392 real
postings, `locations = ["Jakarta", "Indonesia", "Remote"]` dropped 45, of which
only 10 were genuinely foreign (Singapore). The rest were Indonesian —
Tangerang, Bekasi, Bandung, Cikarang, and Jakarta *districts* like Gambir and
Mampang Prapatan, which contain neither "Jakarta" nor "Indonesia". Chasing that
with a longer city list does not converge. `locations = []` is the right setting
while the alerts are already Indonesia-targeted.

### Two failures that cost a whole run

Both hit while capturing `--explain` output on Windows, both after the expensive
Gmail fetch was already paid for:

- A week of alerts is 82 messages, each its own API call, so one connect timeout
  is ordinary rather than exceptional — and it discarded all 82. Gmail calls now
  retry.
- A redirected stdout on Windows falls back to the ANSI code page, so the first
  title carrying `★` raised `UnicodeEncodeError` mid-print. Only the *error
  handler* is overridden, not the encoding: forcing UTF-8 cures the crash but
  hands PowerShell bytes it decodes with the console code page, turning every
  bullet in `split from:` into mojibake — the one character a reader most needs.

### How `--explain` earns its keep

It prints evidence, not just verdicts, so one run corrects a heuristic without a
second round trip:

- `split from:` is the exact string `_split_details` was handed. `(no text found
  after the link)` is what identified the entire Glassdoor problem in one line.
- Job-shaped links that produced no row are listed with the reason.
- When an email yields nothing, the links it *did* see are grouped by host, each
  with its anchor text — because when a URL is an opaque redirect, the anchor is
  all that is left to build a posting from.

`redact_url` scrubs every URL on the way out: tracking parameter values, opaque
path segments, and email addresses go, while numeric job ids and parameter
*names* stay. So the output is safe to paste elsewhere — a board hiding its id
in an unexpected parameter still shows as `?vacancyId=<redacted>`, which names
what to ask for.

One defect this surfaced *before* any real mail arrived: `senders` accepted
`jobstreet.com` while the link pattern only matched `jobstreet.co.id`. An
inconsistency between `senders` and `pattern` is invisible until an email lands.
