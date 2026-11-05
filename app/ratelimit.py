# -*- coding: utf-8 -*-
"""Простое ограничение частоты запросов в памяти одного процесса.

Для одного процесса uvicorn этого достаточно. Если запускать несколько
воркеров, счётчики нужно вынести в Redis или аналогичное хранилище.
"""
import ipaddress
import os
import time
from collections import defaultdict

from fastapi import Request

from .config import TRUST_PROXY

# Ключ -> моменты запросов (time.monotonic).
_events: dict[str, list[float]] = defaultdict(list)


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


def is_limited(key: str, limit: int, window_seconds: int) -> bool:
    """Превышен ли лимит для ключа за скользящее окно."""
    if not rate_limit_enabled():
        return False
    now = time.monotonic()
    recent = [moment for moment in _events.get(key, []) if now - moment < window_seconds]
    _events[key] = recent
    return len(recent) >= limit


def register_event(key: str, window_seconds: int) -> None:
    """Запоминает успешное событие для ограничения частоты."""
    if not rate_limit_enabled():
        return
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


def reset_rate_limits() -> None:
    """Очищает счётчики. Используется в тестах."""
    _events.clear()
