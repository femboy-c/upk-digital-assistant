# -*- coding: utf-8 -*-
"""Ограничение частоты запросов.

По умолчанию счётчики хранятся в памяти процесса. Если задан `REDIS_URL`,
используется Redis — это нужно для нескольких workers. При недоступном Redis
приложение не падает, а возвращается к памяти процесса.
"""
import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict

from fastapi import Request

from .config import TRUST_PROXY

logger = logging.getLogger(__name__)

# Ключ -> моменты запросов (time.monotonic) для локального режима.
_events: dict[str, list[float]] = defaultdict(list)

REDIS_URL = os.getenv("REDIS_URL", "").strip()
_redis_client = None
_redis_checked = False


def rate_limit_enabled() -> bool:
    """Ограничение можно выключить только в автотестах."""
    return os.getenv("UPK_DISABLE_RATE_LIMIT", "0").lower() not in ("1", "true", "yes")


def _valid_ip(value: str) -> str | None:
    """Возвращает нормализованный IP или None, если заголовок подделан."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """IP клиента. Заголовки прокси принимаем только при TRUST_PROXY=1.

    За Cloudflare сначала смотрим CF-Connecting-IP: этот заголовок edge
    перезаписывает сам. X-Forwarded-For может содержать подставленное
    клиентом значение первым, поэтому он только запасной вариант.
    """
    if TRUST_PROXY:
        cloudflare_ip = _valid_ip(request.headers.get("cf-connecting-ip", ""))
        if cloudflare_ip:
            return cloudflare_ip

        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            valid = _valid_ip(first)
            if valid:
                return valid

    return request.client.host if request.client else "unknown"


def _redis():
    """Ленивое подключение к Redis. При ошибке работаем в памяти."""
    global _redis_client, _redis_checked
    if not REDIS_URL:
        return None
    if _redis_checked:
        return _redis_client

    _redis_checked = True
    try:
        import redis

        client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
    except Exception as error:
        logger.warning(
            "Redis недоступен, лимиты останутся в памяти: %s",
            type(error).__name__,
        )
        _redis_client = None
    return _redis_client


def _redis_key(key: str) -> str:
    return f"upk:rl:{key}"


def _is_limited_backend(key: str, limit: int, window_seconds: int) -> bool:
    client = _redis()
    if client is not None:
        try:
            now = time.time()
            redis_key = _redis_key(key)
            pipe = client.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
            pipe.zcard(redis_key)
            _, count = pipe.execute()
            return int(count) >= limit
        except Exception as error:
            logger.warning("Redis rate limit failed: %s", type(error).__name__)

    now = time.monotonic()
    recent = [moment for moment in _events.get(key, []) if now - moment < window_seconds]
    _events[key] = recent
    return len(recent) >= limit


def _register_event_backend(key: str, window_seconds: int) -> None:
    client = _redis()
    if client is not None:
        try:
            now = time.time()
            redis_key = _redis_key(key)
            member = f"{now}:{secrets.token_hex(4)}"
            pipe = client.pipeline()
            pipe.zadd(redis_key, {member: now})
            pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
            pipe.expire(redis_key, window_seconds + 1)
            pipe.execute()
            return
        except Exception as error:
            logger.warning("Redis rate limit failed: %s", type(error).__name__)

    now = time.monotonic()
    recent = [moment for moment in _events.get(key, []) if now - moment < window_seconds]
    recent.append(now)
    _events[key] = recent

    # При большом числе разных IP/ключей чистим устаревшие записи.
    if len(_events) > 5000:
        cutoff = now - 3600
        for old_key in list(_events):
            fresh = [moment for moment in _events[old_key] if moment >= cutoff]
            if fresh:
                _events[old_key] = fresh
            else:
                _events.pop(old_key, None)


def is_limited(key: str, limit: int, window_seconds: int) -> bool:
    """Превышен ли лимит для ключа за скользящее окно."""
    if not rate_limit_enabled():
        return False
    return _is_limited_backend(key, limit, window_seconds)


def register_event(key: str, window_seconds: int) -> None:
    """Запоминает успешное событие для ограничения частоты."""
    if not rate_limit_enabled():
        return
    _register_event_backend(key, window_seconds)


# Лимит входа проверяется даже при UPK_DISABLE_RATE_LIMIT: это защита
# аутентификации, а не ограничение публичной формы.
def login_is_limited(key: str, limit: int, window_seconds: int) -> bool:
    return _is_limited_backend(f"login:{key}", limit, window_seconds)


def register_login_failure(key: str, window_seconds: int) -> None:
    _register_event_backend(f"login:{key}", window_seconds)


def delete_key(key: str) -> None:
    """Удаляет счётчик (например, после успешного входа)."""
    client = _redis()
    if client is not None:
        try:
            client.delete(_redis_key(key))
        except Exception as error:
            logger.warning("Redis rate limit failed: %s", type(error).__name__)
    _events.pop(key, None)


def reset_login_failures(key: str) -> None:
    delete_key(f"login:{key}")


def reset_rate_limits() -> None:
    """Очищает счётчики. Используется в тестах."""
    _events.clear()
    client = _redis()
    if client is not None:
        try:
            for redis_key in client.scan_iter("upk:rl:*"):
                client.delete(redis_key)
        except Exception as error:
            logger.warning("Redis rate limit cleanup failed: %s", type(error).__name__)
