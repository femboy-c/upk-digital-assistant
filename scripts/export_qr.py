#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Выгружает PNG-файлы QR-кодов для всех помещений из базы.

Примеры:
    .venv/bin/python scripts/export_qr.py
    .venv/bin/python scripts/export_qr.py --base-url http://192.168.1.50:8000
"""
import argparse
import csv
import html
import sys
from pathlib import Path
from urllib.parse import quote

import qrcode
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import BASE_DIR, PUBLIC_BASE_URL
from app.database import SessionLocal
from app.models import Location


def build_page(rows: list[dict], base_url: str) -> str:
    cards = "\n".join(
        f"""
        <article class="card">
          <h2>{html.escape(row["title"])}</h2>
          <p class="muted">{html.escape(row["details"])}</p>
          <img src="{html.escape(row["file"])}" alt="QR-код: {html.escape(row["title"])}">
          <p class="key">{html.escape(row["qr_key"])}</p>
          <p class="url">{html.escape(row["url"])}</p>
        </article>"""
        for row in rows
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QR-коды помещений Колледжа ДГУ</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #172033; background: #f3f6fc; }}
    h1 {{ margin-bottom: 4px; }}
    .muted {{ color: #667085; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }}
    .card {{ background: #fff; border: 1px solid #e2e8f3; border-radius: 14px; padding: 16px; text-align: center; }}
    .card h2 {{ font-size: 17px; margin: 0 0 4px; }}
    .card img {{ width: 190px; height: 190px; }}
    .key {{ font-family: ui-monospace, monospace; font-weight: 600; }}
    .url {{ color: #667085; font-size: 12px; word-break: break-all; }}
    @media print {{
      body {{ background: #fff; margin: 8mm; }}
      .grid {{ grid-template-columns: 1fr 1fr; }}
      .card {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <h1>QR-коды помещений Колледжа ДГУ</h1>
  <p class="muted">Базовый адрес: {html.escape(base_url)}</p>
  <div class="grid">{cards}
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка QR-кодов помещений")
    parser.add_argument(
        "--base-url",
        default=PUBLIC_BASE_URL or "http://127.0.0.1:8000",
        help="Адрес сайта, который попадёт в QR (по умолчанию PUBLIC_BASE_URL)",
    )
    parser.add_argument(
        "--out",
        default=str(BASE_DIR / "docs" / "qr"),
        help="Папка для PNG и index.html",
    )
    args = parser.parse_args()

    base_url = args.base_url.strip().rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        locations = db.scalars(
            select(Location).order_by(Location.floor, Location.room, Location.zone_name)
        ).all()

    if not locations:
        raise SystemExit("В базе нет помещений. Запустите приложение, чтобы создать демо-данные.")

    rows: list[dict] = []
    for location in locations:
        url = f"{base_url}/?qr={quote(location.qr_key)}"
        filename = f"{location.qr_key}.png"
        qrcode.make(url).save(out_dir / filename)
        details = " · ".join(
            part
            for part in [
                location.floor_label,
                f"каб. {location.room}" if location.room else "",
                location.zone_name,
            ]
            if part
        )
        rows.append(
            {
                "qr_key": location.qr_key,
                "title": location.title,
                "details": details,
                "file": filename,
                "url": url,
            }
        )

    (out_dir / "index.html").write_text(build_page(rows, base_url), encoding="utf-8")
    with (out_dir / "qr-codes.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["qr_key", "title", "url", "png"])
        for row in rows:
            writer.writerow([row["qr_key"], row["title"], row["url"], row["file"]])

    print(f"Готово: {len(rows)} QR-кодов в {out_dir}")
    print(f"Базовый адрес: {base_url}")
    print("Для смартфона запустите с --base-url https://IP-компьютера:8443")


if __name__ == "__main__":
    main()
