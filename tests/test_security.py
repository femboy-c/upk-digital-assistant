# -*- coding: utf-8 -*-
"""Проверка CSRF, ограничения попыток входа и закрытых вложений."""
import io
import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-sec-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-sec-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Attachment, Location, Ticket


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "staff", password: str = "staff123"):
    return client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_security_headers_are_present(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_login_form_without_csrf_is_rejected(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_CSRF", "0")
    response = login(client, "staff", "staff123")
    assert response.status_code == 400
    assert "Форма устарела" in response.text
    monkeypatch.setenv("UPK_DISABLE_CSRF", "1")


def test_login_attempts_are_limited(client: TestClient):
    username = f"nobody-{uuid.uuid4().hex[:8]}"
    for _ in range(5):
        response = login(client, username, "wrong-password")
        assert response.status_code == 401
    blocked = login(client, username, "wrong-password")
    assert blocked.status_code == 429
    assert "Слишком много" in blocked.text


def test_attachment_is_closed_and_available_by_rules(client: TestClient):
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        location_id = location.id

    response = client.post(
        "/tickets",
        data={
            "description": "Проверка закрытого вложения с фотографией",
            "location_id": str(location_id),
            "category_id": "",
        },
        files={"photo": ("photo.png", png_bytes(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    query = parse_qs(urlparse(response.headers["location"]).query)
    token = query["token"][0]

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == query["number"][0]))
        assert ticket is not None
        attachment = db.scalar(select(Attachment).where(Attachment.ticket_id == ticket.id))
        assert attachment is not None
        attachment_id = attachment.id
        storage_key = attachment.storage_key

    # Прямая ссылка на старую публичную папку не работает.
    assert client.get(f"/static/uploads/{storage_key}").status_code == 404

    # Без входа сотрудник не получает файл.
    anonymous = client.get(f"/staff/attachment/{attachment_id}", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"

    # Заявитель получает только своё вложение по секретной ссылке.
    assert client.get(f"/t/{token}/attachment/{attachment_id}").status_code == 200
    assert client.get("/t/wrong-token/attachment/{attachment_id}".format(attachment_id=attachment_id)).status_code == 404

    assert login(client).status_code == 303
    staff_file = client.get(f"/staff/attachment/{attachment_id}")
    assert staff_file.status_code == 200
    assert staff_file.headers["content-type"].startswith("image/png")
    client.post("/logout", follow_redirects=False)
