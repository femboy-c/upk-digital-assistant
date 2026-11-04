# -*- coding: utf-8 -*-
"""Простое ограничение частоты запросов в памяти одного процесса.

Для одного процесса uvicorn этого достаточно. Если запускать несколько
воркеров, счётчики нужно вынести в Redis или аналогичное хранилище.
"""
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


def client_ip(request: Request) -> str:
    """IP клиента. X-Forwarded-For принимаем только при TRUST_PROXY=1."""
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return first
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


def reset_rate_limits() -> None:
    """Очищает счётчики. Используется в тестах."""
    _events.clear()
