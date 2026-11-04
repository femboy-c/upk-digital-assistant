# -*- coding: utf-8 -*-
"""Смена пароля должна завершать старые сессии."""
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-session-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-session-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password


def login(client: TestClient, username: str, password: str):
    return client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )


def make_user(password: str) -> User:
    login_name = f"session-{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        user = User(
            name="Тест сессии",
            login=login_name,
            role="staff",
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_admin_password_reset_invalidates_old_session():
    user = make_user("old-password-123")
    with TestClient(app) as old_client, TestClient(app) as admin_client:
        assert login(old_client, user.login, "old-password-123").status_code == 303
        assert old_client.get("/staff").status_code == 200

        assert login(admin_client, "admin", "admin123").status_code == 303
        changed = admin_client.post(
            f"/staff/users/{user.id}",
            data={
                "name": user.name,
                "login": user.login,
                "role": user.role,
                "is_active": "1",
                "password": "new-password-456",
                "confirm_password": "new-password-456",
            },
            follow_redirects=False,
        )
        assert changed.status_code == 303

        # Старая cookie больше не даёт доступ.
        stale = old_client.get("/staff", follow_redirects=False)
        assert stale.status_code == 303
        assert stale.headers["location"] == "/login"

        # Новый пароль работает.
        assert login(old_client, user.login, "new-password-456").status_code == 303
        assert old_client.get("/staff").status_code == 200


def test_own_password_change_keeps_current_session():
    user = make_user("current-password-123")
    with TestClient(app) as client:
        assert login(client, user.login, "current-password-123").status_code == 303

        changed = client.post(
            "/staff/password",
            data={
                "current_password": "current-password-123",
                "new_password": "another-password-456",
                "confirm_password": "another-password-456",
            },
            follow_redirects=False,
        )
        assert changed.status_code == 303

        # Текущая сессия остаётся рабочей, logout не требуется.
        assert client.get("/staff").status_code == 200
        client.post("/logout", follow_redirects=False)
