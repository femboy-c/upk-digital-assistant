# -*- coding: utf-8 -*-
"""Двухфакторный вход по TOTP (RFC 6238), без внешних зависимостей.

Секрет — base32 без padding. Код — 6 цифр, шаг 30 секунд, SHA-1.
Совместимо с Google Authenticator, Aegis, 1Password и другими приложениями.
"""
import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

TOTP_STEP = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # принимаем соседние 30-секундные окна


def new_totp_secret() -> str:
    """Случайный секрет для приложения-аутентификатора."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = (secret or "").strip().upper().replace(" ", "")
    normalized += "=" * (-len(normalized) % 8)
    return base64.b32decode(normalized, casefold=True)


def _code_for_counter(secret: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    digest = hmac.new(
        _decode_secret(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def totp_code(secret: str, timestamp: float | None = None) -> str:
    """Код для текущего (или заданного) момента."""
    moment = time.time() if timestamp is None else timestamp
    return _code_for_counter(secret, int(moment // TOTP_STEP))


def verify_totp(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    window: int = TOTP_WINDOW,
) -> int | None:
    """Проверяет код и возвращает номер окна или None.

    Номер окна нужен, чтобы запретить повторное использование того же кода.
    """
    digits = "".join(char for char in (code or "") if char.isdigit())
    if len(digits) != TOTP_DIGITS:
        return None

    moment = time.time() if timestamp is None else timestamp
    current = int(moment // TOTP_STEP)
    for offset in range(-window, window + 1):
        counter = current + offset
        try:
            candidate = _code_for_counter(secret, counter)
        except (ValueError, TypeError, binascii.Error):
            return None
        if hmac.compare_digest(candidate, digits):
            return counter
    return None


def provisioning_uri(secret: str, login: str, issuer: str = "Колледж ДГУ") -> str:
    """otpauth:// ссылка для QR-кода."""
    label = quote(f"{issuer}:{login}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer, safe='')}"
        "&algorithm=SHA1&digits=6&period=30"
    )
