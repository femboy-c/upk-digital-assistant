# Эксплуатация

## Запуск и перезапуск

```bash
cd /home/mamai/upk
fuser -k 8000/tcp 2>/dev/null
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > /tmp/opencode/upk-uvicorn.log 2>&1 &
```

Не использовать `pkill -f "uvicorn app.main:app"`.

## Автозапуск через systemd (без sudo)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd-user/*.service deploy/systemd-user/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now upk.service upk-tunnel.service
systemctl --user enable --now upk-backup.timer upk-cleanup.timer
loginctl enable-linger "$USER"
```

Проверка:

```bash
systemctl --user status upk.service upk-tunnel.service
systemctl --user list-timers
journalctl --user -u upk-tunnel -n 30
```

Cloudflare Quick Tunnel меняет адрес при перезапуске. После смены адреса обновите
`PUBLIC_BASE_URL` в `.env` и перезапустите `upk.service`. Для стабильного
адреса нужен домен и именованный туннель.

## Бэкапы и очистка

- `scripts/backup.py --keep 14` — архив базы и вложений.
- `scripts/cleanup_old_tickets.py` — удаление старых закрытых заявок.
- `scripts/cleanup_orphans.py` — файлы вложений без записи в базе
  (по умолчанию не трогает файлы младше 24 часов).
- Таймеры `upk-backup.timer` (03:00) и `upk-cleanup.timer` (03:30).

Ручной запуск:

```bash
.venv/bin/python scripts/backup.py --keep 14
.venv/bin/python scripts/cleanup_old_tickets.py
.venv/bin/python scripts/cleanup_orphans.py --dry-run
```

## Двухфакторный вход

Администратор открывает `/staff/mfa`, сканирует QR в приложении-аутентификаторе
и подтверждает 6-значный код. После включения при входе нужен пароль + код.

Сброс при потере телефона:

```bash
.venv/bin/python scripts/reset_mfa.py --login admin
```

После сброса старые сессии завершаются.

## Баннер плановых работ

`/staff/banner` (только администратор). Текст показывается над содержимым всех
страниц. Пустой баннер включить нельзя.

## Уведомления

Без настроек уведомления не отправляются. Переменные в `.env`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

SMTP_HOST=smtp.example.ru
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=upk@example.ru
SMTP_TLS=1
NOTIFY_EMAIL=staff@example.ru
```

Уведомления отправляются в фоновом потоке и не задерживают заявку. Ошибки
внешних сервисов пишутся в лог без токенов и паролей.

## Redis для нескольких workers

По умолчанию лимиты хранятся в памяти процесса. Для нескольких workers:

```env
REDIS_URL=redis://127.0.0.1:6379/0
```

Если Redis недоступен, приложение работает дальше с лимитами в памяти и
пишет предупреждение в лог.

## ClamAV

Проверка вложений включается переменными:

```env
CLAMAV_HOST=127.0.0.1
CLAMAV_PORT=3310
```

Если ClamAV задан, но недоступен, загрузка фото блокируется (fail closed).
Для локальной установки: `sudo pacman -S clamav`, обновить базы
`sudo freshclam`, запустить `clamav-daemon`/`clamd`.

## CI и аудит зависимостей

GitHub Actions: `.github/workflows/ci.yml` — тесты и `pip-audit`.
Локально:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/pip-audit -r requirements.txt --progress-spinner off
```

## PostgreSQL

Проект всё ещё использует SQLite и SQLite-миграции. Для PostgreSQL нужен
отдельный слой миграций и проверка типов; это не входит в текущую версию.
