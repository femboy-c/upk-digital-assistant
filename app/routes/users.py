# -*- coding: utf-8 -*-
"""Управление пользователями в панели администратора."""
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import changes_between, record_audit
from ..authz import require_roles
from ..database import get_db
from ..models import Comment, Ticket, TicketHistory, User
from ..security import hash_password, require_csrf
from ..templating import templates

router = APIRouter()

LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,60}$")
ROLES = ("staff", "admin")


def _empty_values() -> dict:
    return {"name": "", "login": "", "role": "staff", "is_active": True}


def _validate_user(
    db: Session,
    *,
    name: str,
    login: str,
    role: str,
    user_id: int | None = None,
) -> tuple[dict, str]:
    values = {
        "name": (name or "").strip(),
        "login": (login or "").strip().lower(),
        "role": (role or "").strip(),
    }

    if not values["name"]:
        return values, "Укажите имя пользователя."
    if len(values["name"]) > 120:
        return values, "Имя слишком длинное — максимум 120 символов."
    if not LOGIN_RE.fullmatch(values["login"]):
        return values, (
            "Логин должен содержать 3–60 латинских букв, цифр, точек, "
            "дефисов или подчёркиваний."
        )
    if values["role"] not in ROLES:
        return values, "Выберите роль: сотрудник или администратор."

    query = select(User).where(func.lower(User.login) == values["login"])
    if user_id is not None:
        query = query.where(User.id != user_id)
    if db.scalar(query) is not None:
        return values, "Такой логин уже занят. Придумайте другой."

    return values, ""


def _validate_password(password: str, confirm: str) -> str | None:
    if len(password) < 8:
        return "Пароль должен содержать минимум 8 символов."
    if password != confirm:
        return "Пароль и подтверждение не совпадают."
    return None


def _active_admin_count(db: Session, exclude_id: int | None = None) -> int:
    query = select(func.count(User.id)).where(
        User.role == "admin", User.is_active.is_(True)
    )
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.scalar(query) or 0


def _render_users(
    request: Request,
    db: Session,
    user: User,
    *,
    error: str = "",
    saved: str = "",
):
    users = db.scalars(select(User).order_by(User.role.desc(), User.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="staff_users.html",
        context={"user": user, "users": users, "error": error, "saved": saved},
    )


def _render_form(
    request: Request,
    user: User,
    *,
    values: dict,
    target: User | None = None,
    error: str = "",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="staff_user_form.html",
        context={
            "user": user,
            "values": values,
            "target": target,
            "is_edit": target is not None,
            "roles": ROLES,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/staff/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    error: str = "",
    saved: str = "",
    db: Session = Depends(get_db),
):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial
    return _render_users(request, db, user, error=error, saved=saved)


@router.get("/staff/users/new", response_class=HTMLResponse)
def new_user_page(request: Request, db: Session = Depends(get_db)):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial
    return _render_form(request, user, values=_empty_values())


@router.post("/staff/users")
def create_user(
    request: Request,
    name: str = Form(""),
    login: str = Form(""),
    role: str = Form("staff"),
    password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    values, error = _validate_user(db, name=name, login=login, role=role)
    values["is_active"] = True
    if error:
        return _render_form(request, user, values=values, error=error, status_code=400)

    password_error = _validate_password(password, confirm_password)
    if password_error:
        return _render_form(
            request, user, values=values, error=password_error, status_code=400
        )

    target = User(
        name=values["name"],
        login=values["login"],
        role=values["role"],
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(target)
    try:
        db.flush()
        record_audit(
            db,
            actor=user,
            action="user.create",
            entity_type="user",
            entity_id=target.id,
            summary=f"Создан пользователь @{target.login}",
            details={"after": {"name": target.name, "login": target.login, "role": target.role}},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return _render_form(
            request,
            user,
            values=values,
            error="Такой логин уже занят. Придумайте другой.",
            status_code=400,
        )

    return RedirectResponse("/staff/users?saved=1", status_code=303)


@router.get("/staff/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_page(request: Request, user_id: int, db: Session = Depends(get_db)):
    actor, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/staff/users?error=Пользователь+не+найден", status_code=303)

    values = {
        "name": target.name,
        "login": target.login,
        "role": target.role,
        "is_active": target.is_active,
    }
    return _render_form(request, actor, values=values, target=target)


@router.post("/staff/users/{user_id}")
def update_user(
    request: Request,
    user_id: int,
    name: str = Form(""),
    login: str = Form(""),
    role: str = Form("staff"),
    is_active: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    actor, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/staff/users?error=Пользователь+не+найден", status_code=303)

    values, error = _validate_user(
        db, name=name, login=login, role=role, user_id=target.id
    )
    want_active = is_active.strip().lower() in ("1", "true", "on", "yes")
    values["is_active"] = want_active

    if error:
        return _render_form(
            request, actor, values=values, target=target, error=error, status_code=400
        )

    if target.id == actor.id and values["role"] != "admin":
        return _render_form(
            request,
            actor,
            values=values,
            target=target,
            error="Нельзя понизить собственную роль администратора.",
            status_code=400,
        )
    if target.id == actor.id and not want_active:
        return _render_form(
            request,
            actor,
            values=values,
            target=target,
            error="Нельзя отключить собственную учётную запись.",
            status_code=400,
        )
    if (
        target.role == "admin"
        and target.is_active
        and (values["role"] != "admin" or not want_active)
        and _active_admin_count(db, exclude_id=target.id) == 0
    ):
        return _render_form(
            request,
            actor,
            values=values,
            target=target,
            error="Нельзя оставить колледж без активного администратора.",
            status_code=400,
        )

    if password:
        password_error = _validate_password(password, confirm_password)
        if password_error:
            return _render_form(
                request,
                actor,
                values=values,
                target=target,
                error=password_error,
                status_code=400,
            )

    before = {
        "name": target.name,
        "login": target.login,
        "role": target.role,
        "is_active": target.is_active,
    }
    after = {
        "name": values["name"],
        "login": values["login"],
        "role": values["role"],
        "is_active": want_active,
    }
    changes = changes_between(before, after)
    if password:
        changes["password"] = {"old": "—", "new": "изменён"}

    target.name = after["name"]
    target.login = after["login"]
    target.role = after["role"]
    target.is_active = after["is_active"]
    if password:
        target.password_hash = hash_password(password)
        target.session_version += 1
        if target.id == actor.id:
            request.session["user_version"] = target.session_version

    if changes:
        record_audit(
            db,
            actor=actor,
            action="user.update",
            entity_type="user",
            entity_id=target.id,
            summary=f"Изменён пользователь @{target.login}",
            details={"changes": changes},
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _render_form(
            request,
            actor,
            values=values,
            target=target,
            error="Такой логин уже занят. Придумайте другой.",
            status_code=400,
        )

    return RedirectResponse("/staff/users?saved=1", status_code=303)


@router.post("/staff/users/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    actor, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/staff/users?error=Пользователь+не+найден", status_code=303)

    if target.id == actor.id:
        return RedirectResponse(
            "/staff/users?error=Нельзя+удалить+собственную+учётную+запись",
            status_code=303,
        )
    if (
        target.role == "admin"
        and target.is_active
        and _active_admin_count(db, exclude_id=target.id) == 0
    ):
        return RedirectResponse(
            "/staff/users?error=Нельзя+удалить+последнего+активного+администратора",
            status_code=303,
        )

    assigned = db.scalar(select(func.count(Ticket.id)).where(Ticket.assignee_id == target.id)) or 0
    comments = db.scalar(select(func.count(Comment.id)).where(Comment.author_id == target.id)) or 0
    history = db.scalar(
        select(func.count(TicketHistory.id)).where(TicketHistory.actor_id == target.id)
    ) or 0
    if assigned or comments or history:
        return RedirectResponse(
            "/staff/users?error=У+пользователя+есть+связанные+заявки,+"
            "комментарии+или+история.+Отключите+его+вместо+удаления",
            status_code=303,
        )

    record_audit(
        db,
        actor=actor,
        action="user.delete",
        entity_type="user",
        entity_id=target.id,
        summary=f"Удалён пользователь @{target.login}",
        details={"before": {"name": target.name, "login": target.login, "role": target.role}},
    )
    db.delete(target)
    db.commit()
    return RedirectResponse("/staff/users?saved=1", status_code=303)
