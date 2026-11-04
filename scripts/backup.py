#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Создаёт ZIP-архив с базой и вложениями.

Пример:
    .venv/bin/python scripts/backup.py --keep 14
"""
import argparse
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import UPLOAD_DIR
from app.database import DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Резервная копия базы и вложений Колледжа ДГУ")
    parser.add_argument("--out", default=str(ROOT / "backups"), help="папка для архивов")
    parser.add_argument("--keep", type=int, default=14, help="сколько архивов хранить")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        raise SystemExit(f"База не найдена: {DB_PATH}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = out_dir / f"upk-backup-{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="upk-backup-") as temp_dir:
        db_copy = Path(temp_dir) / "upk.db"
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(db_copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(db_copy, "data/upk.db")
            if UPLOAD_DIR.is_dir():
                for file in UPLOAD_DIR.glob("*"):
                    if file.is_file():
                        bundle.write(file, f"data/uploads/{file.name}")

    # В архиве лежат база и фотографии — закрываем его от других пользователей.
    try:
        archive.chmod(0o600)
    except OSError:
        pass

    old_archives = sorted(out_dir.glob("upk-backup-*.zip"))
    for old in old_archives[: max(0, len(old_archives) - args.keep)]:
        old.unlink(missing_ok=True)

    print(f"Резервная копия создана: {archive}")
    print(f"Хранится архивов: {min(len(old_archives), args.keep)}")


if __name__ == "__main__":
    main()
