"""SMTP mail sending.

`MailSender` is a thin Protocol so the orchestration in `planer.mailing` can be
unit-tested with a fake. `SmtpMailSender` is the real STARTTLS implementation.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol


class MailSender(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None:
        """Send a plain-text email. Raises on failure."""
        ...


def apply_override(to: str, subject: str, override_to: str) -> tuple[str, str]:
    """If a test override recipient is set, redirect the mail there and keep the
    original recipient visible in the subject. Returns (actual_to, subject)."""
    if override_to:
        return override_to, f"[TEST → {to}] {subject}"
    return to, subject


class SmtpMailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        mail_from: str,
        reply_to: str = "",
        override_to: str = "",
        starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._mail_from = mail_from
        self._reply_to = reply_to
        self._override_to = override_to
        self._starttls = starttls

    def send(self, *, to: str, subject: str, body: str) -> None:
        actual_to, subject = apply_override(to, subject, self._override_to)
        msg = EmailMessage()
        msg["From"] = self._mail_from
        msg["To"] = actual_to
        msg["Subject"] = subject
        if self._reply_to:
            msg["Reply-To"] = self._reply_to
        msg.set_content(body)

        with smtplib.SMTP(self._host, self._port) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)
