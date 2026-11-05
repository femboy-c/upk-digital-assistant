# -*- coding: utf-8 -*-
"""Общий объект шаблонов и функции, доступные во всех HTML-страницах."""
from fastapi import Request
from fastapi.templating import Jinja2Templates

from .database import BASE_DIR, SessionLocal
from .models import User
from .security import get_csrf_token


def _navigation_context(request: Request) -> dict:
    """Добавляет во все шаблоны текущего пользователя для меню аккаунта."""
    user_id = request.session.get("user_id")
    user = None
    if user_id:
        try:
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if user is not None and (
                    not user.is_active
                    or request.session.get("user_version") != user.session_version
                ):
                    user = None
        except Exception:
            # Меню не должно ломать страницу ошибки, если база недоступна.
            user = None
    my_tickets = request.session.get("my_tickets", [])
    return {
        "nav_user": user,
        "my_tickets_count": len([token for token in my_tickets if token]),
    }


templates = Jinja2Templates(
    directory=str(BASE_DIR / "app" / "templates"),
    context_processors=[_navigation_context],
)
templates.env.globals["csrf_token"] = get_csrf_token
