# -*- coding: utf-8 -*-
"""
Настройки приложения, которые можно менять без правки кода.

Значения читаются из переменных окружения и файла .env в корне проекта.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env не обязателен: демонстрационный запуск работает и без него.
load_dotenv(BASE_DIR / ".env")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in ("production", "prod")
DEV_SECRET_KEY = "dev-secret-поменяйте-в-проде"

# В колледже три этажа и прилегающая территория.
# Выбирается этаж или территория, отдельного корпуса нет.
LOCATION_GROUPS = (
    ("Этажи", (("1", "1 этаж"), ("2", "2 этаж"), ("3", "3 этаж"))),
    ("Территории", (("yard-front", "Передний двор"), ("yard-back", "Задний двор"))),
)
FLOOR_LABELS = {value: label for _, options in LOCATION_GROUPS for value, label in options}

# Публичный адрес, который зашивается в QR-коды.
# Пример: http://192.168.1.50:8000 или https://upk.example.ru
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Вложения храним вне static/: доступ к ним выдаётся только через проверяемые роуты.
UPLOAD_DIR = Path(os.getenv("UPK_UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Общий лимит хранилища вложений, чтобы диск не переполнился.
MAX_UPLOAD_TOTAL_MB = int(os.getenv("MAX_UPLOAD_TOTAL_MB", "200"))
MAX_UPLOAD_TOTAL_BYTES = MAX_UPLOAD_TOTAL_MB * 1024 * 1024

# Максимальный размер всего HTTP-запроса (форма + фотографии + заголовки).
try:
    MAX_REQUEST_BODY_MB = max(1, int(os.getenv("MAX_REQUEST_BODY_MB", "30")))
except ValueError:
    MAX_REQUEST_BODY_MB = 30
MAX_REQUEST_BODY_BYTES = MAX_REQUEST_BODY_MB * 1024 * 1024

# Защита от изображений-«бомб»: максимум пикселей в одном файле.
MAX_IMAGE_PIXELS = 40_000_000

# Сколько фотографий можно приложить к одной заявке и до какого размера
# сжимать их на сервере. Оригиналы не хранятся: это экономит диск и убирает EXIF.
MAX_PHOTOS_PER_TICKET = 5
MAX_PHOTO_DIMENSION = 1600
PHOTO_JPEG_QUALITY = 82

# Ограничения длины текста. Проверяются и на сервере, и в HTML (maxlength).
MIN_DESCRIPTION_LENGTH = 10
MAX_DESCRIPTION_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000
MAX_NOTE_LENGTH = 500

# Сколько заявок показывать на одной странице панели колледжа.
STAFF_PAGE_SIZE = 25

# Ограничение частоты запросов (в памяти процесса).
TICKETS_RATE_LIMIT = 5
TICKETS_RATE_WINDOW_SECONDS = 10 * 60
ASSISTANT_RATE_LIMIT = 30
ASSISTANT_RATE_WINDOW_SECONDS = 60

# Доверять X-Forwarded-For можно только за reverse proxy (Caddy/nginx).
# При прямом доступе клиент может подделать этот заголовок.
TRUST_PROXY = os.getenv("TRUST_PROXY", "0").strip().lower() in ("1", "true", "yes")

# Сколько дней действует ссылка отслеживания. 0 = бессрочно.
try:
    TRACKING_LINK_TTL_DAYS = max(0, int(os.getenv("TRACKING_LINK_TTL_DAYS", "90")))
except ValueError:
    TRACKING_LINK_TTL_DAYS = 90

# Политика обработки персональных данных.
PRIVACY_POLICY_VERSION = os.getenv("PRIVACY_POLICY_VERSION", "2026-11-05").strip()
PRIVACY_CONTACT_EMAIL = os.getenv("PRIVACY_CONTACT_EMAIL", "").strip()

# Сколько дней хранить закрытые заявки и контакты заявителей.
try:
    TICKET_RETENTION_DAYS = max(1, int(os.getenv("TICKET_RETENTION_DAYS", "365")))
except ValueError:
    TICKET_RETENTION_DAYS = 365


def tracking_expiry(now: datetime | None = None) -> datetime | None:
    """Дата окончания действия ссылки отслеживания для новой заявки."""
    if TRACKING_LINK_TTL_DAYS <= 0:
        return None
    return (now or datetime.now()) + timedelta(days=TRACKING_LINK_TTL_DAYS)


def public_base_url(request) -> str:
    """Возвращает адрес сайта без завершающего слэша.

    Если PUBLIC_BASE_URL задан — используем его (так QR откроется со смартфона).
    Иначе откатываемся к адресу текущего запроса (удобно для локальной проверки).
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def qr_target_url(request, qr_key: str) -> str:
    """Ссылка, которую содержит QR-код помещения."""
    return f"{public_base_url(request)}/?qr={quote(qr_key)}"


def is_public_base_configured() -> bool:
    """Задан ли публичный адрес явно (нужно для предупреждения в панели)."""
    return bool(PUBLIC_BASE_URL)
