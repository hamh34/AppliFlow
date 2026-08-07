from datetime import date

import pytest

from appliflow.sources import (
    _looks_remote,
    _parse_timestamp,
    parse_arbeitnow,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
    parse_remoteok,
)

# Payloads mirror the documented shape of each public API. They are the only
# way to exercise the parsers without network access.

GREENHOUSE = {
    "jobs": [
        {
            "title": "Data Analyst",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "location": {"name": "Jakarta, Indonesia"},
            "updated_at": "2026-03-01T10:00:00-05:00",
        },
        {
            "title": "Remote Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
            "location": {"name": "Remote"},
            "updated_at": "2026-02-01T10:00:00-05:00",
        },
    ]
}

LEVER = [
    {
        "text": "Product Analyst",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "categories": {"location": "Singapore", "commitment": "Full-time"},
        "createdAt": 1772409600000,
    }
]

ASHBY = {
    "jobs": [
        {
            "title": "BI Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
            "location": "Bandung",
            "publishedAt": "2026-03-02T00:00:00.000Z",
            "isRemote": True,
        }
    ]
}

ARBEITNOW = {
    "data": [
        {
            "title": "Data Engineer",
            "company_name": "Globex",
            "location": "Berlin",
            "url": "https://www.arbeitnow.com/view/data-engineer",
            "remote": True,
            "created_at": 1772409600,
        }
    ]
}

REMOTEOK = [
    {"legal": "Scraping this API is not allowed without attribution"},
    {
        "position": "Analytics Engineer",
        "company": "Initech",
        "location": "Worldwide",
        "url": "https://remoteok.com/remote-jobs/123",
        "epoch": 1772409600,
    },
]


class TestGreenhouse:
    def test_parses_postings(self):
        openings = parse_greenhouse(GREENHOUSE, "acme")
        assert len(openings) == 2
        first = openings[0]
        assert first.company == "acme"
        assert first.title == "Data Analyst"
        assert first.location == "Jakarta, Indonesia"
        assert first.url == "https://boards.greenhouse.io/acme/jobs/1"
        assert first.posted == date(2026, 3, 1)
        assert first.source == "greenhouse"
        assert first.remote is False

    def test_detects_remote_from_location(self):
        assert parse_greenhouse(GREENHOUSE, "acme")[1].remote is True

    def test_skips_entries_without_a_title(self):
        assert parse_greenhouse({"jobs": [{"absolute_url": "x"}]}, "acme") == []

    def test_tolerates_missing_location(self):
        payload = {"jobs": [{"title": "Analyst", "absolute_url": "u"}]}
        assert parse_greenhouse(payload, "acme")[0].location == ""


class TestLever:
    def test_parses_postings(self):
        opening = parse_lever(LEVER, "acme")[0]
        assert opening.title == "Product Analyst"
        assert opening.location == "Singapore"
        assert opening.url == "https://jobs.lever.co/acme/abc"
        assert opening.posted == date(2026, 3, 2)
        assert opening.source == "lever"

    def test_falls_back_to_apply_url(self):
        payload = [{"text": "Analyst", "applyUrl": "https://apply", "categories": {}}]
        assert parse_lever(payload, "acme")[0].url == "https://apply"

    def test_detects_remote_workplace_type(self):
        payload = [{"text": "Analyst", "categories": {"location": "Anywhere"},
                    "workplaceType": "remote"}]
        assert parse_lever(payload, "acme")[0].remote is True


class TestAshby:
    def test_parses_postings(self):
        opening = parse_ashby(ASHBY, "acme")[0]
        assert opening.title == "BI Engineer"
        assert opening.location == "Bandung"
        assert opening.posted == date(2026, 3, 2)
        assert opening.remote is True
        assert opening.source == "ashby"


class TestArbeitnow:
    def test_parses_postings(self):
        opening = parse_arbeitnow(ARBEITNOW)[0]
        assert opening.company == "Globex"
        assert opening.title == "Data Engineer"
        assert opening.posted == date(2026, 3, 2)
        assert opening.remote is True

    def test_skips_entries_missing_a_company(self):
        assert parse_arbeitnow({"data": [{"title": "Analyst"}]}) == []


class TestRemoteOK:
    def test_skips_the_legal_notice_element(self):
        openings = parse_remoteok(REMOTEOK)
        assert len(openings) == 1
        assert openings[0].company == "Initech"

    def test_marks_everything_remote(self):
        assert parse_remoteok(REMOTEOK)[0].remote is True

    def test_ignores_non_dict_entries(self):
        assert parse_remoteok(["junk", None, 42]) == []


class TestEmptyAndMalformedPayloads:
    @pytest.mark.parametrize(
        "parser, empty",
        [
            (lambda p: parse_greenhouse(p, "acme"), {}),
            (lambda p: parse_greenhouse(p, "acme"), None),
            (lambda p: parse_lever(p, "acme"), []),
            (lambda p: parse_lever(p, "acme"), None),
            (lambda p: parse_ashby(p, "acme"), {}),
            (parse_arbeitnow, {}),
            (parse_remoteok, []),
        ],
    )
    def test_returns_empty_rather_than_raising(self, parser, empty):
        assert parser(empty) == []


class TestParseTimestamp:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-03-01T10:00:00-05:00", date(2026, 3, 1)),
            ("2026-03-02T00:00:00.000Z", date(2026, 3, 2)),
            ("2026-03-02", date(2026, 3, 2)),
            (1772409600, date(2026, 3, 2)),          # epoch seconds
            (1772409600000, date(2026, 3, 2)),       # epoch milliseconds
            ("1772409600", date(2026, 3, 2)),        # epoch as a string
            (None, None),
            ("", None),
            ("not a date", None),
        ],
    )
    def test_handles_the_formats_these_boards_use(self, value, expected):
        assert _parse_timestamp(value) == expected


def test_looks_remote():
    assert _looks_remote("Remote - Asia") is True
    assert _looks_remote("Jakarta", "Full-time") is False
    assert _looks_remote(None, "") is False
