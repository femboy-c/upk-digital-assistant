# -*- coding: utf-8 -*-
"""Запись действий сотрудников в журнал аудита."""
import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, User


def record_audit(
    db: Session,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    summary: str = "",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Добавляет запись в журнал (commit остаётся за вызывающим кодом)."""
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:255],
        details=json.dumps(details or {}, ensure_ascii=False, default=str),
    )
    db.add(entry)
    return entry


def changes_between(before: dict, after: dict) -> dict[str, dict[str, str]]:
    """Возвращает только реально изменившиеся поля."""
    changes: dict[str, dict[str, str]] = {}
    for key, new_value in after.items():
        old_value = before.get(key, "")
        if old_value != new_value:
            changes[key] = {"old": old_value, "new": new_value}
    return changes
