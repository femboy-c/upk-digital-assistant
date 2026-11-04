# -*- coding: utf-8 -*-
"""Ограничения переходов статусов заявки."""
import os
import tempfile
import uuid
from urllib.parse import parse_qs, unquote_plus, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-status-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-status-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Location, Ticket, TicketHistory, User


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


def create_ticket(client: TestClient) -> Ticket:
    response = client.post(
        "/tickets",
        data={
            "description": f"Проверка переходов статусов {uuid.uuid4().hex}",
            "location_id": str(room214_id()),
            "category_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    number = parse_qs(urlparse(response.headers["location"]).query)["number"][0]
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        return ticket


def staff_id() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == "staff"))
        assert user is not None
        return user.id


def history_count(ticket_id: int) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(TicketHistory.id)).where(TicketHistory.ticket_id == ticket_id)
        ) or 0


def test_forbidden_transition_is_rejected_without_history(client: TestClient):
    ticket = create_ticket(client)
    login(client)
    before = history_count(ticket.id)

    response = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "В работе", "assignee_id": str(staff_id()), "note": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "недоступен" in unquote_plus(response.headers["location"])

    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).status == "Новая"
    assert history_count(ticket.id) == before
    logout(client)


def test_assignee_is_required_when_entering_assigned_status(client: TestClient):
    ticket = create_ticket(client)
    login(client)
    response = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "Назначен исполнитель", "assignee_id": "", "note": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "нужно назначить исполнителя" in unquote_plus(response.headers["location"])

    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).status == "Новая"
    logout(client)


def test_allowed_chain_and_admin_reopen(client: TestClient):
    ticket = create_ticket(client)
    login(client)

    assigned = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={
            "status": "Назначен исполнитель",
            "assignee_id": str(staff_id()),
            "note": "Передано сотруднику.",
        },
        follow_redirects=False,
    )
    assert assigned.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).status == "Назначен исполнитель"

    resolved = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={
            "status": "Решена",
            "assignee_id": str(staff_id()),
            "note": "Проблема устранена.",
        },
        follow_redirects=False,
    )
    assert resolved.status_code == 303

    # Обычный сотрудник не может вернуть итоговый статус.
    staff_reopen = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "В работе", "assignee_id": str(staff_id()), "note": ""},
        follow_redirects=False,
    )
    assert staff_reopen.status_code == 303
    assert "недоступен" in unquote_plus(staff_reopen.headers["location"])
    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).status == "Решена"
    logout(client)

    # Администратор может вернуть заявку в работу.
    login(client, "admin", "admin123")
    admin_reopen = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "В работе", "assignee_id": str(staff_id()), "note": ""},
        follow_redirects=False,
    )
    assert admin_reopen.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).status == "В работе"
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ticket.status",
                AuditLog.entity_id == ticket.id,
            )
        )
        assert audit is not None
    logout(client)
