from datetime import date

from appliflow.records import (
    Application,
    Status,
    match_application,
    parse_date,
    parse_status,
)


def make(company, **kwargs):
    return Application(company=company, **kwargs)


class TestAbsorb:
    def test_status_moves_forward(self):
        app = make("Acme", status=Status.APPLIED)
        assert app.absorb(make("Acme", status=Status.INTERVIEW)) is True
        assert app.status == Status.INTERVIEW

    def test_status_never_regresses(self):
        app = make("Acme", status=Status.INTERVIEW)
        app.absorb(make("Acme", status=Status.APPLIED))
        assert app.status == Status.INTERVIEW

    def test_rejection_always_wins(self):
        app = make("Acme", status=Status.INTERVIEW)
        app.absorb(make("Acme", status=Status.REJECTED))
        assert app.status == Status.REJECTED

    def test_rejection_is_terminal(self):
        app = make("Acme", status=Status.REJECTED)
        app.absorb(make("Acme", status=Status.INTERVIEW))
        assert app.status == Status.REJECTED

    def test_keeps_earliest_applied_date(self):
        app = make("Acme", applied_date=date(2026, 3, 10))
        app.absorb(make("Acme", applied_date=date(2026, 3, 1)))
        assert app.applied_date == date(2026, 3, 1)

    def test_seeds_applied_date_from_its_own_last_update(self):
        """A hand-typed row with only a last-update date must not be back-dated wrong."""
        app = make("Acme", applied_date=None, last_update=date(2026, 3, 1))
        app.absorb(make("Acme", last_update=date(2026, 3, 20)))
        assert app.applied_date == date(2026, 3, 1)

    def test_ignores_later_applied_date(self):
        app = make("Acme", applied_date=date(2026, 3, 1))
        app.absorb(make("Acme", applied_date=date(2026, 3, 20)))
        assert app.applied_date == date(2026, 3, 1)

    def test_advances_last_update_and_subject(self):
        app = make(
            "Acme", last_update=date(2026, 3, 1), last_subject="Application received"
        )
        app.absorb(
            make("Acme", last_update=date(2026, 3, 9), last_subject="Interview invite")
        )
        assert app.last_update == date(2026, 3, 9)
        assert app.last_subject == "Interview invite"

    def test_older_email_does_not_rewrite_last_subject(self):
        app = make("Acme", last_update=date(2026, 3, 9), last_subject="Interview invite")
        app.absorb(make("Acme", last_update=date(2026, 3, 1), last_subject="Received"))
        assert app.last_subject == "Interview invite"

    def test_fills_in_missing_role(self):
        app = make("Acme", role=None)
        app.absorb(make("Acme", role="Data Analyst"))
        assert app.role == "Data Analyst"

    def test_reports_no_change_when_nothing_new(self):
        app = make(
            "Acme",
            status=Status.INTERVIEW,
            applied_date=date(2026, 3, 1),
            last_update=date(2026, 3, 9),
        )
        assert app.absorb(make("Acme", status=Status.APPLIED)) is False


class TestMatchApplication:
    def test_matches_on_thread_id(self):
        existing = [make("Acme", thread_id="t1"), make("Other", thread_id="t2")]
        incoming = make("Totally Different Name", thread_id="t2")
        assert match_application(existing, incoming) is existing[1]

    def test_matches_on_company_and_role(self):
        existing = [
            make("Acme", role="Data Analyst"),
            make("Acme", role="Backend Engineer"),
        ]
        incoming = make("acme", role="backend engineer")
        assert match_application(existing, incoming) is existing[1]

    def test_new_role_at_known_company_is_a_new_application(self):
        existing = [make("Acme", role="Data Analyst")]
        assert match_application(existing, make("Acme", role="Designer")) is None

    def test_roleless_email_matches_the_single_open_application(self):
        existing = [
            make("Acme", role="Data Analyst", status=Status.REJECTED),
            make("Acme", role="Backend Engineer", status=Status.APPLIED),
        ]
        assert match_application(existing, make("Acme")) is existing[1]

    def test_roleless_email_is_ambiguous_with_two_open_applications(self):
        existing = [
            make("Acme", role="Data Analyst", status=Status.APPLIED),
            make("Acme", role="Backend Engineer", status=Status.APPLIED),
        ]
        assert match_application(existing, make("Acme")) is None

    def test_unknown_company_does_not_match(self):
        assert match_application([make("Acme")], make("Zeta")) is None


class TestSerialization:
    def test_round_trips_through_a_sheet_row(self):
        app = Application(
            company="Acme",
            role="Data Analyst",
            status=Status.INTERVIEW,
            applied_date=date(2026, 3, 1),
            last_update=date(2026, 3, 9),
            last_subject="Interview invite",
            thread_id="t1",
            notes="referred",
        )
        restored = Application.from_row(app.to_row(), row=4)
        assert restored.company == app.company
        assert restored.role == app.role
        assert restored.status == app.status
        assert restored.applied_date == app.applied_date
        assert restored.last_update == app.last_update
        assert restored.thread_id == app.thread_id
        assert restored.row == 4

    def test_tolerates_short_rows(self):
        app = Application.from_row(["Acme", "Data Analyst"])
        assert app.company == "Acme"
        assert app.status == Status.UNKNOWN
        assert app.applied_date is None

    def test_parses_statuses_case_insensitively(self):
        assert parse_status(" rejected ") == Status.REJECTED
        assert parse_status("nonsense") == Status.UNKNOWN

    def test_parses_common_date_formats(self):
        assert parse_date("2026-03-01") == date(2026, 3, 1)
        assert parse_date("01/03/2026") == date(2026, 3, 1)
        assert parse_date("") is None
        assert parse_date("not a date") is None
