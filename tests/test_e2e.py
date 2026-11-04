# -*- coding: utf-8 -*-
"""Полный сценарий: QR → ИИ → заявка → сотрудник → «Решена» → отслеживание."""
import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-e2e-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-e2e-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Category, Location, Ticket, User


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_full_ticket_lifecycle(client: TestClient):
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        category = db.scalar(select(Category).where(Category.name == "Оборудование"))
        staff = db.scalar(select(User).where(User.login == "staff"))
        assert location and category and staff
        location_id = location.id
        category_id = category.id
        staff_id = staff.id

    # 1. Открытие по QR: этаж и помещение подставляются.
    qr_page = client.get("/?qr=room214")
    assert qr_page.status_code == 200
    assert "QR-код распознан" in qr_page.text

    # 2. ИИ-помощник распознаёт неисправность и предлагает категорию.
    ai = client.post(
        "/assistant/analyze",
        json={"text": "В кабинете 214 не работает проектор"},
    ).json()
    assert ai["kind"] == "malfunction"
    assert ai["suggested_category_id"] == category_id

    # 3. Заявка создаётся и получает номер + секретный токен.
    description = f"E2E: в кабинете 214 не работает проектор {uuid.uuid4().hex[:6]}"
    created = client.post(
        "/tickets",
        data={
            "description": description,
            "location_id": str(location_id),
            "category_id": str(category_id),
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    success_query = parse_qs(urlparse(created.headers["location"]).query)
    number = success_query["number"][0]
    token = success_query["token"][0]
    assert number.startswith("ДГУ-")

    # 4. По секретной ссылке виден статус «Новая».
    track = client.get(f"/t/{token}")
    assert track.status_code == 200
    assert number in track.text
    assert "Новая" in track.text

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        ticket_id = ticket.id

    # 5. Без входа панель недоступна.
    anonymous = TestClient(app)
    assert anonymous.get("/staff", follow_redirects=False).status_code == 303

    # 6. Сотрудник входит и находит заявку.
    login = client.post(
        "/login",
        data={"login": "staff", "password": "staff123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    found = client.get(f"/staff?q={number}")
    assert found.status_code == 200
    assert number in found.text

    # 7. Назначение исполнителя.
    assigned = client.post(
        f"/staff/ticket/{ticket_id}/status",
        data={
            "status": "Назначен исполнитель",
            "assignee_id": str(staff_id),
            "note": "Передано сотруднику колледжа.",
        },
        follow_redirects=False,
    )
    assert assigned.status_code == 303

    # 8. Заявка решена с обязательным комментарием.
    resolved = client.post(
        f"/staff/ticket/{ticket_id}/status",
        data={
            "status": "Решена",
            "assignee_id": str(staff_id),
            "note": "Проектор проверен и снова работает.",
        },
        follow_redirects=False,
    )
    assert resolved.status_code == 303

    card = client.get(f"/staff/ticket/{ticket_id}")
    assert card.status_code == 200
    assert "Назначен исполнитель" in card.text
    assert "Решена" in card.text

    # 9. Заявитель видит актуальный статус, историю и публичные комментарии.
    final_track = client.get(f"/t/{token}")
    assert "Решена" in final_track.text
    assert "Проектор проверен и снова работает." in final_track.text
    assert "Назначен исполнитель" in final_track.text

    # 10. Внутренняя заметка не попадает на публичную страницу.
    internal_text = f"Внутренняя заметка {uuid.uuid4().hex[:8]}"
    client.post(
        f"/staff/ticket/{ticket_id}/comment",
        data={"body": internal_text, "visibility": "internal"},
        follow_redirects=False,
    )
    public_after_internal = client.get(f"/t/{token}")
    assert internal_text not in public_after_internal.text
    staff_after_internal = client.get(f"/staff/ticket/{ticket_id}")
    assert internal_text in staff_after_internal.text

    # 11. Опасный и неизвестный запросы к ИИ понятны и не меняют заявку.
    danger = client.post("/assistant/analyze", json={"text": "В розетке искрит и пахнет гарью"}).json()
    unknown = client.post("/assistant/analyze", json={"text": "Как приготовить борщ?"}).json()
    assert danger["kind"] == "danger"
    assert "не трогайте" in danger["answer"].lower()
    assert unknown["kind"] == "unknown"
    assert "нет ответа" in unknown["answer"].lower()

    with SessionLocal() as db:
        final_ticket = db.get(Ticket, ticket_id)
        assert final_ticket.status == "Решена"
