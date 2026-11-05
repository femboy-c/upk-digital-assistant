# -*- coding: utf-8 -*-
"""SEO-основа: заголовки, canonical, sitemap и мобильный вход."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-seo-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-seo-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_home_has_search_metadata(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>Жалобы и заявки — Колледж ДГУ</title>" in page.text
    assert "жалобы ДГУ" in page.text
    assert 'rel="canonical" href="http://upk.test/"' in page.text
    assert 'property="og:url" content="http://upk.test/"' in page.text
    assert 'application/ld+json' in page.text
    assert 'class="nav-btn mobile-login"' in page.text
    assert 'class="nav-btn login-desktop"' in page.text


def test_robots_and_sitemap(client: TestClient):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /staff" in robots.text
    assert "Sitemap: http://upk.test/sitemap.xml" in robots.text

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "<loc>http://upk.test/</loc>" in sitemap.text
    assert "<loc>http://upk.test/faq</loc>" in sitemap.text
    assert "<loc>http://upk.test/privacy</loc>" in sitemap.text


def test_logged_in_user_has_no_login_buttons(client: TestClient):
    response = client.post(
        "/login",
        data={"login": "staff", "password": "staff123"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/")
    assert "mobile-login" not in page.text
    assert "login-desktop" not in page.text
    client.post("/logout", follow_redirects=False)
