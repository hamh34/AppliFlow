from datetime import date

from appliflow.gmail import Message
from appliflow.records import Application, Status, match_application, merge_all
from appliflow.sync import applications_from_messages, message_to_application


def email(subject, body="", sender_email="no-reply@acme.com", sender_name="Acme Careers",
          thread_id="t1", received=date(2026, 3, 1), message_id="m1"):
    return Message(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        received=received,
        body=body,
    )


class TestMessageToApplication:
    def test_builds_a_record_from_a_confirmation(self):
        record = message_to_application(
            email("Your application for Data Analyst at Acme",
                  "Thank you for applying. We have received your application.")
        )
        assert record.company == "Acme"
        assert record.role == "Data Analyst"
        assert record.status == Status.APPLIED
        assert record.applied_date == date(2026, 3, 1)
        assert record.thread_id == "t1"

    def test_drops_unrelated_mail(self):
        assert message_to_application(email("Your order shipped", "On its way")) is None

    def test_filters_the_batch(self):
        messages = [
            email("Thank you for applying", "We received your application."),
            email("Newsletter", "This week in tech"),
        ]
        assert len(applications_from_messages(messages)) == 1


class TestMergeAll:
    def test_adds_unseen_applications(self):
        changed, added = merge_all([], [Application(company="Acme", status=Status.APPLIED)])
        assert not changed
        assert len(added) == 1

    def test_advances_an_existing_application(self):
        existing = [
            Application(
                company="Acme", role="Data Analyst", status=Status.APPLIED,
                thread_id="t1", last_update=date(2026, 3, 1), row=2,
            )
        ]
        incoming = [
            Application(
                company="Acme", role="Data Analyst", status=Status.INTERVIEW,
                thread_id="t1", last_update=date(2026, 3, 8),
            )
        ]
        changed, added = merge_all(existing, incoming)
        assert not added
        assert changed == [existing[0]]
        assert existing[0].status == Status.INTERVIEW
        assert existing[0].row == 2

    def test_a_record_created_mid_run_is_not_also_reported_as_changed(self):
        """The confirmation creates the row; the later rejection must not double-count."""
        incoming = [
            Application(company="Acme", role="Data Analyst", status=Status.APPLIED,
                        thread_id="t1", applied_date=date(2026, 3, 1),
                        last_update=date(2026, 3, 1)),
            Application(company="Acme", role="Data Analyst", status=Status.REJECTED,
                        thread_id="t2", applied_date=date(2026, 3, 20),
                        last_update=date(2026, 3, 20)),
        ]
        changed, added = merge_all([], incoming)
        assert not changed
        assert len(added) == 1
        assert added[0].status == Status.REJECTED
        assert added[0].applied_date == date(2026, 3, 1)

    def test_no_op_scan_reports_nothing(self):
        existing = [
            Application(company="Acme", status=Status.INTERVIEW, thread_id="t1",
                        applied_date=date(2026, 3, 1), last_update=date(2026, 3, 8), row=2)
        ]
        incoming = [
            Application(company="Acme", status=Status.APPLIED, thread_id="t1",
                        applied_date=date(2026, 3, 1), last_update=date(2026, 3, 1))
        ]
        changed, added = merge_all(existing, incoming)
        assert not changed and not added

    def test_unknown_companies_stay_separate(self):
        incoming = [
            Application(company="Unknown", status=Status.APPLIED, thread_id="t1"),
            Application(company="Unknown", status=Status.APPLIED, thread_id="t2"),
        ]
        _, added = merge_all([], incoming)
        assert len(added) == 2

    def test_unknown_company_still_joins_on_thread(self):
        existing = [Application(company="Unknown", status=Status.APPLIED, thread_id="t1", row=2)]
        assert match_application(
            existing, Application(company="Unknown", status=Status.REJECTED, thread_id="t1")
        ) is existing[0]


def test_full_lifecycle_across_three_emails():
    """Confirmation, then interview invite, then rejection on separate threads."""
    messages = [
        email("Your application for Data Analyst at Acme",
              "Thank you for applying to Acme.", thread_id="t1", received=date(2026, 3, 1)),
        email("Interview invitation",
              "We would like to invite you to an interview.",
              thread_id="t2", received=date(2026, 3, 10)),
        email("Update on your application",
              "Unfortunately we have decided to move forward with other candidates.",
              thread_id="t3", received=date(2026, 3, 25)),
    ]
    changed, added = merge_all([], applications_from_messages(messages))

    assert not changed
    assert len(added) == 1
    record = added[0]
    assert record.company == "Acme"
    assert record.role == "Data Analyst"
    assert record.status == Status.REJECTED
    assert record.applied_date == date(2026, 3, 1)
    assert record.last_update == date(2026, 3, 25)
