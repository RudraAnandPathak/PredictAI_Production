"""Delivery adapters. `local` always works and needs no credentials. `email` and `slack` are NOT active until credentials exist: creating a schedule that uses one answers a typed error, and an
adapter that is somehow called without credentials refuses instead of sending. Neither adapter sends report contents: they send a short notice (title, version, readiness) and the in-app link; the
recipient must sign in and have access to open the report."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..identity.email import EmailMessage


class DeliveryError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LocalAdapter:
    kind = "local"
    configured = True
    needs = "Nothing: the export is kept in your delivery history."

    def send(self, notice: Dict[str, Any]) -> str:
        return "Saved to your delivery history."


class EmailAdapter:
    kind = "email"
    needs = "SMTP credentials: set PREDICTAI_SMTP_HOST (and username/password) so the account-email backend is 'smtp'."

    def __init__(self, settings: Any, sender: Any) -> None:
        self.settings, self.sender = settings, sender

    @property
    def configured(self) -> bool:
        return self.settings.email_backend == "smtp" and bool(self.settings.smtp_host)

    def send(self, notice: Dict[str, Any]) -> str:
        if not self.configured:
            raise DeliveryError("Email is not configured on this server.", retryable=False)
        body = (f"A scheduled PredictAI report is ready.\n\nReport: {notice['title']}\nVersion: {notice['version']}\nReadiness: {notice['readiness']}\n\n"
                f"Sign in to open it: {notice['link']}\n\nThis message does not contain the report. You need access to the report to open it.")
        try:
            self.sender.send(EmailMessage(to=notice["recipient"], subject=f"Scheduled report: {notice['title'][:80]}", body=body))
        except Exception as exc:  # noqa: BLE001
            raise DeliveryError("The mail server did not accept the message.") from exc
        return "Email handed to the mail server."


class SlackAdapter:
    kind = "slack"
    needs = "A Slack incoming-webhook URL in PREDICTAI_P7_SLACK_WEBHOOK_URL (https://hooks.slack.com/...)."

    def __init__(self, url: Optional[str] = None, opener: Any = None) -> None:
        self.url = (url if url is not None else os.environ.get("PREDICTAI_P7_SLACK_WEBHOOK_URL", "")).strip()
        self.opener = opener

    @property
    def configured(self) -> bool:
        u = urlparse(self.url)
        return u.scheme == "https" and u.hostname == "hooks.slack.com" and u.path.startswith("/services/")

    def send(self, notice: Dict[str, Any]) -> str:
        if not self.configured:
            raise DeliveryError("Slack is not configured on this server.", retryable=False)
        payload = json.dumps({"text": f"Scheduled PredictAI report: *{notice['title'][:100]}* (version {notice['version']}, {notice['readiness']}). Open it: {notice['link']}"}).encode()
        req = urllib.request.Request(self.url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = (self.opener or urllib.request.urlopen)(req, timeout=10)
            code = getattr(resp, "status", 200)
        except Exception as exc:  # noqa: BLE001
            raise DeliveryError("Slack did not accept the message.") from exc
        if code >= 400:
            raise DeliveryError(f"Slack answered {code}.", retryable=code >= 500 or code == 429)
        return "Posted to Slack."


def build_adapters(settings: Any, sender: Any) -> Dict[str, Any]:
    return {"local": LocalAdapter(), "email": EmailAdapter(settings, sender), "slack": SlackAdapter()}
