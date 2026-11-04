# -*- coding: utf-8 -*-
"""Срок хранения и удаление старых заявок."""
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import retention
from app.database import Base
from app.models import Attachment, AuditLog, Ticket
from app.security import hash_token


def make_ticket(number: str, status: str, days_ago: int) -> Ticket:
    moment = datetime.now() - timedelta(days=days_ago)
    return Ticket(
        public_number=number,
        title="Тест хранения",
        description="Проверка срока хранения",
        status=status,
        tracking_token_hash=hash_token(number),
        created_at=moment,
        updated_at=moment,
    )


def test_only_old_closed_tickets_are_selected():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                make_ticket("OLD-CLOSED", "Решена", 400),
                make_ticket("OLD-OPEN", "В работе", 400),
                make_ticket("RECENT-CLOSED", "Отклонена", 10),
            ]
        )
        db.commit()

        found = retention.find_expired_tickets(db, days=365)
        assert [ticket.public_number for ticket in found] == ["OLD-CLOSED"]

        with_open = retention.find_expired_tickets(db, days=365, include_open=True)
        assert {ticket.public_number for ticket in with_open} == {
            "OLD-CLOSED",
            "OLD-OPEN",
        }


def test_delete_removes_ticket_attachment_and_file(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    stored = upload_dir / "photo.jpg"
    stored.write_bytes(b"test-image")
    monkeypatch.setattr(retention, "UPLOAD_DIR", upload_dir)

    with Session() as db:
        ticket = make_ticket("DELETE-OLD", "Решена", 400)
        db.add(ticket)
        db.flush()
        db.add(
            Attachment(
                ticket_id=ticket.id,
                storage_key=stored.name,
                file_type="image/jpeg",
            )
        )
        db.commit()

        retention.delete_ticket_with_files(db, ticket)

        assert db.get(Ticket, ticket.id) is None
        assert db.scalar(
            select(Attachment).where(Attachment.ticket_id == ticket.id)
        ) is None
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ticket.retention_delete",
                AuditLog.entity_id == ticket.id,
            )
        )
        assert audit is not None

    assert not stored.exists()
