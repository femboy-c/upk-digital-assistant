# -*- coding: utf-8 -*-
"""Проверка сценариев демонстрационного ИИ-помощника."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-ai-test-"), "test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import Ticket, TicketHistory


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def analyze(client: TestClient, text: str) -> dict:
    response = client.post("/assistant/analyze", json={"text": text})
    assert response.status_code == 200
    return response.json()


def test_typical_question_gets_answer_and_source(client: TestClient):
    data = analyze(client, "Как подключить ноутбук к проектору?")
    assert data["kind"] == "question"
    assert data["answer"]
    assert data["source"] == "Инструкция колледжа"
    assert data["suggested_category"] == "Оборудование"


def test_malfunction_gets_category_and_draft(client: TestClient):
    data = analyze(client, "В кабинете 214 не работает проектор")
    assert data["kind"] == "malfunction"
    assert data["suggested_category"] == "Оборудование"
    assert data["suggested_category_id"] is not None
    assert "Прошу проверить" in data["draft"]
    assert data["can_create_ticket"] is True


def test_insufficient_description_asks_clarifying_question(client: TestClient):
    data = analyze(client, "Не работает")
    assert data["kind"] == "insufficient"
    assert data["clarifying_question"]
    assert data["draft"] == ""


def test_dangerous_situation_recommends_not_touching(client: TestClient):
    data = analyze(client, "В розетке искрит и пахнет гарью")
    assert data["kind"] == "danger"
    assert "не трогайте" in data["answer"].lower()
    assert "ответственному" in data["answer"].lower()
    assert data["source"]
    assert "Опасная ситуация" in data["draft"]


def test_unknown_question_is_honest(client: TestClient):
    data = analyze(client, "Как приготовить борщ?")
    assert data["kind"] == "unknown"
    assert "нет ответа" in data["answer"].lower()
    assert data["source"] == ""


def test_printer_malfunction_uses_knowledge_base(client: TestClient):
    data = analyze(client, "Принтер не печатает")
    assert data["kind"] == "malfunction"
    assert data["source"] == "Инструкция колледжа"
    assert "бумага" in data["answer"].lower()


def test_assistant_does_not_create_or_close_tickets(client: TestClient):
    with SessionLocal() as db:
        tickets_before = db.scalar(select(func.count(Ticket.id)))
        history_before = db.scalar(select(func.count(TicketHistory.id)))
        statuses_before = db.execute(select(Ticket.id, Ticket.status)).all()

    analyze(client, "В кабинете 305 не работает проектор")
    analyze(client, "В розетке искрит")
    analyze(client, "Как приготовить борщ?")

    with SessionLocal() as db:
        assert db.scalar(select(func.count(Ticket.id))) == tickets_before
        assert db.scalar(select(func.count(TicketHistory.id))) == history_before
        assert db.execute(select(Ticket.id, Ticket.status)).all() == statuses_before


def test_disclaimer_explains_rules_without_demo_label(client: TestClient):
    data = analyze(client, "Как проверить статус заявки?")
    assert "по правилам и проверенной базе знаний" in data["disclaimer"]
    assert "демонстрационн" not in data["disclaimer"].lower()

    page = client.get("/")
    assert "демонстрационн" not in page.text.lower()
    assert "Проверить текст" in page.text
