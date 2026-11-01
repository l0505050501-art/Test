"""Отправка писем через SMTP (подойдёт бесплатная почта Gmail / Яндекс / Mail.ru)."""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)
last_error = ""


def configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"))


def _send(subject: str, text: str, html: str) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "465"))
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", user)
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.send_message(msg)


async def send_email(subject: str, text: str, html: str) -> bool:
    global last_error
    last_error = ""
    if not configured():
        last_error = "не заданы SMTP_HOST, SMTP_USER, SMTP_PASSWORD или MAIL_TO"
        log.warning("Почта не настроена (SMTP_* / MAIL_TO) — письмо не отправлено")
        return False
    try:
        await asyncio.to_thread(_send, subject, text, html)
        return True
    except Exception as e:  # noqa: BLE001
        last_error = f"{type(e).__name__}: {e}"[:300]
        log.exception("Не удалось отправить письмо")
        return False
