# -*- coding: utf-8 -*-
"""
Подключение к базе данных.

База — SQLite, это просто файл data/upk.db.
SQLAlchemy — "переводчик" между Python-кодом и этим файлом.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Папка проекта (на уровень выше папки app/).
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Переменная UPK_DB_PATH позволяет подставить отдельную базу,
# например временную при запуске тестов.
DB_PATH = Path(os.getenv("UPK_DB_PATH", str(DATA_DIR / "upk.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# check_same_thread=False — разрешаем обращаться к SQLite из разных потоков.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Включает проверку внешних ключей и ожидание блокировки для каждого соединения.

    SQLite по умолчанию не проверяет FOREIGN KEY, поэтому осиротевшие
    комментарии и вложения могли бы остаться в базе незамеченными.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()

# Фабрика сессий: каждая сессия — "разговор" с базой.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц (models.py)."""


def restrict_data_permissions() -> None:
    """Закрывает рабочую базу от чтения другими пользователями системы.

    Временные базы тестов лежат вне data/, поэтому их права не трогаем.
    """
    if DB_PATH.parent != DATA_DIR:
        return
    try:
        DATA_DIR.chmod(0o700)
        if DB_PATH.exists():
            DB_PATH.chmod(0o600)
    except OSError:
        # Права не должны мешать запуску, например на нестандартной ФС.
        pass


def get_db():
    """FastAPI будет выдавать по одной сессии на каждый запрос."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
