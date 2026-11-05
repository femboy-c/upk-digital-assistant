# -*- coding: utf-8 -*-
"""Баннер плановых работ на публичной части."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import require_roles
from ..config import MAX_BANNER_LENGTH
from ..database import get_db
from ..models import Banner
from ..security import require_csrf
from ..templating import templates

router = APIRouter()


@router.get("/staff/banner", response_class=HTMLResponse)
def banner_page(
    request: Request,
    saved: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    banner = db.scalar(select(Banner).order_by(Banner.id))
    return templates.TemplateResponse(
        request=request,
        name="staff_banner.html",
        context={
            "user": user,
            "banner": banner,
            "saved": saved,
            "error": error,
            "max_banner_length": MAX_BANNER_LENGTH,
        },
    )


@router.post("/staff/banner")
def banner_update(
    request: Request,
    message: str = Form(""),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    message = message.strip()
    active = is_active.strip().lower() in ("1", "on", "true", "yes")

    if len(message) > MAX_BANNER_LENGTH:
        return RedirectResponse(
            f"/staff/banner?error=Текст+длиннее+{MAX_BANNER_LENGTH}+символов",
            status_code=303,
        )
    if active and not message:
        return RedirectResponse(
            "/staff/banner?error=Нельзя+включить+пустой+баннер",
            status_code=303,
        )

    banner = db.scalar(select(Banner).order_by(Banner.id))
    if banner is None:
        banner = Banner()
        db.add(banner)

    old_message = banner.message
    old_active = banner.is_active
    banner.message = message
    banner.is_active = active

    record_audit(
        db,
        actor=user,
        action="banner.update",
        entity_type="banner",
        entity_id=banner.id,
        summary="Обновлён баннер плановых работ",
        details={
            "message": {"old": old_message, "new": message},
            "is_active": {"old": old_active, "new": active},
        },
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return RedirectResponse(
            "/staff/banner?error=Не+удалось+сохранить+баннер",
            status_code=303,
        )

    return RedirectResponse("/staff/banner?saved=1", status_code=303)
