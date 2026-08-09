"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import config as config_module
from .auth import AuthError, get_credentials
from .config import ConfigError
from .records import Status, merge_all, normalize, parse_status
from .reports import stale_applications, summarize


def _connect(args):
    """Load config and open an authenticated tracker."""
    from .sheet import Tracker

    config = config_module.load(args.config)
    creds = get_credentials(config.credentials_file, config.token_file)
    return config, creds, Tracker(creds, config.spreadsheet_id, config.worksheet)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(out)


def _describe(app) -> str:
    return f"{app.company} - {app.role}" if app.role else app.company


def cmd_init(args) -> int:
    _, _, tracker = _connect(args)
    if tracker.ensure_header():
        print("Header row written. Your sheet is ready.")
    else:
        print("Header row already present; nothing to do.")
    return 0


def cmd_scan(args) -> int:
    from .gmail import fetch_messages
    from .sync import applications_from_messages

    config, creds, tracker = _connect(args)
    lookback = args.days or config.lookback_days
    query = config.query(lookback)

    print(f"Searching Gmail for the last {lookback} days...")
    messages = fetch_messages(creds, query, max_results=args.max)
    candidates = applications_from_messages(messages)
    print(f"{len(messages)} messages matched, {len(candidates)} look application-related.")

    tracker.ensure_header()
    existing = tracker.read_all()
    changed, added = merge_all(existing, candidates)

    if added:
        print("\nNew applications:")
        print(_table(
            ["Company", "Role", "Status", "Date"],
            [
                [a.company, a.role or "-", a.status.value,
                 a.applied_date.isoformat() if a.applied_date else "-"]
                for a in added
            ],
        ))
    if changed:
        print("\nUpdated:")
        print(_table(
            ["Company", "Role", "Status", "Last update"],
            [
                [a.company, a.role or "-", a.status.value,
                 a.last_update.isoformat() if a.last_update else "-"]
                for a in changed
            ],
        ))
    if not added and not changed:
        print("\nNothing new since the last scan.")
        return 0

    if args.dry_run:
        print("\nDry run: the sheet was not modified.")
        return 0

    tracker.update(changed)
    tracker.append(added)
    print(f"\nSheet updated: {len(added)} added, {len(changed)} changed.")
    return 0


def cmd_doctor(args) -> int:
    """Check the setup end to end and name the specific fix for anything broken."""
    from . import diagnose
    from .diagnose import FAIL, OK, Check

    config_path = Path(args.config) if args.config else config_module.DEFAULT_CONFIG_PATH
    checks = [diagnose.check_config(config_path)]

    print("Checking your setup...\n")

    config = None
    if checks[0].state == OK:
        try:
            config = config_module.load(config_path)
        except ConfigError as exc:
            checks[0] = Check("config.toml", FAIL, str(exc).splitlines()[0])

    if config is None:
        print(diagnose.render(checks))
        print("\nFix the above, then run doctor again.")
        return 1

    checks.append(diagnose.check_credentials(config.credentials_file))
    checks.append(diagnose.check_token(config.token_file))
    checks.append(diagnose.check_alert_config(config.find.alerts))

    # Live checks only make sense once the local files are in order.
    if all(check.state != FAIL for check in checks[:3]):
        try:
            creds = get_credentials(config.credentials_file, config.token_file)
            checks.append(_check_sheet(creds, config))
            if config.find.alerts.enabled:
                checks.append(_check_inbox(creds, config))
        except AuthError as exc:
            checks.append(Check("google sign-in", FAIL, str(exc).splitlines()[0],
                                "run: python -m appliflow init"))
        except Exception as exc:  # network, API not enabled, revoked access
            checks.append(Check("google sign-in", FAIL, f"{type(exc).__name__}: {exc}",
                                "check that Gmail API and Sheets API are enabled "
                                "in your Google Cloud project"))

    print(diagnose.render(checks))
    failed = [check for check in checks if check.state == FAIL]
    print()
    if failed:
        print(f"{len(failed)} problem(s) found. Fix the ones marked [--] above.")
        return 1
    print("Everything checks out. Try: python -m appliflow find --explain --no-sheet")
    return 0


def _check_sheet(creds, config):
    """Confirm the signed-in account can actually open the configured sheet."""
    from googleapiclient.discovery import build

    from .diagnose import FAIL, OK, Check

    try:
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = service.spreadsheets().get(spreadsheetId=config.spreadsheet_id).execute()
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            return Check("spreadsheet", FAIL, "not found",
                         "check spreadsheet_id in config.toml")
        if status == 403:
            return Check(
                "spreadsheet", FAIL, "signed-in account cannot open it",
                "sign in as the account that owns the sheet, or share the sheet "
                "with the account you authorized",
            )
        return Check("spreadsheet", FAIL, f"{type(exc).__name__}: {exc}")

    title = meta.get("properties", {}).get("title", "untitled")
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    return Check("spreadsheet", OK, f"'{title}' reachable; tabs: {', '.join(tabs)}")


def _check_inbox(creds, config):
    """Report whether alert emails have actually started arriving yet."""
    from googleapiclient.discovery import build

    from .alerts import gmail_query
    from .diagnose import FAIL, OK, Check

    query = gmail_query(config.find.alerts.lookback_days, config.find.alerts.boards)
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        response = (
            service.users().messages()
            .list(userId="me", q=query, maxResults=10)
            .execute()
        )
    except Exception as exc:
        return Check("job alerts in inbox", FAIL, f"{type(exc).__name__}: {exc}")

    count = len(response.get("messages", []))
    if count == 0:
        return Check(
            "job alerts in inbox", FAIL,
            f"no alert emails in the last {config.find.alerts.lookback_days} days",
            "turn on job alerts (LinkedIn, JobStreet, Glints, Kalibrr, Glassdoor), "
            "set them to Daily with email delivery on, and wait for the first one",
        )
    more = "+" if count >= 10 else ""
    return Check("job alerts in inbox", OK, f"{count}{more} alert email(s) found")


def _explain_alerts(messages) -> None:
    """Show what was pulled out of each alert email, so misreads are visible.

    Nothing leaves the machine: this prints locally so you can compare the
    parser's guesses against the mail in front of you and report what is wrong.
    """
    from .alerts import openings_from_html

    print("\n--- alert parsing detail ---")
    for message in messages:
        openings = openings_from_html(message.html)
        received = message.received.isoformat() if message.received else "unknown date"
        print(f"\n{message.sender_email} | {received}")
        print(f"  subject: {message.subject[:70]}")
        if not message.html:
            print("  (no HTML part found in this email)")
            continue
        if not openings:
            print("  no job links matched -- send this sender/subject over so the "
                  "pattern can be added")
            continue
        for opening in openings:
            print(f"  - title:    {opening.title}")
            print(f"    company:  {opening.company or '(not found)'}")
            print(f"    location: {opening.location or '(not found)'}")
            print(f"    source:   {opening.source}")
    print("\n--- end detail ---\n")


def cmd_find(args) -> int:
    from .openings import dedupe, exclude_known, filter_openings, sort_openings
    from .sheet import OpeningsSheet
    from .sources import collect

    config = config_module.load(args.config)
    settings = config.find

    if not settings.has_sources:
        print(
            "No job sources configured. Add company tokens under [find] in "
            "config.toml - see config.example.toml for the format.",
            file=sys.stderr,
        )
        return 1

    keywords = args.keyword or settings.keywords
    # --days 0 means "no age limit"; omitting the flag keeps the configured value.
    max_age = settings.max_age_days if args.days is None else (args.days or None)

    # Google auth is needed for alerts, for the sheet, or for both. Fetch it at
    # most once, and only if something actually asks for it.
    cached: dict = {}

    def credentials():
        if "creds" not in cached:
            cached["creds"] = get_credentials(config.credentials_file, config.token_file)
        return cached["creds"]

    warnings: list[str] = []
    found = []

    if any([settings.greenhouse, settings.lever, settings.ashby,
            settings.arbeitnow, settings.remoteok]):
        print("Fetching job boards...")
        found = collect(
            settings, on_error=lambda label, msg: warnings.append(f"{label}: {msg}")
        )

    if settings.alerts.enabled:
        from .alerts import gmail_query, openings_from_messages
        from .gmail import fetch_messages

        print("Reading job alerts from Gmail...")
        try:
            messages = fetch_messages(
                credentials(),
                gmail_query(settings.alerts.lookback_days, settings.alerts.boards),
                max_results=settings.alerts.max_emails,
                include_html=True,
            )
            from_alerts = openings_from_messages(messages)
            print(f"  {len(messages)} alert email(s), {len(from_alerts)} posting(s) linked.")
            if args.explain:
                _explain_alerts(messages)
            found.extend(from_alerts)
        except AuthError as exc:
            warnings.append(f"alerts: {exc}")

    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)

    if not found:
        print("No postings returned. Check the company tokens in your config.")
        return 0

    matching = sort_openings(dedupe(filter_openings(
        found,
        keywords=keywords,
        locations=settings.locations,
        max_age_days=max_age,
        as_of=date.today(),
    )))
    print(f"{len(found)} postings fetched, {len(matching)} match your filters.")
    if not matching:
        return 0

    def render(openings):
        return _table(
            ["Company", "Title", "Location", "Posted"],
            [
                [o.company, o.title, o.location or "-",
                 o.posted.isoformat() if o.posted else "-"]
                for o in openings[: args.limit]
            ],
        )

    if args.no_sheet:
        print()
        print(render(matching))
        return 0

    sheet = OpeningsSheet(credentials(), config.spreadsheet_id, settings.worksheet)
    sheet.ensure_tab()
    sheet.ensure_header()

    fresh = exclude_known(matching, sheet.known_urls())
    if not fresh:
        print("Nothing new since the last run.")
        return 0

    print(f"\n{len(fresh)} new posting(s):\n")
    print(render(fresh))

    if args.dry_run:
        print("\nDry run: the sheet was not modified.")
        return 0

    sheet.append(fresh)
    print(f"\nAdded {len(fresh)} row(s) to '{settings.worksheet}'.")
    return 0


def cmd_followups(args) -> int:
    config, _, tracker = _connect(args)
    threshold = args.days or config.followup_days
    aged = stale_applications(tracker.read_all(), as_of=date.today(), min_days=threshold)

    if not aged:
        print(f"Nothing has been quiet for {threshold}+ days. All caught up.")
        return 0

    print(f"{len(aged)} application(s) with no movement for {threshold}+ days:\n")
    print(_table(
        ["Company", "Role", "Status", "Last update", "Days"],
        [
            [a.company, a.role or "-", a.status.value,
             (a.last_update or a.applied_date).isoformat(), str(age)]
            for a, age in aged
        ],
    ))
    return 0


def cmd_stats(args) -> int:
    _, _, tracker = _connect(args)
    summary = summarize(tracker.read_all())

    if not summary.total:
        print("No applications tracked yet. Run 'scan' first.")
        return 0

    print(f"Total applications   {summary.total}")
    for status in Status:
        count = summary.by_status.get(status, 0)
        if count:
            print(f"  {status.value:<19}{count}")
    print(f"\nResponses            {summary.responses} ({summary.response_rate:.0%})")
    print(f"Pace                 {summary.per_week:.1f} / week")
    if summary.first_applied:
        print(f"Window               {summary.first_applied} to {summary.last_applied}")
    return 0


def cmd_status(args) -> int:
    new_status = parse_status(args.new_status)
    if new_status is Status.UNKNOWN:
        valid = ", ".join(s.value for s in Status if s is not Status.UNKNOWN)
        print(f"Unknown status '{args.new_status}'. Valid values: {valid}", file=sys.stderr)
        return 1

    _, _, tracker = _connect(args)
    applications = tracker.read_all()

    company_key = normalize(args.company)
    matches = [a for a in applications if company_key in normalize(a.company)]
    if args.role:
        role_key = normalize(args.role)
        matches = [a for a in matches if role_key in normalize(a.role)]

    if not matches:
        print(f"No application matching '{args.company}'.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print("That matches more than one application; narrow it with --role:\n", file=sys.stderr)
        for app in matches:
            print(f"  {_describe(app)} [{app.status.value}]", file=sys.stderr)
        return 1

    app = matches[0]
    previous = app.status
    app.status = new_status
    app.last_update = date.today()
    tracker.update([app])
    print(f"{_describe(app)}: {previous.value} -> {new_status.value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appliflow",
        description="Track job applications in Google Sheets, fed from Gmail.",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="path to config.toml"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="write the header row into the sheet")
    init.set_defaults(func=cmd_init)

    scan = subparsers.add_parser("scan", help="scan Gmail and update the sheet")
    scan.add_argument("--days", type=int, help="how far back to look")
    scan.add_argument("--max", type=int, default=500, help="max messages to read")
    scan.add_argument(
        "--dry-run", action="store_true", help="show changes without writing"
    )
    scan.set_defaults(func=cmd_scan)

    subparsers.add_parser(
        "doctor", help="check your setup and report what needs fixing"
    ).set_defaults(func=cmd_doctor)

    find = subparsers.add_parser("find", help="search job boards for new openings")
    find.add_argument(
        "--keyword", action="append", help="title keyword; repeatable, overrides config"
    )
    find.add_argument("--days", type=int, help="max posting age; 0 for no limit")
    find.add_argument("--limit", type=int, default=40, help="max rows to print")
    find.add_argument(
        "--no-sheet", action="store_true", help="print results only, skip Google entirely"
    )
    find.add_argument("--dry-run", action="store_true", help="show results without writing")
    find.add_argument(
        "--explain",
        action="store_true",
        help="show what was parsed out of each alert email, to spot misreads",
    )
    find.set_defaults(func=cmd_find)

    followups = subparsers.add_parser(
        "followups", help="list applications that have gone quiet"
    )
    followups.add_argument("--days", type=int, help="silence threshold in days")
    followups.set_defaults(func=cmd_followups)

    stats = subparsers.add_parser("stats", help="summary of the pipeline")
    stats.set_defaults(func=cmd_stats)

    status = subparsers.add_parser("status", help="set an application's status by hand")
    status.add_argument("company")
    status.add_argument("new_status", help="Applied, Assessment, Interview, Offer, Rejected")
    status.add_argument("--role", help="disambiguate when a company has several roles")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, AuthError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
