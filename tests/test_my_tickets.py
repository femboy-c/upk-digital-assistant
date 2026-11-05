# -*- coding: utf-8 -*-
"""Личный список заявок в браузере: добавление, удаление, изоляция."""
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-my-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-my-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Location, Ticket
from app.security import hash_token


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def create_ticket(client: TestClient) -> tuple[str, str]:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        location_id = location.id

    response = client.post(
        "/tickets",
        data={
            "description": f"Кабинет 214: шумит вентиляция {uuid.uuid4().hex[:6]}",
            "location_id": str(location_id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["number"][0], query["token"][0]


def test_created_ticket_appears_in_my_tickets(client: TestClient):
    number, _ = create_ticket(client)
    page = client.get("/my")
    assert page.status_code == 200
    assert number in page.text
    assert "Мои заявки" in page.text


def test_another_browser_does_not_see_the_ticket(client: TestClient):
    number, _ = create_ticket(client)
    with TestClient(app) as other:
        page = other.get("/my")
        assert page.status_code == 200
        assert number not in page.text
        assert "Пока здесь нет сохранённых заявок" in page.text


def test_opening_tracking_link_adds_ticket(client: TestClient):
    number, token = create_ticket(client)
    with TestClient(app) as other:
        assert other.get(f"/t/{token}").status_code == 200
        page = other.get("/my")
        assert number in page.text


def test_remove_and_clear_list(client: TestClient):
    first_number, first_token = create_ticket(client)
    second_number, _ = create_ticket(client)

    response = client.post(
        "/my/remove",
        data={"token": first_token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/my")
    assert first_number not in page.text
    assert second_number in page.text

    response = client.post("/my/clear", follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/my")
    assert second_number not in page.text
    assert "Пока здесь нет сохранённых заявок" in page.text


def test_revoked_and_expired_tickets_disappear(client: TestClient):
    revoked_number, revoked_token = create_ticket(client)
    expired_number, expired_token = create_ticket(client)

    with SessionLocal() as db:
        revoked = db.scalar(
            select(Ticket).where(Ticket.tracking_token_hash == hash_token(revoked_token))
        )
        expired = db.scalar(
            select(Ticket).where(Ticket.tracking_token_hash == hash_token(expired_token))
        )
        assert revoked is not None and expired is not None
        revoked.tracking_revoked_at = datetime.now()
        expired.tracking_expires_at = datetime.now() - timedelta(days=1)
        db.commit()

    page = client.get("/my")
    assert page.status_code == 200
    assert revoked_number not in page.text
    assert expired_number not in page.text
