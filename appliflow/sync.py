"""Bridge between parsed emails and tracked application records."""

from __future__ import annotations

from .classify import classify, extract_company, extract_role
from .gmail import Message
from .records import Application, Status


def message_to_application(message: Message) -> Application | None:
    """Build a record from an email, or None when it isn't about an application.

    The Gmail search is deliberately loose, so most of the filtering happens
    here: anything the classifier can't place is dropped rather than guessed at.
    """
    status = classify(message.subject, message.body)
    if status is Status.UNKNOWN:
        return None

    return Application(
        company=extract_company(
            message.sender_name, message.sender_email, message.subject
        ),
        role=extract_role(message.subject),
        status=status,
        # Earliest evidence we have; absorb() keeps the earliest across emails.
        applied_date=message.received,
        last_update=message.received,
        last_subject=message.subject,
        thread_id=message.thread_id,
    )


def applications_from_messages(messages: list[Message]) -> list[Application]:
    return [
        record
        for record in (message_to_application(message) for message in messages)
        if record is not None
    ]
