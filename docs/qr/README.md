# QR-коды помещений

Сейчас в папке **55 QR-кодов**: все кабинеты 1–3 этажей, коридоры, УПК,
кафедра математических и естественных дисциплин, столовая, спортзал,
актовый зал, раздевалки, медпункт, санузлы и территории.

- `index.html` — печатная страница со всеми QR.
- `qr-codes.pdf` — готовый PDF для печати (A4, 14 страниц).
- `qr-codes.csv` — ключи, названия, адреса и имена PNG.
- `<qr_key>.png` — отдельный QR каждого места.

Базовый адрес сейчас: `https://unlucky-eagle-38.loca.lt` (временный
localtunnel). Если адрес изменится или появится домен, перегенерируйте коды:

```bash
cd /home/mamai/upk
.venv/bin/python scripts/export_qr.py --base-url https://ВАШ-АДРЕС
```

PDF:

```bash
chromium --headless --no-sandbox --disable-gpu \
  --print-to-pdf=docs/qr/qr-codes.pdf --no-pdf-header-footer \
  file:///home/mamai/upk/docs/qr/index.html
```

Проверить отдельный код:

```bash
zbarimg -q --raw docs/qr/room109.png
```

Для временного localtunnel-адреса печатать QR не стоит: он меняется при
перезапуске туннеля. Дождитесь постоянного домена и перегенерируйте коды.
