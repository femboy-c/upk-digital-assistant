# -*- coding: utf-8 -*-
"""Опциональные Redis-лимиты и проверка вложений ClamAV."""
import io
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-int-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-int-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import fakeredis
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app import ratelimit
from app.antivirus import AntivirusError, VirusFoundError
from app.database import SessionLocal
from app.main import app
from app.models import Location, Ticket


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _location_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def test_redis_backend_limits_and_deletes(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(ratelimit, "_redis", lambda: fake)
    key = f"test:{uuid.uuid4().hex}"

    assert ratelimit._is_limited_backend(key, 2, 60) is False
    ratelimit._register_event_backend(key, 60)
    assert ratelimit._is_limited_backend(key, 2, 60) is False
    ratelimit._register_event_backend(key, 60)
    assert ratelimit._is_limited_backend(key, 2, 60) is True

    ratelimit.delete_key(key)
    assert ratelimit._is_limited_backend(key, 2, 60) is False


def test_virus_file_is_rejected(client: TestClient, monkeypatch):
    with SessionLocal() as db:
        before = db.scalar(select(func.count(Ticket.id)))

    def found(data):
        raise VirusFoundError("EICAR")

    monkeypatch.setattr("app.routes.public.scan_bytes", found)
    response = client.post(
        "/tickets",
        data={
            "description": "Проверка антивируса: тестовый файл",
            "location_id": str(_location_id()),
        },
        files=[("photos", ("test.jpg", _image(), "image/jpeg"))],
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "не прошёл проверку безопасности" in response.text

    with SessionLocal() as db:
        assert db.scalar(select(func.count(Ticket.id))) == before


def test_scanner_unavailable_is_fail_closed(client: TestClient, monkeypatch):
    def unavailable(data):
        raise AntivirusError("down")

    monkeypatch.setattr("app.routes.public.scan_bytes", unavailable)
    response = client.post(
        "/tickets",
        data={
            "description": "Проверка недоступного антивируса",
            "location_id": str(_location_id()),
        },
        files=[("photos", ("test.jpg", _image(), "image/jpeg"))],
        follow_redirects=False,
    )
    assert response.status_code == 503
    assert "Проверка вложений временно недоступна" in response.text
