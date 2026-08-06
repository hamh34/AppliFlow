import pytest

from appliflow.classify import classify, extract_company, extract_role
from appliflow.records import Status


@pytest.mark.parametrize(
    "subject, body, expected",
    [
        (
            "Thank you for applying to Stripe",
            "We have received your application and will review it shortly.",
            Status.APPLIED,
        ),
        (
            "Your application to Acme",
            "Thanks for applying! Unfortunately, we have decided to move forward "
            "with other candidates at this time.",
            Status.REJECTED,
        ),
        (
            "Update on your application",
            "We regret to inform you that you were not selected for this role.",
            Status.REJECTED,
        ),
        (
            "Interview invitation - Data Analyst",
            "We would like to invite you to an interview next week.",
            Status.INTERVIEW,
        ),
        (
            "Next steps for your application",
            "Thank you for applying. Please book a time that works for you.",
            Status.INTERVIEW,
        ),
        (
            "Your coding challenge",
            "Please complete the take-home assignment within 5 days.",
            Status.ASSESSMENT,
        ),
        (
            "Online Assessment invitation",
            "Complete the assessment on HackerRank.",
            Status.ASSESSMENT,
        ),
        (
            "Offer of employment",
            "We are pleased to extend an offer for the position.",
            Status.OFFER,
        ),
        ("Your Amazon.com order has shipped", "Your package is on the way.", Status.UNKNOWN),
    ],
)
def test_classify(subject, body, expected):
    assert classify(subject, body) == expected


def test_rejection_outranks_confirmation_language():
    """Rejections routinely open by thanking you for applying; that must not win."""
    subject = "Thank you for your application"
    body = (
        "Thank you for applying to the Data Analyst position. "
        "Unfortunately we will not be moving forward with your candidacy."
    )
    assert classify(subject, body) == Status.REJECTED


def test_interview_outranks_confirmation_language():
    body = (
        "Thanks for your interest in our company. We would like to speak with you "
        "and schedule a call this week."
    )
    assert classify("Your application", body) == Status.INTERVIEW


def test_unfortunately_does_not_override_a_clear_interview_invite():
    body = (
        "We would like to invite you to an interview. Unfortunately Tuesday no "
        "longer works, so please pick another slot."
    )
    assert classify("Interview scheduling", body) == Status.INTERVIEW


class TestExtractCompany:
    def test_uses_employer_domain(self):
        assert extract_company("Careers", "no-reply@stripe.com", "") == "Stripe"

    def test_strips_subdomains(self):
        assert extract_company("", "jobs@careers.shopify.com", "") == "Shopify"

    def test_handles_two_part_tlds(self):
        assert extract_company("", "hr@recruit.tokopedia.co.id", "") == "Tokopedia"

    def test_falls_back_to_display_name_for_ats_domains(self):
        assert (
            extract_company("Figma Careers", "no-reply@greenhouse.io", "") == "Figma"
        )

    def test_falls_back_to_subject_when_sender_is_opaque(self):
        company = extract_company(
            "Recruiting Team",
            "notification@myworkday.com",
            "Thank you for applying to Netflix",
        )
        assert company == "Netflix"

    def test_returns_unknown_when_nothing_identifies_the_employer(self):
        assert extract_company("no-reply", "no-reply@greenhouse.io", "Update") == "Unknown"

    def test_hyphenated_domain_becomes_title_case(self):
        assert extract_company("", "jobs@acme-corp.com", "") == "Acme Corp"


class TestExtractRole:
    @pytest.mark.parametrize(
        "subject, expected",
        [
            ("Your application for Data Analyst at Acme", "Data Analyst"),
            ("Application for the Senior Backend Engineer position", "Senior Backend Engineer"),
            ("Thank you for applying to Product Manager at Stripe", "Product Manager"),
            ("Data Scientist - Application Received", "Data Scientist"),
            ("Regarding the Machine Learning Engineer role", "Machine Learning Engineer"),
        ],
    )
    def test_pulls_title_from_subject(self, subject, expected):
        assert extract_role(subject) == expected

    def test_returns_none_when_subject_has_no_role(self):
        assert extract_role("Update on your application") is None

    def test_rejects_junk_matches(self):
        assert extract_role("Your application for us") is None
