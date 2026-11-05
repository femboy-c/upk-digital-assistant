# -*- coding: utf-8 -*-
"""CSP с nonce, защитные заголовки и обновление старых хешей паролей."""
import hashlib
import os
import re
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-sec-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-sec-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import BASE_DIR
from app.database import SessionLocal
from app.main import app
from app.models import User
from app.ratelimit import client_ip
from app.security import (
    LEGACY_PBKDF2_ROUNDS,
    PBKDF2_ROUNDS,
    hash_password,
    verify_password,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_csp_allows_scripts_only_by_nonce(client: TestClient):
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)
    assert match is not None
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]

    nonce = match.group(1)
    assert f'<script nonce="{nonce}">' in page.text
    assert f'application/ld+json" nonce="{nonce}"' in page.text


def test_csp_nonce_is_different_on_each_request(client: TestClient):
    first = client.get("/").headers["content-security-policy"]
    second = client.get("/").headers["content-security-policy"]
    assert first != second


def test_additional_security_headers(client: TestClient):
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["x-permitted-cross-domain-policies"] == "none"
    assert "geolocation=()" in headers["permissions-policy"]


def test_personal_pages_are_not_indexed_or_cached(client: TestClient):
    page = client.get("/my")
    assert page.headers["x-robots-tag"].startswith("noindex")
    assert "no-store" in page.headers["cache-control"]


def test_templates_have_no_inline_event_handlers():
    pattern = re.compile(r"\son[a-z]+=", re.IGNORECASE)
    for path in (BASE_DIR / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"inline event handler in {path}"


def test_legacy_password_hash_is_rehashed_on_login(client: TestClient):
    login = f"legacy-{uuid.uuid4().hex[:8]}"
    password = "legacy-password-123"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), LEGACY_PBKDF2_ROUNDS
    )
    with SessionLocal() as db:
        db.add(
            User(
                name="Старый хеш",
                login=login,
                role="staff",
                password_hash=f"{salt}${digest.hex()}",
            )
        )
        db.commit()

    response = client.post(
        "/login",
        data={"login": login, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == login))
        assert user is not None
        assert user.password_hash.startswith(f"pbkdf2_sha256${PBKDF2_ROUNDS}$")
        assert verify_password(password, user.password_hash)

    client.post("/logout", follow_redirects=False)


class FakeRequest:
    def __init__(self, headers, host="10.0.0.1"):
        self.headers = headers
        self.client = type("Client", (), {"host": host})()


def test_client_ip_prefers_cloudflare_header(monkeypatch):
    monkeypatch.setattr("app.ratelimit.TRUST_PROXY", True)
    request = FakeRequest(
        {
            "cf-connecting-ip": "203.0.113.5",
            "x-forwarded-for": "1.2.3.4",
        }
    )
    assert client_ip(request) == "203.0.113.5"

    request = FakeRequest({"x-forwarded-for": "198.51.100.7, 10.0.0.1"})
    assert client_ip(request) == "198.51.100.7"

    request = FakeRequest({"x-forwarded-for": "not-an-ip"}, host="10.0.0.9")
    assert client_ip(request) == "10.0.0.9"


def _import_app(overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_production_requires_secure_cookie():
    result = _import_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x" * 48,
            "SESSION_HTTPS_ONLY": "0",
            "PUBLIC_BASE_URL": "https://upk.test",
        }
    )
    assert result.returncode != 0
    assert "SESSION_HTTPS_ONLY" in result.stderr


def test_production_requires_https_public_url():
    result = _import_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "x" * 48,
            "SESSION_HTTPS_ONLY": "1",
            "PUBLIC_BASE_URL": "http://upk.test",
        }
    )
    assert result.returncode != 0
    assert "PUBLIC_BASE_URL" in result.stderr


def test_production_requires_long_secret_key():
    result = _import_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "short",
            "SESSION_HTTPS_ONLY": "1",
            "PUBLIC_BASE_URL": "https://upk.test",
        }
    )
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_short_or_tampered_hash_is_rejected():
    assert not verify_password("secret", "not-a-hash")
    salt = secrets.token_hex(16)
    expected = hashlib.pbkdf2_hmac("sha256", b"secret", salt.encode(), 1000).hex()
    assert not verify_password("secret", f"pbkdf2_sha256$999999999${salt}${expected}")
    assert verify_password("secret", hash_password("secret"))
