# -*- coding: utf-8 -*-
"""CSV-экспорт, защита от формул и фильтр статистики по датам."""
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-export-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-export-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
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


def add_ticket(description: str, title: str = "Экспорт") -> Ticket:
    with SessionLocal() as db:
        ticket = Ticket(
            public_number=f"CSV-{uuid.uuid4().hex[:10]}",
            title=title,
            description=description,
            location_id=room214_id(),
            status="Новая",
            tracking_token_hash=hash_token(uuid.uuid4().hex),
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket


def test_anonymous_export_redirects_to_login(client: TestClient):
    logout(client)
    response = client.get("/staff/export.csv", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_export_has_bom_header_and_rows(client: TestClient):
    marker = f"Экспорт {uuid.uuid4().hex}"
    add_ticket(marker)
    login(client)

    response = client.get("/staff/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith("\ufeff".encode("utf-8"))
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Номер;Создана;Обновлена;Статус")
    assert marker in text
    logout(client)


def test_export_respects_search_filter(client: TestClient):
    marker = f"Уникальный поиск {uuid.uuid4().hex}"
    add_ticket(marker)
    login(client)
    response = client.get("/staff/export.csv", params={"q": marker})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert marker in text
    assert text.count("\r\n") == 2  # заголовок + одна заявка
    logout(client)


def test_export_protects_from_formula_injection(client: TestClient):
    marker = f"formula-{uuid.uuid4().hex}"
    add_ticket(marker, title="=SUM(A1:A2)")
    login(client)
    response = client.get("/staff/export.csv", params={"q": marker})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "'=SUM(A1:A2)" in text
    logout(client)


def test_stats_and_list_accept_date_range(client: TestClient):
    login(client)

    future = client.get("/staff/stats", params={"date_from": "2999-01-01"})
    assert future.status_code == 200
    assert "За период" in future.text
    assert "Пока нет заявок" in future.text

    future_list = client.get("/staff", params={"date_from": "2999-01-01"})
    assert future_list.status_code == 200
    assert "Ничего не найдено" in future_list.text

    invalid = client.get("/staff/stats", params={"date_from": "not-a-date"})
    assert invalid.status_code == 200
    assert "Неверная дата начала" in invalid.text

    reversed_range = client.get(
        "/staff/stats", params={"date_from": "2026-12-31", "date_to": "2026-01-01"}
    )
    assert reversed_range.status_code == 200
    assert "Дата начала позже даты окончания" in reversed_range.text
    logout(client)


def test_export_with_future_date_contains_only_header(client: TestClient):
    login(client)
    response = client.get("/staff/export.csv", params={"date_from": "2999-01-01"})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert len(text.splitlines()) == 1
    logout(client)
