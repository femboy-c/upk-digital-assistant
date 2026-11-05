# QR-коды помещений

В этой папке лежат готовые PNG-файлы и печатная страница `index.html`.

| Место | QR-ключ | Файл |
|---|---|---|
| 1 этаж: кабинет 101 | `lab101` | `lab101.png` |
| 1 этаж: коридор | `corridor-1` | `corridor-1.png` |
| 1 этаж: столовая | `canteen` | `canteen.png` |
| 1 этаж: спортзал | `gym` | `gym.png` |
| 1 этаж: актовый зал | `assembly-hall` | `assembly-hall.png` |
| 1 этаж: женская раздевалка | `locker-women` | `locker-women.png` |
| 1 этаж: мужская раздевалка | `locker-men` | `locker-men.png` |
| 2 этаж: кабинет 214 | `room214` | `room214.png` |
| 2 этаж: коридор | `corridor-2` | `corridor-2.png` |
| 3 этаж: кабинет 305 | `room305` | `room305.png` |
| 3 этаж: коридор | `corridor-3` | `corridor-3.png` |
| 3 этаж: медпункт | `medical` | `medical.png` |
| Передний двор | `yard-front` | `yard-front.png` |
| Задний двор | `yard-back` | `yard-back.png` |
| Задний двор: женский санузел | `yard-back-women` | `yard-back-women.png` |
| Задний двор: мужской санузел | `yard-back-men` | `yard-back-men.png` |

`index.html` можно открыть в браузере и распечатать.
`qr-codes.csv` содержит ключи, названия и адреса.

## Важно про адрес

Текущая выгрузка сделана для адреса `http://10.167.225.109:8000`.
Это сетевой адрес компьютера, поэтому QR должен открываться со смартфона,
если телефон находится в той же сети.

Проверить текущий IP компьютера:

```bash
ip -4 -o addr show scope global
```

Если адрес изменился, перегенерируйте коды:

```bash
cd /home/mamai/upk
.venv/bin/python scripts/export_qr.py --base-url http://НОВЫЙ_IP:8000
```

Сервер должен быть запущен с `--host 0.0.0.0 --port 8000`.
Проверить, что он доступен с телефона: открыть `http://IP:8000/health`.

Для реального сайта:

```bash
PUBLIC_BASE_URL=https://upk.example.ru .venv/bin/python scripts/export_qr.py
```

## Если изменился QR-ключ

После изменения ключа в панели `/staff/locations` старый QR перестаёт работать.
Выгрузите PNG заново и замените распечатанный код.
