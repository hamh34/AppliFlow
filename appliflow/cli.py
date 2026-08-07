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

    warnings: list[str] = []
    print("Fetching job boards...")
    found = collect(settings, on_error=lambda label, msg: warnings.append(f"{label}: {msg}"))
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

    creds = get_credentials(config.credentials_file, config.token_file)
    sheet = OpeningsSheet(creds, config.spreadsheet_id, settings.worksheet)
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
