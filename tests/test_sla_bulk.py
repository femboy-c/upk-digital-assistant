# -*- coding: utf-8 -*-
"""SLA-подсветка и массовые действия в панели сотрудника."""
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-sla-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-sla-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Location, Ticket, TicketHistory, User
from app.sla import age_hours, is_overdue, target_hours


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


def create_ticket(client: TestClient, description: str | None = None) -> tuple[int, str]:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        location_id = location.id

    response = client.post(
        "/tickets",
        data={
            "description": description or f"SLA-тест {uuid.uuid4().hex[:8]}",
            "location_id": str(location_id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    number = query["number"][0]
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        return ticket.id, number


def backdate(ticket_id: int, hours: int) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        assert ticket is not None
        ticket.created_at = datetime.now() - timedelta(hours=hours)
        db.commit()


def staff_id() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == "staff"))
        assert user is not None
        return user.id


def test_sla_rules_and_overdue():
    now = datetime.now()
    new_ticket = Ticket(
        priority="Обычный", status="Новая", created_at=now - timedelta(hours=9)
    )
    assert target_hours(new_ticket) == 8
    assert is_overdue(new_ticket, now)
    assert age_hours(new_ticket, now) > 8

    urgent = Ticket(
        priority="Срочный", status="В работе", created_at=now - timedelta(hours=9)
    )
    assert target_hours(urgent) == 8
    assert is_overdue(urgent, now)

    resolved = Ticket(
        priority="Обычный", status="Решена", created_at=now - timedelta(days=10)
    )
    assert not is_overdue(resolved, now)


def test_overdue_filter_card_and_stats(client: TestClient):
    ticket_id, number = create_ticket(client, "Просроченная заявка: не работает свет")
    backdate(ticket_id, 10)

    logout(client)
    login(client)
    page = client.get("/staff?overdue=1")
    assert page.status_code == 200
    assert number in page.text
    assert "Только просроченные" in page.text
    assert "Просрочено" in page.text

    card = client.get(f"/staff/ticket/{ticket_id}")
    assert "просрочена по SLA" in card.text

    stats = client.get("/staff/stats")
    assert "Просрочены" in stats.text

    exported = client.get("/staff/export.csv?overdue=1")
    assert exported.status_code == 200
    assert number in exported.text


def test_bulk_assign_priority_and_status(client: TestClient):
    first_id, first_number = create_ticket(client, "Массовое действие: сломана парта")
    second_id, second_number = create_ticket(client, "Массовое действие: шатается стул")

    logout(client)
    login(client)
    assignee = staff_id()

    response = client.post(
        "/staff/bulk",
        data={
            "action": "assign",
            "assignee_id": str(assignee),
            "ticket_ids": [str(first_id), str(second_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "bulk_updated=2" in response.headers["location"]

    with SessionLocal() as db:
        assert db.get(Ticket, first_id).assignee_id == assignee
        assert db.get(Ticket, second_id).assignee_id == assignee

    response = client.post(
        "/staff/bulk",
        data={
            "action": "priority",
            "priority": "Высокий",
            "ticket_ids": [str(first_id), str(second_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, first_id).priority == "Высокий"
        assert db.get(Ticket, second_id).priority == "Высокий"

    response = client.post(
        "/staff/bulk",
        data={
            "action": "status",
            "status": "Назначен исполнитель",
            "ticket_ids": [str(first_id), str(second_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, first_id).status == "Назначен исполнитель"
        assert db.get(Ticket, second_id).status == "Назначен исполнитель"

        for ticket_id in (first_id, second_id):
            history = db.scalars(
                select(TicketHistory).where(TicketHistory.ticket_id == ticket_id)
            ).all()
            assert any(item.new_status == "Назначен исполнитель" for item in history)

        audit = db.scalars(
            select(AuditLog).where(AuditLog.action == "ticket.bulk_status")
        ).all()
        assert audit


def test_bulk_skips_invalid_transition_and_empty_selection(client: TestClient):
    ticket_id, number = create_ticket(client, "Массовая ошибка: перегорёла лампа")

    logout(client)
    login(client)

    response = client.post(
        "/staff/bulk",
        data={
            "action": "status",
            "status": "Решена",
            "note": "Проверено",
            "ticket_ids": [str(ticket_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, ticket_id).status == "Новая"

    response = client.post(
        "/staff/bulk",
        data={"action": "assign", "ticket_ids": []},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "Выберите хотя бы одну заявку" in page.text
    logout(client)
