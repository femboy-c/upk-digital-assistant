# -*- coding: utf-8 -*-
"""Приоритеты заявок, контакты заявителя и быстрые ответы."""
import os
import tempfile
import uuid
from urllib.parse import parse_qs, unquote_plus, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-prio-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-prio-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Location, Ticket


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


def create_ticket(client: TestClient, description: str, **extra):
    data = {
        "description": description,
        "location_id": str(room214_id()),
        "category_id": "",
    }
    data.update(extra)
    return client.post("/tickets", data=data, follow_redirects=False)


def test_contacts_are_saved_and_shown_to_staff(client: TestClient):
    description = f"Контакты {uuid.uuid4().hex}"
    response = create_ticket(
        client,
        description,
        contact_phone="+7 900 123-45-67",
        contact_email="student@example.ru",
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.description == description))
        assert ticket is not None
        assert ticket.contact_phone == "+7 900 123-45-67"
        assert ticket.contact_email == "student@example.ru"
        ticket_id = ticket.id

    login(client)
    page = client.get(f"/staff/ticket/{ticket_id}")
    assert page.status_code == 200
    assert "+7 900 123-45-67" in page.text
    assert "student@example.ru" in page.text
    assert "Нужны уточнения" in page.text
    assert "Передано в работу" in page.text
    assert "Проблема устранена" in page.text
    logout(client)


def test_invalid_contacts_are_rejected(client: TestClient):
    description = f"Плохой контакт {uuid.uuid4().hex}"
    response = create_ticket(client, description, contact_phone="телефон")
    assert response.status_code == 400
    assert "Телефон указан в неверном формате" in response.text

    bad_email = create_ticket(
        client,
        description + "-2",
        contact_email="not-an-email",
    )
    assert bad_email.status_code == 400
    assert "Email указан в неверном формате" in bad_email.text

    with SessionLocal() as db:
        assert db.scalar(select(Ticket).where(Ticket.description == description)) is None


def test_priority_is_updated_and_filtered(client: TestClient):
    description = f"Приоритет {uuid.uuid4().hex}"
    assert create_ticket(client, description).status_code == 303
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.description == description))
        assert ticket is not None
        ticket_id = ticket.id
        assert ticket.priority == "Обычный"

    login(client)
    response = client.post(
        f"/staff/ticket/{ticket_id}/status",
        data={
            "status": "На рассмотрении",
            "priority": "Срочный",
            "assignee_id": "",
            "note": "Срочный вопрос.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id).priority == "Срочный"
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ticket.status",
                AuditLog.entity_id == ticket_id,
            )
        )
        assert audit is not None
        assert "Срочный" in audit.details

    filtered = client.get("/staff", params={"priority": "Срочный"})
    assert filtered.status_code == 200
    assert description in filtered.text

    invalid = client.get("/staff", params={"priority": "Неизвестно"})
    assert invalid.status_code == 200
    assert "Неизвестный приоритет" in invalid.text
    logout(client)


def test_unknown_priority_does_not_change_ticket(client: TestClient):
    description = f"Плохой приоритет {uuid.uuid4().hex}"
    assert create_ticket(client, description).status_code == 303
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.description == description))
        assert ticket is not None
        ticket_id = ticket.id

    login(client)
    response = client.post(
        f"/staff/ticket/{ticket_id}/status",
        data={
            "status": "На рассмотрении",
            "priority": "Очень срочный",
            "assignee_id": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Неизвестный приоритет" in unquote_plus(response.headers["location"])
    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id).priority == "Обычный"
    logout(client)


def test_export_contains_priority_and_contacts(client: TestClient):
    marker = f"Экспорт контактов {uuid.uuid4().hex}"
    assert create_ticket(
        client,
        marker,
        contact_phone="+7 111 222-33-44",
        contact_email="export@example.ru",
    ).status_code == 303

    login(client)
    response = client.get("/staff/export.csv", params={"q": marker})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "Приоритет" in text.splitlines()[0]
    assert "Обычный" in text
    assert "+7 111 222-33-44" in text
    assert "export@example.ru" in text
    logout(client)
