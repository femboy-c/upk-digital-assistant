# -*- coding: utf-8 -*-
"""
Проверка QR-кодов, выбора этажа/помещения и управления привязкой QR.

Запуск: .venv/bin/pytest -q
"""
import os
import re
import tempfile

# Отдельная временная база, чтобы тесты не трогали data/upk.db.
os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-test-"), "test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import qr_target_url
from app.database import SessionLocal
from app.main import app
from app.models import Location


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = "admin123"):
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client: TestClient):
    client.post("/logout", follow_redirects=False)


def get_location(qr_key: str) -> Location | None:
    with SessionLocal() as db:
        return db.scalar(select(Location).where(Location.qr_key == qr_key))


class DummyRequest:
    base_url = "http://127.0.0.1:8000/"


def test_qr_target_uses_public_base_url():
    assert qr_target_url(DummyRequest(), "room214") == "http://upk.test/?qr=room214"


def test_known_qr_preselects_floor_and_room(client: TestClient):
    response = client.get("/?qr=room214")
    assert response.status_code == 200
    assert "QR-код распознан" in response.text
    # Этаж подставлен, помещение отмечено как выбранное.
    assert re.search(r'value="2"\s+selected>2 этаж', response.text, re.S)
    assert re.search(r'value="\d+"\s+data-floor="2"[^>]*selected', response.text, re.S)
    assert 'id="building"' not in response.text
    assert 'id="floor"' in response.text


def test_location_catalog_has_college_areas(client: TestClient):
    with SessionLocal() as db:
        titles = {location.title for location in db.scalars(select(Location)).all()}

    for title in (
        "Столовая",
        "Спортзал",
        "Актовый зал",
        "Женская раздевалка",
        "Мужская раздевалка",
        "Медпункт",
        "Передний двор",
        "Задний двор",
        "Задний двор: женский санузел",
        "Задний двор: мужской санузел",
    ):
        assert title in titles

    page = client.get("/")
    assert "Передний двор" in page.text
    assert "Задний двор" in page.text
    assert 'value="yard-back"' in page.text
    assert "Главный корпус" not in page.text


def test_unknown_qr_shows_error_and_manual_choice(client: TestClient):
    response = client.get("/?qr=does-not-exist")
    assert response.status_code == 200
    assert "QR-код не распознан" in response.text
    assert 'name="location_id"' in response.text


def test_qr_png_is_generated(client: TestClient):
    response = client.get("/qr/room214.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")


def test_unknown_qr_png_returns_404(client: TestClient):
    assert client.get("/qr/no-such-room.png").status_code == 404


def test_locations_page_requires_login(client: TestClient):
    logout(client)
    response = client.get("/staff/locations", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_staff_cannot_create_location(client: TestClient):
    logout(client)
    login(client, "staff", "staff123")
    response = client.post(
        "/staff/locations",
        data={
            "floor": "1",
            "room": "900",
            "qr_key": "staffroom900",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403
    logout(client)


def test_admin_can_create_location_and_duplicate_is_rejected(client: TestClient):
    logout(client)
    login(client)
    payload = {
        "floor": "1",
        "room": "900",
        "zone_name": "",
        "qr_key": "testroom900",
    }
    response = client.post("/staff/locations", data=payload, follow_redirects=False)
    assert response.status_code == 303
    assert get_location("testroom900") is not None

    duplicate = client.post("/staff/locations", data=payload)
    assert duplicate.status_code == 400
    assert "уже используется" in duplicate.text

    page = client.get("/staff/locations")
    assert "testroom900" in page.text
    assert "/qr/testroom900.png" in page.text


def test_admin_can_change_qr_binding_and_old_key_stops_working(client: TestClient):
    location = get_location("testroom900")
    assert location is not None

    response = client.post(
        f"/staff/locations/{location.id}",
        data={
            "floor": "2",
            "room": "901",
            "zone_name": "",
            "qr_key": "testroom901",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    old_qr = client.get("/?qr=testroom900")
    assert "QR-код не распознан" in old_qr.text

    new_qr = client.get("/?qr=testroom901")
    assert "QR-код распознан" in new_qr.text
    assert "каб. 901" in new_qr.text


def test_invalid_location_id_is_form_error_not_500(client: TestClient):
    response = client.post(
        "/tickets",
        data={
            "description": "Проверка ошибки выбора помещения",
            "location_id": "not-a-number",
            "category_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Выберите этаж или территорию и помещение" in response.text


def test_valid_ticket_is_created_from_qr_room(client: TestClient):
    location = get_location("room305")
    assert location is not None
    response = client.post(
        "/tickets",
        data={
            "description": "В кабинете 305 не работает проектор",
            "location_id": str(location.id),
            "category_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/success?number=")
