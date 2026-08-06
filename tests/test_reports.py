from datetime import date

from appliflow.records import Application, Status
from appliflow.reports import stale_applications, summarize

TODAY = date(2026, 4, 1)


def make(company, status, last_update=None, applied_date=None):
    return Application(
        company=company,
        status=status,
        applied_date=applied_date,
        last_update=last_update,
    )


class TestStaleApplications:
    def test_flags_open_applications_past_the_threshold(self):
        apps = [
            make("Stale", Status.APPLIED, last_update=date(2026, 3, 1)),
            make("Fresh", Status.APPLIED, last_update=date(2026, 3, 30)),
        ]
        result = stale_applications(apps, as_of=TODAY, min_days=14)
        assert [app.company for app, _ in result] == ["Stale"]
        assert result[0][1] == 31

    def test_ignores_closed_applications(self):
        apps = [
            make("Rejected", Status.REJECTED, last_update=date(2026, 1, 1)),
            make("Offered", Status.OFFER, last_update=date(2026, 1, 1)),
        ]
        assert stale_applications(apps, as_of=TODAY, min_days=14) == []

    def test_chases_interviews_and_assessments_too(self):
        apps = [
            make("Interviewing", Status.INTERVIEW, last_update=date(2026, 3, 1)),
            make("Assessing", Status.ASSESSMENT, last_update=date(2026, 3, 1)),
        ]
        assert len(stale_applications(apps, as_of=TODAY, min_days=14)) == 2

    def test_falls_back_to_applied_date(self):
        apps = [make("NoUpdate", Status.APPLIED, applied_date=date(2026, 3, 1))]
        result = stale_applications(apps, as_of=TODAY, min_days=14)
        assert result[0][1] == 31

    def test_skips_records_with_no_dates(self):
        assert stale_applications([make("Bare", Status.APPLIED)], TODAY, 14) == []

    def test_sorts_stalest_first(self):
        apps = [
            make("Recent", Status.APPLIED, last_update=date(2026, 3, 10)),
            make("Ancient", Status.APPLIED, last_update=date(2026, 1, 1)),
            make("Middle", Status.APPLIED, last_update=date(2026, 2, 1)),
        ]
        result = stale_applications(apps, as_of=TODAY, min_days=14)
        assert [app.company for app, _ in result] == ["Ancient", "Middle", "Recent"]

    def test_boundary_is_inclusive(self):
        apps = [make("Exact", Status.APPLIED, last_update=date(2026, 3, 18))]
        assert len(stale_applications(apps, as_of=TODAY, min_days=14)) == 1


class TestSummarize:
    def test_counts_by_status(self):
        apps = [
            make("A", Status.APPLIED),
            make("B", Status.APPLIED),
            make("C", Status.REJECTED),
            make("D", Status.INTERVIEW),
        ]
        summary = summarize(apps)
        assert summary.total == 4
        assert summary.by_status[Status.APPLIED] == 2
        assert summary.by_status[Status.REJECTED] == 1
        assert summary.by_status[Status.OFFER] == 0

    def test_response_rate_counts_rejections_as_responses(self):
        apps = [
            make("A", Status.APPLIED),
            make("B", Status.APPLIED),
            make("C", Status.REJECTED),
            make("D", Status.INTERVIEW),
        ]
        summary = summarize(apps)
        assert summary.responses == 2
        assert summary.response_rate == 0.5

    def test_empty_list_does_not_divide_by_zero(self):
        summary = summarize([])
        assert summary.total == 0
        assert summary.response_rate == 0.0
        assert summary.per_week == 0.0

    def test_pace_across_four_weeks(self):
        apps = [
            make("A", Status.APPLIED, applied_date=date(2026, 3, 4)),
            make("B", Status.APPLIED, applied_date=date(2026, 3, 11)),
            make("C", Status.APPLIED, applied_date=date(2026, 3, 18)),
            make("D", Status.APPLIED, applied_date=date(2026, 3, 25)),
        ]
        summary = summarize(apps)
        assert summary.first_applied == date(2026, 3, 4)
        assert summary.last_applied == date(2026, 3, 25)
        assert summary.per_week == 4 / 3

    def test_same_day_applications_count_as_one_week(self):
        apps = [
            make("A", Status.APPLIED, applied_date=date(2026, 3, 4)),
            make("B", Status.APPLIED, applied_date=date(2026, 3, 4)),
        ]
        assert summarize(apps).per_week == 2.0
