# -*- coding: utf-8 -*-
"""Двухфакторный вход: настройка, вход по коду, повторное использование и сброс."""
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-mfa-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-mfa-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import BASE_DIR
from app.database import SessionLocal
from app.main import app
from app.mfa import TOTP_STEP, new_totp_secret, totp_code, verify_totp
from app.models import User
from app.security import hash_password

PASSWORD = "mfa-password-123"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def make_user() -> str:
    login = f"mfa-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(
            User(
                name="Тест MFA",
                login=login,
                role="admin",
                password_hash=hash_password(PASSWORD),
            )
        )
        db.commit()
    return login


def login_password(client: TestClient, username: str):
    return client.post(
        "/login",
        data={"login": username, "password": PASSWORD},
        follow_redirects=False,
    )


def secret_from_page(text: str) -> str:
    match = re.search(r'id="mfa-secret">([^<]+)<', text)
    assert match is not None
    return match.group(1).strip()


def test_totp_roundtrip_and_replay_window():
    secret = new_totp_secret()
    now = 1_700_000_000
    code = totp_code(secret, now)
    assert len(code) == 6 and code.isdigit()
    assert verify_totp(secret, code, timestamp=now) == int(now // TOTP_STEP)
    assert verify_totp(secret, "000000", timestamp=now) is None
    assert verify_totp(secret, totp_code(secret, now + TOTP_STEP), timestamp=now) is not None


def test_mfa_setup_login_replay_and_disable(client: TestClient):
    username = make_user()

    assert login_password(client, username).status_code == 303
    page = client.get("/staff/mfa")
    assert page.status_code == 200
    secret = secret_from_page(page.text)

    qr = client.get("/staff/mfa/qr")
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/png")

    code = totp_code(secret)
    enabled = client.post(
        "/staff/mfa/enable",
        data={"code": code},
        follow_redirects=False,
    )
    assert enabled.status_code == 303

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        assert user.totp_enabled is True
        assert user.totp_secret == secret

    client.post("/logout", follow_redirects=False)

    # Пароль принят, но панель ещё недоступна.
    response = login_password(client, username)
    assert response.status_code == 303
    assert response.headers["location"] == "/login/mfa"
    assert client.get("/staff", follow_redirects=False).status_code == 303

    # Неверный код не пускает.
    bad = client.post("/login/mfa", data={"code": "000000"}, follow_redirects=False)
    assert bad.status_code == 401
    assert client.get("/staff", follow_redirects=False).status_code == 303

    # Следующее окно после включения подходит.
    login_code = totp_code(secret, time.time() + TOTP_STEP)
    good = client.post("/login/mfa", data={"code": login_code}, follow_redirects=False)
    assert good.status_code == 303
    assert good.headers["location"] == "/staff"
    assert client.get("/staff").status_code == 200

    # Повторное использование того же кода запрещено.
    client.post("/logout", follow_redirects=False)
    login_password(client, username)
    replay = client.post("/login/mfa", data={"code": login_code}, follow_redirects=False)
    assert replay.status_code == 401

    # После сброса счётчика тем же кодом снова можно войти (проверка disable).
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        user.totp_last_counter = 0
        db.commit()
    good = client.post("/login/mfa", data={"code": login_code}, follow_redirects=False)
    assert good.status_code == 303
    assert client.get("/staff").status_code == 200

    # Отключение требует пароль и свежий код.
    wrong_password = client.post(
        "/staff/mfa/disable",
        data={"password": "wrong", "code": totp_code(secret)},
        follow_redirects=False,
    )
    assert wrong_password.status_code == 303
    assert "error" in wrong_password.headers["location"]

    # Сбрасываем счётчик окна, чтобы не ждать 30 секунд в тесте.
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        user.totp_last_counter = 0
        db.commit()

    disable_code = totp_code(secret)
    disabled = client.post(
        "/staff/mfa/disable",
        data={"password": PASSWORD, "code": disable_code},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    assert "saved=1" in disabled.headers["location"]

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        assert user.totp_enabled is False
        assert user.totp_secret == ""

    client.post("/logout", follow_redirects=False)


def test_reset_mfa_script(client: TestClient):
    username = make_user()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        user.totp_enabled = True
        user.totp_secret = new_totp_secret()
        db.commit()

    result = subprocess.run(
        [sys.executable, "scripts/reset_mfa.py", "--login", username],
        cwd=BASE_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == username))
        assert user is not None
        assert user.totp_enabled is False
        assert user.totp_secret == ""
