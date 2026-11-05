# -*- coding: utf-8 -*-
"""Поиск похожих заявок и объединение дублей."""
import io
import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-dup-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-dup-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.database import SessionLocal
from app.duplicates import find_similar_tickets, text_similarity
from app.main import app
from app.models import Attachment, AuditLog, Comment, Location, Ticket, TicketHistory


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def location_id():
    key = f"dup-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        location = Location(floor="1", room=key, zone_name="", qr_key=key)
        db.add(location)
        db.commit()
        return location.id


def login(client: TestClient, username: str = "admin", password: str = "admin123") -> None:
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client: TestClient) -> None:
    client.post("/logout", follow_redirects=False)


def create_ticket(client: TestClient, location_id: int, description: str, photo: bytes | None = None):
    files = None
    if photo is not None:
        files = [("photos", ("photo.jpg", photo, "image/jpeg"))]
    response = client.post(
        "/tickets",
        data={"description": description, "location_id": str(location_id)},
        files=files,
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    number = query["number"][0]
    token = query["token"][0]
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        return ticket.id, number, token


def test_text_similarity_detects_same_problem():
    assert text_similarity(
        "В кабинете 214 не работает проектор, нет изображения",
        "В кабинете 214 не работает проектор, изображения нет",
    ) >= 0.4
    assert text_similarity("Протекает кран в санузле", "Не работает проектор") < 0.4


def test_find_similar_only_open_tickets_at_same_location(client: TestClient, location_id: int):
    _, first_number, _ = create_ticket(
        client, location_id, "В кабинете 214 не работает проектор, нет изображения"
    )
    second_id, _, _ = create_ticket(
        client, location_id, "В кабинете 214 не работает проектор, изображения нет"
    )

    with SessionLocal() as db:
        first = db.scalar(select(Ticket).where(Ticket.public_number == first_number))
        second = db.get(Ticket, second_id)
        assert first and second

        found = find_similar_tickets(
            db, second.description, location_id, exclude_id=second.id
        )
        assert first in [ticket for ticket, _ in found]

        first.status = "Решена"
        db.commit()
        found = find_similar_tickets(
            db, second.description, location_id, exclude_id=second.id
        )
        assert first not in [ticket for ticket, _ in found]


def test_staff_ticket_page_shows_similar_tickets(client: TestClient, location_id: int):
    _, primary_number, _ = create_ticket(
        client, location_id, "В коридоре 2 этажа перегорела лампа, темно"
    )
    duplicate_id, _, _ = create_ticket(
        client, location_id, "В коридоре 2 этажа перегорела лампа, вечером темно"
    )

    logout(client)
    login(client, "staff", "staff123")
    page = client.get(f"/staff/ticket/{duplicate_id}")
    assert page.status_code == 200
    assert "Похожие заявки" in page.text
    assert primary_number in page.text
    assert "Объединять заявки может только администратор" in page.text
    logout(client)


def test_admin_merges_duplicate_and_moves_attachments(client: TestClient, location_id: int):
    buffer = io.BytesIO()
    Image.new("RGB", (200, 150), (10, 20, 30)).save(buffer, format="JPEG")

    target_id, target_number, _ = create_ticket(
        client, location_id, "Сломана дверь в кабинете 305, не закрывается"
    )
    source_id, source_number, source_token = create_ticket(
        client,
        location_id,
        "Сломана дверь в кабинете 305, не закрывается совсем",
        photo=buffer.getvalue(),
    )

    logout(client)
    login(client, "admin", "admin123")
    response = client.post(
        f"/staff/ticket/{source_id}/merge",
        data={"target_id": str(target_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/staff/ticket/{target_id}" in response.headers["location"]

    with SessionLocal() as db:
        source = db.get(Ticket, source_id)
        target = db.get(Ticket, target_id)
        assert source is not None and target is not None
        assert source.status == "Отклонена"
        assert source.merged_into_id == target_id
        assert target.status != "Отклонена"

        attachment = db.scalar(
            select(Attachment).where(Attachment.ticket_id == target_id)
        )
        assert attachment is not None

        comment = db.scalar(
            select(Comment).where(
                Comment.ticket_id == target_id,
                Comment.visibility == "internal",
            )
        )
        assert comment is not None
        assert source_number in comment.body

        history = db.scalars(
            select(TicketHistory).where(TicketHistory.ticket_id == source_id)
        ).all()
        assert any(target_number in item.note for item in history)

        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ticket.merge",
                AuditLog.entity_id == source_id,
            )
        )
        assert audit is not None

    track = client.get(f"/t/{source_token}")
    assert track.status_code == 200
    assert "объединена" in track.text
    assert target_number in track.text

    # Статус объединённой заявки менять нельзя.
    response = client.post(
        f"/staff/ticket/{source_id}/status",
        data={"status": "В работе"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, source_id).status == "Отклонена"
    logout(client)


def test_staff_cannot_merge(client: TestClient, location_id: int):
    target_id, _, _ = create_ticket(
        client, location_id, "Не работает розетка в кабинете 101, искрит"
    )
    source_id, _, _ = create_ticket(
        client, location_id, "Не работает розетка в кабинете 101, искрит и греется"
    )

    logout(client)
    login(client, "staff", "staff123")
    response = client.post(
        f"/staff/ticket/{source_id}/merge",
        data={"target_id": str(target_id)},
        follow_redirects=False,
    )
    assert response.status_code == 403

    with SessionLocal() as db:
        source = db.get(Ticket, source_id)
        assert source is not None
        assert source.merged_into_id is None
        assert source.status != "Отклонена"
    logout(client)
