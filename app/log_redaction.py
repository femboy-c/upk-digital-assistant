# -*- coding: utf-8 -*-
"""Не даём секретным токенам попадать в access-log uvicorn."""
import logging

# Пути, где токен отслеживания передаётся в URL.
SENSITIVE_PREFIXES = ("/t/", "/track", "/success")


def redact_path(path: str) -> str:
    """Скрывает токен и query-параметры в опасных путях."""
    if path.startswith("/t/"):
        return "/t/***"
    if path.startswith("/track"):
        return "/track?***" if "?" in path else path
    if path.startswith("/success"):
        return "/success?***" if "?" in path else path
    return path


class SensitivePathFilter(logging.Filter):
    """Фильтр для логгера uvicorn.access."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access" or not record.args:
            return True
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str):
                redacted = redact_path(path)
                if redacted != path:
                    record.args = args[:2] + (redacted,) + args[3:]
        return True


def install_log_redaction() -> None:
    """Добавляет фильтр один раз (повторный импорт не дублирует его)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SensitivePathFilter) for item in logger.filters):
        logger.addFilter(SensitivePathFilter())
