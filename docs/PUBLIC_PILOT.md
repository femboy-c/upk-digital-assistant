# Публичный пилот: временный туннель

Дата запуска: 5 ноября 2026 года.

## Статус

- Режим: `APP_ENV=production`.
- Приложение: `127.0.0.1:8000`, наружу не слушает.
- Публичный HTTPS: `https://stupid-falcon-15.loca.lt`
- Туннель: `localtunnel` (резервный вариант). Cloudflare Quick Tunnel в этой
  сети рвёт соединение с edge каждые несколько минут, поэтому оставлен как
  запасной, а публичный доступ идёт через localtunnel.
- Домена и постоянного адреса нет.

Проверено снаружи:

- `/health` → `200 {"status":"ok"}`;
- `/` и `/login` → `200`;
- `/staff` без входа → `303` на `/login`;
- `/docs` и `/openapi.json` → `404`;
- `Strict-Transport-Security` присутствует;
- `/login` и `/staff` отдают `X-Robots-Tag: noindex, nofollow`;
- вход `admin` работает, отключённый `staff` получает `401`.

## Что изменено при запуске

- `SECRET_KEY` заменён на случайный; старые сессии недействительны.
- Пароль `admin` заменён на длинный случайный. Пароль не хранится в репозитории.
- Учётная запись `staff` отключена (`is_active = 0`), демо-пароль больше не работает.
- `.env`: `APP_ENV=production`, `SESSION_HTTPS_ONLY=1`, `TRUST_PROXY=1`,
  `PUBLIC_BASE_URL` равен адресу туннеля. Права на файл — `600`.
- `tests/conftest.py` теперь принудительно ставит `APP_ENV=test`, чтобы
  production-настройки из `.env` не ломали тесты.
- Перед изменениями создан бэкап
  `backups/upk-backup-20261105-033114.zip`.

## Как это запущено сейчас

```bash
systemctl --user start upk.service upk-localtunnel.service
```

Юниты лежат в `deploy/systemd-user/`. `cloudflared` установлен в
`~/.local/bin/cloudflared` (без sudo), но его quick tunnel в этой сети
нестабилен.

Если localtunnel перезапустился и адрес изменился:

```bash
journalctl --user -u upk-localtunnel -n 20
# обновить PUBLIC_BASE_URL в .env и перезапустить upk.service
```

## Ограничения пилота

- Адрес `*.loca.lt` меняется при каждом перезапуске туннеля.
- QR-коды в `docs/qr/` по-прежнему ведут на локальный адрес
  `http://10.167.225.109:8000`. Перегенерировать их имеет смысл только после
  получения постоянного домена.
- Это не постоянный публичный запуск: нет домена и стабильных QR. Для
  постоянного запуска нужен домен и именованный туннель Cloudflare
  (или VPS + Caddy).
- localtunnel отдаёт свои заголовки `X-Robots-Tag: noindex`, поэтому поиск
  в Яндексе/Google с него невозможен. Для поиска нужен постоянный домен и
  регистрация в Вебмастере — см. `docs/SEO.md`.
- localtunnel — сторонний сервис: он терминирует HTTPS и видит трафик.
  Для реальных персональных данных используйте только домен + свой прокси.
- Прямой вход по `http://127.0.0.1:8000` после включения
  `SESSION_HTTPS_ONLY=1` не сохраняет сессию: для входа используйте публичный
  HTTPS-адрес.

## Что нужно для постоянного запуска

1. Домен (например, `.ru` или недорогой `.xyz`) и аккаунт Cloudflare.
2. Добавить домен в Cloudflare, дождаться смены NS.
3. Создать именованный туннель:

   ```bash
   ~/.local/bin/cloudflared tunnel login
   ~/.local/bin/cloudflared tunnel create upk
   ~/.local/bin/cloudflared tunnel route dns upk upk.example.ru
   ```

4. Запускать туннель с конфигом и `PUBLIC_BASE_URL=https://upk.example.ru`.
5. Перегенерировать QR:

   ```bash
   .venv/bin/python scripts/export_qr.py --base-url https://upk.example.ru
   ```

6. Настроить автозапуск (systemd user или system) для uvicorn и туннеля.
7. Настроить cron-бэкап `scripts/backup.py --keep 14`.

## Что добавлено после запуска пилота

- публичный UX: «Как это работает», примеры, счётчик, черновик, превью фото,
  копирование ссылки и прогресс статуса;
- до 5 фото на заявку, сжатие до 1600 px и удаление EXIF;
- поиск похожих заявок и объединение дублей администратором;
- личный кабинет `/my` со списком заявок текущего браузера;
- SLA-подсветка просроченных и массовые действия в панели;
- MFA (TOTP), баннер плановых работ, уведомления Telegram/email;
- Redis-лимиты, ClamAV, очистка осиротевших вложений;
- полный CSP без `unsafe-inline`, CI с `pip-audit`.

Схема `data/upk.db` дополнена колонками `tickets.merged_into_id` и MFA-полями
`users.totp_*`, создана таблица `banner`. Миграция выполняется автоматически
при старте. Тесты: **162 passed**.

## Остановка пилота

```bash
systemctl --user stop upk-localtunnel.service upk.service
```

Не использовать `pkill -f "uvicorn app.main:app"`.
