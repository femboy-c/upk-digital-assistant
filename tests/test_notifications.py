# -*- coding: utf-8 -*-
"""Формирование необязательных уведомлений без внешних запросов."""
from types import SimpleNamespace

from app import notifications


def test_new_ticket_notification_text(monkeypatch):
    captured = {}

    def fake_async(subject, text):
        captured["subject"] = subject
        captured["text"] = text

    monkeypatch.setattr(notifications, "_notify_async", fake_async)
    ticket = SimpleNamespace(
        public_number="ДГУ-2026-000001",
        priority="Высокий",
        description="Не работает проектор",
        location=SimpleNamespace(title="2 этаж, каб. 214"),
        category=SimpleNamespace(name="Оборудование"),
    )
    notifications.notify_new_ticket(ticket)
    assert "ДГУ-2026-000001" in captured["subject"]
    assert "каб. 214" in captured["text"]
    assert "Оборудование" in captured["text"]


def test_status_notification_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        notifications,
        "_notify_async",
        lambda subject, text: captured.update(subject=subject, text=text),
    )
    ticket = SimpleNamespace(
        public_number="ДГУ-2026-000002",
        assignee=SimpleNamespace(name="Исполнитель"),
    )
    notifications.notify_status_change(ticket, "Новая", "В работе", "Принято")
    assert "Новая → В работе" in captured["text"]
    assert "Принято" in captured["text"]


def test_deliver_uses_both_channels(monkeypatch):
    calls = []
    monkeypatch.setattr(notifications, "_send_telegram", lambda text: calls.append(("tg", text)))
    monkeypatch.setattr(notifications, "_send_email", lambda subject, text: calls.append(("email", subject)))
    notifications._deliver("Тема", "Текст")
    assert {item[0] for item in calls} == {"tg", "email"}
