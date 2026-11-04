#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Удаляет закрытые заявки, срок хранения которых истёк.

По умолчанию ничего не удаляет, а только показывает список. Для удаления
запустите с --apply. Перед запуском сделайте бэкап.

Примеры:
    .venv/bin/python scripts/cleanup_old_tickets.py
    .venv/bin/python scripts/cleanup_old_tickets.py --apply
    .venv/bin/python scripts/cleanup_old_tickets.py --days 180 --apply
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import TICKET_RETENTION_DAYS
from app.database import SessionLocal
from app.retention import delete_ticket_with_files, find_expired_tickets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Удаление заявок по сроку хранения персональных данных"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=TICKET_RETENTION_DAYS,
        help=f"срок хранения в днях (по умолчанию {TICKET_RETENTION_DAYS})",
    )
    parser.add_argument(
        "--include-open",
        action="store_true",
        help="удалять также незакрытые заявки (по умолчанию только закрытые)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="действительно удалить заявки (без флага — только показать список)",
    )
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("Срок хранения должен быть не меньше 1 дня.")

    with SessionLocal() as db:
        tickets = find_expired_tickets(
            db,
            days=args.days,
            include_open=args.include_open,
        )

        if not tickets:
            print(f"Заявок старше {args.days} дней не найдено.")
            return

        print(f"Найдено заявок: {len(tickets)} (срок {args.days} дней)")
        for ticket in tickets:
            print(
                f"  {ticket.public_number} · {ticket.status} · "
                f"обновлена {ticket.updated_at:%d.%m.%Y %H:%M}"
            )

        if not args.apply:
            print("\nЭто режим проверки. Ничего не удалено.")
            print("Для удаления запустите с --apply после бэкапа:")
            print("  .venv/bin/python scripts/backup.py")
            print("  .venv/bin/python scripts/cleanup_old_tickets.py --apply")
            return

        removed = 0
        for ticket in tickets:
            delete_ticket_with_files(db, ticket)
            removed += 1
        print(f"\nУдалено заявок: {removed}.")


if __name__ == "__main__":
    main()
