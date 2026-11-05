# -*- coding: utf-8 -*-
"""Несколько фотографий к заявке: проверка, сжатие и хранение."""
import io
import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-photos-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-photos-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.config import MAX_PHOTO_DIMENSION, UPLOAD_DIR
from app.database import SessionLocal
from app.main import app
from app.models import Attachment, Location, Ticket


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _image_bytes(size=(2400, 1800), fmt="JPEG", mode="RGB", color=(120, 80, 40)):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _location_id() -> int:
    with SessionLocal() as db:
        location = db.scalar(select(Location).where(Location.qr_key == "room214"))
        assert location is not None
        return location.id


def _post(client: TestClient, files, data=None):
    payload = {"description": f"Фото-тест {uuid.uuid4().hex[:8]}", "location_id": str(_location_id())}
    if data:
        payload.update(data)
    return client.post("/tickets", data=payload, files=files, follow_redirects=False)


def test_multiple_photos_are_saved_and_compressed(client: TestClient):
    large = _image_bytes((2600, 2000), "JPEG")
    small_png = _image_bytes((300, 200), "PNG", "RGBA", (0, 0, 0, 0))
    response = _post(
        client,
        [
            ("photos", ("large.jpg", large, "image/jpeg")),
            ("photos", ("small.png", small_png, "image/png")),
        ],
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    number = query["number"][0]

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        attachments = db.scalars(
            select(Attachment).where(Attachment.ticket_id == ticket.id)
        ).all()
        assert len(attachments) == 2
        keys = [item.storage_key for item in attachments]

    for key in keys:
        path = UPLOAD_DIR / key
        assert path.exists()
        assert path.stat().st_size < len(large)
        with Image.open(path) as image:
            assert image.format == "JPEG"
            assert max(image.size) <= MAX_PHOTO_DIMENSION
            assert "exif" not in image.info


def test_too_many_photos_are_rejected_without_ticket(client: TestClient):
    photo = _image_bytes((20, 20))
    with SessionLocal() as db:
        before = db.scalar(select(func.count(Ticket.id)))

    files = [
        ("photos", (f"photo-{index}.jpg", photo, "image/jpeg"))
        for index in range(6)
    ]
    response = _post(client, files)
    assert response.status_code == 400
    assert "не больше 5" in response.text

    with SessionLocal() as db:
        after = db.scalar(select(func.count(Ticket.id)))
    assert after == before


def test_non_image_upload_is_rejected(client: TestClient):
    response = _post(
        client,
        [("photos", ("note.txt", b"not an image", "image/jpeg"))],
    )
    assert response.status_code == 400
    assert "не похож на изображение" in response.text


def test_legacy_single_photo_field_still_works(client: TestClient):
    response = _post(
        client,
        [("photo", ("legacy.jpg", _image_bytes((400, 300)), "image/jpeg"))],
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    number = query["number"][0]

    with SessionLocal() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.public_number == number))
        assert ticket is not None
        count = db.scalar(
            select(func.count(Attachment.id)).where(Attachment.ticket_id == ticket.id)
        )
    assert count == 1
