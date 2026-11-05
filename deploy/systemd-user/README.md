# systemd user units

Установка без sudo:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd-user/*.service deploy/systemd-user/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload

# Автозапуск приложения и публичного туннеля
systemctl --user enable --now upk.service upk-localtunnel.service
# upk-tunnel.service (Cloudflare Quick Tunnel) — запасной, в этой сети нестабилен

# Ежедневные бэкапы и очистка
systemctl --user enable --now upk-backup.timer upk-cleanup.timer

# Чтобы сервисы работали без активного входа в систему
loginctl enable-linger "$USER"
```

Проверка:

```bash
systemctl --user status upk.service upk-tunnel.service
systemctl --user list-timers
journalctl --user -u upk-tunnel -n 30
```

Важно: адрес localtunnel меняется при перезапуске
`upk-localtunnel.service`. После перезапуска найдите новый адрес:

```bash
journalctl --user -u upk-localtunnel -n 20
```

Обновите `PUBLIC_BASE_URL` в `.env` и перезапустите `upk.service`.
Для постоянного адреса нужен домен и именованный туннель — см.
`docs/PUBLIC_PILOT.md`.
