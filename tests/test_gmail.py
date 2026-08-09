import base64
from datetime import date

from appliflow.gmail import BODY_CHAR_LIMIT, parse_message


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(
    subject="Thank you for applying",
    sender="Acme Careers <no-reply@acme.com>",
    parts=None,
    body_data=None,
    mime="text/plain",
    internal_date="1772409600000",  # 2026-03-02 UTC
    **extra,
):
    payload = {
        "mimeType": mime,
        "headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": sender},
        ],
        "body": {"data": body_data} if body_data else {},
    }
    if parts:
        payload["parts"] = parts
    raw = {"id": "m1", "threadId": "t1", "internalDate": internal_date, "payload": payload}
    raw.update(extra)
    return raw


def test_parses_headers_and_sender():
    parsed = parse_message(message())
    assert parsed.message_id == "m1"
    assert parsed.thread_id == "t1"
    assert parsed.subject == "Thank you for applying"
    assert parsed.sender_name == "Acme Careers"
    assert parsed.sender_email == "no-reply@acme.com"


def test_uses_internal_date():
    assert parse_message(message()).received == date(2026, 3, 2)


def test_falls_back_to_date_header():
    raw = message(internal_date=None)
    raw.pop("internalDate")
    raw["payload"]["headers"].append(
        {"name": "Date", "value": "Mon, 2 Mar 2026 10:00:00 +0000"}
    )
    assert parse_message(raw).received == date(2026, 3, 2)


def test_missing_date_is_none():
    raw = message()
    raw.pop("internalDate")
    assert parse_message(raw).received is None


def test_reads_plain_text_body():
    raw = message(body_data=encode("We received your application."))
    assert "received your application" in parse_message(raw).body


def test_prefers_plain_text_over_html():
    parts = [
        {"mimeType": "text/html", "body": {"data": encode("<p>HTML version</p>")}},
        {"mimeType": "text/plain", "body": {"data": encode("Plain version")}},
    ]
    assert parse_message(message(parts=parts, mime="multipart/alternative")).body == "Plain version"


def test_strips_html_when_that_is_all_there_is():
    html = "<style>p{color:red}</style><p>Hello&nbsp;<b>there</b></p>"
    parts = [{"mimeType": "text/html", "body": {"data": encode(html)}}]
    assert parse_message(message(parts=parts, mime="multipart/alternative")).body == "Hello there"


def test_walks_nested_multipart():
    parts = [
        {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": encode("Nested body")}}
            ],
        }
    ]
    assert parse_message(message(parts=parts, mime="multipart/mixed")).body == "Nested body"


def test_falls_back_to_snippet_when_no_body():
    raw = message(snippet="Snippet text")
    assert parse_message(raw).body == "Snippet text"


def test_body_is_truncated():
    raw = message(body_data=encode("x" * (BODY_CHAR_LIMIT + 500)))
    assert len(parse_message(raw).body) == BODY_CHAR_LIMIT


def test_survives_undecodable_body():
    raw = message(body_data="!!!not base64!!!")
    assert parse_message(raw).body == ""


def test_handles_missing_payload():
    parsed = parse_message({"id": "m9", "threadId": "t9"})
    assert parsed.subject == ""
    assert parsed.sender_email == ""
    assert parsed.body == ""


# --- Raw HTML extraction, used by job-alert parsing --------------------------
# `_extract_body` strips tags and truncates, which destroys the links alerts
# carry. These cover the separate path that keeps the markup intact.

HTML_BODY = '<html><body><a href="https://jobs.example/view/1">Data Analyst</a></body></html>'


def test_extract_html_keeps_href_attributes():
    from appliflow.gmail import extract_html

    raw = message(mime="text/html", body_data=encode(HTML_BODY))
    assert 'href="https://jobs.example/view/1"' in extract_html(raw["payload"])


def test_extract_html_walks_nested_multipart():
    from appliflow.gmail import extract_html

    raw = message(
        mime="multipart/alternative",
        parts=[
            {"mimeType": "text/plain", "body": {"data": encode("plain version")}},
            {
                "mimeType": "multipart/related",
                "parts": [{"mimeType": "text/html", "body": {"data": encode(HTML_BODY)}}],
            },
        ],
    )
    assert "Data Analyst" in extract_html(raw["payload"])


def test_extract_html_ignores_plain_text_parts():
    from appliflow.gmail import extract_html

    raw = message(mime="text/plain", body_data=encode("no markup here"))
    assert extract_html(raw["payload"]) == ""


def test_extract_html_on_empty_payload():
    from appliflow.gmail import extract_html

    assert extract_html({}) == ""
    assert extract_html(None) == ""


def test_html_is_empty_unless_requested():
    """Regression: `scan` must not start carrying raw markup around."""
    raw = message(mime="text/html", body_data=encode(HTML_BODY))
    assert parse_message(raw).html == ""


def test_html_is_populated_when_requested():
    raw = message(mime="text/html", body_data=encode(HTML_BODY))
    assert "href=" in parse_message(raw, include_html=True).html


def test_html_is_not_truncated_like_body():
    """An alert with many jobs must not be cut off at BODY_CHAR_LIMIT."""
    long_html = "<html>" + ("<a href='https://x/1'>Job</a>" * 900) + "</html>"
    parsed = parse_message(
        message(mime="text/html", body_data=encode(long_html)), include_html=True
    )
    assert len(parsed.html) > BODY_CHAR_LIMIT
    assert len(parsed.body) <= BODY_CHAR_LIMIT


def test_requesting_html_leaves_the_other_fields_identical():
    raw = message(mime="text/html", body_data=encode(HTML_BODY))
    plain, with_html = parse_message(raw), parse_message(raw, include_html=True)
    assert plain.subject == with_html.subject
    assert plain.sender_email == with_html.sender_email
    assert plain.received == with_html.received
    assert plain.body == with_html.body
