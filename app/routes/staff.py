# -*- coding: utf-8 -*-
"""
Панель сотрудника колледжа: вход, список заявок, карточка, статусы, комментарии.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import require_roles
from ..config import (
    MAX_COMMENT_LENGTH,
    MAX_NOTE_LENGTH,
    STAFF_PAGE_SIZE,
    TRACKING_LINK_TTL_DAYS,
    UPLOAD_DIR,
)
from ..database import get_db
from ..filters import build_ticket_filters
from ..models import Attachment, Category, Comment, Location, STATUSES, Ticket, TicketHistory, User
from ..parsing import as_int
from ..security import (
    current_user,
    hash_password,
    login_is_blocked,
    register_login_failure,
    require_csrf,
    reset_login_failures,
    verify_password,
)
from ..templating import templates
from ..workflow import ASSIGNEE_REQUIRED_STATUSES, allowed_status_options, allowed_transitions

router = APIRouter()
logger = logging.getLogger(__name__)


def _redirect_login():
    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/staff", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})


@router.post("/login")
def login(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)

    if login_is_blocked(request, login):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Слишком много неудачных попыток входа. "
                "Подождите несколько минут и попробуйте снова."
            },
            status_code=429,
        )

    user = db.scalar(select(User).where(User.login == login.strip()))
    # Отключённая учётная запись получает такое же нейтральное сообщение,
    # чтобы нельзя было перебором узнать, существует ли пользователь.
    if (
        user is None
        or not verify_password(password, user.password_hash)
        or not user.is_active
    ):
        register_login_failure(request, login)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Неверный логин или пароль."},
            status_code=401,
        )

    reset_login_failures(request, login)
    request.session["user_id"] = user.id
    request.session["user_version"] = user.session_version
    return RedirectResponse("/staff", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    require_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/staff/password", response_class=HTMLResponse)
def password_page(request: Request, saved: str = "", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user is None:
        return _redirect_login()
    return templates.TemplateResponse(
        request=request,
        name="staff_password.html",
        context={"user": user, "error": "", "saved": saved},
    )


@router.post("/staff/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    def error(message: str):
        return templates.TemplateResponse(
            request=request,
            name="staff_password.html",
            context={"user": user, "error": message, "saved": ""},
            status_code=400,
        )

    if not verify_password(current_password, user.password_hash):
        return error("Текущий пароль указан неверно.")
    if len(new_password) < 8:
        return error("Новый пароль должен содержать минимум 8 символов.")
    if new_password != confirm_password:
        return error("Новый пароль и подтверждение не совпадают.")
    if new_password == current_password:
        return error("Новый пароль должен отличаться от текущего.")

    user.password_hash = hash_password(new_password)
    user.session_version += 1
    db.commit()
    # Своя текущая сессия должна остаться рабочей, остальные — завершиться.
    request.session["user_version"] = user.session_version
    return RedirectResponse("/staff/password?saved=1", status_code=303)


@router.get("/staff", response_class=HTMLResponse)
def staff_list(
    request: Request,
    q: str = "",
    status: str = "",
    category_id: str = "",
    location_id: str = "",
    date_from: str = "",
    date_to: str = "",
    page: str = "",
    deleted: str = "",
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    page_number = as_int(page)
    if page_number is None or page_number < 1:
        page_number = 1

    filters, conditions, filter_errors = build_ticket_filters(
        db,
        q=q,
        status=status,
        category_id=category_id,
        location_id=location_id,
        date_from=date_from,
        date_to=date_to,
    )

    total = db.scalar(select(func.count(Ticket.id)).where(*conditions)) or 0
    pages = max(1, (total + STAFF_PAGE_SIZE - 1) // STAFF_PAGE_SIZE)
    if page_number > pages:
        page_number = pages

    query = select(Ticket)
    if conditions:
        query = query.where(*conditions)
    tickets = db.scalars(
        query.order_by(Ticket.created_at.desc())
        .offset((page_number - 1) * STAFF_PAGE_SIZE)
        .limit(STAFF_PAGE_SIZE)
    ).all()

    # Ссылки пагинации и экспорта сохраняют текущие фильтры.
    url_filters = {key: value for key, value in filters.items() if value}
    pagination_query = urlencode(url_filters)
    pagination_prefix = f"?{pagination_query}&" if pagination_query else "?"
    export_query = f"?{pagination_query}" if pagination_query else ""

    return templates.TemplateResponse(
        request=request,
        name="staff_list.html",
        context={
            "user": user,
            "tickets": tickets,
            "statuses": STATUSES,
            "categories": db.scalars(select(Category).order_by(Category.name)).all(),
            "locations": db.scalars(select(Location).order_by(Location.room)).all(),
            "filters": filters,
            "filter_errors": filter_errors,
            "page": page_number,
            "pages": pages,
            "total": total,
            "pagination_prefix": pagination_prefix,
            "export_query": export_query,
            "deleted": deleted,
        },
    )


def _get_ticket_or_none(db: Session, ticket_id: int):
    return db.get(Ticket, ticket_id)


def _ticket_redirect(ticket_id: int, **params: str) -> RedirectResponse:
    """Редирект в карточку заявки с сообщением в query-параметрах."""
    query = urlencode(params)
    suffix = f"?{query}" if query else ""
    return RedirectResponse(f"/staff/ticket/{ticket_id}{suffix}", status_code=303)


@router.get("/staff/ticket/{ticket_id}", response_class=HTMLResponse)
def staff_ticket(
    request: Request,
    ticket_id: int,
    error: str = "",
    saved: str = "",
    comment_saved: str = "",
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    comments = db.scalars(
        select(Comment).where(Comment.ticket_id == ticket.id).order_by(Comment.created_at)
    ).all()
    history = db.scalars(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id).order_by(TicketHistory.created_at)
    ).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.ticket_id == ticket.id).order_by(Attachment.created_at)
    ).all()
    assignees = db.scalars(select(User).where(User.role.in_(["staff", "admin"])).order_by(User.name)).all()

    return templates.TemplateResponse(
        request=request,
        name="staff_ticket.html",
        context={
            "user": user,
            "ticket": ticket,
            "comments": comments,
            "history": history,
            "attachments": attachments,
            "statuses": STATUSES,
            "status_options": allowed_status_options(ticket.status, user.role),
            "assignees": assignees,
            "error": error,
            "saved": saved,
            "comment_saved": comment_saved,
            "max_comment_length": MAX_COMMENT_LENGTH,
            "max_note_length": MAX_NOTE_LENGTH,
            "tracking_ttl_days": TRACKING_LINK_TTL_DAYS or 90,
        },
    )


@router.post("/staff/ticket/{ticket_id}/status")
def update_status(
    request: Request,
    ticket_id: int,
    status: str = Form(...),
    assignee_id: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    note = note.strip()

    if len(note) > MAX_NOTE_LENGTH:
        return _ticket_redirect(
            ticket_id,
            error=f"Комментарий слишком длинный — максимум {MAX_NOTE_LENGTH} символов.",
        )

    if status not in STATUSES:
        return _ticket_redirect(ticket_id, error="Неизвестный статус.")

    # Для «Решена» и «Отклонена» итоговый комментарий обязателен (раздел 7 ТЗ).
    if status in ("Решена", "Отклонена") and not note:
        return _ticket_redirect(
            ticket_id,
            error="Для этого статуса нужен комментарий.",
        )

    # Пустое значение — осознанное снятие исполнителя.
    assignee_value: int | None = None
    assignee_raw = assignee_id.strip()
    if assignee_raw:
        assignee_pk = as_int(assignee_raw)
        assignee = db.get(User, assignee_pk) if assignee_pk is not None else None
        if assignee is None or assignee.role not in ("staff", "admin"):
            return _ticket_redirect(
                ticket_id,
                error="Исполнитель не найден. Выберите сотрудника из списка.",
            )
        assignee_value = assignee.id

    old_status = ticket.status
    old_assignee = ticket.assignee_id

    # Нельзя перескакивать через этапы. Текущий статус разрешён всегда:
    # так можно изменить исполнителя или добавить заметку без смены статуса.
    if status != old_status and status not in allowed_transitions(old_status, user.role):
        options = ", ".join(allowed_status_options(old_status, user.role))
        return _ticket_redirect(
            ticket_id,
            error=f"Переход из «{old_status}» в «{status}» недоступен. "
            f"Допустимые статусы: {options}.",
        )

    if (
        status != old_status
        and status in ASSIGNEE_REQUIRED_STATUSES
        and assignee_value is None
    ):
        return _ticket_redirect(
            ticket_id,
            error=f"Для статуса «{status}» нужно назначить исполнителя.",
        )

    ticket.status = status
    ticket.assignee_id = assignee_value

    if status != old_status or assignee_value != old_assignee:
        record_audit(
            db,
            actor=user,
            action="ticket.status",
            entity_type="ticket",
            entity_id=ticket.id,
            summary=f"{ticket.public_number}: {old_status} → {status}",
            details={
                "status": {"old": old_status, "new": status},
                "assignee_id": {"old": old_assignee, "new": assignee_value},
            },
        )

    db.add(
        TicketHistory(
            ticket_id=ticket.id,
            old_status=old_status,
            new_status=status,
            actor_id=user.id,
            note=note,
        )
    )
    if note:
        db.add(
            Comment(
                ticket_id=ticket.id,
                author_id=user.id,
                body=note,
                visibility="public",
            )
        )
    db.commit()
    return _ticket_redirect(ticket_id, saved="1")


@router.post("/staff/ticket/{ticket_id}/delete")
def delete_ticket(
    request: Request,
    ticket_id: int,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Безвозвратное удаление заявки администратором.

    Удаляются комментарии, история, вложения и файлы фотографий. Действие
    фиксируется в журнале аудита.
    """
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    attachments = db.scalars(
        select(Attachment).where(Attachment.ticket_id == ticket.id)
    ).all()

    # Файлы удаляем только после успешного commit, чтобы не потерять их зря.
    upload_root = UPLOAD_DIR.resolve()
    file_paths = []
    for attachment in attachments:
        path = (UPLOAD_DIR / attachment.storage_key).resolve()
        if path.parent == upload_root:
            file_paths.append(path)
        db.delete(attachment)

    record_audit(
        db,
        actor=user,
        action="ticket.delete",
        entity_type="ticket",
        entity_id=ticket.id,
        summary=f"Удалена заявка {ticket.public_number}",
        details={
            "number": ticket.public_number,
            "status": ticket.status,
            "location": ticket.location.title if ticket.location else "",
            "attachments": len(attachments),
        },
    )
    db.delete(ticket)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Не удалось удалить заявку id=%s", ticket_id)
        return _ticket_redirect(
            ticket_id, error="Не удалось удалить заявку. Попробуйте ещё раз."
        )

    for path in file_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить файл вложения: %s", path)

    return RedirectResponse("/staff?deleted=1", status_code=303)


@router.post("/staff/ticket/{ticket_id}/tracking/revoke")
def revoke_tracking_link(
    request: Request,
    ticket_id: int,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    if ticket.tracking_revoked_at is None:
        ticket.tracking_revoked_at = datetime.now()
        record_audit(
            db,
            actor=user,
            action="tracking.revoke",
            entity_type="ticket",
            entity_id=ticket.id,
            summary=f"{ticket.public_number}: ссылка отслеживания отозвана",
        )
        db.commit()
    return _ticket_redirect(ticket_id, saved="1")


@router.post("/staff/ticket/{ticket_id}/tracking/restore")
def restore_tracking_link(
    request: Request,
    ticket_id: int,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    if ticket.tracking_revoked_at is not None:
        now = datetime.now()
        ticket.tracking_revoked_at = None
        # Если за время отзыва истёк срок, продлеваем его автоматически.
        if ticket.tracking_expires_at is not None and ticket.tracking_expires_at < now:
            days = TRACKING_LINK_TTL_DAYS or 90
            ticket.tracking_expires_at = now + timedelta(days=days)
        record_audit(
            db,
            actor=user,
            action="tracking.restore",
            entity_type="ticket",
            entity_id=ticket.id,
            summary=f"{ticket.public_number}: ссылка отслеживания восстановлена",
        )
        db.commit()
    return _ticket_redirect(ticket_id, saved="1")


@router.post("/staff/ticket/{ticket_id}/tracking/extend")
def extend_tracking_link(
    request: Request,
    ticket_id: int,
    days: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return RedirectResponse("/staff", status_code=303)

    days_value = as_int(days)
    if days_value is None or not 1 <= days_value <= 3650:
        return _ticket_redirect(
            ticket_id,
            error="Срок продления должен быть от 1 до 3650 дней.",
        )

    old_expiry = ticket.tracking_expires_at
    ticket.tracking_expires_at = datetime.now() + timedelta(days=days_value)
    record_audit(
        db,
        actor=user,
        action="tracking.extend",
        entity_type="ticket",
        entity_id=ticket.id,
        summary=f"{ticket.public_number}: ссылка продлена на {days_value} дн.",
        details={
            "expires_at": {
                "old": old_expiry.isoformat() if old_expiry else None,
                "new": ticket.tracking_expires_at.isoformat(),
            }
        },
    )
    db.commit()
    return _ticket_redirect(ticket_id, saved="1")


@router.post("/staff/ticket/{ticket_id}/comment")
def add_comment(
    request: Request,
    ticket_id: int,
    body: str = Form(...),
    visibility: str = Form("public"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ticket = _get_ticket_or_none(db, ticket_id)
    if ticket is None:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Заявка не найдена",
                "message": "Заявка с таким номером не существует. Комментарий не добавлен.",
            },
            status_code=404,
        )

    body = body.strip()
    if len(body) > MAX_COMMENT_LENGTH:
        return _ticket_redirect(
            ticket_id,
            error=f"Комментарий слишком длинный — максимум {MAX_COMMENT_LENGTH} символов.",
        )

    if body:
        db.add(
            Comment(
                ticket_id=ticket.id,
                author_id=user.id,
                body=body,
                visibility="internal" if visibility == "internal" else "public",
            )
        )
        db.commit()
    return _ticket_redirect(ticket_id, comment_saved="1")
