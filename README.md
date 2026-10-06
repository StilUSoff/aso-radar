# ASO Radar

Дашборд позиций приложения в поиске App Store по ключевым словам с разбивкой по странам (гео).

- Ключевые слова — вручную или импортом из **App Store Connect** (поле Keywords каждой локализации → страны витрин).
- Позиции — топ‑200 поиска App Store в каждой стране через публичный iTunes Search API (без ключей).
- Сводка по странам: сколько ключей в топ‑10 / топ‑50 / топ‑200, лучшая позиция, покрытие.
- Таблица ключей: позиция, изменение с прошлой проверки, лидер выдачи, история позиций по клику.
- Автоматическая проверка по расписанию (по умолчанию раз в сутки), экспорт в CSV.
- Доступ по логину и паролю, HTTPS через Caddy + Let's Encrypt.

Идея — ASO‑дашборд из [поста @golyakovph](https://www.threads.com/@golyakovph/post/DXV-QeClIrc); работа с App Store Connect API — по мотивам [asc-mcp](https://github.com/zelentsov-dev/asc-mcp) (сам asc-mcp работает только на macOS, поэтому здесь свой минимальный клиент).

## Как это работает

```
App Store Connect API ──(keywords по локалям)──┐
                                               ▼
             ручной ввод ──────────────► SQLite (apps, keywords, checks)
                                               ▲
iTunes Search API ──(топ‑200 по стране)────────┘  ← планировщик / кнопка «Проверить»
```

Apple ограничивает iTunes Search API примерно 20 запросами в минуту с одного IP, поэтому между запросами пауза ~3,2 с: 445 ключей проверяются примерно за 25 минут. Одинаковые «слово + страна» для разных приложений запрашиваются один раз.

Позиции из iTunes Search API близки к поиску в App Store на устройстве, но не идентичны ему (персонализация, рекламные места, A/B‑тесты Apple). Для отслеживания динамики этого достаточно.

## Локальный запуск

```bash
cp .env.example .env          # для локальной работы можно оставить DASHBOARD_PASSWORD пустым
docker compose up -d --build
open http://localhost:8000
```

Без Docker (Python 3.10+):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DB_PATH=data/aso.db .venv/bin/uvicorn app.main:app --reload
```

## Импорт из App Store Connect (необязательно)

1. App Store Connect → Users and Access → Integrations → Team Keys → создать ключ с ролью **App Manager** (или Developer — достаточно доступа на чтение метаданных).
2. Скачать `.p8` и положить его в `secrets/AuthKey.p8` (папка в `.gitignore`).
3. Заполнить `ASC_KEY_ID` и `ASC_ISSUER_ID` в `.env`, перезапустить контейнер.
4. В дашборде: «Импорт из ASC». Берётся текущая опубликованная версия приложения.

Соответствие локалей и стран — в [app/locales.py](app/locales.py). По умолчанию локаль проверяется в основной стране (`fr-FR` → FR); галочка «дополнительные витрины» добавляет остальные (`fr-FR` → BE, CH).

## Развёртывание на сервере (Ubuntu)

Репозиторий приватный, поэтому серверу нужен доступ к нему — проще всего через deploy key:

```bash
# на сервере
ssh-keygen -t ed25519 -f ~/.ssh/aso_radar_deploy -N ""
cat ~/.ssh/aso_radar_deploy.pub
```

Добавьте ключ в GitHub: репозиторий → Settings → Deploy keys → Add (только чтение). Затем:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-aso-radar
  HostName github.com
  IdentityFile ~/.ssh/aso_radar_deploy
EOF
git clone git@github-aso-radar:<user>/aso-radar.git ~/aso-radar
cd ~/aso-radar
sudo ./deploy/install.sh      # Docker, .env со случайным паролем, права на data/
# перелогиньтесь, чтобы применилась группа docker
./deploy/update.sh            # сборка и запуск
```

Доступ к дашборду:

- **С доменом (рекомендуется):** направьте A‑запись домена на сервер, укажите `DOMAIN=aso.example.com` в `.env`, откройте порты 80/443 и выполните `./deploy/update.sh` — поднимется Caddy с HTTPS.
- **Если 443 занят** (например, VPN): задайте `HTTPS_PORT=8443` — дашборд будет на `https://домен:8443`, порт 80 по‑прежнему нужен для выпуска сертификата.
- **Без домена:** сервис слушает только `127.0.0.1:8000`. Откройте его через SSH‑туннель: `ssh -L 8000:localhost:8000 user@server`, затем http://localhost:8000.

Обновление после `git push`: `cd ~/aso-radar && ./deploy/update.sh`.

Данные лежат в `data/aso.db` (SQLite) — для бэкапа достаточно копировать этот файл.

## Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `admin` / — | Логин в дашборд. Пустой пароль отключает авторизацию |
| `CHECK_INTERVAL_HOURS` | `24` | Период автопроверки всех ключей, `0` — только вручную |
| `ITUNES_REQUEST_DELAY` | `3.2` | Пауза между запросами к Apple, сек |
| `ASC_KEY_ID`, `ASC_ISSUER_ID` | — | Ключ App Store Connect API |
| `DOMAIN` | — | Домен для HTTPS через Caddy |
| `HTTPS_PORT` | `443` | Внешний HTTPS‑порт Caddy |
| `APP_PORT` | `8000` | Локальный порт сервиса |
