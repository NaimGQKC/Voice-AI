"""Telling the restaurant about a message the agent took.

Storage (``store.py``) is what makes the agent's promise *true*; this module is
how the restaurant finds out *quickly*. They are deliberately separate:

* The DB write must succeed before the agent says "I've passed this on."
* Delivery is **best-effort**. An SMS that fails must never lose the message or
  turn into an exception mid-call — it leaves ``delivered_at`` NULL, and the row
  stays in ``pending_messages()`` to be retried or picked up by a human.

The destination (a mobile number? a shared inbox?) is question #4 on the owner
questionnaire and is not yet answered. Until it is, the default notifier logs and
the record still lands durably — so nothing is lost in the meantime.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """Send one short alert. Return "" on success, or an error string."""

    def send(self, subject: str, body: str) -> str: ...


class LogNotifier:
    """Default: write it to the log. No config, cannot fail, loses nothing.

    Used until the owner tells us where messages should actually go.
    """

    def send(self, subject: str, body: str) -> str:
        logger.warning("MESSAGE FOR THE RESTAURANT | %s | %s", subject, body)
        return ""


class SmsNotifier:
    """Twilio SMS. Twilio is already in the stack for the phone number, so this
    adds no new vendor — deliberate, on a system meant to run unattended.
    """

    def __init__(self, *, account_sid: str, auth_token: str,
                 from_number: str, to_number: str):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._to = to_number

    def send(self, subject: str, body: str) -> str:
        try:
            import httpx

            r = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json",
                auth=(self._sid, self._token),
                data={"From": self._from, "To": self._to,
                      "Body": f"{subject}\n{body}"[:1500]},
                timeout=10.0,
            )
            if r.status_code >= 300:
                return f"twilio {r.status_code}"
            return ""
        except Exception as exc:  # never let delivery break a call
            return f"{type(exc).__name__}: {exc}"


class EmailNotifier:
    """Plain SMTP. Useful when the restaurant prefers a shared inbox."""

    def __init__(self, *, host: str, port: int, user: str, password: str,
                 sender: str, recipient: str):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._sender, self._recipient = sender, recipient

    def send(self, subject: str, body: str) -> str:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self._sender
            msg["To"] = self._recipient
            msg.set_content(body)
            with smtplib.SMTP(self._host, self._port, timeout=10) as s:
                s.starttls()
                if self._user:
                    s.login(self._user, self._password)
                s.send_message(msg)
            return ""
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"


def build_notifier() -> Notifier:
    """Pick a notifier from the environment; fall back to logging.

    Falling back is intentional: a misconfigured notifier must degrade to "still
    recorded, just not pushed", never to a crashed call.
    """
    to_sms = os.environ.get("AGENT_ALERT_SMS_TO", "")
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_sms = os.environ.get("TWILIO_FROM_NUMBER", "")
    if to_sms and sid and token and from_sms:
        return SmsNotifier(account_sid=sid, auth_token=token,
                           from_number=from_sms, to_number=to_sms)

    to_email = os.environ.get("AGENT_ALERT_EMAIL_TO", "")
    host = os.environ.get("AGENT_SMTP_HOST", "")
    if to_email and host:
        return EmailNotifier(
            host=host,
            port=int(os.environ.get("AGENT_SMTP_PORT", "587")),
            user=os.environ.get("AGENT_SMTP_USER", ""),
            password=os.environ.get("AGENT_SMTP_PASSWORD", ""),
            sender=os.environ.get("AGENT_SMTP_FROM", to_email),
            recipient=to_email,
        )

    return LogNotifier()
