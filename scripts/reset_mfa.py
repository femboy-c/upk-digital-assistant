#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Сбрасывает двухфакторный вход пользователю (например, при потере телефона)."""
import argparse
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Сброс MFA пользователя Колледжа ДГУ")
    parser.add_argument("--login", required=True, help="логин пользователя")
    args = parser.parse_args()

    login = args.login.strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == login))
        if user is None:
            raise SystemExit(f"Пользователь {login!r} не найден.")

        user.totp_enabled = False
        user.totp_secret = ""
        user.totp_last_counter = 0
        user.session_version += 1
        db.commit()
        print(f"Двухфакторный вход для @{login} отключён. Старые сессии завершены.")


if __name__ == "__main__":
    main()
