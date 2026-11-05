# -*- coding: utf-8 -*-
"""Баннер плановых работ: показ на сайте и права администратора."""
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-banner-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-banner-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Banner


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


def test_admin_shows_and_hides_banner(client: TestClient):
    message = f"Плановые работы {uuid.uuid4().hex[:6]}"
    logout(client)
    login(client)

    response = client.post(
        "/staff/banner",
        data={"message": message, "is_active": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]

    assert message in client.get("/").text
    assert message in client.get("/faq").text

    with SessionLocal() as db:
        banner = db.scalar(select(Banner).order_by(Banner.id))
        assert banner is not None and banner.is_active is True
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "banner.update")
        )
        assert audit is not None

    response = client.post(
        "/staff/banner",
        data={"message": message, "is_active": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert message not in client.get("/").text
    logout(client)


def test_empty_active_banner_is_rejected(client: TestClient):
    logout(client)
    login(client)
    response = client.post(
        "/staff/banner",
        data={"message": "  ", "is_active": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    logout(client)


def test_banner_is_admin_only(client: TestClient):
    logout(client)
    login(client, "staff", "staff123")
    assert client.get("/staff/banner").status_code == 403
    logout(client)
