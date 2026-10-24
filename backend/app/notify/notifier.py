"""Notifications: Telegram and email, fed by the event hub.

The notifier is a hub subscriber with its own queue, so a slow SMTP server never delays the
dashboard or a trade. Which events go out is a setting (trades, auto decisions, risk halts); every
send's outcome is kept per channel for the System page. A failed send is logged, never retried in
a loop and never turned into another notification.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import httpx

from app.core.events import Event
from app.settings import Settings

log = logging.getLogger(__name__)

TRADE_EVENTS = {"StructureOpened", "StructureClosed", "StructureFailed", "StructureError"}
RISK_EVENTS = {"RiskHalt"}
SMTP_TIMEOUT = 20


class NotifyError(Exception):
    """Safe to show: never carries a token or a password."""


class Notifier:
    def __init__(self, settings: Any, http: httpx.AsyncClient | None = None) -> None:
        self.settings: Any = settings  # () -> Settings
        self.http = http or httpx.AsyncClient(timeout=15)
        self.channels: dict[str, dict[str, Any]] = {
            "telegram": {"sent": 0, "failed": 0, "last_at": None, "last_error": ""},
            "email": {"sent": 0, "failed": 0, "last_at": None, "last_error": ""},
        }

    def wants(self, event: Event, s: Settings) -> bool:
        if event.type in TRADE_EVENTS:
            return s.notify_trades
        if event.type in RISK_EVENTS:
            return s.notify_risk
        if event.type == "AutoDecision":
            return s.notify_decisions
        return False

    async def on_event(self, event: Event) -> None:
        s: Settings = self.settings()
        if self.wants(event, s):
            await self.send(event.type, event.message)

    def enabled(self, s: Settings) -> list[str]:
        out = []
        if s.notify_telegram and s.telegram_bot_token and s.telegram_chat_id:
            out.append("telegram")
        if s.notify_email and s.email_smtp_host and s.email_recipients:
            out.append("email")
        return out

    async def send(self, subject: str, text: str) -> dict[str, str]:
        """To every enabled channel. Returns channel -> "ok" or the error."""
        s: Settings = self.settings()
        results: dict[str, str] = {}
        for channel in self.enabled(s):
            stat = self.channels[channel]
            try:
                if channel == "telegram":
                    await self._telegram(s, f"TradeBuddy · {subject}\n{text}")
                else:
                    await asyncio.to_thread(self._email, s, f"TradeBuddy · {subject}", text)
                stat["sent"] += 1
                stat["last_error"] = ""
                results[channel] = "ok"
            except NotifyError as exc:
                stat["failed"] += 1
                stat["last_error"] = str(exc)
                results[channel] = str(exc)
                log.warning("notify_failed channel=%s error=%s", channel, exc)
            stat["last_at"] = time.time()
        return results

    async def _telegram(self, s: Settings, text: str) -> None:
        try:
            res = await self.http.post(
                f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                json={"chat_id": s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True},
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"could not reach Telegram: {type(exc).__name__}") from exc
        if res.status_code != 200:
            try:
                detail = res.json().get("description", "")
            except ValueError:
                detail = ""
            raise NotifyError(f"Telegram answered HTTP {res.status_code} {detail}"[:200])

    @staticmethod
    def _email(s: Settings, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.email_smtp_user or f"tradebuddy@{s.email_smtp_host}"
        msg["To"] = ", ".join(s.email_recipients)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="tradebuddy")
        msg.set_content(text)
        try:
            if s.email_smtp_port == 465:
                with smtplib.SMTP_SSL(s.email_smtp_host, s.email_smtp_port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context()) as smtp:
                    if s.email_smtp_user:
                        smtp.login(s.email_smtp_user, s.email_smtp_pass)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(s.email_smtp_host, s.email_smtp_port, timeout=SMTP_TIMEOUT) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    if s.email_smtp_user:
                        smtp.login(s.email_smtp_user, s.email_smtp_pass)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            # The exception text can echo the login; keep only its type and the server's code.
            code = getattr(exc, "smtp_code", "")
            raise NotifyError(f"email failed: {type(exc).__name__} {code}".strip()) from exc

    async def aclose(self) -> None:
        await self.http.aclose()
