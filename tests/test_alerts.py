from datetime import date

import pytest

from appliflow.alerts import (
    gmail_query,
    identify,
    openings_from_html,
    openings_from_messages,
    unwrap_url,
)
from appliflow.gmail import Message

# Synthetic markup shaped like a real alert: a job link whose anchor text is the
# title, followed by company and location as loose text.


def alert(href, title, trailing=""):
    return f'<html><body><a href="{href}">{title}</a> {trailing}</body></html>'


class TestIdentify:
    @pytest.mark.parametrize(
        "url, board, job_id",
        [
            ("https://www.linkedin.com/jobs/view/3812345678/", "linkedin", "3812345678"),
            ("https://www.linkedin.com/comm/jobs/view/3812345678", "linkedin", "3812345678"),
            ("https://www.jobstreet.co.id/id/job/74839201", "jobstreet", "74839201"),
            ("https://glints.com/id/opportunities/jobs/abc-123", "glints", "abc-123"),
            ("https://www.kalibrr.com/c/tokopedia/jobs/998877", "kalibrr", "998877"),
            (
                "https://www.glassdoor.com/job-listing/?jobListingId=1009988",
                "glassdoor",
                "1009988",
            ),
        ],
    )
    def test_recognizes_each_board(self, url, board, job_id):
        found = identify(url)
        assert found is not None
        assert found[0].name == board
        assert found[1] == job_id

    def test_ignores_non_job_links(self):
        assert identify("https://www.linkedin.com/feed/") is None
        assert identify("https://example.com/newsletter") is None
        assert identify("") is None

    def test_strips_tracking_parameters_into_a_stable_url(self):
        a = identify("https://www.linkedin.com/jobs/view/3812345678/?trackingId=AAA")
        b = identify("https://www.linkedin.com/comm/jobs/view/3812345678?refId=BBB")
        assert a[2] == b[2]  # same canonical URL, so dedupe collapses them

    def test_kalibrr_canonical_keeps_the_company_slug(self):
        _, _, url = identify("https://www.kalibrr.com/c/gojek/jobs/5551234")
        assert url == "https://www.kalibrr.com/c/gojek/jobs/5551234"


class TestUnwrapUrl:
    def test_sees_through_a_tracking_redirect(self):
        wrapped = (
            "https://click.email.com/redirect?url="
            "https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F3812345678"
        )
        assert identify(wrapped) is not None

    def test_handles_double_encoding(self):
        wrapped = "https://t.co/x?u=https%253A%252F%252Fglints.com%252Fopportunities%252Fjobs%252Fzz-9"
        assert identify(wrapped) is not None

    def test_leaves_plain_urls_alone(self):
        plain = "https://www.linkedin.com/jobs/view/1"
        assert unwrap_url(plain) == plain


class TestOpeningsFromHtml:
    def test_extracts_title_company_and_location(self):
        html = alert(
            "https://www.linkedin.com/jobs/view/3812345678/",
            "Data Analyst",
            "Tokopedia · Jakarta, Indonesia",
        )
        opening = openings_from_html(html)[0]
        assert opening.title == "Data Analyst"
        assert opening.company == "Tokopedia"
        assert opening.location == "Jakarta, Indonesia"
        assert opening.url == "https://www.linkedin.com/jobs/view/3812345678"
        assert opening.source == "alert:linkedin"

    def test_handles_dash_separators(self):
        html = alert(
            "https://www.jobstreet.co.id/job/74839201",
            "Business Analyst",
            "Bank Mandiri - Jakarta",
        )
        opening = openings_from_html(html)[0]
        assert opening.company == "Bank Mandiri"
        assert opening.location == "Jakarta"

    def test_lone_fragment_is_read_as_company(self):
        html = alert("https://www.linkedin.com/jobs/view/1", "Data Analyst", "Grab")
        opening = openings_from_html(html)[0]
        assert opening.company == "Grab"
        assert opening.location == ""

    def test_skips_chrome_links(self):
        """'Apply now' and 'Unsubscribe' are not job titles."""
        html = (
            '<a href="https://www.linkedin.com/jobs/view/1">Apply now</a>'
            '<a href="https://www.linkedin.com/jobs/view/2">Unsubscribe</a>'
        )
        assert openings_from_html(html) == []

    def test_finds_several_postings_in_one_email(self):
        html = (
            '<a href="https://www.linkedin.com/jobs/view/1">Data Analyst</a> Grab · Jakarta'
            '<a href="https://www.linkedin.com/jobs/view/2">Data Scientist</a> Gojek · Bandung'
        )
        openings = openings_from_html(html)
        assert [o.title for o in openings] == ["Data Analyst", "Data Scientist"]
        assert openings[1].company == "Gojek"

    def test_kalibrr_falls_back_to_the_company_in_the_url(self):
        html = alert("https://www.kalibrr.com/c/traveloka/jobs/9911", "Data Engineer")
        assert openings_from_html(html)[0].company == "Traveloka"

    def test_ignores_links_that_are_not_jobs(self):
        html = '<a href="https://www.linkedin.com/feed/">Your network</a>'
        assert openings_from_html(html) == []

    @pytest.mark.parametrize("html", ["", None, "not html at all", "<a href=>broken"])
    def test_survives_empty_and_malformed_input(self, html):
        assert openings_from_html(html) == []

    def test_unclosed_anchor_still_yields_the_posting(self):
        html = '<a href="https://www.linkedin.com/jobs/view/7">Data Analyst'
        assert openings_from_html(html)[0].title == "Data Analyst"


class TestOpeningsFromMessages:
    def test_dates_each_posting_by_its_email(self):
        message = Message(
            message_id="m1", thread_id="t1", subject="Your job alert",
            sender_name="LinkedIn", sender_email="jobs-noreply@linkedin.com",
            received=date(2026, 4, 1), body="",
            html=alert("https://www.linkedin.com/jobs/view/1", "Data Analyst", "Grab · Jakarta"),
        )
        opening = openings_from_messages([message])[0]
        assert opening.posted == date(2026, 4, 1)

    def test_emails_without_html_are_skipped(self):
        message = Message(
            message_id="m", thread_id="t", subject="", sender_name="",
            sender_email="x@linkedin.com", received=None, body="text only", html="",
        )
        assert openings_from_messages([message]) == []


class TestGmailQuery:
    def test_covers_every_board_by_default(self):
        query = gmail_query(7)
        for domain in ("linkedin.com", "jobstreet.co.id", "glints.com",
                       "kalibrr.com", "glassdoor.com"):
            assert f"from:{domain}" in query
        assert "newer_than:7d" in query

    def test_narrows_to_the_boards_asked_for(self):
        query = gmail_query(14, ["linkedin"])
        assert "from:linkedin.com" in query
        assert "glassdoor" not in query
        assert "newer_than:14d" in query

    def test_unknown_board_names_fall_back_to_everything(self):
        assert "from:linkedin.com" in gmail_query(7, ["nonsense"])

    def test_lookback_is_never_below_one_day(self):
        assert "newer_than:1d" in gmail_query(0)


def test_postings_from_alerts_dedupe_against_each_other():
    """The same job in Monday's and Tuesday's alert must collapse to one row."""
    from appliflow.openings import dedupe

    monday = openings_from_html(
        alert("https://www.linkedin.com/jobs/view/555/?trackingId=A", "Data Analyst", "Grab")
    )
    tuesday = openings_from_html(
        alert("https://www.linkedin.com/comm/jobs/view/555?refId=B", "Data Analyst", "Grab")
    )
    assert len(dedupe(monday + tuesday)) == 1
