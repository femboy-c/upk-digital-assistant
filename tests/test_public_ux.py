# -*- coding: utf-8 -*-
"""Публичный UX: подсказки формы, полная ссылка и прогресс статуса."""
import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-ux-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-ux-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Location


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _create_ticket(client: TestClient) -> tuple[str, str]:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        location_id = location.id

    description = f"UX-тест: не работает лампа {uuid.uuid4().hex[:6]}"
    response = client.post(
        "/tickets",
        data={"description": description, "location_id": str(location_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["number"][0], query["token"][0]


def test_report_page_has_guidance_without_demo_label(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200
    text = page.text.lower()
    assert "демонстрация" not in text
    assert "демонстрационная" not in text
    assert "как это работает" in text
    assert 'id="description-count"' in page.text
    assert 'id="photo-preview"' in page.text
    assert 'id="draft-restored"' in page.text
    assert "в течение рабочего дня" in text


def test_success_page_has_full_link_and_copy_button(client: TestClient):
    _, token = _create_ticket(client)
    page = client.get(f"/success?number=ДГУ-2026-000001&token={token}")
    assert page.status_code == 200
    assert f"http://upk.test/t/{token}" in page.text
    assert 'id="copy-link"' in page.text
    assert "Скопировать ссылку" in page.text


def test_track_page_shows_status_steps(client: TestClient):
    _, token = _create_ticket(client)
    page = client.get(f"/t/{token}")
    assert page.status_code == 200
    assert "status-steps" in page.text
    assert "Заявка получена" in page.text
    assert "Исполнитель назначен" in page.text
    assert "Решена" in page.text
    assert 'aria-current="step"' in page.text


def test_track_page_has_no_stepper_for_rejected_ticket(client: TestClient):
    from app.models import Ticket

    number, token = _create_ticket(client)
    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        ticket.status = "Отклонена"
        db.commit()

    page = client.get(f"/t/{token}")
    assert page.status_code == 200
    assert "Заявка отклонена" in page.text
    assert "status-steps" not in page.text
