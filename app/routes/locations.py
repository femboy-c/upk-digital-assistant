# -*- coding: utf-8 -*-
"""
Управление помещениями и привязкой QR-кодов (только для сотрудников колледжа).

Просматривать и скачивать QR может любой вошедший сотрудник,
создавать и изменять привязку — только администратор.
"""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import changes_between, record_audit
from ..authz import require_roles
from ..config import (
    FLOOR_LABELS,
    LOCATION_GROUPS,
    is_public_base_configured,
    public_base_url,
    qr_target_url,
)
from ..database import get_db
from ..models import Location
from ..security import require_csrf
from ..templating import templates

router = APIRouter()

QR_KEY_RE = re.compile(r"^[a-z0-9_-]{3,40}$")


def _validate_location(
    db: Session,
    *,
    floor: str,
    room: str,
    zone_name: str,
    qr_key: str,
    location_id: int | None = None,
) -> tuple[dict, str]:
    """Проверяет поля формы. Возвращает очищенные значения и текст ошибки."""
    values = {
        "floor": floor.strip(),
        "room": room.strip(),
        "zone_name": zone_name.strip(),
        "qr_key": qr_key.strip().lower(),
    }

    if values["floor"] not in FLOOR_LABELS:
        return values, "Выберите этаж или территорию."

    if not values["room"] and not values["zone_name"]:
        return values, "Укажите кабинет или название зоны (например, «Холл 1 этажа»)."

    if not QR_KEY_RE.fullmatch(values["qr_key"]):
        return values, (
            "QR-ключ должен содержать 3–40 латинских букв, цифр, дефисов или подчёркиваний. "
            "Например: room214."
        )

    query = select(Location).where(Location.qr_key == values["qr_key"])
    if location_id is not None:
        query = query.where(Location.id != location_id)
    if db.scalar(query) is not None:
        return values, "Такой QR-ключ уже используется другим помещением. Придумайте другой."

    return values, ""


def _render_form(
    request: Request,
    *,
    values: dict,
    location: Location | None = None,
    error: str = "",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="staff_location_form.html",
        context={
            "values": values,
            "location": location,
            "error": error,
            "is_edit": location is not None,
            "location_groups": LOCATION_GROUPS,
            "qr_url": qr_target_url(request, values["qr_key"]) if values.get("qr_key") else "",
        },
        status_code=status_code,
    )


def _empty_values() -> dict:
    return {"floor": "", "room": "", "zone_name": "", "qr_key": ""}


@router.get("/staff/locations", response_class=HTMLResponse)
def locations_list(request: Request, error: str = "", saved: str = "", db: Session = Depends(get_db)):
    user, denial = require_roles(request, db, ("staff", "admin"))
    if denial:
        return denial

    locations = db.scalars(
        select(Location).order_by(Location.floor, Location.room, Location.zone_name)
    ).all()
    base = public_base_url(request)

    return templates.TemplateResponse(
        request=request,
        name="staff_locations.html",
        context={
            "user": user,
            "locations": locations,
            "base_url": base,
            "public_base_configured": is_public_base_configured(),
            "qr_urls": {loc.id: f"{base}/?qr={quote(loc.qr_key)}" for loc in locations},
            "error": error,
            "saved": saved,
        },
    )


@router.get("/staff/locations/new", response_class=HTMLResponse)
def new_location_page(request: Request, db: Session = Depends(get_db)):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial
    return _render_form(request, values=_empty_values())


@router.post("/staff/locations")
def create_location(
    request: Request,
    floor: str = Form(""),
    room: str = Form(""),
    zone_name: str = Form(""),
    qr_key: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    values, error = _validate_location(
        db,
        floor=floor,
        room=room,
        zone_name=zone_name,
        qr_key=qr_key,
    )
    if error:
        return _render_form(request, values=values, error=error, status_code=400)

    location = Location(**values)
    db.add(location)
    try:
        db.flush()
        record_audit(
            db,
            actor=user,
            action="location.create",
            entity_type="location",
            entity_id=location.id,
            summary=f"Создано помещение: {location.title}",
            details={"after": values},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return _render_form(
            request,
            values=values,
            error="Такой QR-ключ уже используется. Придумайте другой.",
            status_code=400,
        )

    return RedirectResponse("/staff/locations?saved=1", status_code=303)


@router.get("/staff/locations/{location_id}/edit", response_class=HTMLResponse)
def edit_location_page(request: Request, location_id: int, db: Session = Depends(get_db)):
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    location = db.get(Location, location_id)
    if location is None:
        return RedirectResponse("/staff/locations?error=Помещение+не+найдено", status_code=303)

    values = {
        "floor": location.floor,
        "room": location.room,
        "zone_name": location.zone_name,
        "qr_key": location.qr_key,
    }
    return _render_form(request, values=values, location=location)


@router.post("/staff/locations/{location_id}")
def update_location(
    request: Request,
    location_id: int,
    floor: str = Form(""),
    room: str = Form(""),
    zone_name: str = Form(""),
    qr_key: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user, denial = require_roles(request, db, ("admin",))
    if denial:
        return denial

    location = db.get(Location, location_id)
    if location is None:
        return RedirectResponse("/staff/locations?error=Помещение+не+найдено", status_code=303)

    values, error = _validate_location(
        db,
        floor=floor,
        room=room,
        zone_name=zone_name,
        qr_key=qr_key,
        location_id=location.id,
    )
    if error:
        return _render_form(request, values=values, location=location, error=error, status_code=400)

    before = {
        "floor": location.floor,
        "room": location.room,
        "zone_name": location.zone_name,
        "qr_key": location.qr_key,
    }
    location.floor = values["floor"]
    location.room = values["room"]
    location.zone_name = values["zone_name"]
    location.qr_key = values["qr_key"]
    changes = changes_between(before, values)
    if changes:
        record_audit(
            db,
            actor=user,
            action="location.update",
            entity_type="location",
            entity_id=location.id,
            summary=f"Изменено помещение: {location.title}",
            details={"changes": changes},
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _render_form(
            request,
            values=values,
            location=location,
            error="Такой QR-ключ уже используется. Придумайте другой.",
            status_code=400,
        )

    return RedirectResponse("/staff/locations?saved=1", status_code=303)
