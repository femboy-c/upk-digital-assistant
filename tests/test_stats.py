# -*- coding: utf-8 -*-
"""Проверка статистики в панели колледжа."""
import os
import tempfile
import uuid
from datetime import datetime

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-stats-test-"), "test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import Category, Location, STATUSES, Ticket
from app.security import hash_token

OPEN_STATUSES = [status for status in STATUSES if status not in ("Решена", "Отклонена")]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "staff", password: str = "staff123"):
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def add_demo_tickets() -> None:
    with SessionLocal() as db:
        category = db.scalar(select(Category).where(Category.name == "Оборудование"))
        room_214 = db.scalar(select(Location).where(Location.qr_key == "room214"))
        room_305 = db.scalar(select(Location).where(Location.qr_key == "room305"))
        assert category and room_214 and room_305

        db.add_all(
            [
                Ticket(
                    public_number=f"STATS-{uuid.uuid4().hex[:10]}",
                    title="Проектор",
                    description="Не работает проектор",
                    category_id=category.id,
                    location_id=room_214.id,
                    status="Новая",
                    tracking_token_hash=hash_token(uuid.uuid4().hex),
                ),
                Ticket(
                    public_number=f"STATS-{uuid.uuid4().hex[:10]}",
                    title="Принтер",
                    description="Принтер не печатает",
                    category_id=category.id,
                    location_id=room_305.id,
                    status="Решена",
                    tracking_token_hash=hash_token(uuid.uuid4().hex),
                ),
                Ticket(
                    public_number=f"STATS-{uuid.uuid4().hex[:10]}",
                    title="Без категории",
                    description="Проблема без категории",
                    category_id=None,
                    location_id=room_214.id,
                    status="В работе",
                    tracking_token_hash=hash_token(uuid.uuid4().hex),
                ),
            ]
        )
        db.commit()


def test_stats_requires_login(client: TestClient):
    client.post("/logout", follow_redirects=False)
    response = client.get("/staff/stats", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_stats_shows_real_numbers(client: TestClient):
    add_demo_tickets()
    login(client)

    with SessionLocal() as db:
        total = db.scalar(select(func.count(Ticket.id))) or 0
        open_count = db.scalar(select(func.count(Ticket.id)).where(Ticket.status.in_(OPEN_STATUSES))) or 0
        resolved = db.scalar(select(func.count(Ticket.id)).where(Ticket.status == "Решена")) or 0

    response = client.get("/staff/stats")
    assert response.status_code == 200
    assert "Статистика заявок" in response.text
    assert "По категориям" in response.text
    assert "По помещениям" in response.text
    assert f'<strong class="stat-value">{total}</strong>' in response.text
    assert f'<strong class="stat-value">{open_count}</strong>' in response.text
    assert f'<strong class="stat-value">{resolved}</strong>' in response.text

    assert "Оборудование" in response.text
    assert "2 этаж, каб. 214" in response.text
    assert "Без категории" in response.text


def test_stats_link_is_available_in_panel(client: TestClient):
    page = client.get("/staff")
    assert page.status_code == 200
    assert 'href="/staff/stats"' in page.text
