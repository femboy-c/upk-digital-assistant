# -*- coding: utf-8 -*-
"""Политика ПДн, согласие в форме и его фиксация в заявке."""
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-privacy-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-privacy-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("UPK_DISABLE_CONSENT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import PRIVACY_POLICY_VERSION, TICKET_RETENTION_DAYS
from app.database import SessionLocal
from app.main import app
from app.models import Location, Ticket


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def room214_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def ticket_data(description: str, **extra):
    data = {
        "description": description,
        "location_id": str(room214_id()),
        "category_id": "",
    }
    data.update(extra)
    return data


def test_privacy_policy_page(client: TestClient):
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "Политика обработки персональных данных" in response.text
    assert "Срок хранения" in response.text
    assert f"{TICKET_RETENTION_DAYS} дней" in response.text
    assert "Отозвать согласие" in response.text or "отозвать согласие" in response.text
    assert 'href="/privacy"' in response.text or "Политика" in response.text


def test_form_has_consent_checkbox_and_link(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="personal_data_consent"' in response.text
    assert 'href="/privacy"' in response.text
    assert "Я согласен на обработку персональных данных" in response.text


def test_ticket_without_consent_is_rejected_when_required(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_CONSENT", "0")
    description = f"Без согласия {uuid.uuid4().hex}"
    response = client.post(
        "/tickets",
        data=ticket_data(description),
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "согласие на обработку персональных данных" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(Ticket).where(Ticket.description == description)) is None


def test_ticket_with_consent_is_saved_with_version(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_CONSENT", "0")
    description = f"С согласием {uuid.uuid4().hex}"
    response = client.post(
        "/tickets",
        data=ticket_data(description, personal_data_consent="1"),
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.description == description))
        assert ticket is not None
        assert ticket.consent_at is not None
        assert ticket.consent_version == PRIVACY_POLICY_VERSION
