# -*- coding: utf-8 -*-
"""Общие фильтры заявок: поиск, статус, категория, помещение и даты.

Используются списком заявок, статистикой и CSV-экспортом, чтобы правила
фильтрации везде были одинаковыми.
"""
from datetime import date, datetime, time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import Category, Location, STATUSES, Ticket
from .parsing import as_int


def parse_iso_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_ticket_filters(
    db: Session,
    *,
    q: str = "",
    status: str = "",
    category_id: str = "",
    location_id: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """Проверяет фильтры и возвращает (очищенные значения, условия, ошибки).

    Некорректные фильтры не дают 500: они сбрасываются с понятным сообщением.
    """
    errors: list[str] = []
    conditions = []
    clean = {
        "q": (q or "").strip(),
        "status": "",
        "category_id": "",
        "location_id": "",
        "date_from": "",
        "date_to": "",
    }

    if clean["q"]:
        needle = f"%{clean['q']}%"
        conditions.append(
            or_(
                Ticket.public_number.ilike(needle),
                Ticket.title.ilike(needle),
                Ticket.description.ilike(needle),
            )
        )

    status_filter = (status or "").strip()
    if status_filter:
        if status_filter in STATUSES:
            clean["status"] = status_filter
            conditions.append(Ticket.status == status_filter)
        else:
            errors.append("Неизвестный статус — фильтр сброшен.")

    category_pk = as_int(category_id)
    if category_id:
        if category_pk is None or db.get(Category, category_pk) is None:
            errors.append("Неизвестная категория — фильтр сброшен.")
        else:
            clean["category_id"] = str(category_pk)
            conditions.append(Ticket.category_id == category_pk)

    location_pk = as_int(location_id)
    if location_id:
        if location_pk is None or db.get(Location, location_pk) is None:
            errors.append("Неизвестное помещение — фильтр сброшен.")
        else:
            clean["location_id"] = str(location_pk)
            conditions.append(Ticket.location_id == location_pk)

    start_raw = (date_from or "").strip()
    end_raw = (date_to or "").strip()
    start = parse_iso_date(start_raw) if start_raw else None
    end = parse_iso_date(end_raw) if end_raw else None

    if start_raw and start is None:
        errors.append("Неверная дата начала — фильтр по датам сброшен.")
    if end_raw and end is None:
        errors.append("Неверная дата окончания — фильтр по датам сброшен.")

    if start and end and start > end:
        errors.append("Дата начала позже даты окончания — фильтр по датам сброшен.")
        start = end = None

    if start:
        clean["date_from"] = start.isoformat()
        conditions.append(Ticket.created_at >= datetime.combine(start, time.min))
    if end:
        clean["date_to"] = end.isoformat()
        conditions.append(Ticket.created_at <= datetime.combine(end, time.max))

    return clean, conditions, errors
