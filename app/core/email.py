"""Outgoing email.

``console`` writes the message to the log, which is what development and tests need.
``smtp`` sends it through any SMTP relay (most transactional providers offer one).
"""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, email: Email) -> None: ...


class ConsoleEmailSender:
    """Logs the message instead of sending it.

    The body carries a sign-in secret (the link token), so it is only logged outside
    production. In production this backend delivers nothing: configure SMTP.
    """

    def __init__(self, include_body: bool = True) -> None:
        self._include_body = include_body

    async def send(self, email: Email) -> None:
        if self._include_body:
            logger.info("Email to %s | %s\n%s", email.to, email.subject, email.body)
        else:
            logger.warning(
                "Email to %s | %s was not sent: EMAIL_BACKEND=console delivers nothing "
                "in production",
                email.to,
                email.subject,
            )


class SmtpEmailSender:
    def __init__(self, config: Settings) -> None:
        self._config = config

    def _send_sync(self, email: Email) -> None:
        message = EmailMessage()
        message["From"] = self._config.email_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=15) as smtp:
            if self._config.smtp_starttls:
                smtp.starttls()
            if self._config.smtp_username:
                smtp.login(self._config.smtp_username, self._config.smtp_password)
            smtp.send_message(message)

    async def send(self, email: Email) -> None:
        await asyncio.to_thread(self._send_sync, email)


def build_email_sender(config: Settings = settings) -> EmailSender:
    if config.email_backend == "smtp":
        return SmtpEmailSender(config)
    return ConsoleEmailSender(include_body=not config.is_production)


async def send_safely(sender: EmailSender, email: Email) -> None:
    """Send without letting a delivery failure escape (used from background tasks)."""
    try:
        await sender.send(email)
    except Exception:
        logger.exception("Could not send email '%s'", email.subject)


def verification_email(to: str, token: str) -> Email:
    link = f"{settings.frontend_base_url.rstrip('/')}/verify-email?token={token}"
    return Email(
        to=to,
        subject=f"Confirm your email for {settings.app_name}",
        body=(
            "Confirm your email address by opening this link:\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {settings.email_verification_hours} hours.\n"
            "If you did not create an account, ignore this message.\n"
        ),
    )


def password_reset_email(to: str, token: str) -> Email:
    link = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={token}"
    return Email(
        to=to,
        subject=f"Reset your {settings.app_name} password",
        body=(
            "Choose a new password by opening this link:\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {settings.password_reset_minutes} minutes.\n"
            "If you did not ask for this, ignore this message; your password is unchanged.\n"
        ),
    )
