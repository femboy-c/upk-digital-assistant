# -*- coding: utf-8 -*-
"""
Публичные страницы: форма заявки, подтверждение, отслеживание, QR-коды.

Здесь не нужен вход — сюда попадает любой студент или преподаватель.
"""
import io
import os
import re
import uuid
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import (
    LOCATION_GROUPS,
    MAX_DESCRIPTION_LENGTH,
    MAX_IMAGE_PIXELS,
    MAX_PHOTOS_PER_TICKET,
    MAX_PHOTO_DIMENSION,
    MAX_UPLOAD_TOTAL_BYTES,
    MIN_DESCRIPTION_LENGTH,
    PHOTO_JPEG_QUALITY,
    PRIVACY_CONTACT_EMAIL,
    PRIVACY_POLICY_VERSION,
    TICKETS_RATE_LIMIT,
    TICKET_RETENTION_DAYS,
    TICKETS_RATE_WINDOW_SECONDS,
    UPLOAD_DIR,
    public_base_url,
    qr_target_url,
    tracking_expiry,
)
from ..database import get_db
from ..models import (
    Attachment,
    Category,
    Comment,
    KnowledgeBase,
    Location,
    Ticket,
    TicketHistory,
)
from ..parsing import as_int
from ..ratelimit import client_ip, is_limited, register_event
from ..security import (
    consent_required,
    hash_token,
    new_tracking_token,
    require_csrf,
    verify_csrf,
)
from ..templating import templates
from ..workflow import STATUS_STEPS, status_step

router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 МБ на один файл
UPLOAD_CHUNK_SIZE = 64 * 1024
PHOTO_CONTENT_TYPE = "image/jpeg"
PHOTO_SUFFIX = ".jpg"

# Личный кабинет заявителя: список токенов живёт в подписанной cookie-сессии.
# Это удобство для текущего браузера, а не замена секретной ссылке.
MY_TICKETS_KEY = "my_tickets"
MAX_MY_TICKETS = 20
PHONE_RE = re.compile(r"^[0-9+()\-\s]{5,40}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Pillow не должен пытаться декодировать изображения-«бомбы».
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _upload_dir_size() -> int:
    """Суммарный размер всех вложений на диске."""
    return sum(path.stat().st_size for path in UPLOAD_DIR.glob("*") if path.is_file())


def _clean_contacts(phone: str, email: str) -> tuple[str, str, str]:
    """Проверяет необязательные контакты заявителя."""
    phone = (phone or "").strip()
    email = (email or "").strip()
    if phone and not PHONE_RE.fullmatch(phone):
        return phone, email, (
            "Телефон указан в неверном формате. Используйте цифры, пробелы, "
            "скобки, дефис и знак «+»."
        )
    if email and (len(email) > 120 or not EMAIL_RE.fullmatch(email)):
        return phone, email, "Email указан в неверном формате."
    return phone, email, ""


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


class PhotoTooLargeError(Exception):
    """Изображение превышает допустимое число пикселей."""


def _prepare_photo(raw: bytes) -> bytes:
    """Проверяет изображение, поворачивает по EXIF и сжимает в JPEG.

    Оригинал не сохраняется: это уменьшает диск, убирает метаданные и
    приводит все фотографии к предсказуемому формату.
    """
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise PhotoTooLargeError
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_PHOTO_DIMENSION, MAX_PHOTO_DIMENSION), Image.LANCZOS)

            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=PHOTO_JPEG_QUALITY,
                optimize=True,
            )
            return output.getvalue()
    except PhotoTooLargeError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("invalid image") from error


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
    contact_phone: str = "",
    contact_email: str = "",
    consent_checked: bool = False,
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
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "consent_checked": consent_checked,
        "selected_location_id": selected_location_id,
        "selected_category_id": selected_category_id,
        "max_description_length": MAX_DESCRIPTION_LENGTH,
        "min_description_length": MIN_DESCRIPTION_LENGTH,
        "max_photos_per_ticket": MAX_PHOTOS_PER_TICKET,
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
    contact_phone: str = "",
    contact_email: str = "",
    consent_checked: bool = False,
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
            contact_phone=contact_phone,
            contact_email=contact_email,
            consent_checked=consent_checked,
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
    contact_phone: str = Form(""),
    contact_email: str = Form(""),
    personal_data_consent: str = Form(""),
    csrf_token: str = Form(""),
    website: str = Form(""),
    photos: list[UploadFile] | None = File(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Сохраняет заявку в базу и показывает номер + ссылку отслеживания."""
    description = description.strip()
    consent_checked = personal_data_consent.strip().lower() in ("1", "on", "true", "yes")
    contact_phone, contact_email, contact_error = _clean_contacts(
        contact_phone, contact_email
    )
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
            contact_phone=contact_phone,
            contact_email=contact_email,
            consent_checked=consent_checked,
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
    if consent_required() and not consent_checked:
        return form_error(
            "Чтобы отправить заявку, нужно согласие на обработку персональных данных."
        )

    if len(description) < MIN_DESCRIPTION_LENGTH:
        return form_error(
            f"Опишите проблему подробнее — минимум {MIN_DESCRIPTION_LENGTH} символов."
        )

    if len(description) > MAX_DESCRIPTION_LENGTH:
        return form_error(
            f"Описание слишком длинное — максимум {MAX_DESCRIPTION_LENGTH} символов."
        )

    if contact_error:
        return form_error(contact_error)

    if location is None:
        return form_error("Выберите этаж или территорию и помещение, где произошла проблема.")

    if category_id and category is None:
        return form_error("Выбрана неизвестная категория. Выберите категорию из списка.")

    # 5. Фотографии проверяем и сжимаем до создания заявки, чтобы не писать
    # в базу зря. Старое поле photo поддерживаем для закешированных форм.
    uploads = [item for item in (photos or []) if item is not None and item.filename]
    if photo is not None and photo.filename:
        uploads.append(photo)
    if len(uploads) > MAX_PHOTOS_PER_TICKET:
        return form_error(
            f"Можно прикрепить не больше {MAX_PHOTOS_PER_TICKET} фотографий."
        )

    prepared_photos: list[bytes] = []
    total_photo_size = 0
    for upload in uploads:
        if upload.content_type not in ALLOWED_TYPES:
            return form_error("Можно прикреплять только JPG, PNG или WEBP.")
        raw = await _read_upload_limited(upload, MAX_FILE_SIZE)
        if raw is None:
            return form_error("Каждый файл должен быть не больше 5 МБ.")
        try:
            prepared = _prepare_photo(raw)
        except PhotoTooLargeError:
            return form_error(
                "Одна из фотографий слишком большая по размеру в пикселях. "
                "Уменьшите её и попробуйте снова."
            )
        except ValueError:
            return form_error(
                "Один из файлов не похож на изображение. Приложите JPG, PNG или WEBP."
            )
        prepared_photos.append(prepared)
        total_photo_size += len(prepared)

    if _upload_dir_size() + total_photo_size > MAX_UPLOAD_TOTAL_BYTES:
        return form_error(
            "Хранилище вложений заполнено. Отправьте заявку без фотографий "
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
        contact_phone=contact_phone,
        contact_email=contact_email,
        consent_at=datetime.now() if consent_checked else None,
        consent_version=PRIVACY_POLICY_VERSION if consent_checked else "",
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

    # 7. Сохраняем сжатые фотографии вне static/ и добавляем вложения.
    saved_filenames: list[str] = []
    for prepared in prepared_photos:
        saved_filename = f"{uuid.uuid4().hex}{PHOTO_SUFFIX}"
        try:
            saved_path = UPLOAD_DIR / saved_filename
            saved_path.write_bytes(prepared)
            try:
                os.chmod(saved_path, 0o600)
            except OSError:
                pass
        except OSError:
            db.rollback()
            for name in saved_filenames:
                (UPLOAD_DIR / name).unlink(missing_ok=True)
            return _server_error(
                request,
                "Не удалось отправить заявку",
                "Не удалось сохранить фотографии. Заявка не отправлена — попробуйте ещё раз.",
            )
        saved_filenames.append(saved_filename)
        db.add(
            Attachment(
                ticket_id=ticket.id,
                storage_key=saved_filename,
                file_type=PHOTO_CONTENT_TYPE,
            )
        )

    db.add(TicketHistory(ticket_id=ticket.id, old_status="", new_status="Новая", note="Заявка создана"))

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        for name in saved_filenames:
            (UPLOAD_DIR / name).unlink(missing_ok=True)
        return _server_error(
            request,
            "Не удалось отправить заявку",
            "На сервере произошла ошибка. Заявка не сохранена — попробуйте ещё раз.",
        )

    register_event(rate_key, TICKETS_RATE_WINDOW_SECONDS)
    _remember_ticket(request, token)
    return RedirectResponse(f"/success?number={number}&token={token}", status_code=303)


@router.get("/success", response_class=HTMLResponse)
def success(request: Request, number: str = "", token: str = ""):
    """Страница после отправки: номер заявки и ссылка отслеживания."""
    _remember_ticket(request, token)
    return templates.TemplateResponse(
        request=request,
        name="success.html",
        context={
            "number": number,
            "token": token,
            "base_url": public_base_url(request),
        },
    )


def _ticket_by_token(db: Session, token: str) -> Ticket | None:
    """Находит заявку по ссылке и проверяет срок её действия/отзыв."""
    if not token:
        return None
    ticket = db.scalar(select(Ticket).where(Ticket.tracking_token_hash == hash_token(token)))
    if ticket is None or not ticket.tracking_is_active:
        return None
    return ticket


def _session_tokens(request: Request) -> list[str]:
    """Аккуратно достаёт список токенов из сессии."""
    stored = request.session.get(MY_TICKETS_KEY, [])
    if not isinstance(stored, list):
        return []
    return [token for token in stored if isinstance(token, str) and token]


def _remember_ticket(request: Request, token: str) -> None:
    """Добавляет токен в личный список текущего браузера."""
    if not token:
        return
    tokens = [item for item in _session_tokens(request) if item != token]
    tokens.insert(0, token)
    request.session[MY_TICKETS_KEY] = tokens[:MAX_MY_TICKETS]


def _forget_ticket(request: Request, token: str) -> None:
    request.session[MY_TICKETS_KEY] = [
        item for item in _session_tokens(request) if item != token
    ]


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
    _remember_ticket(request, token)
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
            _remember_ticket(request, token.strip())
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
    merged_into = (
        db.get(Ticket, ticket.merged_into_id) if ticket.merged_into_id else None
    )
    return templates.TemplateResponse(
        request=request,
        name="track.html",
        context={
            "ticket": ticket,
            "comments": comments,
            "history": history,
            "attachments": attachments,
            "token": token,
            "status_steps": STATUS_STEPS,
            "status_step": status_step(ticket.status),
            "base_url": public_base_url(request),
            "merged_into": merged_into,
        },
    )


@router.get("/my", response_class=HTMLResponse)
def my_tickets(request: Request, db: Session = Depends(get_db)):
    """Список заявок, сохранённых в этом браузере."""
    tokens = _session_tokens(request)
    items: list[tuple[Ticket, str]] = []
    valid_tokens: list[str] = []

    for token in tokens:
        ticket = _ticket_by_token(db, token)
        if ticket is None:
            continue
        items.append((ticket, token))
        valid_tokens.append(token)

    if valid_tokens != tokens:
        request.session[MY_TICKETS_KEY] = valid_tokens

    items.sort(key=lambda item: item[0].created_at, reverse=True)
    return templates.TemplateResponse(
        request=request,
        name="my_tickets.html",
        context={"items": items},
    )


@router.post("/my/remove")
def remove_my_ticket(
    request: Request,
    token: str = Form(""),
    csrf_token: str = Form(""),
):
    require_csrf(request, csrf_token)
    _forget_ticket(request, token.strip())
    return RedirectResponse("/my", status_code=303)


@router.post("/my/clear")
def clear_my_tickets(
    request: Request,
    csrf_token: str = Form(""),
):
    require_csrf(request, csrf_token)
    request.session.pop(MY_TICKETS_KEY, None)
    return RedirectResponse("/my", status_code=303)


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    """Политика обработки персональных данных."""
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context={
            "policy_version": PRIVACY_POLICY_VERSION,
            "retention_days": TICKET_RETENTION_DAYS,
            "privacy_contact_email": PRIVACY_CONTACT_EMAIL,
        },
    )


@router.get("/faq", response_class=HTMLResponse)
def faq(request: Request, db: Session = Depends(get_db)):
    """Публичная страница частых вопросов из базы знаний помощника."""
    entries = db.scalars(
        select(KnowledgeBase).order_by(KnowledgeBase.category, KnowledgeBase.question)
    ).all()
    groups: dict[str, list[KnowledgeBase]] = {}
    for entry in entries:
        groups.setdefault(entry.category or "Прочее", []).append(entry)
    return templates.TemplateResponse(
        request=request,
        name="faq.html",
        context={"groups": groups},
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
