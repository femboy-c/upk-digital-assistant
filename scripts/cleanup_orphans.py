#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Удаляет файлы вложений, на которые больше нет ссылок в базе.

Безопасно: трогает только файлы непосредственно в UPLOAD_DIR и только
старше заданного возраста, чтобы не удалить файл в момент загрузки.
"""
import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import UPLOAD_DIR
from app.database import SessionLocal
from app.models import Attachment


def main() -> None:
    parser = argparse.ArgumentParser(description="Очистка осиротевших вложений")
    parser.add_argument("--dry-run", action="store_true", help="только показать файлы")
    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=24,
        help="не трогать файлы младше N часов (по умолчанию 24)",
    )
    args = parser.parse_args()

    if not UPLOAD_DIR.exists():
        print("Каталог вложений отсутствует — чистить нечего.")
        return

    with SessionLocal() as db:
        referenced = {
            attachment.storage_key
            for attachment in db.scalars(select(Attachment)).all()
        }

    cutoff = time.time() - max(1, args.min_age_hours) * 3600
    removed = 0
    skipped = 0

    for path in UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name in referenced:
            continue
        try:
            if path.stat().st_mtime > cutoff:
                skipped += 1
                continue
            if args.dry_run:
                print(f"[dry-run] {path.name}")
            else:
                path.unlink()
                print(f"Удалён: {path.name}")
            removed += 1
        except OSError as error:
            print(f"Ошибка с файлом {path.name}: {error}", file=sys.stderr)

    action = "Найдено" if args.dry_run else "Удалено"
    print(f"{action} осиротевших файлов: {removed}. Пропущено свежих: {skipped}.")


if __name__ == "__main__":
    main()
