# -*- coding: utf-8 -*-
"""Журнал аудита действий сотрудников (только администратор)."""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..authz import require_roles
from ..database import get_db
from ..models import AuditLog
from ..parsing import as_int
from ..templating import templates

router = APIRouter()

PAGE_SIZE = 50

ACTION_LABELS = {
    "location.create": "Создание помещения",
    "location.update": "Изменение помещения/QR",
    "user.create": "Создание пользователя",
    "user.update": "Изменение пользователя",
    "user.delete": "Удаление пользователя",
    "ticket.status": "Изменение статуса заявки",
    "ticket.delete": "Удаление заявки",
    "tracking.revoke": "Отзыв ссылки отслеживания",
    "tracking.restore": "Восстановление ссылки",
    "tracking.extend": "Продление ссылки",
}

ENTITY_LABELS = {
    "location": "Помещение",
    "user": "Пользователь",
    "ticket": "Заявка",
}


def _pretty_details(raw: str) -> str:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return raw or ""
    return json.dumps(parsed, ensure_ascii=False, indent=2)


@router.get("/staff/audit", response_class=HTMLResponse)
def audit_list(
    request: Request,
    action: str = "",
    entity_type: str = "",
    page: str = "",
    db: Session = Depends(get_db),
):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    action_filter = action.strip()
    if action_filter and action_filter not in ACTION_LABELS:
        action_filter = ""
    entity_filter = entity_type.strip()
    if entity_filter and entity_filter not in ENTITY_LABELS:
        entity_filter = ""

    page_number = as_int(page)
    if page_number is None or page_number < 1:
        page_number = 1

    conditions = []
    if action_filter:
        conditions.append(AuditLog.action == action_filter)
    if entity_filter:
        conditions.append(AuditLog.entity_type == entity_filter)

    total = db.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page_number > pages:
        page_number = pages

    query = select(AuditLog)
    if conditions:
        query = query.where(*conditions)
    entries = db.scalars(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page_number - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()

    rows = [
        {
            "entry": entry,
            "action_label": ACTION_LABELS.get(entry.action, entry.action),
            "entity_label": ENTITY_LABELS.get(entry.entity_type, entry.entity_type),
            "details_pretty": _pretty_details(entry.details),
        }
        for entry in entries
    ]

    return templates.TemplateResponse(
        request=request,
        name="staff_audit.html",
        context={
            "user": user,
            "rows": rows,
            "total": total,
            "page": page_number,
            "pages": pages,
            "action_labels": ACTION_LABELS,
            "entity_labels": ENTITY_LABELS,
            "filters": {"action": action_filter, "entity_type": entity_filter},
        },
    )
