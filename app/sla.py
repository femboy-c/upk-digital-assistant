# -*- coding: utf-8 -*-
"""Простые сроки реакции и решения заявок (SLA).

Правила фиксированные и не зависят от календаря рабочих дней: для колледжа
важнее предсказуемая подсветка просроченных заявок, чем точный производственный
календарь. Часы считаются от момента создания заявки.
"""
from datetime import datetime, timedelta

from .models import Ticket

FINAL_STATUSES = {"Решена", "Отклонена"}

# priority -> (часы на первый ответ, часы на решение)
SLA_RULES = {
    "Срочный": (1, 8),
    "Высокий": (4, 24),
    "Обычный": (8, 72),
    "Низкий": (24, 168),
}
DEFAULT_RULE = SLA_RULES["Обычный"]


def target_hours(ticket: Ticket) -> int:
    """Норматив в часах: для новой заявки — ответ, для остальных — решение."""
    response_hours, resolve_hours = SLA_RULES.get(ticket.priority, DEFAULT_RULE)
    return response_hours if ticket.status == "Новая" else resolve_hours


def age_hours(ticket: Ticket, now: datetime | None = None) -> float:
    created = ticket.created_at or datetime.now()
    return max(0.0, ((now or datetime.now()) - created).total_seconds() / 3600)


def is_overdue(ticket: Ticket, now: datetime | None = None) -> bool:
    """Просрочена ли открытая заявка по своему нормативу."""
    if ticket.status in FINAL_STATUSES:
        return False
    if ticket.merged_into_id:
        return False
    return age_hours(ticket, now) > target_hours(ticket)


def overdue_hours(ticket: Ticket, now: datetime | None = None) -> float:
    """На сколько часов заявка вышла за норматив (0, если не просрочена)."""
    if not is_overdue(ticket, now):
        return 0.0
    return round(age_hours(ticket, now) - target_hours(ticket), 1)


def deadline(ticket: Ticket) -> datetime:
    """Момент, к которому заявка должна быть обработана по нормативу."""
    created = ticket.created_at or datetime.now()
    return created + timedelta(hours=target_hours(ticket))
