# -*- coding: utf-8 -*-
"""Аккуратное обновление схемы существующей SQLite-базы.

`create_all` создаёт только новые таблицы, но не добавляет колонки в уже
существующие. Поэтому перед стартом приложения мы проверяем схему и при
необходимости выполняем безопасные `ALTER TABLE ... ADD COLUMN`.

Функция идемпотентна: повторный запуск ничего не меняет.
"""
from sqlalchemy import Engine

from . import models  # noqa: F401  (регистрирует таблицы в Base.metadata)
from .database import Base, engine


def _column_names(connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def ensure_schema(target_engine: Engine | None = None) -> None:
    """Создаёт таблицы и добавляет недостающие колонки в старую базу."""
    target = target_engine or engine
    Base.metadata.create_all(target)

    with target.begin() as connection:
        user_columns = _column_names(connection, "users")
        if "is_active" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        if "session_version" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"
            )

        ticket_columns = _column_names(connection, "tickets")
        if "tracking_expires_at" not in ticket_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN tracking_expires_at DATETIME"
            )
        if "tracking_revoked_at" not in ticket_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN tracking_revoked_at DATETIME"
            )
        if "contact_phone" not in ticket_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN contact_phone VARCHAR(40) DEFAULT ''"
            )
        if "contact_email" not in ticket_columns:
            connection.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN contact_email VARCHAR(120) DEFAULT ''"
            )
