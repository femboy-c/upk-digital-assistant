# -*- coding: utf-8 -*-
"""
Пароли, токены и проверка "кто сейчас вошёл".

Пароли НИКОГДА не храним в открытом виде — только хеш (PBKDF2 из стандартной
библиотеки Python, поэтому ничего дополнительно ставить не нужно).
"""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

# Сколько раз "перемешиваем" пароль. Чем больше — тем медленнее подобрать.
PBKDF2_ROUNDS = 200_000

CSRF_SESSION_KEY = "_csrf_token"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 10 * 60

# Небольшой счётчик неудачных входов. Для одного процесса uvicorn этого
# достаточно; при нескольких процессах нужен общий Redis или аналог.
_login_failures: dict[str, list[float]] = defaultdict(list)



def hash_password(password: str) -> str:
    """Превращает пароль в необратимый хеш вида 'соль$хеш'."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Проверяет, подходит ли введённый пароль к сохранённому хешу."""
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return hmac.compare_digest(digest.hex(), expected)


def new_tracking_token() -> str:
    """Случайный токен для ссылки отслеживания (его нельзя угадать)."""
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    """В базе храним только хеш токена."""
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: Session) -> User | None:
    """Достаёт пользователя из cookie-сессии (если он вошёл и не отключён)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    # Смена пароля увеличивает session_version и завершает старые сессии.
    if request.session.get("user_version") != user.session_version:
        return None
    return user


def require_staff(request: Request, db: Session = Depends(get_db)) -> User:
    """Защита панели: без входа — ошибка 401 (или редирект делает роут)."""
    user = current_user(request, db)
    if not user or user.role not in ("staff", "admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход сотрудника")
    return user


def csrf_protection_enabled() -> bool:
    """CSRF можно отключить только для автотестов через переменную окружения."""
    return os.getenv("UPK_DISABLE_CSRF", "0").lower() not in ("1", "true", "yes")


def consent_required() -> bool:
    """Согласие на обработку ПДн можно отключить только в автотестах."""
    return os.getenv("UPK_DISABLE_CONSENT", "0").lower() not in ("1", "true", "yes")


def get_csrf_token(request: Request) -> str:
    """Возвращает токен формы из сессии, создавая его при необходимости."""
    if not csrf_protection_enabled():
        return "test-csrf-disabled"
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf(request: Request, token: str) -> bool:
    """Проверяет токен формы. Сравнение защищено от подбора по времени."""
    if not csrf_protection_enabled():
        return True
    expected = request.session.get(CSRF_SESSION_KEY)
    return bool(expected and token and hmac.compare_digest(expected, token))


def require_csrf(request: Request, token: str) -> None:
    """Прерывает обработку формы, если токен отсутствует или устарел."""
    if not verify_csrf(request, token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Форма устарела. Обновите страницу и попробуйте снова.",
        )


def _login_key(request: Request, login: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{login.strip().lower()}"


def login_is_blocked(request: Request, login: str) -> bool:
    """Слишком много неудачных попыток за короткое время?"""
    key = _login_key(request, login)
    now = time.monotonic()
    recent = [moment for moment in _login_failures.get(key, []) if now - moment < LOGIN_WINDOW_SECONDS]
    _login_failures[key] = recent
    return len(recent) >= LOGIN_MAX_ATTEMPTS


def register_login_failure(request: Request, login: str) -> None:
    """Запоминает неудачную попытку входа."""
    key = _login_key(request, login)
    _login_failures[key].append(time.monotonic())


def reset_login_failures(request: Request, login: str) -> None:
    """Сбрасывает счётчик после успешного входа."""
    _login_failures.pop(_login_key(request, login), None)
