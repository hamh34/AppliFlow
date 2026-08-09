from datetime import date

from appliflow.openings import (
    Opening,
    dedupe,
    exclude_known,
    filter_openings,
    is_recent,
    matches_keywords,
    matches_locations,
    sort_openings,
)

TODAY = date(2026, 4, 1)


def make(title="Data Analyst", company="Acme", location="Jakarta", url="", posted=None,
         remote=False):
    return Opening(company=company, title=title, location=location, url=url,
                   posted=posted, remote=remote)


class TestKeywords:
    def test_matches_case_insensitively(self):
        assert matches_keywords(make(title="Senior Data Analyst"), ["data analyst"])

    def test_rejects_non_matches(self):
        assert not matches_keywords(make(title="Chef"), ["data analyst"])

    def test_empty_list_matches_everything(self):
        assert matches_keywords(make(title="Anything"), [])

    def test_ignores_blank_entries(self):
        assert not matches_keywords(make(title="Chef"), ["   "])


class TestLocations:
    def test_substring_match(self):
        assert matches_locations(make(location="Jakarta, Indonesia"), ["indonesia"])

    def test_remote_keyword_matches_remote_flag(self):
        assert matches_locations(make(location="Anywhere", remote=True), ["remote"])

    def test_remote_keyword_does_not_match_onsite(self):
        assert not matches_locations(make(location="Jakarta", remote=False), ["remote"])

    def test_empty_list_matches_everything(self):
        assert matches_locations(make(location="Mars"), [])

    def test_blank_location_is_kept(self):
        """An unknown location is not evidence of a mismatch.

        Alert emails are the case that matters: location is the least reliable
        field the parser produces, so dropping blanks would bin real postings
        and read as a board that sent nothing.
        """
        assert matches_locations(make(location=""), ["Jakarta"])
        assert matches_locations(make(location="   "), ["Jakarta"])

    def test_a_location_that_is_present_and_wrong_still_fails(self):
        assert not matches_locations(make(location="Berlin, Germany"), ["Jakarta"])


class TestRecency:
    def test_recent_posting_passes(self):
        assert is_recent(make(posted=date(2026, 3, 20)), TODAY, 30)

    def test_old_posting_fails(self):
        assert not is_recent(make(posted=date(2026, 1, 1)), TODAY, 30)

    def test_undated_posting_is_kept(self):
        """An unknown date is not evidence of staleness."""
        assert is_recent(make(posted=None), TODAY, 30)

    def test_none_threshold_disables_the_filter(self):
        assert is_recent(make(posted=date(2020, 1, 1)), TODAY, None)

    def test_boundary_is_inclusive(self):
        assert is_recent(make(posted=date(2026, 3, 2)), TODAY, 30)


class TestDedupe:
    def test_collapses_identical_urls(self):
        items = [make(url="https://x.com/job/1"), make(url="https://x.com/job/1")]
        assert len(dedupe(items)) == 1

    def test_ignores_query_strings_and_trailing_slash(self):
        items = [
            make(url="https://x.com/job/1"),
            make(url="https://x.com/job/1/?utm_source=feed"),
        ]
        assert len(dedupe(items)) == 1

    def test_falls_back_to_company_title_location(self):
        items = [make(url=""), make(url="")]
        assert len(dedupe(items)) == 1

    def test_keeps_genuinely_different_postings(self):
        items = [make(title="Data Analyst"), make(title="Data Scientist")]
        assert len(dedupe(items)) == 2

    def test_preserves_first_occurrence_order(self):
        items = [make(title="A", url="u1"), make(title="B", url="u2"), make(title="A", url="u1")]
        assert [o.title for o in dedupe(items)] == ["A", "B"]


class TestExcludeKnown:
    def test_drops_urls_already_on_the_sheet(self):
        items = [make(url="https://x.com/job/1"), make(url="https://x.com/job/2")]
        remaining = exclude_known(items, {"https://x.com/job/1"})
        assert [o.url for o in remaining] == ["https://x.com/job/2"]

    def test_normalizes_known_urls_too(self):
        items = [make(url="https://x.com/job/1")]
        assert exclude_known(items, {"https://x.com/job/1/?ref=email"}) == []

    def test_empty_known_set_keeps_everything(self):
        items = [make(url="https://x.com/job/1")]
        assert len(exclude_known(items, set())) == 1


class TestSorting:
    def test_newest_first_with_undated_last(self):
        items = [
            make(title="old", posted=date(2026, 1, 1)),
            make(title="undated", posted=None),
            make(title="new", posted=date(2026, 3, 1)),
        ]
        assert [o.title for o in sort_openings(items)] == ["new", "old", "undated"]


def test_filter_applies_all_three_criteria():
    items = [
        make(title="Data Analyst", location="Jakarta", posted=date(2026, 3, 20)),
        make(title="Data Analyst", location="London", posted=date(2026, 3, 20)),
        make(title="Chef", location="Jakarta", posted=date(2026, 3, 20)),
        make(title="Data Analyst", location="Jakarta", posted=date(2025, 1, 1)),
    ]
    result = filter_openings(
        items,
        keywords=["data analyst"],
        locations=["jakarta"],
        max_age_days=30,
        as_of=TODAY,
    )
    assert len(result) == 1
    assert result[0].location == "Jakarta"


def test_row_serialization_matches_column_count():
    from appliflow.openings import COLUMNS

    row = make(url="https://x.com/1", posted=date(2026, 3, 1), remote=True).to_row()
    assert len(row) == len(COLUMNS)
    assert row[COLUMNS.index("Remote")] == "Yes"
    assert row[COLUMNS.index("Posted")] == "2026-03-01"
