# AppliFlow

A job application tracker that keeps itself up to date.

Scans Gmail for job application emails and keeps a Google Sheet up to date: who you applied to, what stage each application is at, which ones have gone quiet, and how the whole search is going.

The point is to stop maintaining the tracker by hand. You apply, the confirmation lands in Gmail, and `scan` does the bookkeeping.

## What it does

- **Reads Gmail** for application confirmations, interview invites, assessments, offers, and rejections.
- **Classifies each email** into a status and pulls out the company and role.
- **Merges by application, not by email.** Three emails about one job become one row that advances Applied → Interview → Rejected, rather than three rows.
- **Flags stale applications** so you know who to chase.
- **Summarizes** counts by status, response rate, and how many applications you're sending per week.

Gmail access is read-only. The tool never sends, deletes, or modifies mail.

## Setup

### 1. Install

```bash
cd Stack-and-Tools
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

Python 3.11 or newer.

### 2. Create the Google credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project (any name).
2. **APIs & Services → Library**: enable both **Gmail API** and **Google Sheets API**.
3. **APIs & Services → OAuth consent screen**: choose **External**, fill in the required name and email, and add yourself under **Test users**. It can stay in "Testing" — you don't need to publish it.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type **Desktop app**.
5. Download the JSON and save it in this folder as `credentials.json`.

`credentials.json` and the `token.json` generated on first run are both gitignored. Don't commit them.

### 3. Point it at a sheet

Create a Google Sheet, then copy its ID out of the URL:

```
https://docs.google.com/spreadsheets/d/1AbCdEf...THIS_PART.../edit
```

```bash
copy config.example.toml config.toml   # Windows
# cp config.example.toml config.toml   # macOS / Linux
```

Put the ID in `config.toml`, then write the header row:

```bash
python -m appliflow init
```

A browser window opens the first time for you to grant access. After that the cached token is reused.

## Usage

```bash
# Scan Gmail and update the sheet
python -m appliflow scan

# See what a scan would change, without writing anything
python -m appliflow scan --dry-run

# Look further back than the configured default
python -m appliflow scan --days 180

# Applications with no movement in 14+ days
python -m appliflow followups
python -m appliflow followups --days 30

# Pipeline summary
python -m appliflow stats

# Fix a status by hand when the classifier gets one wrong
python -m appliflow status Stripe Interview
python -m appliflow status Acme Rejected --role "BI Engineer"
```

`scan` is safe to re-run. It merges into existing rows rather than duplicating them, so running it daily is fine.

Example output:

```
2 application(s) with no movement for 14+ days:

Company    Role             Status     Last update  Days
---------  ---------------  ---------  -----------  ----
Tokopedia  -                Applied    2026-06-27   40
Figma      Product Analyst  Interview  2026-07-12   25
```

## Finding new openings

`scan` tracks what you already applied to. `find` is the other half: it searches
public job-board APIs for roles you have not applied to yet.

```bash
python -m appliflow find --no-sheet        # print to terminal, no Google needed
python -m appliflow find --dry-run         # show what would be written
python -m appliflow find                   # write new rows to the Openings tab
python -m appliflow find --keyword "data analyst" --days 14
```

Configure sources under `[find]` in `config.toml`. The token is the company
handle from its careers URL:

| Their careers page | Put the token under |
| --- | --- |
| `boards.greenhouse.io/gitlab` | `greenhouse = ["gitlab"]` |
| `jobs.lever.co/netflix` | `lever = ["netflix"]` |
| `jobs.ashbyhq.com/ramp` | `ashby = ["ramp"]` |

Results are de-duplicated against what is already on the sheet, so repeat runs
only add genuinely new postings. A board that is down or misconfigured prints a
warning and the rest of the run continues.

### Reading job alerts from your inbox

ATS coverage is good for global tech companies and thin for Indonesian
employers, many of whom only post to LinkedIn, JobStreet, Glints, Kalibrr, or
Glassdoor. Those sites have no public API, and scraping them means violating
their terms and risking your own account.

All of them will email you new postings if you set up a job alert. Those emails
are in your inbox, so reading them needs no scraping and no login. Turn on
`[find.alerts]` in `config.toml` and `find` picks them up alongside the APIs.

```bash
python -m appliflow find --explain --no-sheet   # show what was read from each email
```

Set your alerts to **Daily** (not weekly) and make sure **email** delivery is
on, not just in-app notifications.

Two honest limits: alerts arrive daily rather than in real time, and they
usually list only the top matches rather than everything. Good enough for a job
hunt, but not a complete or instant feed.

#### How the parsing survives redesigns

Alert emails are parsed by **link pattern, not by template**. Rather than
looking for a particular `<div>`, it finds links pointing at a job-URL shape
(`linkedin.com/jobs/view/<id>`) and reads the anchor text as the title. A board
can redesign its email freely; as long as the links still point at jobs, this
keeps working.

Job IDs come out of the URL, so click-tracking parameters cannot create
duplicates -- the same posting in Monday's and Tuesday's alert collapses to one
row.

Company and location are the soft spot: they sit in loose text next to the
link, and every board arranges it differently. That is what `--explain` is for
-- it prints what was extracted so you can spot a misread and report it. Your
mail stays on your machine.


### Why these sources and not LinkedIn

These are official, documented, public endpoints. No API key, no login, no
scraping, and they do not break when a site is redesigned. Scraping LinkedIn or
JobStreet means violating their terms of service, fighting anti-bot systems, and
putting your own account credentials in a config file. Not worth it for a tool
you want to keep working -- and unnecessary, since those boards will email you
the same postings (see above).

The trade-off is honest: coverage depends on which ATS a company uses. Greenhouse
and Lever are common at global tech companies, less so at Indonesian employers,
many of whom run their own career pages. Check a company's careers URL before
adding it.


## The sheet

`init` writes these columns. Anything you type into **Notes** is preserved across scans.

| Column | Filled by |
| --- | --- |
| Company | Scan |
| Role | Scan |
| Status | Scan, or `status` command |
| Applied Date | Scan — earliest email seen |
| Last Update | Scan — most recent email seen |
| Last Subject | Scan |
| Thread ID | Scan — used to match emails to rows |
| Notes | You |

Editing Company or Role by hand is fine and often useful — a corrected company name makes future matching better.

## How the matching works

Deciding that two emails are about the *same* application is the part that's easy to get wrong, so it goes in this order:

1. **Gmail thread ID.** Same thread, same application.
2. **Company + role.** Catches the common case where the rejection arrives on a new thread weeks later.
3. **Company alone**, but only when the email named no role and that company has exactly one application still open. If it's ambiguous, a new row is created rather than guessing.

Status only moves forward, so an old confirmation email can't drag a record back from Interview to Applied. A rejection always wins and is final.

Emails where the employer can't be identified are logged as `Unknown` and never merged with each other — only a shared thread ID can join them. Rename them in the sheet and future emails will match properly.

## Accuracy

This is pattern matching over email text, not magic. It handles the standard phrasings well, but expect to correct the occasional row:

- **Applicant tracking systems hide the employer.** Mail from Greenhouse, Lever, Workday and friends has no company in the sender domain, so the tool falls back to the sender's display name and then the subject line. Some still land as `Unknown`.
- **Vague subject lines** like "Update on your application" yield no role.
- **Unusual rejection wording** may be read as a plain confirmation.

Use `scan --dry-run` the first few times to see what it's doing before it writes, and fix anything wrong with the `status` command or by editing the sheet.

## Tests

The classification, matching, and reporting logic is pure and covered by tests that don't touch Google:

```bash
python -m pytest
```

## Layout

```
Stack-and-Tools/   (branch: AppliFlow)
├── appliflow/
│   ├── classify.py   # email text -> status, company, role
│   ├── records.py    # Application record, merge rules, matching
│   ├── sync.py       # email -> record
│   ├── reports.py    # follow-ups and summary stats
│   ├── gmail.py      # Gmail search and MIME parsing
│   ├── sheet.py      # Google Sheets read/write
│   ├── auth.py       # OAuth
│   ├── config.py     # config.toml loading
│   └── cli.py        # commands
└── tests/
```

Google API calls live only in `gmail.py`, `sheet.py`, and `auth.py`. Everything else is plain data in, plain data out.
