from datetime import date

import pytest

from appliflow.alerts import (
    CHROME_ANCHOR,
    NO_ANCHOR,
    NOT_A_JOB,
    analyze_html,
    explain_messages,
    gmail_query,
    identify,
    openings_from_html,
    openings_from_messages,
    redact_url,
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
            # JobStreet Indonesia also serves the .com domain, which `senders`
            # already accepts mail from.
            ("https://id.jobstreet.com/id/job/74839201", "jobstreet", "74839201"),
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

    def test_both_jobstreet_domains_reach_one_canonical_url(self):
        """Same job id on either domain must collapse to a single row."""
        old = identify("https://www.jobstreet.co.id/id/job/74839201")
        new = identify("https://id.jobstreet.com/id/job/74839201")
        assert old[2] == new[2]


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


class TestRealLinkedInCards:
    """Strings taken verbatim from a real LinkedIn alert, first run.

    Company came out right on every row; the location did not, because
    LinkedIn's card decoration sits in its own elements and joins onto the end
    of it. These are the exact tails that arrived.
    """

    @pytest.mark.parametrize(
        "trailing, company, location",
        [
            (
                "Xurya Daya Indonesia · South Jakarta (Hybrid) Actively recruiting Easy Apply",
                "Xurya Daya Indonesia",
                "South Jakarta (Hybrid)",
            ),
            (
                "Deloitte · Jakarta, Indonesia (On-site) 17 connections Easy Apply",
                "Deloitte",
                "Jakarta, Indonesia (On-site)",
            ),
            (
                "UNDP Careers · Jakarta Actively recruiting",
                "UNDP Careers",
                "Jakarta",
            ),
            (
                "PT HM Sampoerna Tbk. · Surabaya, East Java, Indonesia 1 connection",
                "PT HM Sampoerna Tbk.",
                "Surabaya, East Java, Indonesia",
            ),
            (
                "PT. Softex Indonesia · West Karawang, West Java, Indonesia "
                "10 school alumni Easy Apply",
                "PT. Softex Indonesia",
                "West Karawang, West Java, Indonesia",
            ),
            (
                "ORIMBA · Jakarta Metropolitan Area 1 school alum",
                "ORIMBA",
                "Jakarta Metropolitan Area",
            ),
            (
                "MR.D.I.Y. Indonesia · Jakarta, Jakarta, Indonesia 9 school alumni",
                "MR.D.I.Y. Indonesia",
                "Jakarta, Jakarta, Indonesia",
            ),
        ],
    )
    def test_location_survives_the_card_decoration(self, trailing, company, location):
        html = alert("https://www.linkedin.com/jobs/view/1", "ESG Specialist", trailing)
        opening = openings_from_html(html)[0]
        assert opening.company == company
        assert opening.location == location

    def test_recommendation_links_are_not_postings(self):
        """'Jobs similar to X' points at a job URL but is not this alert's job."""
        html = alert(
            "https://www.linkedin.com/jobs/view/9",
            "Jobs similar to Sustainability Intern at MSD",
        )
        assert openings_from_html(html) == []

    def test_remote_arrangement_sets_the_flag(self):
        html = alert("https://www.linkedin.com/jobs/view/1", "ESG Analyst",
                     "ORIMBA · Jakarta (Remote) Easy Apply")
        opening = openings_from_html(html)[0]
        assert opening.remote is True

    def test_onsite_arrangement_does_not_set_the_flag(self):
        html = alert("https://www.linkedin.com/jobs/view/1", "ESG Analyst",
                     "Deloitte · Jakarta, Indonesia (On-site)")
        assert openings_from_html(html)[0].remote is False

    def test_a_cleaned_location_now_passes_a_jakarta_filter(self):
        """The point of the fix: these rows become filterable again."""
        from appliflow.openings import matches_locations

        html = alert("https://www.linkedin.com/jobs/view/1", "ESG Specialist",
                     "ORIMBA · Jakarta Metropolitan Area 1 school alum")
        assert matches_locations(openings_from_html(html)[0], ["Jakarta"])


class TestGlassdoorCards:
    """Anchor text taken verbatim from a real Glassdoor alert.

    Glassdoor puts the entire card inside the anchor and leaves nothing after
    the link, so the generic path produced 200 postings with no company and no
    location, and a title carrying the rating, the location and the age.
    """

    def card(self, anchor):
        html = f'<a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1">{anchor}</a>'
        return openings_from_html(html)[0]

    @pytest.mark.parametrize(
        "anchor, company, location, title",
        [
            (
                "Sephora 3.7 ★ Accounts Payable Accountant (SEA Team) Jakarta 5d",
                "Sephora", "Jakarta", "Accounts Payable Accountant (SEA Team)",
            ),
            (
                "Standard Chartered Bank 3.7 ★ Fund Accountant Jakarta 2d",
                "Standard Chartered Bank", "Jakarta", "Fund Accountant",
            ),
            (
                "UNDP 4.0 ★ Sustainable Trade and Market Partnerships Analyst "
                "[Open to internal and external applicants] Indonesia 1d",
                "UNDP", "Indonesia",
                "Sustainable Trade and Market Partnerships Analyst "
                "[Open to internal and external applicants]",
            ),
            (
                "World Resources Institute 4.1 ★ Industrial Park Analyst Jakarta 3d",
                "World Resources Institute", "Jakarta", "Industrial Park Analyst",
            ),
            (
                "Mastercard 4.1 ★ Associate Managing Consultant – Deploy Jakarta "
                "Best Place to Work 4d",
                "Mastercard", "Jakarta", "Associate Managing Consultant – Deploy",
            ),
            (
                "Nokia 4.0 ★ Business Operations Co-Ordinator and Analyst Indonesia "
                "Best-Led Company 6d",
                "Nokia", "Indonesia", "Business Operations Co-Ordinator and Analyst",
            ),
        ],
    )
    def test_splits_a_rated_card(self, anchor, company, location, title):
        opening = self.card(anchor)
        assert opening.company == company
        assert opening.location == location
        assert opening.title == title

    @pytest.mark.parametrize(
        "anchor, location, title",
        [
            (
                "PT Green City Traffic Finance, Accounting, Tax Manager Indonesia "
                "IDR 24M - IDR 30M ( Employer est. ) Easy Apply 2d",
                "Indonesia",
                "PT Green City Traffic Finance, Accounting, Tax Manager",
            ),
            (
                "PT MINTONG OVERSEAS ACCOUNTANT (MANDARIN SPEAKER) Indonesia "
                "IDR 8M - IDR 12M ( Employer est. ) Easy Apply 2d",
                "Indonesia",
                "PT MINTONG OVERSEAS ACCOUNTANT (MANDARIN SPEAKER)",
            ),
        ],
    )
    def test_unrated_cards_keep_the_company_in_the_title(self, anchor, location, title):
        """Without a rating there is no delimiter, so no boundary is invented.

        The salary and the apply badge still come off, and the title still
        matches keywords -- it just carries the employer name with it.
        """
        opening = self.card(anchor)
        assert opening.company == ""
        assert opening.location == location
        assert opening.title == title

    def test_just_posted_is_an_age_not_a_location(self):
        opening = self.card("Grab 4.2 ★ ESG Analyst Jakarta Just posted")
        assert opening.title == "ESG Analyst"
        assert opening.location == "Jakarta"

    def test_an_unknown_place_leaves_the_location_empty(self):
        """A gap in the gazetteer costs a blank cell, never the posting."""
        opening = self.card("Acme 4.0 ★ ESG Analyst Reykjavik 2d")
        assert opening.company == "Acme"
        assert opening.location == ""
        assert "Reykjavik" in opening.title

    def test_a_remote_card_sets_the_flag(self):
        assert self.card("Acme 4.0 ★ ESG Analyst Remote 2d").remote is True


class TestRedactUrl:
    """`--explain` output is meant to be shared, so it must not carry tokens."""

    def test_keeps_the_job_id_a_pattern_would_key_on(self):
        cleaned = redact_url(
            "https://www.glassdoor.com/partner/jobListing.htm"
            "?jobListingId=1009988&ao=SecretToken"
        )
        assert "jobListingId=1009988" in cleaned
        assert "SecretToken" not in cleaned

    def test_redacts_tracking_values_but_keeps_their_names(self):
        cleaned = redact_url("https://www.linkedin.com/jobs/view/1?midToken=AQFsecret")
        # The name is what tells you where to look next; the value is the leak.
        assert "midToken=<redacted>" in cleaned
        assert "AQFsecret" not in cleaned

    # example.com is reserved for documentation -- never a real address, and a
    # test about not leaking addresses should not carry one.

    def test_removes_an_email_address_in_a_query_value(self):
        cleaned = redact_url("https://x.com/unsub?email=jobseeker%40example.com")
        assert "jobseeker" not in cleaned

    def test_removes_an_email_address_outside_the_query(self):
        """Value-redaction only covers the query; the path needs its own sweep."""
        cleaned = redact_url("https://x.com/unsub/jobseeker@example.com")
        assert "jobseeker" not in cleaned
        assert "<email>" in cleaned

    def test_keeps_a_nested_redirect_target_and_redacts_inside_it(self):
        cleaned = redact_url(
            "https://click.example.com/r?u=https://glints.com/opportunities/jobs/zz-9?tok=abc"
        )
        assert "glints.com/opportunities/jobs/zz-9" in cleaned
        assert "abc" not in cleaned

    def test_collapses_an_opaque_path_segment(self):
        cleaned = redact_url("https://click.x.com/f/a/" + "A1b2C3d4" * 5 + "/unsub")
        assert "<token>" in cleaned
        assert "A1b2C3d4A1b2" not in cleaned

    def test_leaves_a_clean_job_url_untouched(self):
        url = "https://www.kalibrr.com/c/gojek/jobs/5551234"
        assert redact_url(url) == url

    def test_never_raises_on_junk(self):
        assert redact_url("http://[") == "<unparseable url>"
        assert redact_url("") == ""


class TestAnalyzeHtml:
    """The evidence `--explain` prints, which is what misreads get fixed from."""

    def test_reports_the_text_the_split_ran_on(self):
        html = alert(
            "https://www.linkedin.com/jobs/view/1",
            "Data Analyst",
            "Tokopedia · Jakarta, Indonesia · 2 days ago",
        )
        report = analyze_html(html)[0]
        assert report.trailing == "Tokopedia · Jakarta, Indonesia · 2 days ago"
        assert report.opening.company == "Tokopedia"

    def test_explains_why_a_job_link_produced_nothing(self):
        html = (
            '<a href="https://www.linkedin.com/jobs/view/1">Apply now</a>'
            '<a href="https://www.linkedin.com/jobs/view/2"><img src="x"/></a>'
            '<a href="https://www.linkedin.com/feed/">Your network</a>'
        )
        reasons = [r.skipped for r in analyze_html(html)]
        assert reasons == [CHROME_ANCHOR, NO_ANCHOR, NOT_A_JOB]

    def test_redacts_the_href_it_reports(self):
        html = alert("https://www.linkedin.com/jobs/view/1?midToken=AQFsecret", "Data Analyst")
        assert "AQFsecret" not in analyze_html(html)[0].href

    def test_matches_what_openings_from_html_returns(self):
        """One parsing pass, so `--explain` cannot describe a different run."""
        html = (
            '<a href="https://www.linkedin.com/jobs/view/1">Data Analyst</a> Grab · Jakarta'
            '<a href="https://www.linkedin.com/jobs/view/2">Apply now</a>'
            '<a href="https://www.linkedin.com/jobs/view/3">Data Scientist</a> Gojek'
        )
        from_reports = [r.opening for r in analyze_html(html) if r.opening]
        assert from_reports == openings_from_html(html)

    @pytest.mark.parametrize("html", ["", None, "not html at all", "<a href=>broken"])
    def test_survives_empty_and_malformed_input(self, html):
        # Bad markup may still yield reports -- an anchor with no usable href is
        # worth showing -- but it must never raise and never invent a posting.
        assert [r.opening for r in analyze_html(html) if r.opening] == []


class TestExplainMessages:
    def message(self, html):
        return Message(
            message_id="m", thread_id="t", subject="Your job alert",
            sender_name="LinkedIn", sender_email="jobs@linkedin.com",
            received=date(2026, 8, 9), body="", html=html,
        )

    def test_separates_postings_from_skipped_job_links(self):
        html = (
            '<a href="https://www.linkedin.com/jobs/view/1">Data Analyst</a> Grab'
            '<a href="https://www.linkedin.com/jobs/view/2">Apply now</a>'
        )
        report = explain_messages([self.message(html)])[0]
        assert [r.opening.title for r in report.matched] == ["Data Analyst"]
        assert [r.skipped for r in report.skipped_jobs] == [CHROME_ANCHOR]

    def test_groups_unmatched_links_by_host_for_diagnosis(self):
        """When nothing matches, the URLs it failed on are the only clue."""
        html = (
            '<a href="https://jobs.newboard.com/vacancy/8812">Data Engineer</a> PT Maju'
            '<a href="https://jobs.newboard.com/vacancy/8813">Analis Data</a> Traveloka'
            '<a href="https://newboard.com/about">About</a>'
        )
        report = explain_messages([self.message(html)])[0]
        assert report.matched == []
        hosts = report.unmatched_hosts()
        assert hosts[0][0] == "jobs.newboard.com"
        assert hosts[0][1] == 2
        # Samples carry the anchor too: when the URL is an opaque redirect the
        # anchor is the only thing left to build a posting from.
        assert ("https://jobs.newboard.com/vacancy/8812", "Data Engineer") in hosts[0][2]

    def test_caps_the_samples_per_host(self):
        html = "".join(
            f'<a href="https://newboard.com/vacancy/{n}">Role {n}</a>' for n in range(10)
        )
        host, count, samples = explain_messages([self.message(html)])[0].unmatched_hosts()[0]
        assert count == 10
        assert len(samples) == 3

    def test_flags_an_email_with_no_html_part(self):
        report = explain_messages([self.message("")])[0]
        assert report.has_html is False
        assert report.links == []


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
