# -*- coding: utf-8 -*-
"""Production-seed, noindex, favicon, robots.txt и своя страница 404."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-hardening-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-hardening-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import seed
from app.database import Base
from app.main import app
from app.models import Category, Location, User
from app.ratelimit import reset_rate_limits
from app.routes import assistant as assistant_routes


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


def test_production_seed_does_not_create_demo_users(monkeypatch):
    memory_engine = create_engine("sqlite://")
    Base.metadata.create_all(memory_engine)
    Session = sessionmaker(bind=memory_engine)

    monkeypatch.setattr(seed, "IS_PRODUCTION", True)
    with Session() as db:
        seed.seed_database(db)
        assert db.scalar(select(User).limit(1)) is None
        # Справочники и база знаний нужны и в production.
        assert db.scalar(select(Category).limit(1)) is not None
        assert db.scalar(select(Location).limit(1)) is not None


def test_development_seed_still_creates_demo_users(monkeypatch):
    memory_engine = create_engine("sqlite://")
    Base.metadata.create_all(memory_engine)
    Session = sessionmaker(bind=memory_engine)

    monkeypatch.setattr(seed, "IS_PRODUCTION", False)
    with Session() as db:
        seed.seed_database(db)
        logins = set(db.scalars(select(User.login)).all())
    assert {"staff", "admin"} <= logins


def test_noindex_headers_on_private_pages(client: TestClient):
    for path in ("/login", "/track", "/t/not-a-real-token"):
        response = client.get(path, follow_redirects=False)
        assert response.headers.get("x-robots-tag") == "noindex, nofollow", path

    panel = client.get("/staff", follow_redirects=False)
    assert panel.status_code == 303
    assert panel.headers.get("x-robots-tag") == "noindex, nofollow"


def test_public_page_is_indexable(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "x-robots-tag" not in response.headers
    assert 'name="robots"' not in response.text


def test_private_pages_have_meta_noindex(client: TestClient):
    login(client)
    response = client.get("/staff")
    assert response.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in response.text


def test_robots_txt_disallows_private_paths(client: TestClient):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for path in ("/staff", "/login", "/track", "/t/", "/success", "/assistant"):
        assert f"Disallow: {path}" in response.text


def test_favicon_is_served(client: TestClient):
    response = client.get("/static/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_unknown_page_returns_html_404(client: TestClient):
    response = client.get("/definitely-not-a-real-page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Страница не найдена" in response.text


def test_unknown_api_path_returns_json_404(client: TestClient):
    response = client.get("/assistant/definitely-not-a-real-page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_assistant_rate_limit(client: TestClient, monkeypatch):
    monkeypatch.setenv("UPK_DISABLE_RATE_LIMIT", "0")
    monkeypatch.setattr(assistant_routes, "ASSISTANT_RATE_LIMIT", 2)
    reset_rate_limits()
    try:
        for _ in range(2):
            response = client.post(
                "/assistant/analyze",
                json={"text": "В кабинете не работает проектор"},
            )
            assert response.status_code == 200

        blocked = client.post(
            "/assistant/analyze",
            json={"text": "В кабинете не работает проектор"},
        )
        assert blocked.status_code == 429
        assert "Слишком много" in blocked.json()["detail"]
    finally:
        reset_rate_limits()
