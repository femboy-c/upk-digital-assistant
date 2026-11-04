# -*- coding: utf-8 -*-
"""Публичный FAQ и печатные QR-таблички."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-faq-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-faq-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def test_faq_page_is_public_and_uses_knowledge_base(client: TestClient):
    response = client.get("/faq")
    assert response.status_code == 200
    assert "Частые вопросы" in response.text
    assert "Как подключить ноутбук к проектору?" in response.text
    assert "Оборудование" in response.text
    assert "Источник:" in response.text
    assert 'href="/"' in response.text


def test_print_page_requires_login(client: TestClient):
    client.post("/logout", follow_redirects=False)
    response = client.get("/staff/locations/print", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_print_page_contains_qr_cards(client: TestClient):
    login(client)
    response = client.get("/staff/locations/print")
    assert response.status_code == 200
    assert "Печать QR-табличек" in response.text
    assert "каб. 101" in response.text
    assert "/qr/lab101.png" in response.text
    assert "Сообщите о проблеме" in response.text
    client.post("/logout", follow_redirects=False)
