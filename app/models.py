# -*- coding: utf-8 -*-
"""
Модели — описание таблиц базы данных.

Каждый класс = одна таблица. Каждое поле = колонка.
Состав таблиц взят из раздела 10 технического задания.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import FLOOR_LABELS
from .database import Base

# Все возможные статусы заявки (раздел 7 ТЗ).
STATUSES = [
    "Новая",
    "На рассмотрении",
    "Назначен исполнитель",
    "В работе",
    "Нужны уточнения",
    "Решена",
    "Отклонена",
]


class User(Base):
    """Пользователи: заявители, сотрудники колледжа, администраторы."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    login: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="staff")  # staff / admin
    password_hash: Mapped[str] = mapped_column(String(255))
    # Отключённый пользователь не может войти, но его история сохраняется.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Растёт при смене пароля. Старые cookie-сессии после этого недействительны.
    session_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Category(Base):
    """Справочник категорий обращений."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class Location(Base):
    """Помещения. qr_key — то, что зашито в QR-код."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Устаревшее служебное поле: в колледже один корпус, в интерфейсе не используется.
    building: Mapped[str] = mapped_column(String(80), default="")
    floor: Mapped[str] = mapped_column(String(20), default="")
    room: Mapped[str] = mapped_column(String(40), default="")
    zone_name: Mapped[str] = mapped_column(String(120), default="")
    qr_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)

    @property
    def floor_label(self) -> str:
        """Человеческое название этажа или территории."""
        return FLOOR_LABELS.get(self.floor, f"{self.floor} этаж" if self.floor else "")

    @property
    def title(self) -> str:
        """Название для интерфейса: зона, либо этаж/территория и кабинет."""
        if self.zone_name:
            return self.zone_name
        parts = [
            self.floor_label,
            f"каб. {self.room}" if self.room else "",
        ]
        return ", ".join(p for p in parts if p)


class Ticket(Base):
    """Заявка — главная сущность системы."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="Новая", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="Обычный")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tracking_token_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Срок жизни ссылки и отметка об отзыве. None = бессрочно / не отозвана.
    tracking_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tracking_revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    category = relationship("Category")
    location = relationship("Location")
    assignee = relationship("User", foreign_keys=[assignee_id])
    comments = relationship("Comment", back_populates="ticket", cascade="all, delete-orphan")
    history = relationship("TicketHistory", back_populates="ticket", cascade="all, delete-orphan")

    @property
    def tracking_state(self) -> str:
        """Состояние ссылки: active / revoked / expired."""
        if self.tracking_revoked_at is not None:
            return "revoked"
        if self.tracking_expires_at is not None and self.tracking_expires_at < datetime.now():
            return "expired"
        return "active"

    @property
    def tracking_is_active(self) -> bool:
        return self.tracking_state == "active"


class Comment(Base):
    """Комментарии к заявке. visibility=internal видит только сотрудник."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(20), default="public")  # public / internal
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    ticket = relationship("Ticket", back_populates="comments")
    author = relationship("User")


class TicketHistory(Base):
    """История изменений: кто, когда и какой статус поставил."""

    __tablename__ = "ticket_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    old_status: Mapped[str] = mapped_column(String(40), default="")
    new_status: Mapped[str] = mapped_column(String(40), default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    ticket = relationship("Ticket", back_populates="history")
    actor = relationship("User")


class Attachment(Base):
    """Файлы (фотографии), прикреплённые к заявке."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    storage_key: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class KnowledgeBase(Base):
    """База знаний для ИИ-помощника (раздел 8.2 ТЗ)."""

    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AuditLog(Base):
    """Журнал действий: помещения/QR, пользователи, статусы, ссылки.

    actor_id обнуляется при удалении пользователя, чтобы журнал не пропадал
    и не мешал удалять учётные записи без связанных заявок.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    actor = relationship("User")
