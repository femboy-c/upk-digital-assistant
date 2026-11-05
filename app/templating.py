# -*- coding: utf-8 -*-
"""Общий объект шаблонов и функции, доступные во всех HTML-страницах."""
from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .config import (
    GOOGLE_VERIFICATION,
    SITE_DESCRIPTION,
    SITE_NAME,
    YANDEX_VERIFICATION,
    public_base_url,
)
from .database import BASE_DIR, SessionLocal
from .models import Banner, User
from .security import get_csrf_token


def _navigation_context(request: Request) -> dict:
    """Добавляет во все шаблоны текущего пользователя для меню аккаунта."""
    user_id = request.session.get("user_id")
    user = None
    banner = None
    try:
        with SessionLocal() as db:
            if user_id:
                user = db.get(User, user_id)
                if user is not None and (
                    not user.is_active
                    or request.session.get("user_version") != user.session_version
                ):
                    user = None
            banner = db.scalar(
                select(Banner)
                .where(Banner.is_active.is_(True))
                .order_by(Banner.updated_at.desc())
            )
    except Exception:
        # Меню и баннер не должны ломать страницу ошибки, если база недоступна.
        user = None
        banner = None
    my_tickets = request.session.get("my_tickets", [])
    base_url = public_base_url(request)
    return {
        "nav_user": user,
        "my_tickets_count": len([token for token in my_tickets if token]),
        "site_base_url": base_url,
        "canonical_url": f"{base_url}{request.url.path}",
        "site_name": SITE_NAME,
        "site_description": SITE_DESCRIPTION,
        "yandex_verification": YANDEX_VERIFICATION,
        "google_verification": GOOGLE_VERIFICATION,
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "site_banner": banner,
    }


templates = Jinja2Templates(
    directory=str(BASE_DIR / "app" / "templates"),
    context_processors=[_navigation_context],
)
templates.env.globals["csrf_token"] = get_csrf_token
