# -*- coding: utf-8 -*-
"""Экспорт отфильтрованного списка заявок в CSV."""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..filters import build_ticket_filters
from ..models import Ticket
from ..security import current_user

router = APIRouter()


def _csv_cell(value) -> str:
    """Защищает Excel от формул, начинающихся с =, +, -, @."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


@router.get("/staff/export.csv")
def export_tickets(
    request: Request,
    q: str = "",
    status: str = "",
    category_id: str = "",
    location_id: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    _, conditions, _ = build_ticket_filters(
        db,
        q=q,
        status=status,
        category_id=category_id,
        location_id=location_id,
        date_from=date_from,
        date_to=date_to,
    )

    query = select(Ticket)
    if conditions:
        query = query.where(*conditions)
    tickets = db.scalars(query.order_by(Ticket.created_at.desc())).all()

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Номер",
            "Создана",
            "Обновлена",
            "Статус",
            "Приоритет",
            "Категория",
            "Помещение",
            "Исполнитель",
            "Телефон",
            "Email",
            "Кратко",
            "Описание",
        ]
    )
    for ticket in tickets:
        writer.writerow(
            [
                _csv_cell(ticket.public_number),
                ticket.created_at.strftime("%d.%m.%Y %H:%M"),
                ticket.updated_at.strftime("%d.%m.%Y %H:%M"),
                _csv_cell(ticket.status),
                _csv_cell(ticket.priority),
                _csv_cell(ticket.category.name if ticket.category else ""),
                _csv_cell(ticket.location.title if ticket.location else ""),
                _csv_cell(ticket.assignee.name if ticket.assignee else ""),
                _csv_cell(ticket.contact_phone),
                _csv_cell(ticket.contact_email),
                _csv_cell(ticket.title),
                _csv_cell(ticket.description),
            ]
        )

    filename = f"tickets-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )
