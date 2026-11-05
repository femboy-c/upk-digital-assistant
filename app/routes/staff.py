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
from ..duplicates import FINAL_STATUSES, find_similar_tickets
from ..filters import build_ticket_filters
from ..models import (
    Attachment,
    Category,
    Comment,
    Location,
    PRIORITIES,
    STATUSES,
    Ticket,
    TicketHistory,
    User,
)
from ..parsing import as_int
from ..sla import age_hours, is_overdue, overdue_hours, target_hours
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
    priority: str = "",
    category_id: str = "",
    location_id: str = "",
    date_from: str = "",
    date_to: str = "",
    overdue: str = "",
    page: str = "",
    deleted: str = "",
    bulk_updated: str = "",
    bulk_skipped: str = "",
    bulk_error: str = "",
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
        priority=priority,
        category_id=category_id,
        location_id=location_id,
        date_from=date_from,
        date_to=date_to,
    )

    overdue_only = overdue.strip().lower() in ("1", "true", "on", "yes")
    query = select(Ticket)
    if conditions:
        query = query.where(*conditions)

    if overdue_only:
        # Просрочка зависит от приоритета и статуса, поэтому считаем в Python
        # и уже потом режем на страницы. Для колледжа объём заявок небольшой.
        matching = db.scalars(query.order_by(Ticket.created_at.desc())).all()
        matching = [ticket for ticket in matching if is_overdue(ticket)]
        total = len(matching)
        pages = max(1, (total + STAFF_PAGE_SIZE - 1) // STAFF_PAGE_SIZE)
        if page_number > pages:
            page_number = pages
        start = (page_number - 1) * STAFF_PAGE_SIZE
        tickets = matching[start:start + STAFF_PAGE_SIZE]
    else:
        total = db.scalar(select(func.count(Ticket.id)).where(*conditions)) or 0
        pages = max(1, (total + STAFF_PAGE_SIZE - 1) // STAFF_PAGE_SIZE)
        if page_number > pages:
            page_number = pages
        tickets = db.scalars(
            query.order_by(Ticket.created_at.desc())
            .offset((page_number - 1) * STAFF_PAGE_SIZE)
            .limit(STAFF_PAGE_SIZE)
        ).all()

    overdue_ids = {ticket.id for ticket in tickets if is_overdue(ticket)}

    # Ссылки пагинации и экспорта сохраняют текущие фильтры.
    url_filters = {key: value for key, value in filters.items() if value}
    if overdue_only:
        url_filters["overdue"] = "1"
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
            "priorities": PRIORITIES,
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
            "overdue_only": overdue_only,
            "overdue_ids": overdue_ids,
            "assignees": db.scalars(
                select(User)
                .where(User.role.in_(["staff", "admin"]), User.is_active.is_(True))
                .order_by(User.name)
            ).all(),
            "bulk_updated": bulk_updated,
            "bulk_skipped": bulk_skipped,
            "bulk_error": bulk_error,
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
    similar_tickets = find_similar_tickets(
        db,
        ticket.description,
        ticket.location_id,
        exclude_id=ticket.id,
    )
    merged_into = (
        db.get(Ticket, ticket.merged_into_id) if ticket.merged_into_id else None
    )

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
            "priorities": PRIORITIES,
            "assignees": assignees,
            "error": error,
            "saved": saved,
            "comment_saved": comment_saved,
            "max_comment_length": MAX_COMMENT_LENGTH,
            "max_note_length": MAX_NOTE_LENGTH,
            "tracking_ttl_days": TRACKING_LINK_TTL_DAYS or 90,
            "similar_tickets": similar_tickets,
            "merged_into": merged_into,
            "ticket_overdue": is_overdue(ticket),
            "ticket_overdue_hours": overdue_hours(ticket),
            "ticket_sla_hours": target_hours(ticket),
            "ticket_age_hours": round(age_hours(ticket), 1),
        },
    )


@router.post("/staff/ticket/{ticket_id}/status")
def update_status(
    request: Request,
    ticket_id: int,
    status: str = Form(...),
    priority: str = Form(""),
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

    if ticket.merged_into_id:
        return _ticket_redirect(
            ticket_id,
            error="Заявка объединена с другой. Изменение статуса недоступно.",
        )

    note = note.strip()

    if len(note) > MAX_NOTE_LENGTH:
        return _ticket_redirect(
            ticket_id,
            error=f"Комментарий слишком длинный — максимум {MAX_NOTE_LENGTH} символов.",
        )

    if status not in STATUSES:
        return _ticket_redirect(ticket_id, error="Неизвестный статус.")

    priority_value = priority.strip()
    if priority_value and priority_value not in PRIORITIES:
        return _ticket_redirect(ticket_id, error="Неизвестный приоритет.")

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
    old_priority = ticket.priority

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
    if priority_value:
        ticket.priority = priority_value

    if (
        status != old_status
        or assignee_value != old_assignee
        or ticket.priority != old_priority
    ):
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
                "priority": {"old": old_priority, "new": ticket.priority},
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


@router.post("/staff/bulk")
def bulk_update(
    request: Request,
    action: str = Form(""),
    ticket_ids: list[str] = Form([]),
    assignee_id: str = Form(""),
    priority: str = Form(""),
    status: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Массовое назначение исполнителя, приоритета или статуса."""
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user is None:
        return _redirect_login()

    ids: list[int] = []
    for raw in ticket_ids[:201]:
        pk = as_int(raw)
        if pk is not None and pk not in ids:
            ids.append(pk)
    if not ids:
        return RedirectResponse(
            "/staff?" + urlencode({"bulk_error": "Выберите хотя бы одну заявку."}),
            status_code=303,
        )
    if len(ids) > 200:
        return RedirectResponse(
            "/staff?" + urlencode({"bulk_error": "За один раз можно обработать не больше 200 заявок."}),
            status_code=303,
        )

    note = note.strip()
    if len(note) > MAX_NOTE_LENGTH:
        return RedirectResponse(
            "/staff?" + urlencode({"bulk_error": f"Комментарий длиннее {MAX_NOTE_LENGTH} символов."}),
            status_code=303,
        )

    assignee_value: int | None = None
    assignee_raw = assignee_id.strip()
    if assignee_raw:
        assignee_pk = as_int(assignee_raw)
        assignee = db.get(User, assignee_pk) if assignee_pk is not None else None
        if assignee is None or assignee.role not in ("staff", "admin") or not assignee.is_active:
            return RedirectResponse(
                "/staff?" + urlencode({"bulk_error": "Исполнитель не найден."}),
                status_code=303,
            )
        assignee_value = assignee.id

    if action == "assign":
        pass
    elif action == "priority":
        if priority not in PRIORITIES:
            return RedirectResponse(
                "/staff?" + urlencode({"bulk_error": "Выберите приоритет."}),
                status_code=303,
            )
    elif action == "status":
        if status not in STATUSES:
            return RedirectResponse(
                "/staff?" + urlencode({"bulk_error": "Выберите статус."}),
                status_code=303,
            )
        if status in ("Решена", "Отклонена") and not note:
            return RedirectResponse(
                "/staff?" + urlencode({"bulk_error": "Для итогового статуса нужен комментарий."}),
                status_code=303,
            )
    else:
        return RedirectResponse(
            "/staff?" + urlencode({"bulk_error": "Неизвестное массовое действие."}),
            status_code=303,
        )

    updated = 0
    skipped = 0

    for ticket_id in ids:
        ticket = _get_ticket_or_none(db, ticket_id)
        if ticket is None or ticket.merged_into_id:
            skipped += 1
            continue

        old_status = ticket.status
        old_assignee = ticket.assignee_id
        old_priority = ticket.priority

        if action == "assign":
            if old_assignee == assignee_value:
                skipped += 1
                continue
            ticket.assignee_id = assignee_value
            db.add(
                TicketHistory(
                    ticket_id=ticket.id,
                    old_status=old_status,
                    new_status=old_status,
                    actor_id=user.id,
                    note="Массовое назначение исполнителя",
                )
            )
            record_audit(
                db,
                actor=user,
                action="ticket.bulk_assign",
                entity_type="ticket",
                entity_id=ticket.id,
                summary=f"{ticket.public_number}: массовое назначение исполнителя",
                details={"assignee_id": {"old": old_assignee, "new": assignee_value}},
            )

        elif action == "priority":
            if old_priority == priority:
                skipped += 1
                continue
            ticket.priority = priority
            record_audit(
                db,
                actor=user,
                action="ticket.bulk_priority",
                entity_type="ticket",
                entity_id=ticket.id,
                summary=f"{ticket.public_number}: приоритет {old_priority} → {priority}",
                details={"priority": {"old": old_priority, "new": priority}},
            )

        else:  # status
            if status == old_status:
                skipped += 1
                continue
            if status not in allowed_transitions(old_status, user.role):
                skipped += 1
                continue
            if (
                status in ASSIGNEE_REQUIRED_STATUSES
                and ticket.assignee_id is None
                and assignee_value is None
            ):
                skipped += 1
                continue

            if assignee_value is not None:
                ticket.assignee_id = assignee_value
            ticket.status = status
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
            record_audit(
                db,
                actor=user,
                action="ticket.bulk_status",
                entity_type="ticket",
                entity_id=ticket.id,
                summary=f"{ticket.public_number}: {old_status} → {status}",
                details={
                    "status": {"old": old_status, "new": status},
                    "assignee_id": {"old": old_assignee, "new": ticket.assignee_id},
                },
            )

        updated += 1

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Не удалось выполнить массовое действие %s", action)
        return RedirectResponse(
            "/staff?" + urlencode({"bulk_error": "Не удалось применить изменения. Попробуйте ещё раз."}),
            status_code=303,
        )

    return RedirectResponse(
        "/staff?"
        + urlencode(
            {
                "bulk_updated": str(updated),
                "bulk_skipped": str(skipped),
            }
        ),
        status_code=303,
    )


@router.post("/staff/ticket/{ticket_id}/merge")
def merge_ticket(
    request: Request,
    ticket_id: int,
    target_id: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Присоединяет заявку-дубль к основной заявке (только администратор).

    Вложения переносятся, описание дубля сохраняется внутренним комментарием,
    дубль помечается «Отклонена» и получает ссылку на основную заявку.
    """
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    source = _get_ticket_or_none(db, ticket_id)
    if source is None:
        return RedirectResponse("/staff", status_code=303)
    if source.merged_into_id:
        return _ticket_redirect(ticket_id, error="Эта заявка уже объединена с другой.")
    if source.status in FINAL_STATUSES:
        return _ticket_redirect(
            ticket_id, error="Объединять можно только открытые заявки."
        )

    target_pk = as_int(target_id)
    target = db.get(Ticket, target_pk) if target_pk is not None else None
    if target is None or target.id == source.id:
        return _ticket_redirect(ticket_id, error="Выберите другую заявку для объединения.")
    if target.merged_into_id:
        return _ticket_redirect(
            ticket_id, error="Целевая заявка уже объединена с другой."
        )
    if target.status in FINAL_STATUSES:
        return _ticket_redirect(
            ticket_id, error="Нельзя объединить с закрытой заявкой."
        )

    attachments = db.scalars(
        select(Attachment).where(Attachment.ticket_id == source.id)
    ).all()
    for attachment in attachments:
        attachment.ticket_id = target.id

    db.add(
        Comment(
            ticket_id=target.id,
            author_id=user.id,
            body=(
                f"Присоединена заявка {source.public_number}.\n\n"
                f"{source.description}"
            ),
            visibility="internal",
        )
    )

    old_status = source.status
    source.status = "Отклонена"
    source.merged_into_id = target.id
    db.add(
        TicketHistory(
            ticket_id=source.id,
            old_status=old_status,
            new_status="Отклонена",
            actor_id=user.id,
            note=f"Объединена с {target.public_number}",
        )
    )
    db.add(
        TicketHistory(
            ticket_id=target.id,
            old_status=target.status,
            new_status=target.status,
            actor_id=user.id,
            note=f"Присоединена заявка {source.public_number}",
        )
    )
    record_audit(
        db,
        actor=user,
        action="ticket.merge",
        entity_type="ticket",
        entity_id=source.id,
        summary=f"{source.public_number} объединена с {target.public_number}",
        details={
            "source": source.public_number,
            "target": target.public_number,
            "attachments": len(attachments),
        },
    )

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Не удалось объединить заявки id=%s и id=%s", ticket_id, target.id)
        return _ticket_redirect(
            ticket_id, error="Не удалось объединить заявки. Попробуйте ещё раз."
        )

    return _ticket_redirect(target.id, saved="1")


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
