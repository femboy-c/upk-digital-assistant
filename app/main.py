# -*- coding: utf-8 -*-
"""
Точка входа приложения.

Запуск:  .venv/bin/uvicorn app.main:app --reload
Открыть: http://127.0.0.1:8000
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    DEV_SECRET_KEY,
    IS_PRODUCTION,
    MAX_REQUEST_BODY_BYTES,
    PUBLIC_BASE_URL,
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

SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "0").lower() in ("1", "true", "yes")

# Страницы с закрытыми данными не должны попадать в поисковые системы.
NOINDEX_PREFIXES = ("/staff", "/login", "/track", "/t/", "/success", "/assistant")

# Эти страницы содержат персональные данные и не должны кешироваться.
NO_STORE_PREFIXES = ("/staff", "/login", "/track", "/t/", "/success")

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
    """Базовые защитные заголовки для браузера."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'self'",
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
def robots_txt():
    """Закрытые разделы не индексируются."""
    return (
        "User-agent: *\n"
        "Disallow: /staff\n"
        "Disallow: /login\n"
        "Disallow: /track\n"
        "Disallow: /t/\n"
        "Disallow: /success\n"
        "Disallow: /assistant\n"
    )


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
