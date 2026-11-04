# -*- coding: utf-8 -*-
"""Регрессионные тесты: фильтры, исполнитель, комментарии, лимиты, пагинация."""
import os
import tempfile
import uuid
from urllib.parse import parse_qs, unquote_plus, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-fix-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-fix-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine
from app.main import app
from app.models import Comment, Location, Ticket, User
from app.ratelimit import reset_rate_limits
from app.security import hash_token


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"login": "staff", "password": "staff123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def room214_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def create_ticket(client: TestClient, description: str | None = None, **extra):
    description = description or f"Тестовая заявка {uuid.uuid4().hex}"
    data = {
        "description": description,
        "location_id": str(room214_id()),
        "category_id": "",
    }
    data.update(extra)
    response = client.post("/tickets", data=data, follow_redirects=False)
    return response, description


def ticket_by_description(description: str):
    with SessionLocal() as db:
        return db.scalar(select(Ticket).where(Ticket.description == description))


def test_invalid_staff_filters_do_not_crash(client: TestClient):
    login(client)
    response = client.get(
        "/staff",
        params={"category_id": "abc", "location_id": "xyz", "status": "Нет такого"},
    )
    assert response.status_code == 200
    assert "Неизвестный статус" in response.text
    assert "Неизвестная категория" in response.text
    assert "Неизвестное помещение" in response.text


def test_assignee_can_be_cleared(client: TestClient):
    response, description = create_ticket(client)
    assert response.status_code == 303
    ticket = ticket_by_description(description)
    assert ticket is not None

    login(client)
    with SessionLocal() as db:
        staff = db.scalar(select(User).where(User.login == "staff"))
        staff_id = staff.id

    assigned = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "Назначен исполнитель", "assignee_id": str(staff_id), "note": ""},
        follow_redirects=False,
    )
    assert assigned.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).assignee_id == staff_id

    cleared = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "Назначен исполнитель", "assignee_id": "", "note": ""},
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    with SessionLocal() as db:
        assert db.get(Ticket, ticket.id).assignee_id is None


def test_invalid_assignee_is_rejected_without_500(client: TestClient):
    response, description = create_ticket(client)
    assert response.status_code == 303
    ticket = ticket_by_description(description)
    assert ticket is not None

    login(client)
    for bad_value in ("abc", "999999"):
        result = client.post(
            f"/staff/ticket/{ticket.id}/status",
            data={"status": "В работе", "assignee_id": bad_value, "note": ""},
            follow_redirects=False,
        )
        assert result.status_code == 303
        assert "Исполнитель" in unquote_plus(result.headers["location"])

    with SessionLocal() as db:
        saved = db.get(Ticket, ticket.id)
        assert saved.assignee_id is None
        assert saved.status == "Новая"


def test_comment_for_missing_ticket_returns_404(client: TestClient):
    login(client)
    with SessionLocal() as db:
        before = db.scalar(select(func.count(Comment.id))) or 0

    response = client.post(
        "/staff/ticket/999999/comment",
        data={"body": "Комментарий в никуда", "visibility": "public"},
        follow_redirects=False,
    )
    assert response.status_code == 404

    with SessionLocal() as db:
        after = db.scalar(select(func.count(Comment.id))) or 0
    assert after == before


def test_ticket_numbers_are_unique_and_match_ids(client: TestClient):
    numbers = []
    for _ in range(2):
        response, description = create_ticket(client)
        assert response.status_code == 303
        location = response.headers["location"]
        number = parse_qs(urlparse(location).query)["number"][0]
        numbers.append(number)

        ticket = ticket_by_description(description)
        assert ticket is not None
        assert ticket.public_number == number
        assert number.endswith(f"{ticket.id:06d}")

    assert len(set(numbers)) == len(numbers)


def test_photo_error_returns_html_form(client: TestClient):
    description = f"Фото-тест {uuid.uuid4().hex}"
    response = client.post(
        "/tickets",
        data={
            "description": description,
            "location_id": str(room214_id()),
            "category_id": "",
        },
        files={"photo": ("photo.png", b"this is not an image", "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "не похож на изображение" in response.text
    assert "<form" in response.text
    assert ticket_by_description(description) is None


def test_csrf_error_returns_html_form(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_CSRF", "0")
    description = f"CSRF-тест {uuid.uuid4().hex}"
    response = client.post(
        "/tickets",
        data={
            "description": description,
            "location_id": str(room214_id()),
            "category_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Форма устарела" in response.text
    assert "<form" in response.text
    assert ticket_by_description(description) is None


def test_description_length_is_limited(client: TestClient):
    description = "а" * 5001
    response, _ = create_ticket(client, description)
    assert response.status_code == 400
    assert "слишком длинное" in response.text
    assert ticket_by_description(description) is None


def test_comment_and_note_lengths_are_limited(client: TestClient):
    response, description = create_ticket(client)
    assert response.status_code == 303
    ticket = ticket_by_description(description)
    assert ticket is not None

    login(client)
    with SessionLocal() as db:
        before = db.scalar(
            select(func.count(Comment.id)).where(Comment.ticket_id == ticket.id)
        ) or 0

    too_long_comment = client.post(
        f"/staff/ticket/{ticket.id}/comment",
        data={"body": "б" * 2001, "visibility": "public"},
        follow_redirects=False,
    )
    assert too_long_comment.status_code == 303
    assert "слишком длинный" in unquote_plus(too_long_comment.headers["location"])

    too_long_note = client.post(
        f"/staff/ticket/{ticket.id}/status",
        data={"status": "Решена", "assignee_id": "", "note": "в" * 501},
        follow_redirects=False,
    )
    assert too_long_note.status_code == 303
    assert "слишком длинный" in unquote_plus(too_long_note.headers["location"])

    with SessionLocal() as db:
        after = db.scalar(
            select(func.count(Comment.id)).where(Comment.ticket_id == ticket.id)
        ) or 0
        assert db.get(Ticket, ticket.id).status == "Новая"
    assert after == before


def test_pagination_handles_pages_and_invalid_values(client: TestClient):
    with SessionLocal() as db:
        for _ in range(30):
            db.add(
                Ticket(
                    public_number=f"PAGE-{uuid.uuid4().hex[:10]}",
                    title="Пагинация",
                    description="Заявка для проверки пагинации",
                    location_id=room214_id(),
                    status="Новая",
                    tracking_token_hash=hash_token(uuid.uuid4().hex),
                )
            )
        db.commit()

    login(client)
    second_page = client.get("/staff", params={"page": 2})
    assert second_page.status_code == 200
    assert "Страница 2 из" in second_page.text
    assert "Вперёд" in second_page.text or "Назад" in second_page.text

    invalid_page = client.get("/staff", params={"page": "abc"})
    assert invalid_page.status_code == 200

    too_far = client.get("/staff", params={"page": 9999})
    assert too_far.status_code == 200


def test_honeypot_blocks_ticket_creation(client: TestClient):
    description = f"Бот-тест {uuid.uuid4().hex}"
    response, _ = create_ticket(client, description, website="http://spam.example")
    assert response.status_code == 400
    assert "Не удалось отправить" in response.text
    assert ticket_by_description(description) is None


def test_ticket_rate_limit(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_RATE_LIMIT", "0")
    reset_rate_limits()
    try:
        for index in range(5):
            response, _ = create_ticket(client, f"Rate limit {index} {uuid.uuid4().hex}")
            assert response.status_code == 303
        blocked, _ = create_ticket(client, f"Rate limit blocked {uuid.uuid4().hex}")
        assert blocked.status_code == 429
        assert "Слишком много заявок" in blocked.text
    finally:
        reset_rate_limits()


def test_sqlite_foreign_keys_are_enabled():
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    with SessionLocal() as db:
        db.add(Comment(ticket_id=999999, body="Осиротевший комментарий"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
