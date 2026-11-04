# -*- coding: utf-8 -*-
"""Журнал аудита изменений помещений и QR-кодов."""
import json
import os
import tempfile
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-audit-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-audit-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Location


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = "admin123") -> None:
    response = client.post(
        "/login",
        data={"login": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client: TestClient) -> None:
    client.post("/logout", follow_redirects=False)


def location_by_key(qr_key: str) -> Location | None:
    with SessionLocal() as db:
        return db.scalar(select(Location).where(Location.qr_key == qr_key))


def test_location_create_and_update_are_logged(client: TestClient):
    qr_key = f"audit-{uuid.uuid4().hex[:8]}"
    new_key = f"audit-{uuid.uuid4().hex[:8]}"
    logout(client)
    login(client)

    created = client.post(
        "/staff/locations",
        data={"floor": "1", "room": "777", "zone_name": "", "qr_key": qr_key},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = location_by_key(qr_key)
    assert location is not None

    with SessionLocal() as db:
        create_entry = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "location.create",
                AuditLog.entity_id == location.id,
            )
        )
        assert create_entry is not None
        assert create_entry.actor.login == "admin"

    updated = client.post(
        f"/staff/locations/{location.id}",
        data={"floor": "2", "room": "778", "zone_name": "", "qr_key": new_key},
        follow_redirects=False,
    )
    assert updated.status_code == 303

    with SessionLocal() as db:
        update_entry = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "location.update",
                AuditLog.entity_id == location.id,
            )
        )
        assert update_entry is not None
        details = json.loads(update_entry.details)
        changes = details["changes"]
        assert changes["qr_key"]["old"] == qr_key
        assert changes["qr_key"]["new"] == new_key
        assert changes["room"]["new"] == "778"


def test_noop_location_update_does_not_create_audit_entry(client: TestClient):
    qr_key = f"same-{uuid.uuid4().hex[:8]}"
    login(client)
    assert (
        client.post(
            "/staff/locations",
            data={"floor": "1", "room": "779", "zone_name": "", "qr_key": qr_key},
            follow_redirects=False,
        ).status_code
        == 303
    )
    location = location_by_key(qr_key)
    assert location is not None

    with SessionLocal() as db:
        before = db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "location.update",
                AuditLog.entity_id == location.id,
            )
        )

    response = client.post(
        f"/staff/locations/{location.id}",
        data={
            "floor": location.floor,
            "room": location.room,
            "zone_name": location.zone_name,
            "qr_key": location.qr_key,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        after = db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "location.update",
                AuditLog.entity_id == location.id,
            )
        )
    assert after == before


def test_audit_page_is_admin_only(client: TestClient):
    logout(client)
    login(client, "staff", "staff123")
    forbidden = client.get("/staff/audit")
    assert forbidden.status_code == 403
    logout(client)

    login(client)
    page = client.get("/staff/audit")
    assert page.status_code == 200
    assert "Журнал аудита" in page.text
    assert "Изменение помещения/QR" in page.text
    assert "Подробности" in page.text
    logout(client)
