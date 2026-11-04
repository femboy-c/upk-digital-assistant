#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Создаёт сотрудника или администратора Колледжа ДГУ с хешированным паролем."""
import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, engine
from app.models import Base, User
from app.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание пользователя Колледжа ДГУ")
    parser.add_argument("--login", required=True, help="логин без пробелов")
    parser.add_argument("--name", required=True, help="отображаемое имя")
    parser.add_argument("--role", choices=("staff", "admin"), default="staff")
    args = parser.parse_args()

    login = args.login.strip().lower()
    if not login or any(char.isspace() for char in login):
        raise SystemExit("Логин не должен быть пустым или содержать пробелы.")

    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.login == login)) is not None:
            raise SystemExit(f"Пользователь {login!r} уже существует.")
        password = getpass.getpass("Пароль: ")
        repeat = getpass.getpass("Повторите пароль: ")
        if len(password) < 8:
            raise SystemExit("Пароль должен содержать минимум 8 символов.")
        if password != repeat:
            raise SystemExit("Пароли не совпадают.")

        db.add(
            User(
                name=args.name.strip(),
                login=login,
                role=args.role,
                password_hash=hash_password(password),
            )
        )
        db.commit()

    print(f"Создан пользователь {login} с ролью {args.role}.")


if __name__ == "__main__":
    main()
