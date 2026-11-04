# -*- coding: utf-8 -*-
"""Удаление заявок, срок хранения которых истёк.

По умолчанию удаляются только закрытые заявки: «Решена» и «Отклонена».
Открытые заявки можно включить явно, чтобы случайно не потерять работу.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_audit
from .config import TICKET_RETENTION_DAYS, UPLOAD_DIR
from .models import Attachment, Ticket

logger = logging.getLogger(__name__)

CLOSED_STATUSES = ("Решена", "Отклонена")


def retention_cutoff(days: int | None = None) -> datetime:
    return datetime.now() - timedelta(days=days or TICKET_RETENTION_DAYS)


def find_expired_tickets(
    db: Session,
    *,
    days: int | None = None,
    include_open: bool = False,
) -> list[Ticket]:
    cutoff = retention_cutoff(days)
    query = select(Ticket).where(Ticket.updated_at < cutoff)
    if not include_open:
        query = query.where(Ticket.status.in_(CLOSED_STATUSES))
    return db.scalars(query.order_by(Ticket.updated_at)).all()


def delete_ticket_with_files(db: Session, ticket: Ticket) -> int:
    """Удаляет заявку, её вложения и файлы. Возвращает число файлов."""
    attachments = db.scalars(
        select(Attachment).where(Attachment.ticket_id == ticket.id)
    ).all()

    upload_root = UPLOAD_DIR.resolve()
    file_paths: list[Path] = []
    for attachment in attachments:
        path = (UPLOAD_DIR / attachment.storage_key).resolve()
        if path.parent == upload_root:
            file_paths.append(path)
        db.delete(attachment)

    record_audit(
        db,
        actor=None,
        action="ticket.retention_delete",
        entity_type="ticket",
        entity_id=ticket.id,
        summary=f"Удалена по сроку хранения: {ticket.public_number}",
        details={
            "number": ticket.public_number,
            "status": ticket.status,
            "updated_at": ticket.updated_at.isoformat(),
            "attachments": len(attachments),
        },
    )
    db.delete(ticket)
    db.commit()

    for path in file_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить файл вложения: %s", path)
    return len(file_paths)
