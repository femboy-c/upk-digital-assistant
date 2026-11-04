# -*- coding: utf-8 -*-
"""Идемпотентное обновление схемы существующей SQLite-базы."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-migr-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-migr-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

from sqlalchemy import create_engine

from app.migrations import ensure_schema


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_old_database_gets_new_columns_without_losing_data():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, name VARCHAR(120), login VARCHAR(60), "
            "role VARCHAR(20), password_hash VARCHAR(255))"
        )
        connection.exec_driver_sql(
            "INSERT INTO users (name, login, role, password_hash) "
            "VALUES ('Старый сотрудник', 'olduser', 'staff', 'hash')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE tickets ("
            "id INTEGER PRIMARY KEY, public_number VARCHAR(30), title VARCHAR(200), "
            "description TEXT, category_id INTEGER, location_id INTEGER, "
            "status VARCHAR(40), priority VARCHAR(20), assignee_id INTEGER, "
            "tracking_token_hash VARCHAR(64), created_at DATETIME, updated_at DATETIME)"
        )

    ensure_schema(engine)

    assert "is_active" in _columns(engine, "users")
    assert "session_version" in _columns(engine, "users")
    assert {
        "tracking_expires_at",
        "tracking_revoked_at",
    } <= _columns(engine, "tickets")

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "audit_log" in tables

    with engine.connect() as connection:
        active, version = connection.exec_driver_sql(
            "SELECT is_active, session_version FROM users WHERE login = 'olduser'"
        ).one()
    assert active == 1
    assert version == 0

    # Повторный запуск не должен падать и что-то дублировать.
    ensure_schema(engine)
    assert "is_active" in _columns(engine, "users")
