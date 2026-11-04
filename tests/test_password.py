# -*- coding: utf-8 -*-
"""Проверка смены пароля сотрудником."""
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-pass-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-pass-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password

OLD_PASSWORD = "old-password-123"
NEW_PASSWORD = "new-password-456"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def make_user() -> str:
    login = f"password-test-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(
            User(
                name="Тест смены пароля",
                login=login,
                role="staff",
                password_hash=hash_password(OLD_PASSWORD),
            )
        )
        db.commit()
    return login


def login(client: TestClient, username: str, password: str):
    return client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )


def test_password_change_flow(client: TestClient):
    username = make_user()
    assert login(client, username, OLD_PASSWORD).status_code == 303

    page = client.get("/staff/password")
    assert page.status_code == 200
    assert "Смена пароля" in page.text

    wrong_current = client.post(
        "/staff/password",
        data={
            "current_password": "wrong-password",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert wrong_current.status_code == 400
    assert "Текущий пароль указан неверно" in wrong_current.text

    mismatch = client.post(
        "/staff/password",
        data={
            "current_password": OLD_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": "another-password",
        },
    )
    assert mismatch.status_code == 400
    assert "не совпадают" in mismatch.text

    too_short = client.post(
        "/staff/password",
        data={
            "current_password": OLD_PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert too_short.status_code == 400
    assert "минимум 8 символов" in too_short.text

    changed = client.post(
        "/staff/password",
        data={
            "current_password": OLD_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/staff/password?saved=1"

    client.post("/logout", follow_redirects=False)
    assert login(client, username, OLD_PASSWORD).status_code == 401
    assert login(client, username, NEW_PASSWORD).status_code == 303
    client.post("/logout", follow_redirects=False)
