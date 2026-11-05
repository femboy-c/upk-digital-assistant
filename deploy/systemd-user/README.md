# systemd user units

Установка без sudo:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd-user/*.service deploy/systemd-user/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload

# Автозапуск приложения и временного туннеля
systemctl --user enable --now upk.service upk-tunnel.service

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

Важно: это **Quick Tunnel**, его адрес меняется при перезапуске
`upk-tunnel.service`. После перезапуска найдите новый адрес в journalctl,
обновите `PUBLIC_BASE_URL` в `.env` и перезапустите `upk.service`.
Для постоянного адреса нужен домен и именованный туннель — см.
`docs/PUBLIC_PILOT.md`.
