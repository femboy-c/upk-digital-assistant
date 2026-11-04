# -*- coding: utf-8 -*-
"""Единая проверка ролей для закрытых разделов панели."""
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .security import current_user
from .templating import templates


def require_roles(
    request: Request,
    db: Session,
    roles: tuple[str, ...],
    *,
    message: str = "Эта страница доступна только администратору колледжа.",
):
    """Возвращает (пользователь, ответ-отказ). Одно из двух всегда None."""
    user = current_user(request, db)
    if user is None:
        return None, RedirectResponse("/login", status_code=303)
    if user.role not in roles:
        return None, templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"title": "Недостаточно прав", "message": message},
            status_code=403,
        )
    return user, None
