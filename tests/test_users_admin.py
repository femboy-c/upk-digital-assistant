# -*- coding: utf-8 -*-
"""Управление пользователями в панели администратора."""
import os
import tempfile
import uuid
from urllib.parse import unquote_plus

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-users-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-users-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Comment, Location, Ticket, User
from app.security import hash_token


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = "admin123") -> None:
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client: TestClient) -> None:
    client.post("/logout", follow_redirects=False)


def unique_login(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def create_user(client: TestClient, *, login_name: str, role: str = "staff", password: str = "password123"):
    return client.post(
        "/staff/users",
        data={
            "name": f"Тестовый {login_name}",
            "login": login_name,
            "role": role,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=False,
    )


def get_user(login_name: str) -> User | None:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.login == login_name))


def test_staff_cannot_open_users_page(client: TestClient):
    logout(client)
    login(client, "staff", "staff123")
    response = client.get("/staff/users")
    assert response.status_code == 403
    assert "Недостаточно прав" in response.text
    logout(client)


def test_admin_creates_user_and_new_user_can_login(client: TestClient):
    login_name = unique_login("created")
    login(client)
    response = create_user(client, login_name=login_name)
    assert response.status_code == 303

    user = get_user(login_name)
    assert user is not None
    assert user.role == "staff"
    assert user.is_active is True

    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "user.create",
                AuditLog.entity_id == user.id,
            )
        )
        assert audit is not None

    logout(client)
    login(client, login_name, "password123")
    assert client.get("/staff").status_code == 200
    logout(client)


def test_duplicate_login_and_weak_password_are_rejected(client: TestClient):
    login_name = unique_login("dup")
    login(client)
    assert create_user(client, login_name=login_name).status_code == 303

    duplicate = create_user(client, login_name=login_name)
    assert duplicate.status_code == 400
    assert "уже занят" in duplicate.text

    weak = create_user(client, login_name=unique_login("weak"), password="short")
    assert weak.status_code == 400
    assert "минимум 8" in weak.text
    logout(client)


def test_admin_deactivates_and_reactivates_user(client: TestClient):
    login_name = unique_login("toggle")
    login(client)
    assert create_user(client, login_name=login_name).status_code == 303
    user = get_user(login_name)
    assert user is not None

    deactivated = client.post(
        f"/staff/users/{user.id}",
        data={"name": user.name, "login": user.login, "role": user.role},
        follow_redirects=False,
    )
    assert deactivated.status_code == 303
    assert get_user(login_name).is_active is False

    logout(client)
    blocked = client.post(
        "/login",
        data={"login": login_name, "password": "password123"},
        follow_redirects=False,
    )
    assert blocked.status_code == 401
    assert "Неверный логин или пароль" in blocked.text

    login(client)
    activated = client.post(
        f"/staff/users/{user.id}",
        data={
            "name": user.name,
            "login": user.login,
            "role": user.role,
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert activated.status_code == 303
    assert get_user(login_name).is_active is True
    logout(client)

    login(client, login_name, "password123")
    assert client.get("/staff").status_code == 200
    logout(client)


def test_admin_resets_password(client: TestClient):
    login_name = unique_login("reset")
    login(client)
    assert create_user(client, login_name=login_name).status_code == 303
    user = get_user(login_name)
    assert user is not None

    changed = client.post(
        f"/staff/users/{user.id}",
        data={
            "name": user.name,
            "login": user.login,
            "role": user.role,
            "is_active": "1",
            "password": "newpassword123",
            "confirm_password": "newpassword123",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    logout(client)

    login(client, login_name, "newpassword123")
    assert client.get("/staff").status_code == 200
    logout(client)


def test_admin_cannot_demote_or_deactivate_self(client: TestClient):
    logout(client)
    login(client)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.login == "admin"))

    demoted = client.post(
        f"/staff/users/{admin.id}",
        data={"name": admin.name, "login": admin.login, "role": "staff", "is_active": "1"},
        follow_redirects=False,
    )
    assert demoted.status_code == 400
    assert "собственную роль" in demoted.text

    disabled = client.post(
        f"/staff/users/{admin.id}",
        data={"name": admin.name, "login": admin.login, "role": "admin"},
        follow_redirects=False,
    )
    assert disabled.status_code == 400
    assert "собственную учётную запись" in disabled.text

    logout(client)
    login(client)
    assert client.get("/staff").status_code == 200
    logout(client)


def test_delete_user_without_links_works(client: TestClient):
    login_name = unique_login("delete")
    login(client)
    assert create_user(client, login_name=login_name).status_code == 303
    user = get_user(login_name)
    assert user is not None

    response = client.post(f"/staff/users/{user.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert get_user(login_name) is None
    logout(client)


def test_delete_user_with_links_is_refused(client: TestClient):
    login_name = unique_login("linked")
    login(client)
    assert create_user(client, login_name=login_name).status_code == 303
    user = get_user(login_name)
    assert user is not None

    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        ticket = Ticket(
            public_number=f"USER-{uuid.uuid4().hex[:10]}",
            title="Связанная заявка",
            description="Проверка запрета удаления пользователя",
            location_id=location.id,
            status="Новая",
            tracking_token_hash=hash_token(uuid.uuid4().hex),
        )
        db.add(ticket)
        db.flush()
        db.add(Comment(ticket_id=ticket.id, author_id=user.id, body="Комментарий"))
        db.commit()

    response = client.post(f"/staff/users/{user.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert "Отключите" in unquote_plus(response.headers["location"])
    assert get_user(login_name) is not None
    logout(client)
