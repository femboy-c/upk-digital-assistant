# Публичный запуск Колледжа ДГУ

Ниже — минимальный чек-лист для запуска на сервере с HTTPS.
Демонстрационный запуск на `127.0.0.1:8000` этих действий не требует.

## 1. Подготовка сервера

Нужны Linux-сервер, домен, указывающий на его IP, Python 3.11+ и Caddy
(или nginx с сертификатом). Приложение слушает только локальный порт
`127.0.0.1:8000`, наружу его публикует HTTPS-прокси.

Создайте отдельного пользователя и папку проекта:

```bash
sudo useradd --system --home /home/mamai/upk --shell /usr/sbin/nologin upk
sudo chown -R upk:upk /home/mamai/upk
```

## 2. Зависимости

```bash
cd /home/mamai/upk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Файл `.env`

Скопируйте `.env.example` в `.env` и заполните:

```bash
APP_ENV=production
SECRET_KEY=<длинная случайная строка>
PUBLIC_BASE_URL=https://upk.example.ru
SESSION_HTTPS_ONLY=1
TRUST_PROXY=1
MAX_UPLOAD_TOTAL_MB=200
```

Сгенерировать секрет:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

При `APP_ENV=production` приложение не запустится с демонстрационным
`SECRET_KEY` — это защита от случайного запуска.

`TRUST_PROXY=1` нужен, потому что приложение стоит за Caddy: только так оно
увидит реальный IP клиента в `X-Forwarded-For`. Не включайте этот параметр,
если uvicorn доступен напрямую.

## 4. Пользователи

Создайте рабочего администратора:

```bash
.venv/bin/python scripts/create_user.py \
  --login admin2 --name "Администратор колледжа" --role admin
```

Пароль вводится скрыто и хранится только в виде хеша.

При первом запуске с `APP_ENV=production` демо-пользователи `staff` и `admin`
не создаются. Если база осталась от демонстрационного запуска, они уже в ней.
В этом случае:

- войдите под каждым и смените пароль на странице `/staff/password`;
- либо удалите демо-пользователей вручную после создания своих.

## 5. HTTPS через Caddy

Установите Caddy, скопируйте пример и замените домен:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy сам выпустит сертификат Let's Encrypt. Проверьте:

```bash
curl -I https://upk.example.ru/health
```

Ожидается `200 OK` и заголовок `Strict-Transport-Security`.

## 6. systemd

```bash
sudo cp deploy/upk.service /etc/systemd/system/upk.service
sudo nano /etc/systemd/system/upk.service
sudo systemctl daemon-reload
sudo systemctl enable --now upk
sudo systemctl status upk
```

В `ExecStart` должен быть только локальный адрес `127.0.0.1:8000`.

## 7. Резервные копии

Ручной запуск:

```bash
.venv/bin/python scripts/backup.py --keep 14
```

Архив содержит `data/upk.db` и `data/uploads/`. Пример cron:

```cron
0 3 * * * cd /home/mamai/upk && .venv/bin/python scripts/backup.py --keep 14
```

Восстановление:

1. Остановить сервис: `sudo systemctl stop upk`.
2. Распаковать архив.
3. Заменить `data/upk.db` и файлы в `data/uploads/`.
4. Выставить владельца: `sudo chown -R upk:upk data`.
5. Запустить сервис: `sudo systemctl start upk`.

## 8. Ограничения запуска

- Счётчики попыток входа и ограничения частоты заявок/ИИ хранятся в памяти
  процесса. Если запускаете несколько workers, вынесите их в Redis.
- SQLite подходит для небольшой нагрузки. При нескольких одновременных
  сотрудниках лучше перейти на PostgreSQL.
- Приложение не выполняет антивирусную проверку вложений.
- Обновляйте резервные копии и следите за свободным местом в `data/uploads/`.

Дополнительно: MFA, Redis, ClamAV, уведомления и systemd-таймеры описаны в
`docs/OPERATIONS.md`.

## 9. Финальная проверка

- [ ] `/health` отвечает по HTTPS.
- [ ] Вход сотрудника работает.
- [ ] Демо-пароли изменены или демо-пользователи удалены.
- [ ] QR ведёт на HTTPS-домен: перегенерируйте `scripts/export_qr.py`.
- [ ] CSRF и ограничение входа работают.
- [ ] `/robots.txt` отдаётся, а `/staff` содержит `X-Robots-Tag: noindex`.
- [ ] Ограничение частоты заявок и запросов к ИИ работает (`TRUST_PROXY=1`).
- [ ] Вложение открывается только после входа или по секретной ссылке.
- [ ] Резервная копия создаётся и восстанавливается.
- [ ] Настроен автозапуск systemd и обновление сертификата Caddy.
