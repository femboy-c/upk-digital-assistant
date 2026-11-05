# -*- coding: utf-8 -*-
"""
Точка входа приложения.

Запуск:  .venv/bin/uvicorn app.main:app --reload
Открыть: http://127.0.0.1:8000
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    DEV_SECRET_KEY,
    IS_PRODUCTION,
    MAX_REQUEST_BODY_BYTES,
    PUBLIC_BASE_URL,
    public_base_url,
)
from .database import BASE_DIR, SessionLocal, restrict_data_permissions
from .log_redaction import install_log_redaction
from .migrations import ensure_schema
from .request_limits import RequestBodyLimitMiddleware
from .routes import assistant, audit, export, files, locations, public, staff, stats, users
from .seed import seed_database
from .templating import templates

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET_KEY)
if IS_PRODUCTION and (not SECRET_KEY or SECRET_KEY == DEV_SECRET_KEY):
    raise RuntimeError(
        "Для APP_ENV=production задайте собственный SECRET_KEY в .env. "
        "Демонстрационный ключ использовать нельзя."
    )
if IS_PRODUCTION and len(SECRET_KEY) < 32:
    raise RuntimeError(
        "Для APP_ENV=production SECRET_KEY должен быть не короче 32 символов."
    )

SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "0").lower() in ("1", "true", "yes")
if IS_PRODUCTION and not SESSION_HTTPS_ONLY:
    raise RuntimeError(
        "Для APP_ENV=production включите SESSION_HTTPS_ONLY=1: "
        "cookie сессии должна передаваться только по HTTPS."
    )
if IS_PRODUCTION and PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith("https://"):
    raise RuntimeError(
        "Для APP_ENV=production PUBLIC_BASE_URL должен начинаться с https://."
    )

# Страницы с закрытыми данными не должны попадать в поисковые системы.
NOINDEX_PREFIXES = (
    "/staff",
    "/login",
    "/track",
    "/my",
    "/t/",
    "/success",
    "/assistant",
)

# Эти страницы содержат персональные данные и не должны кешироваться.
NO_STORE_PREFIXES = ("/staff", "/login", "/track", "/my", "/t/", "/success")

install_log_redaction()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """При старте: обновляем схему и наполняем базу демо-данными."""
    ensure_schema()
    restrict_data_permissions()
    with SessionLocal() as db:
        seed_database(db)
    yield


# В production служебная документация API не нужна и только расширяет
# поверхность атаки. В development она остаётся удобной для проверки.
app = FastAPI(
    title="Колледж ДГУ: Цифровой помощник",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# Секрет для подписи cookie-сессии. В настоящем проекте — только из переменной окружения.
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Базовые защитные заголовки для браузера.

    Для inline-скриптов выдаётся одноразовый nonce. Без него CSP не разрешает
    выполнение сторонних и внедрённых скриптов.
    """
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "frame-src 'none'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "worker-src 'none'; "
        "manifest-src 'self'",
    )
    if SESSION_HTTPS_ONLY or PUBLIC_BASE_URL.startswith("https://"):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    if request.url.path.startswith(NOINDEX_PREFIXES):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if request.url.path.startswith(NO_STORE_PREFIXES):
        response.headers.setdefault("Cache-Control", "no-store, private")
        response.headers.setdefault("Pragma", "no-cache")
    return response


# Статические файлы: только стили и оформление. Вложения здесь не хранятся.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(public.router)
app.include_router(staff.router)
app.include_router(locations.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(assistant.router)
app.include_router(stats.router)
app.include_router(export.router)
app.include_router(files.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Ошибки HTTP: JSON для API, понятная HTML-страница для браузера."""
    accept = request.headers.get("accept", "")
    if request.url.path.startswith("/assistant") or "application/json" in accept:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    if exc.status_code == 404:
        title = "Страница не найдена"
        message = "Такой страницы нет. Проверьте адрес или вернитесь на главную."
    elif exc.status_code == 403:
        title = "Недостаточно прав"
        message = str(exc.detail)
    elif exc.status_code == 400:
        title = "Некорректный запрос"
        message = str(exc.detail)
    else:
        title = "Ошибка запроса"
        message = str(exc.detail)

    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"title": title, "message": message},
        status_code=exc.status_code,
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request):
    """Закрытые разделы не индексируются, публичные — открыты."""
    base = public_base_url(request).rstrip("/")
    lines = [
        "User-agent: *",
        "Disallow: /staff",
        "Disallow: /login",
        "Disallow: /track",
        "Disallow: /t/",
        "Disallow: /success",
        "Disallow: /assistant",
        "Allow: /",
    ]
    if base:
        lines.append(f"Sitemap: {base}/sitemap.xml")
    return "\n".join(lines) + "\n"


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request):
    """Карта публичных страниц для Яндекса и Google."""
    from xml.sax.saxutils import escape

    base = public_base_url(request).rstrip("/")
    pages = (
        ("", "1.0", "daily"),
        ("/faq", "0.8", "weekly"),
        ("/privacy", "0.3", "yearly"),
    )
    items = "".join(
        f"<url><loc>{escape(base + (path or '/'))}</loc>"
        f"<changefreq>{frequency}</changefreq>"
        f"<priority>{priority}</priority></url>"
        for path, priority, frequency in pages
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{items}</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@app.exception_handler(Exception)
async def internal_server_error(request: Request, exc: Exception):
    """Ошибка сервера показывается как ошибка, а не как успешная отправка."""
    logger.exception("Необработанная ошибка", exc_info=exc)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "title": "Ошибка сервера",
            "message": "На сервере произошла ошибка. Данные не были сохранены. "
            "Попробуйте ещё раз или обратитесь к администратору.",
        },
        status_code=500,
    )


@app.get("/health")
def health():
    """Проверка, что сервер жив. Используется при запуске и в мониторинге."""
    return {"status": "ok"}
