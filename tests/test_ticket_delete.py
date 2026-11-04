# -*- coding: utf-8 -*-
"""Удаление заявок администратором: БД, файлы, аудит и права."""
import io
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-delete-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-delete-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.config import UPLOAD_DIR
from app.database import SessionLocal
from app.main import app
from app.models import Attachment, AuditLog, Comment, Location, Ticket, TicketHistory, User


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


def room214_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), (10, 80, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def create_ticket(client: TestClient):
    response = client.post(
        "/tickets",
        data={
            "description": f"Заявка на удаление {uuid.uuid4().hex}",
            "location_id": str(room214_id()),
            "category_id": "",
        },
        files={"photo": ("photo.png", png_bytes(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["number"][0], query["token"][0]


def ticket_with_relations(token_and_number):
    number, token = token_and_number
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        staff = db.scalar(select(User).where(User.login == "staff"))
        db.add(Comment(ticket_id=ticket.id, author_id=staff.id, body="Комментарий перед удалением"))
        db.commit()
        attachment = db.scalar(select(Attachment).where(Attachment.ticket_id == ticket.id))
        assert attachment is not None
        return ticket.id, attachment.id, attachment.storage_key, token


def test_admin_deletes_ticket_with_relations_and_file(client: TestClient):
    ticket_id, attachment_id, storage_key, token = ticket_with_relations(create_ticket(client))
    file_path = UPLOAD_DIR / storage_key
    assert file_path.is_file()

    login(client)
    response = client.post(f"/staff/ticket/{ticket_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/staff?deleted=1"

    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id) is None
        assert db.get(Attachment, attachment_id) is None
        assert db.scalar(select(func.count(Comment.id)).where(Comment.ticket_id == ticket_id)) == 0
        assert db.scalar(
            select(func.count(TicketHistory.id)).where(TicketHistory.ticket_id == ticket_id)
        ) == 0
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ticket.delete",
                AuditLog.entity_id == ticket_id,
            )
        )
        assert audit is not None
        assert audit.actor.login == "admin"

    assert not file_path.exists()
    assert client.get(f"/t/{token}").status_code == 404
    logout(client)


def test_staff_cannot_delete_ticket(client: TestClient):
    number, token = create_ticket(client)
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        ticket_id = ticket.id

    login(client, "staff", "staff123")
    response = client.post(f"/staff/ticket/{ticket_id}/delete", follow_redirects=False)
    assert response.status_code == 403
    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id) is not None
    logout(client)

    # Убираем тестовую заявку администратором.
    login(client)
    client.post(f"/staff/ticket/{ticket_id}/delete", follow_redirects=False)
    logout(client)


def test_anonymous_cannot_delete_ticket(client: TestClient):
    number, token = create_ticket(client)
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        ticket_id = ticket.id

    logout(client)
    response = client.post(f"/staff/ticket/{ticket_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id) is not None

    login(client)
    client.post(f"/staff/ticket/{ticket_id}/delete", follow_redirects=False)
    logout(client)
