# -*- coding: utf-8 -*-
"""Статистика по заявкам в панели колледжа."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..filters import build_ticket_filters
from ..models import Category, Location, STATUSES, Ticket
from ..security import current_user
from ..sla import is_overdue
from ..templating import templates

router = APIRouter()

OPEN_STATUSES = [status for status in STATUSES if status not in ("Решена", "Отклонена")]


def _count(db: Session, *conditions) -> int:
    query = select(func.count(Ticket.id))
    if conditions:
        query = query.where(*conditions)
    return db.scalar(query) or 0


def _rows_with_percent(rows: list[dict], total: int) -> list[dict]:
    for row in rows:
        row["percent"] = round(row["count"] * 100 / total, 1) if total else 0
    return rows


@router.get("/staff/stats", response_class=HTMLResponse)
def staff_stats(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.role not in ("staff", "admin"):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Недостаточно прав",
                "message": "Статистика доступна только сотрудникам колледжа.",
            },
            status_code=403,
        )

    filters, date_conditions, filter_errors = build_ticket_filters(
        db, date_from=date_from, date_to=date_to
    )
    period_active = bool(filters["date_from"] or filters["date_to"])

    total = _count(db, *date_conditions)
    open_count = _count(db, *date_conditions, Ticket.status.in_(OPEN_STATUSES))
    resolved = _count(db, *date_conditions, Ticket.status == "Решена")
    rejected = _count(db, *date_conditions, Ticket.status == "Отклонена")
    new_count = _count(db, *date_conditions, Ticket.status == "Новая")
    last_30_days = _count(db, Ticket.created_at >= datetime.now() - timedelta(days=30))
    open_tickets = db.scalars(
        select(Ticket).where(*date_conditions, Ticket.status.in_(OPEN_STATUSES))
    ).all()
    overdue_count = sum(1 for ticket in open_tickets if is_overdue(ticket))

    # Распределение по категориям. Считаем реальные заявки, включая «без категории».
    category_query = select(Ticket.category_id, func.count(Ticket.id))
    if date_conditions:
        category_query = category_query.where(*date_conditions)
    category_counts = dict(db.execute(category_query.group_by(Ticket.category_id)).all())
    category_rows = [
        {"name": category.name, "count": category_counts.get(category.id, 0)}
        for category in db.scalars(select(Category).order_by(Category.name)).all()
    ]
    uncategorized = _count(db, *date_conditions, Ticket.category_id.is_(None))
    if uncategorized:
        category_rows.append({"name": "Без категории", "count": uncategorized})
    category_rows.sort(key=lambda row: (-row["count"], row["name"]))

    # Распределение по помещениям.
    location_query = select(Ticket.location_id, func.count(Ticket.id))
    if date_conditions:
        location_query = location_query.where(*date_conditions)
    location_counts = dict(db.execute(location_query.group_by(Ticket.location_id)).all())
    location_rows = [
        {"name": location.title, "count": location_counts.get(location.id, 0)}
        for location in db.scalars(
            select(Location).order_by(Location.floor, Location.room, Location.zone_name)
        ).all()
    ]
    no_location = _count(db, *date_conditions, Ticket.location_id.is_(None))
    if no_location:
        location_rows.append({"name": "Без помещения", "count": no_location})
    location_rows.sort(key=lambda row: (-row["count"], row["name"]))

    export_filters = {key: value for key, value in filters.items() if value}
    export_query = urlencode(export_filters)
    export_url = f"/staff/export.csv?{export_query}" if export_query else "/staff/export.csv"

    return templates.TemplateResponse(
        request=request,
        name="staff_stats.html",
        context={
            "user": user,
            "total": total,
            "open_count": open_count,
            "resolved": resolved,
            "rejected": rejected,
            "new_count": new_count,
            "overdue_count": overdue_count,
            "last_30_days": last_30_days,
            "category_rows": _rows_with_percent(category_rows, total),
            "location_rows": _rows_with_percent(location_rows, total),
            "resolved_percent": round(resolved * 100 / total, 1) if total else 0,
            "open_percent": round(open_count * 100 / total, 1) if total else 0,
            "filters": filters,
            "filter_errors": filter_errors,
            "period_active": period_active,
            "export_url": export_url,
        },
    )
