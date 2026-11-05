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

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .ratelimit import (
    client_ip,
    login_is_limited,
    register_login_failure as record_login_failure,
    reset_login_failures as clear_login_failures,
)

# Сколько раз "перемешиваем" пароль. Чем больше — тем медленнее подобрать.
# 600 000 — актуальная рекомендация OWASP для PBKDF2-HMAC-SHA256.
PBKDF2_ROUNDS = 600_000
# Старые хеши без версии использовали 200 000 итераций. Их продолжаем
# принимать и автоматически пересчитываем при следующем входе.
LEGACY_PBKDF2_ROUNDS = 200_000
HASH_PREFIX = "pbkdf2_sha256"

CSRF_SESSION_KEY = "_csrf_token"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 10 * 60





def hash_password(password: str) -> str:
    """Превращает пароль в необратимый хеш с версией и числом итераций."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"{HASH_PREFIX}${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Проверяет пароль и поддерживает старый формат хеша."""
    try:
        parts = stored.split("$")
        if len(parts) == 4 and parts[0] == HASH_PREFIX:
            rounds = int(parts[1])
            # Защита от подмены числа итераций в базе на огромное значение.
            if not 1000 <= rounds <= 2_000_000:
                return False
            salt, expected = parts[2], parts[3]
        elif len(parts) == 2:
            rounds = LEGACY_PBKDF2_ROUNDS
            salt, expected = parts
        else:
            return False
    except (ValueError, TypeError):
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return hmac.compare_digest(digest.hex(), expected)


def needs_rehash(stored: str) -> bool:
    """Нужно ли пересчитать старый или ослабленный хеш."""
    return not stored.startswith(f"{HASH_PREFIX}${PBKDF2_ROUNDS}$")


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


# Хеш-заглушка: проверяется даже для несуществующего логина, чтобы время
# ответа не выдавало, зарегистрирован пользователь или нет.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def _login_key(request: Request, login: str) -> str:
    # За Cloudflare реальный IP приходит в X-Forwarded-For. Если брать
    # request.client.host, все пользователи получат один общий лимит.
    return f"{client_ip(request)}:{login.strip().lower()}"


def login_is_blocked(request: Request, login: str) -> bool:
    """Слишком много неудачных попыток за короткое время?"""
    return login_is_limited(
        _login_key(request, login),
        LOGIN_MAX_ATTEMPTS,
        LOGIN_WINDOW_SECONDS,
    )


def register_login_failure(request: Request, login: str) -> None:
    """Запоминает неудачную попытку входа."""
    record_login_failure(_login_key(request, login), LOGIN_WINDOW_SECONDS)


def reset_login_failures(request: Request, login: str) -> None:
    """Сбрасывает счётчик после успешного входа."""
    clear_login_failures(_login_key(request, login))
