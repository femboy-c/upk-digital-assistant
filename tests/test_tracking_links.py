# -*- coding: utf-8 -*-
"""Срок жизни, отзыв и восстановление ссылки отслеживания."""
import io
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote_plus, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-track-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-track-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Attachment, AuditLog, Location, Ticket


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "staff", password: str = "staff123") -> None:
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client: TestClient) -> None:
    client.post("/logout", follow_redirects=False)


def room214_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), (0, 120, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def create_ticket(client: TestClient, *, with_photo: bool = False):
    description = f"Проверка ссылки {uuid.uuid4().hex}"
    files = {"photo": ("photo.png", png_bytes(), "image/png")} if with_photo else None
    response = client.post(
        "/tickets",
        data={
            "description": description,
            "location_id": str(room214_id()),
            "category_id": "",
        },
        files=files,
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["number"][0], query["token"][0]


def ticket_by_number(number: str) -> Ticket:
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        return ticket


def test_new_ticket_has_expiry_and_link_works(client: TestClient):
    number, token = create_ticket(client)
    ticket = ticket_by_number(number)
    assert ticket.tracking_expires_at is not None
    assert ticket.tracking_expires_at > datetime.now()

    response = client.get(f"/t/{token}")
    assert response.status_code == 200
    assert number in response.text
    assert "ссылка действует до" in response.text


def test_revoke_and_restore_tracking_link(client: TestClient):
    number, token = create_ticket(client)
    ticket = ticket_by_number(number)

    login(client)
    revoked = client.post(
        f"/staff/ticket/{ticket.id}/tracking/revoke",
        follow_redirects=False,
    )
    assert revoked.status_code == 303

    assert client.get(f"/t/{token}").status_code == 404
    assert (
        client.get("/track", params={"number": number, "token": token}).status_code
        == 404
    )

    with SessionLocal() as db:
        entry = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "tracking.revoke",
                AuditLog.entity_id == ticket.id,
            )
        )
        assert entry is not None

    restored = client.post(
        f"/staff/ticket/{ticket.id}/tracking/restore",
        follow_redirects=False,
    )
    assert restored.status_code == 303
    assert client.get(f"/t/{token}").status_code == 200
    logout(client)


def test_expired_link_is_closed_and_can_be_extended(client: TestClient):
    number, token = create_ticket(client)
    ticket = ticket_by_number(number)

    with SessionLocal() as db:
        saved = db.get(Ticket, ticket.id)
        saved.tracking_expires_at = datetime.now() - timedelta(days=1)
        db.commit()

    assert client.get(f"/t/{token}").status_code == 404

    login(client)
    extended = client.post(
        f"/staff/ticket/{ticket.id}/tracking/extend",
        data={"days": "30"},
        follow_redirects=False,
    )
    assert extended.status_code == 303

    with SessionLocal() as db:
        saved = db.get(Ticket, ticket.id)
        assert saved.tracking_expires_at > datetime.now()
        entry = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "tracking.extend",
                AuditLog.entity_id == ticket.id,
            )
        )
        assert entry is not None

    assert client.get(f"/t/{token}").status_code == 200
    logout(client)


def test_invalid_extend_days_are_rejected(client: TestClient):
    number, _ = create_ticket(client)
    ticket = ticket_by_number(number)
    login(client)
    for value in ("0", "-5", "99999", "abc"):
        response = client.post(
            f"/staff/ticket/{ticket.id}/tracking/extend",
            data={"days": value},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "от 1 до 3650" in unquote_plus(response.headers["location"])
    logout(client)


def test_anonymous_cannot_revoke_link(client: TestClient):
    number, _ = create_ticket(client)
    ticket = ticket_by_number(number)
    logout(client)
    response = client.post(
        f"/staff/ticket/{ticket.id}/tracking/revoke",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_revoked_link_blocks_attachment(client: TestClient):
    number, token = create_ticket(client, with_photo=True)
    ticket = ticket_by_number(number)
    with SessionLocal() as db:
        attachment = db.scalar(select(Attachment).where(Attachment.ticket_id == ticket.id))
        assert attachment is not None
        attachment_id = attachment.id

    assert client.get(f"/t/{token}/attachment/{attachment_id}").status_code == 200

    login(client)
    client.post(f"/staff/ticket/{ticket.id}/tracking/revoke", follow_redirects=False)
    assert client.get(f"/t/{token}/attachment/{attachment_id}").status_code == 404
    logout(client)
