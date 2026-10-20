"""Delivering a fired alert. In-app delivery is the stored event itself (the /alerts feed reads it). Telegram and
email are optional: each is used only when its settings are present and the rule asked for it, and a failure is
recorded on the event rather than raised, so one broken channel never loses the alert.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from email.message import EmailMessage

import httpx

from ..config import Settings

log = logging.getLogger("tickersage.alerts")

CHANNELS = ("in_app", "telegram", "email")
TELEGRAM_MAX = 4000  # Telegram's limit is 4096 characters per message


def configured(settings: Settings) -> dict[str, bool]:
    return {
        "in_app": True,
        "telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "email": bool(settings.smtp_host and settings.alert_email_from and settings.alert_email_to),
    }


def plain(text: str) -> str:
    """The brief without Markdown emphasis, for channels that show it raw."""
    return re.sub(r"(\*\*|__|(?<!\w)_(?!\s)|(?<!\s)_(?!\w))", "", text)


async def send_telegram(settings: Settings, http: httpx.AsyncClient, text: str) -> None:
    url = f"{settings.telegram_api_url.rstrip('/')}/bot{settings.telegram_bot_token}/sendMessage"
    resp = await http.post(url, json={"chat_id": settings.telegram_chat_id, "text": plain(text)[:TELEGRAM_MAX],
                                      "disable_web_page_preview": True}, timeout=10.0)
    if resp.status_code != 200:
        # The token is part of the URL: never put the URL in the error.
        raise RuntimeError(f"Telegram returned HTTP {resp.status_code}")


def _send_email(settings: Settings, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_email_from
    msg["To"] = settings.alert_email_to
    msg.set_content(plain(text))
    with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(msg)


async def send_email(settings: Settings, subject: str, text: str) -> None:
    await asyncio.to_thread(_send_email, settings, subject, text)


async def deliver(settings: Settings, http: httpx.AsyncClient | None, channels: list[str], subject: str,
                  text: str) -> dict[str, str]:
    """Channel -> 'delivered', 'skipped: ...' or 'failed: ...'. In-app is always delivered (the stored event)."""
    ready = configured(settings)
    out = {"in_app": "delivered"}
    for channel in ("telegram", "email"):
        if channel not in channels:
            continue
        if not ready[channel]:
            out[channel] = "skipped: not configured on this server"
            continue
        try:
            if channel == "telegram":
                if http is None:
                    raise RuntimeError("no HTTP client")
                await send_telegram(settings, http, text)
            else:
                await send_email(settings, subject, text)
            out[channel] = "delivered"
        except Exception as exc:  # a channel failure is recorded, never raised
            log.warning("alert delivery over %s failed: %s", channel, type(exc).__name__)
            out[channel] = f"failed: {str(exc)[:160] or type(exc).__name__}"
    return out
