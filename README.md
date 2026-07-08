# Google Trends Parser

Сервис для сбора данных Google Trends. Принимает входящие запросы по ключевым словам, скрейпит Google через ротирующие прокси, кеширует результаты в PostgreSQL.

## Архитектура

```
Client → FastAPI (app/api.py)
              │
              ├─ есть в кеше → вернуть сразу
              └─ нет → push → Redis Queue
                                   │
                              Worker (worker.py)
                                   │
                              DCH Proxy (IPv6 ротация)
                                   │
                            Google Trends API
                              /explore
                              /widgetdata/multiline
                                   │
                              PostgreSQL
                              keywords
                              trend_series
                              trend_points
                              proxies
```

## Требования

**Docker + Docker Compose** — всё остальное (Python, PostgreSQL, Redis) поднимается в контейнерах.

Опционально, для запуска скриптов вне Docker (`scripts/proxy.py` и т.д. напрямую с хоста):

```bash
uv sync        # создаст .venv и установит зависимости из uv.lock
```

## Настройка

```bash
cp .env.example .env
```

| Переменная          | По умолчанию                                 | Описание                                       |
|---------------------|----------------------------------------------|------------------------------------------------|
| `DB_DSN`            | `postgresql://trends:trends@postgres/trends` | PostgreSQL DSN                                 |
| `REDIS_URL`         | `redis://redis:6379`                         | Redis URL                                      |
| `API_KEY`           | _(не задано)_                                | Ключ для X-API-Key; если пуст — auth отключена |
| `WAIT_TIMEOUT_SEC`  | `30`                                         | Сколько секунд API ждёт ответа воркера         |
| `WORKER_CONCURRENCY`| `24`                                         | Параллельных задач на один воркер              |
| `WORKER_REPLICAS`   | `1`                                          | Количество воркеров (1 = 1 прокси-аккаунт)    |
| `UVICORN_WORKERS`   | `2`                                          | Количество uvicorn-процессов                   |

## Первый запуск

Быстрый способ — интерактивный скрипт (создаст .env, поднимет контейнеры, применит миграции, спросит прокси):

```bash
bash scripts/setup.sh
```

Вручную:

```bash
# 1. Настроить переменные окружения
cp .env.example .env

# 2. Поднять всё
docker compose up -d

# 3. Применить миграции
docker compose exec api python migrations/run.py

# 4. Добавить прокси
docker compose exec api python scripts/proxy.py add http://user:pass@host:port

# 5. Проверить
curl http://localhost:8000/health
```

Масштабирование воркеров (при добавлении прокси-аккаунтов):
```bash
# Добавить новый прокси
python scripts/proxy.py add http://user2:pass2@host:port

# Сообщить воркерам о новых прокси (без рестарта)
docker compose kill -s HUP worker

# Увеличить количество воркеров в .env: WORKER_REPLICAS=2
docker compose up -d
```

## Чеклист перед продом

Обязательно в `.env` на сервере:

```bash
# Сгенерировать и задать ключ авторизации (без него API открыт всем)
API_KEY=$(openssl rand -hex 32)

# Сменить пароль PostgreSQL (и обновить DB_DSN соответственно)
POSTGRES_PASSWORD=<случайный пароль>
DB_DSN=postgresql://trends:<тот же пароль>@postgres:5432/trends

# Логи только ошибки
LOG_LEVEL=error
```

Дополнительно:
- Настроить cron для cleanup (см. [Retention policy](#retention-policy))
- Порты PostgreSQL/Redis наружу не торчат (биндятся на 127.0.0.1) — проверить `docker compose ps`
- Если API доступен из интернета — поставить nginx с HTTPS перед ним

## Обновление на сервере

Стянуть код, пересобрать контейнеры, применить миграции и проверить health — одной командой:

```bash
cd ~/apps/parser && bash scripts/update.sh
```

Скрипт печатает `✅ ПАРСЕР ОБНОВЛЁН И РАБОТАЕТ (health: ok)` при успехе, либо ругается и
показывает, где смотреть логи, если `/health` не отвечает. Полный процесс деплоя — в [DEPLOY.md](DEPLOY.md).

## Прокси

Прокси хранятся в таблице `proxies` в PostgreSQL.

```bash
# Добавить
python scripts/proxy.py add http://user:pass@host:port

# Список
python scripts/proxy.py list

# Отключить / включить
python scripts/proxy.py disable 2
python scripts/proxy.py enable 2

# Удалить
python scripts/proxy.py remove 2

# Импорт из файла (при переезде со старой схемы)
python scripts/import_proxies.py proxies.txt
```

После добавления/изменения прокси воркер подхватывает их по сигналу `SIGHUP` — без рестарта:
```bash
docker compose kill -s HUP worker
```

## API

Интерактивная документация доступна сразу после запуска:

| Интерфейс | URL |
|-----------|-----|
| Swagger UI | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |

В Swagger UI можно выполнять запросы прямо из браузера. Если задан `API_KEY`, перед тестированием нажми **Authorize** (кнопка с замком вверху) и введи ключ — он будет автоматически добавляться в заголовок `X-API-Key` ко всем запросам.

### GET /trends

Один keyword, ждёт до 60 секунд. Возвращает 200 с данными или 202 если воркер не успел.

```
GET /trends?keyword=bitcoin&geo=US&period=1m&engine=web&category=7
X-API-Key: your-secret
```

**200:**
```json
{
  "keyword": "bitcoin",
  "geo": "US",
  "period": "1m",
  "category": 7,
  "engine": "web",
  "cached": true,
  "points": [
    {"ts": "2025-01-01T00:00:00+00:00", "value": 45},
    {"ts": "2025-01-02T00:00:00+00:00", "value": 52}
  ]
}
```

**202** — данные собираются, повторить запрос позже.

### GET /trends/batch

До 20 keywords × N стран за раз. Возвращает сразу: кеш или `queued`.

```
GET /trends/batch?keywords=bitcoin,ethereum&geos=US,GB&period=1m&engine=youtube
X-API-Key: your-secret
```

```json
{
  "period": "1m",
  "geos": ["US", "GB"],
  "category": 0,
  "engine": "youtube",
  "results": {
    "bitcoin": {
      "US": {"cached": true, "points": [...]},
      "GB": {"cached": false, "status": "queued"}
    }
  }
}
```

### GET /trends/compare

2–5 ключей в **общей шкале**: 100 = пик самого популярного ключа. В отличие от `/trends`,
значения разных ключей сравнимы между собой. Комбинация кешируется независимо от порядка ключей.

```
GET /trends/compare?keywords=bitcoin,trump&period=1m
X-API-Key: your-secret
```

```json
{
  "keywords": ["bitcoin", "trump"],
  "geo": "",
  "period": "1m",
  "category": 0,
  "engine": "web",
  "cached": false,
  "points": [
    {"ts": "2026-06-03T00:00:00+00:00", "values": [35, 64]},
    {"ts": "2026-06-04T00:00:00+00:00", "values": [45, 64]}
  ]
}
```

`keywords` отсортированы по алфавиту, `values[i]` соответствует `keywords[i]`.

### GET /trends/regions

География интереса. `geo` пустой — разбивка по странам мира; `geo=US` — по штатам США.
`value` 0–100 относительно самого активного региона. Регионы без данных не включаются.

```
GET /trends/regions?keyword=bitcoin&period=1m
X-API-Key: your-secret
```

```json
{
  "keyword": "bitcoin",
  "geo": "",
  "period": "1m",
  "cached": false,
  "regions": [
    {"geo_code": "SV", "name": "El Salvador", "value": 100},
    {"geo_code": "NG", "name": "Nigeria", "value": 85}
  ]
}
```

### GET /trends/related

Связанные запросы: `top` — самые популярные (0–100 относительно первого),
`rising` — быстрорастущие (`value` = рост в %, `formatted` = `"+250%"` или `"Breakout"` при росте >5000%).

```
GET /trends/related?keyword=bitcoin&geo=US&period=1m
X-API-Key: your-secret
```

```json
{
  "keyword": "bitcoin",
  "geo": "US",
  "period": "1m",
  "cached": false,
  "top": [
    {"query": "bitcoin price", "value": 100},
    {"query": "bitcoin usd", "value": 45}
  ],
  "rising": [
    {"query": "bitcoin etf approval", "value": 48950, "formatted": "Breakout"},
    {"query": "bitcoin halving 2028", "value": 250, "formatted": "+250%"}
  ]
}
```

### GET /trending

Трендовые запросы страны (Google Trending Now). История копится в БД — при регулярном
опросе `since_hours=168` вернёт всё, что было в трендах за неделю.

```
GET /trending?geo=US&since_hours=24&limit=100&sort=growth&category=17&status=active
X-API-Key: your-secret
```

Параметры: `sort` — volume / growth / recency / title; `category` — ID категории Google
(17 = спорт); `status=active` — только тренды из последнего опроса.

```json
{
  "geo": "US",
  "since_hours": 24,
  "cached": true,
  "count": 360,
  "trends": [
    {
      "keyword": "méxico - inglaterra",
      "volume": 500000,
      "growth_pct": 1000,
      "started_at": "2026-07-03T15:00:00+00:00",
      "breakdown": ["rafa marquez", "méxico vs. inglaterra", "raúl jiménez"],
      "categories": [17],
      "first_seen_at": "2026-07-03T16:02:11+00:00"
    }
  ]
}
```

Данные свежи 60 минут, потом обновляются на лету при запросе.
Для регулярного сбора по всем гео — cron:

```bash
# Каждые 15 минут опросить все ~65 стран (Google обновляет тренды раз в ~10 мин)
*/15 * * * * cd /opt/trends && docker compose exec -T api python scripts/enqueue_trending.py
```

Крупные страны дают ~300–400 трендов/сутки (~1500 ключей с breakdown), мелкие — 20–50.

### GET /status

```json
{"queue": 142, "dead": 3}
```

`dead` — задания упавшие после всех попыток.

### GET /health

Без авторизации — для мониторинга, load balancer'ов и смоук-тестов. Проверяет доступность PostgreSQL и Redis.

```json
{"status": "ok", "db": "ok", "redis": "ok"}
```

Возвращает `200` если всё живо, `503` если БД или Redis недоступны.

### Параметры запроса

| Параметр   | Тип    | По умолчанию | Описание |
|------------|--------|--------------|----------|
| `keyword`  | string | —            | Поисковый запрос |
| `geo`      | string | `""`         | Страна ISO 3166-1 alpha-2. Пусто = worldwide |
| `period`   | string | `1m`         | Временной диапазон (см. ниже) |
| `from`     | date   | —            | Начало диапазона `YYYY-MM-DD` (только при `period=custom`) |
| `to`       | date   | —            | Конец диапазона `YYYY-MM-DD` (только при `period=custom`) |
| `category` | int    | `0`          | ID категории. `0` = все, `7` = финансы, `20` = развлечения, `47` = путешествия |
| `engine`   | string | `web`        | Движок: `web`, `youtube`, `images`, `news`, `shopping` |

**Значения period:**

| period | Диапазон | Гранулярность |
|--------|----------|---------------|
| `1h`   | последний час | по минутам |
| `4h`   | последние 4 часа | по минутам |
| `1d`   | последние 24 часа | по минутам |
| `7d`   | последние 7 дней | по часам |
| `1m`   | последние 30 дней | по дням |
| `3m`   | последние 90 дней | по дням |
| `12m`  | последние 12 месяцев | по неделям |
| `5y`   | последние 5 лет | по месяцам |
| `all`  | с 2004 по сегодня | по месяцам |
| `custom` | произвольный диапазон | зависит от длины |

При `period=custom` обязательны параметры `from` и `to`.

Значения 0–100 **относительны**: 100 = пик интереса внутри запрошенного диапазона.

## Воркер

```bash
python worker.py [опции]

Опции:
  --concurrency 24   Параллельных задач (24 = ~1M req/day на одном DCH)
  --rotating         Режим ротирующего прокси (cooldown 5s вместо 120s)
  --sleep-min 0.5    Пауза между запросами (мин, секунды)
  --sleep-max 0.5    Пауза между запросами (макс, секунды)
  --log-ip           Логировать IP исходящего запроса
  --playwright       Получать куки через Playwright (для статических DC прокси)
  --max-requests 25  Запросов на сессию до ротации
```

## Миграции

```bash
# Применить новые миграции
python migrations/run.py

# Добавить новую миграцию
# → создать файл migrations/006_add_something.sql (следующий свободный номер)
```

## Retention policy

Данные чистятся вручную или по крону скриптом `scripts/cleanup.py`:

| Что | Условие | Действие |
|-----|---------|----------|
| Неактивные серии | Не запрашивались 7 дней | DELETE series + points |
| Кеш compare / regions / related | Не запрашивался 7 дней | DELETE |
| История трендов (trending) | Старше 90 дней | DELETE |
| Старые партиции | Старше 12 месяцев | DROP TABLE (мгновенно) |
| Dead queue | Больше 1000 записей | LTRIM до 1000 |

```bash
# Проверить что будет удалено (без изменений)
python scripts/cleanup.py --dry-run

# Запустить чистку
python scripts/cleanup.py

# Через docker compose
docker compose --profile cleanup run --rm cleanup

# По крону — раз в сутки в 3:00
0 3 * * * cd /opt/trends && docker compose --profile cleanup run --rm cleanup >> logs/cleanup.log 2>&1
```

Параметры:
- `--stale-days 7` — порог неактивности серий и кешей (дней)
- `--trending-days 90` — сколько хранить историю трендов (дней)
- `--partition-months 12` — возраст партиций для удаления (месяцев)
- `--dead-limit 1000` — сколько записей оставить в dead queue

## Утилиты

```bash
# Загрузить ключевые слова в очередь
python scripts/enqueue.py --file tests/keywords.txt --geo US,GB,DE

# Собрать тренды по всем гео (для крона)
python scripts/enqueue_trending.py
python scripts/enqueue_trending.py --geos US,DE --hours 168

# Проверить размер очереди
python scripts/enqueue.py --status

# Бенчмарк прокси (N потоков, 10 минут)
python scripts/run_bench.py logs/run1 600 4

# Бенчмарк только DCH
python scripts/run_dch_only.py logs/run1 600 24
```

## Структура

```
├── app/
│   ├── api.py           FastAPI приложение + авторизация
│   ├── db.py            PostgreSQL (asyncpg), партиции, TTL кеша
│   ├── job_queue.py     Redis очередь + дедупликация
│   ├── proxy_pool.py    Пул прокси (загрузка из БД, cooldown, SIGHUP-reload)
│   ├── scraper.py       Google Trends scraper + ScrapePool
│   └── cookie_warmer.py Playwright для получения кук (опционально)
├── migrations/
│   ├── 001_initial.sql  Начальная схема БД
│   ├── 002_add_category_gprop.sql
│   ├── 003_proxies.sql  Таблица прокси
│   ├── 004_trend_points_default_partition.sql
│   ├── 005_indexes.sql  Индексы для retention policy
│   ├── 006_compare_cache.sql   Кеш сравнений (/trends/compare)
│   ├── 007_widget_cache.sql    Кеш regions/related
│   ├── 008_trending.sql        История трендов (/trending)
│   ├── 009_trending_growth.sql Колонка growth_pct
│   └── run.py           Раннер миграций
├── scripts/
│   ├── setup.sh         Интерактивная установка с нуля
│   ├── proxy.py         CLI управления прокси (add/list/enable/disable/remove)
│   ├── import_proxies.py Импорт прокси из txt-файла в БД
│   ├── cleanup.py       Retention policy (удаление старых данных)
│   ├── enqueue.py       CLI для загрузки ключей в очередь
│   ├── enqueue_trending.py Опрос трендов по всем гео (для крона)
│   ├── test_api.py      Нагрузочный тест API
│   ├── run_bench.py     Бенчмарк нескольких прокси
│   └── run_dch_only.py  Бенчмарк одного прокси
├── tests/
│   ├── keywords.txt     Тестовые ключевые слова
│   ├── test_4proxies.py
│   └── test_mobile_proxy.py
├── pyproject.toml       Зависимости (uv)
├── uv.lock              Lock-файл зависимостей
├── docker-compose.yml   PostgreSQL + Redis + API + Worker
├── Dockerfile
└── worker.py            Воркер (Redis → Google → PostgreSQL)
```

## Производительность

| Потоков (--concurrency) | req/day | Success rate |
|-------------------------|---------|--------------|
| 1                       | ~32k    | 95%          |
| 4                       | ~160k   | 96%          |
| 8                       | ~249k   | 97%          |
| 16                      | ~657k   | 99%          |
| **24**                  | **~1M** | **100%**     |

Тестировалось на DCH ротирующем прокси (`us.dch.dcproxy.com`), один аккаунт.