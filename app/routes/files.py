# -*- coding: utf-8 -*-
"""Выдача вложений только тем, кому они разрешены."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Attachment, Ticket
from ..security import current_user, hash_token

router = APIRouter()


def _attachment_file(attachment: Attachment) -> Path:
    """Возвращает путь к файлу и не даёт выйти за пределы папки вложений."""
    base = UPLOAD_DIR.resolve()
    path = (UPLOAD_DIR / attachment.storage_key).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "Файл не найден")
    return path


def _send(attachment: Attachment) -> FileResponse:
    path = _attachment_file(attachment)
    return FileResponse(
        path,
        media_type=attachment.file_type or "application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/staff/attachment/{attachment_id}")
def staff_attachment(request: Request, attachment_id: int, db: Session = Depends(get_db)):
    """Вложение для вошедшего сотрудника."""
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.role not in ("staff", "admin"):
        raise HTTPException(403, "Доступно только сотрудникам колледжа")

    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Файл не найден")
    return _send(attachment)


@router.get("/t/{token}/attachment/{attachment_id}")
def tracking_attachment(token: str, attachment_id: int, db: Session = Depends(get_db)):
    """Вложение для заявителя по его секретной ссылке."""
    ticket = db.scalar(select(Ticket).where(Ticket.tracking_token_hash == hash_token(token)))
    if ticket is None or not ticket.tracking_is_active:
        raise HTTPException(404, "Заявка не найдена")

    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.ticket_id != ticket.id:
        raise HTTPException(404, "Файл не найден")
    return _send(attachment)
