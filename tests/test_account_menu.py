# -*- coding: utf-8 -*-
"""Меню аккаунта в шапке и страница настроек."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-menu-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-menu-uploads-"))
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


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"login": "staff", "password": "staff123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_anonymous_header_offers_login(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Вход для сотрудника" in response.text
    assert "Настройки аккаунта" not in response.text
    assert 'class="account-menu"' not in response.text


def test_account_menu_appears_after_login(client: TestClient):
    login(client)
    response = client.get("/staff")
    assert response.status_code == 200
    assert 'class="account-menu"' in response.text
    assert "Сотрудник колледжа" in response.text
    assert "@staff" in response.text
    assert 'href="/staff"' in response.text
    assert 'href="/staff/stats"' in response.text
    assert 'href="/staff/locations"' in response.text
    assert 'href="/staff/password"' in response.text
    assert 'action="/logout"' in response.text
    assert "Выйти" in response.text


def test_menu_is_visible_on_public_pages(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Настройки аккаунта" in response.text
    assert "@staff" in response.text


def test_settings_page_shows_account_and_password_form(client: TestClient):
    response = client.get("/staff/password")
    assert response.status_code == 200
    assert "Настройки аккаунта" in response.text
    assert "Сотрудник колледжа" in response.text
    assert "Смена пароля" in response.text
    assert 'action="/staff/password"' in response.text


def test_college_brand_is_visible(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Колледж ДГУ" in response.text
    assert "Колледж Дагестанского государственного университета" in response.text


def test_theme_toggle_is_available(client: TestClient):
    for path in ("/", "/login"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="theme-toggle"' in response.text
        assert "upk-theme" in response.text
        assert "theme-icon-moon" in response.text


def test_dark_theme_styles_are_served(client: TestClient):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert ':root[data-theme="dark"]' in response.text
    assert "--bg: #0b1220" in response.text
    assert ".theme-toggle" in response.text


def test_admin_menu_shows_admin_role(client: TestClient):
    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    login_as_admin = client.post(
        "/login",
        data={"login": "admin", "password": "admin123"},
        follow_redirects=False,
    )
    assert login_as_admin.status_code == 303
    response = client.get("/staff")
    assert "Администратор" in response.text
    assert "@admin" in response.text
