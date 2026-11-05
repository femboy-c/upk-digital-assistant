# -*- coding: utf-8 -*-
"""Необязательные уведомления сотрудникам в Telegram и по email.

Если переменные не заданы, функции молча ничего не делают. Ошибки внешних
сервисов не должны ломать создание заявки или смену статуса.
"""
import json
import logging
import smtplib
import ssl
import threading
import urllib.request
from email.message import EmailMessage

from .config import (
    NOTIFY_EMAIL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_TLS,
    SMTP_USER,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    payload = json.dumps(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except Exception as error:
        # Не логируем URL: в нём находится токен бота.
        logger.warning("Telegram notification failed: %s", type(error).__name__)


def _send_email(subject: str, text: str) -> None:
    if not SMTP_HOST or not SMTP_FROM or not NOTIFY_EMAIL:
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = NOTIFY_EMAIL
    message.set_content(text)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_TLS:
                server.starttls(context=ssl.create_default_context())
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as error:
        logger.warning("Email notification failed: %s", type(error).__name__)


def _deliver(subject: str, text: str) -> None:
    _send_telegram(f"{subject}\n\n{text}")
    _send_email(subject, text)


def _notify_async(subject: str, text: str) -> None:
    """Отправляет уведомление в фоне, чтобы не задерживать ответ пользователю."""
    threading.Thread(target=_deliver, args=(subject, text), daemon=True).start()


def notify_new_ticket(ticket) -> None:
    location = ticket.location.title if getattr(ticket, "location", None) else "—"
    category = ticket.category.name if getattr(ticket, "category", None) else "—"
    text = (
        f"Номер: {ticket.public_number}\n"
        f"Место: {location}\n"
        f"Категория: {category}\n"
        f"Приоритет: {ticket.priority}\n\n"
        f"{ticket.description[:700]}"
    )
    _notify_async(f"Новая заявка {ticket.public_number}", text)


def notify_status_change(ticket, old_status: str, new_status: str, note: str = "") -> None:
    text = (
        f"Номер: {ticket.public_number}\n"
        f"Статус: {old_status} → {new_status}\n"
        f"Исполнитель: {ticket.assignee.name if getattr(ticket, 'assignee', None) else '—'}"
    )
    if note:
        text += f"\n\nКомментарий: {note[:700]}"
    _notify_async(f"Заявка {ticket.public_number}: {new_status}", text)
