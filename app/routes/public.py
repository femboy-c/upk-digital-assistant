# -*- coding: utf-8 -*-
"""
Публичные страницы: форма заявки, подтверждение, отслеживание, QR-коды.

Здесь не нужен вход — сюда попадает любой студент или преподаватель.
"""
import io
import os
import uuid
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import (
    LOCATION_GROUPS,
    MAX_DESCRIPTION_LENGTH,
    MAX_IMAGE_PIXELS,
    MAX_UPLOAD_TOTAL_BYTES,
    MIN_DESCRIPTION_LENGTH,
    TICKETS_RATE_LIMIT,
    TICKETS_RATE_WINDOW_SECONDS,
    UPLOAD_DIR,
    qr_target_url,
    tracking_expiry,
)
from ..database import get_db
from ..models import Attachment, Category, Comment, Location, Ticket, TicketHistory
from ..parsing import as_int
from ..ratelimit import client_ip, is_limited, register_event
from ..security import hash_token, new_tracking_token, verify_csrf
from ..templating import templates

router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
SUFFIX_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 МБ
UPLOAD_CHUNK_SIZE = 64 * 1024

# Pillow не должен пытаться декодировать изображения-«бомбы».
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _upload_dir_size() -> int:
    """Суммарный размер всех вложений на диске."""
    return sum(path.stat().st_size for path in UPLOAD_DIR.glob("*") if path.is_file())


async def _read_upload_limited(upload: UploadFile, limit: int) -> bytes | None:
    """Читает файл частями и не держит в памяти больше лимита."""
    buffer = bytearray()
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            return None
    return bytes(buffer)


def _form_data(db: Session):
    """Справочники для выпадающих списков."""
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    locations = db.scalars(select(Location).order_by(Location.floor, Location.room, Location.zone_name)).all()
    return categories, locations


def _report_context(
    db: Session,
    *,
    preselected: Location | None = None,
    qr_error: str = "",
    error: str = "",
    description: str = "",
    selected_location_id: int | None = None,
    selected_category_id: int | None = None,
):
    """Единый набор данных для формы: и для GET, и для повторного показа с ошибкой."""
    categories, locations = _form_data(db)
    return {
        "categories": categories,
        "locations": locations,
        "location_groups": LOCATION_GROUPS,
        "preselected": preselected,
        "qr_error": qr_error,
        "error": error,
        "description": description,
        "selected_location_id": selected_location_id,
        "selected_category_id": selected_category_id,
        "max_description_length": MAX_DESCRIPTION_LENGTH,
    }


@router.get("/", response_class=HTMLResponse)
def report_form(request: Request, qr: str = "", db: Session = Depends(get_db)):
    """Главная страница — форма обращения. ?qr=room214 подставит помещение."""
    preselected = None
    qr_error = ""
    if qr:
        preselected = db.scalar(select(Location).where(Location.qr_key == qr))
        if preselected is None:
            qr_error = "QR-код не распознан. Выберите этаж или территорию и помещение вручную."

    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context=_report_context(
            db,
            preselected=preselected,
            qr_error=qr_error,
            selected_location_id=preselected.id if preselected else None,
        ),
    )


def _form_error(
    request: Request,
    db: Session,
    message: str,
    *,
    status_code: int = 400,
    description: str = "",
    location: Location | None = None,
    category: Category | None = None,
):
    """Повторно показывает форму заявки с понятным сообщением об ошибке."""
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context=_report_context(
            db,
            error=message,
            description=description,
            selected_location_id=location.id if location else None,
            selected_category_id=category.id if category else None,
        ),
        status_code=status_code,
    )


def _server_error(request: Request, title: str, message: str, status_code: int = 500):
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"title": title, "message": message},
        status_code=status_code,
    )


@router.post("/tickets")
async def create_ticket(
    request: Request,
    description: str = Form(...),
    location_id: str = Form(""),
    category_id: str = Form(""),
    csrf_token: str = Form(""),
    website: str = Form(""),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Сохраняет заявку в базу и показывает номер + ссылку отслеживания."""
    description = description.strip()
    location_pk = as_int(location_id)
    category_pk = as_int(category_id)
    location = db.get(Location, location_pk) if location_pk is not None else None
    category = db.get(Category, category_pk) if category_pk is not None else None

    def form_error(message: str, status_code: int = 400):
        return _form_error(
            request,
            db,
            message,
            status_code=status_code,
            description=description,
            location=location,
            category=category,
        )

    # 1. CSRF проверяем вручную, чтобы вернуть ту же форму, а не JSON.
    if not verify_csrf(request, csrf_token):
        return form_error("Форма устарела. Обновите страницу и попробуйте снова.")

    # 2. Honeypot: скрытое поле, которое заполняют только боты.
    if website.strip():
        return form_error("Не удалось отправить заявку. Попробуйте ещё раз.")

    # 3. Ограничение частоты успешных отправок.
    rate_key = f"ticket:{client_ip(request)}"
    if is_limited(rate_key, TICKETS_RATE_LIMIT, TICKETS_RATE_WINDOW_SECONDS):
        return form_error(
            "Слишком много заявок за короткое время. Подождите несколько минут "
            "и попробуйте снова.",
            status_code=429,
        )

    # 4. Проверка полей.
    if len(description) < MIN_DESCRIPTION_LENGTH:
        return form_error(
            f"Опишите проблему подробнее — минимум {MIN_DESCRIPTION_LENGTH} символов."
        )

    if len(description) > MAX_DESCRIPTION_LENGTH:
        return form_error(
            f"Описание слишком длинное — максимум {MAX_DESCRIPTION_LENGTH} символов."
        )

    if location is None:
        return form_error("Выберите этаж или территорию и помещение, где произошла проблема.")

    if category_id and category is None:
        return form_error("Выбрана неизвестная категория. Выберите категорию из списка.")

    # 5. Фотографию проверяем до создания заявки, чтобы не писать в базу зря.
    photo_content: bytes | None = None
    if photo is not None and photo.filename:
        if photo.content_type not in ALLOWED_TYPES:
            return form_error("Можно прикреплять только JPG, PNG или WEBP.")
        photo_content = await _read_upload_limited(photo, MAX_FILE_SIZE)
        if photo_content is None:
            return form_error("Файл больше 5 МБ.")
        try:
            with Image.open(io.BytesIO(photo_content)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    return form_error(
                        "Изображение слишком большое по размеру в пикселях. "
                        "Уменьшите фотографию и попробуйте снова."
                    )
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return form_error("Файл не похож на изображение. Приложите JPG, PNG или WEBP.")

        if _upload_dir_size() + len(photo_content) > MAX_UPLOAD_TOTAL_BYTES:
            return form_error(
                "Хранилище вложений заполнено. Отправьте заявку без фотографии "
                "или обратитесь к администратору."
            )

    # 6. Номер заявки строим по реальному id, который выдаёт SQLite.
    # Это исключает коллизию при одновременной отправке двух заявок.
    token = new_tracking_token()
    ticket = Ticket(
        public_number=f"tmp-{uuid.uuid4().hex}",
        title=description[:90],
        description=description,
        category_id=category.id if category else None,
        location_id=location.id,
        status="Новая",
        tracking_token_hash=hash_token(token),
        tracking_expires_at=tracking_expiry(),
    )
    db.add(ticket)
    try:
        db.flush()  # получаем ticket.id
    except SQLAlchemyError:
        db.rollback()
        return _server_error(
            request,
            "Не удалось отправить заявку",
            "На сервере произошла ошибка. Заявка не сохранена — попробуйте ещё раз.",
        )

    number = f"ДГУ-{datetime.now().year}-{ticket.id:06d}"
    ticket.public_number = number

    # 7. Сохраняем фотографию вне static/ и добавляем запись о вложении.
    saved_filename: str | None = None
    if photo_content is not None and photo is not None:
        saved_filename = f"{uuid.uuid4().hex}{SUFFIX_BY_TYPE[photo.content_type]}"
        try:
            saved_path = UPLOAD_DIR / saved_filename
            saved_path.write_bytes(photo_content)
            try:
                os.chmod(saved_path, 0o600)
            except OSError:
                pass
        except OSError:
            db.rollback()
            return _server_error(
                request,
                "Не удалось отправить заявку",
                "Не удалось сохранить фотографию. Заявка не отправлена — попробуйте ещё раз.",
            )
        db.add(
            Attachment(
                ticket_id=ticket.id,
                storage_key=saved_filename,
                file_type=photo.content_type,
            )
        )

    db.add(TicketHistory(ticket_id=ticket.id, old_status="", new_status="Новая", note="Заявка создана"))

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        if saved_filename:
            (UPLOAD_DIR / saved_filename).unlink(missing_ok=True)
        return _server_error(
            request,
            "Не удалось отправить заявку",
            "На сервере произошла ошибка. Заявка не сохранена — попробуйте ещё раз.",
        )

    register_event(rate_key, TICKETS_RATE_WINDOW_SECONDS)
    return RedirectResponse(f"/success?number={number}&token={token}", status_code=303)


@router.get("/success", response_class=HTMLResponse)
def success(request: Request, number: str = "", token: str = ""):
    """Страница после отправки: номер заявки и ссылка отслеживания."""
    return templates.TemplateResponse(
        request=request,
        name="success.html",
        context={"number": number, "token": token},
    )


def _ticket_by_token(db: Session, token: str) -> Ticket | None:
    """Находит заявку по ссылке и проверяет срок её действия/отзыв."""
    if not token:
        return None
    ticket = db.scalar(select(Ticket).where(Ticket.tracking_token_hash == hash_token(token)))
    if ticket is None or not ticket.tracking_is_active:
        return None
    return ticket


@router.get("/t/{token}", response_class=HTMLResponse)
def track_by_link(request: Request, token: str, db: Session = Depends(get_db)):
    """Отслеживание по секретной ссылке."""
    ticket = _ticket_by_token(db, token)
    if ticket is None:
        return templates.TemplateResponse(
            request=request,
            name="track_missing.html",
            context={},
            status_code=404,
        )
    return _render_track(request, db, ticket, token)


@router.get("/track", response_class=HTMLResponse)
def track_form(request: Request, number: str = "", token: str = "", db: Session = Depends(get_db)):
    """Отслеживание по номеру + токену из ссылки."""
    if number and token:
        ticket = db.scalar(
            select(Ticket).where(
                Ticket.public_number == number.strip(),
                Ticket.tracking_token_hash == hash_token(token.strip()),
            )
        )
        if ticket and ticket.tracking_is_active:
            return _render_track(request, db, ticket, token.strip())
        return templates.TemplateResponse(
            request=request,
            name="track_missing.html",
            context={"error": "Заявка с таким номером и кодом не найдена."},
            status_code=404,
        )

    return templates.TemplateResponse(request=request, name="track_form.html", context={})


def _render_track(request: Request, db: Session, ticket: Ticket, token: str):
    comments = db.scalars(
        select(Comment)
        .where(Comment.ticket_id == ticket.id, Comment.visibility == "public")
        .order_by(Comment.created_at)
    ).all()
    history = db.scalars(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id).order_by(TicketHistory.created_at)
    ).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.ticket_id == ticket.id).order_by(Attachment.created_at)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="track.html",
        context={
            "ticket": ticket,
            "comments": comments,
            "history": history,
            "attachments": attachments,
            "token": token,
        },
    )


@router.get("/qr/{qr_key}.png")
def qr_png(request: Request, qr_key: str, db: Session = Depends(get_db)):
    """Генерирует PNG с QR-кодом, ведущим на форму с этим помещением."""
    location = db.scalar(select(Location).where(Location.qr_key == qr_key))
    if location is None:
        raise HTTPException(404, "Помещение не найдено")

    url = qr_target_url(request, qr_key)
    image = qrcode.make(url)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")
