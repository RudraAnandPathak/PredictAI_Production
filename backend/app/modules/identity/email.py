"""Outgoing email: a small sender interface with file (development), SMTP and disabled
backends, plus the notifier that builds the messages. Tokens only ever travel in the
message body; nothing sensitive is logged."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage
from pathlib import Path
from typing import List, Optional, Protocol

from ...core.config import Settings

logger = logging.getLogger("predictai.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class MemoryOutbox:
    """Keeps messages in memory (tests and verification harnesses)."""

    def __init__(self) -> None:
        self.messages: List[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


class DisabledSender:
    def send(self, message: EmailMessage) -> None:
        logger.warning("Email is not configured; a message was not sent (subject=%r).", message.subject)


class FileOutbox:
    """Development only: writes each message to a .eml file so links can be opened locally."""

    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)

    def send(self, message: EmailMessage) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"{stamp}-{uuid.uuid4().hex[:6]}.eml"
        path.write_text(f"To: {message.to}\nSubject: {message.subject}\n\n{message.body}\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


class SmtpSender:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"], mime["To"], mime["Subject"] = self.s.email_from, message.to, message.subject
        mime.set_content(message.body)
        context = ssl.create_default_context()
        if self.s.smtp_security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=15, context=context)
        else:
            client = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15)
        with client:
            if self.s.smtp_security == "starttls":
                client.starttls(context=context)
            if self.s.smtp_username:
                client.login(self.s.smtp_username, self.s.smtp_password)
            client.send_message(mime)


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        return SmtpSender(settings)
    if settings.email_backend == "file":
        return FileOutbox(settings.email_outbox_dir)
    return DisabledSender()


_FOOTER = "\n\nIf you did not expect this, reset your password right away and tell the PredictAI owner.\n— PredictAI"

_NOTICES = {
    "password_changed": ("Your PredictAI password was changed", "The password for your PredictAI account was just changed. Other devices were signed out."),
    "password_reset": ("Your PredictAI password was reset", "Your password was reset using a recovery link. All devices were signed out."),
    "special_granted": ("Special access was added to your account", "Special access is now active on your PredictAI account."),
    "special_revoked": ("Special access was removed from your account", "Special access was removed from your PredictAI account."),
    "account_blocked": ("Your PredictAI account was suspended", "Your PredictAI account was suspended by the owner. You cannot sign in until it is restored."),
    "account_unblocked": ("Your PredictAI account was restored", "Your PredictAI account was restored. You can sign in again."),
    "account_deleted": ("Your PredictAI account was deleted", "Your PredictAI account and personal data were deleted, as you requested."),
    "oauth_linked": ("A sign-in provider was connected", "A connected account ({detail}) was added to your PredictAI account."),
    "oauth_unlinked": ("A sign-in provider was disconnected", "A connected account ({detail}) was removed from your PredictAI account."),
    "oauth_signup_blocked": ("A sign-up using a connected account was blocked", "Someone tried to create a PredictAI account from a {detail} account using your email address. Nothing was changed and no accounts were merged. If this was you, sign in with your password and connect the provider from your Account page."),
    "owner_promoted": ("You are now a PredictAI owner", "Your account was given the owner role."),
    "owner_demoted": ("Your PredictAI owner role was removed", "The owner role was removed from your account."),
}


class Notifier:
    def __init__(self, settings: Settings, sender: EmailSender) -> None:
        self.settings = settings
        self.sender = sender

    def _send(self, to: str, subject: str, body: str) -> None:
        try:
            self.sender.send(EmailMessage(to=to, subject=subject, body=body))
        except Exception:  # delivery problems must never break the user's request
            logger.warning("Email delivery failed (subject=%r).", subject, exc_info=False)

    def verification(self, to: str, token: str) -> None:
        link = f"{self.settings.frontend_base_url}/verify-email#token={token}"
        self._send(to, "Confirm your PredictAI email address",
                   f"Welcome to PredictAI.\n\nConfirm your email address to activate your account (link valid for 24 hours):\n{link}\n\nIf you did not create an account, you can ignore this message.\n— PredictAI")

    def password_reset(self, to: str, token: str, minutes: int) -> None:
        link = f"{self.settings.frontend_base_url}/reset-password#token={token}"
        self._send(to, "Reset your PredictAI password",
                   f"Use this link to choose a new password (valid for {minutes} minutes, one use):\n{link}\n\nIf you did not ask for this, ignore this message; your password has not changed.\n— PredictAI")

    def magic_link(self, to: str, token: str, minutes: int) -> None:
        link = f"{self.settings.frontend_base_url}/magic-link#token={token}"
        self._send(to, "Your PredictAI sign-in link",
                   f"Use this link to sign in (valid for {minutes} minutes, one use):\n{link}\n\nIf you did not ask for this, ignore this message. Nobody can sign in without this link.")

    def registration_exists(self, to: str) -> None:
        self._send(to, "Someone tried to register with your email",
                   f"Someone tried to create a PredictAI account with this email address, but one already exists.\nYou can sign in at {self.settings.frontend_base_url}/login or reset your password at {self.settings.frontend_base_url}/forgot-password.\n\nIf this was not you, no action is needed.\n— PredictAI")

    def security_notice(self, to: str, kind: str, detail: Optional[str] = None) -> None:
        if not self.settings.notify_security_events or kind not in _NOTICES:
            return
        subject, body = _NOTICES[kind]
        self._send(to, subject, body.replace("{detail}", detail or "") + _FOOTER)
