# -*- coding: utf-8 -*-
"""Очистка файлов, на которые нет ссылок в базе."""
import os
import subprocess
import sys
import tempfile
import time
import uuid

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-orphan-test-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-orphan-uploads-"))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")

from app.config import BASE_DIR, UPLOAD_DIR
from app.database import SessionLocal
from app.migrations import ensure_schema
from app.models import Attachment, Ticket

ensure_schema()


def test_orphan_file_is_removed_and_referenced_file_kept():
    suffix = uuid.uuid4().hex[:8]
    referenced_name = f"referenced-{suffix}.jpg"
    orphan_name = f"orphan-{suffix}.jpg"
    referenced = UPLOAD_DIR / referenced_name
    orphan = UPLOAD_DIR / orphan_name
    referenced.write_bytes(b"referenced")
    orphan.write_bytes(b"orphan")

    old = time.time() - 48 * 3600
    os.utime(referenced, (old, old))
    os.utime(orphan, (old, old))

    with SessionLocal() as db:
        ticket = Ticket(
            public_number=f"ORPHAN-{suffix}",
            title="Проверка очистки",
            description="Файл используется",
            tracking_token_hash=uuid.uuid4().hex,
        )
        db.add(ticket)
        db.flush()
        db.add(
            Attachment(
                ticket_id=ticket.id,
                storage_key=referenced_name,
                file_type="image/jpeg",
            )
        )
        db.commit()

    result = subprocess.run(
        [sys.executable, "scripts/cleanup_orphans.py"],
        cwd=BASE_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    assert referenced.exists()
    assert not orphan.exists()

    referenced.unlink(missing_ok=True)
