# Деплой парсера на VDS

## Один раз на сервере

```bash
# 1. Установить Docker
curl -fsSL https://get.docker.com | sh

# 2. Клонировать (по HTTPS; вместо пароля — Personal Access Token, либо настроить SSH-ключ)
git clone https://github.com/<user>/google-trends-parser.git ~/parser
cd ~/parser

# 3. Создать .env из прод-шаблона и заполнить
cp .env.prod.example .env
nano .env
#   - POSTGRES_PASSWORD  → openssl rand -hex 24
#   - DB_DSN             → тот же пароль, что в POSTGRES_PASSWORD
#   - API_KEY            → openssl rand -hex 32   (без него API открыт всем!)
#   - API_BIND           → 127.0.0.1 если discovery на этом же сервере, иначе 0.0.0.0

# 4. Собрать и поднять
docker compose -f docker-compose.prod.yml up -d --build

# 5. Применить миграции (одноразово)
docker compose -f docker-compose.prod.yml --profile tools run --rm migrate

# 6. Добавить прокси (парсер без них будет скрейпить с IP сервера и словит бан)
docker compose -f docker-compose.prod.yml exec api python scripts/proxy.py add http://user:pass@host:port
docker compose -f docker-compose.prod.yml exec api python scripts/proxy.py list

# 7. Проверить
curl http://localhost:8000/health          # {"status":"ok","db":"ok","redis":"ok"}
```

## Firewall (если API_BIND=0.0.0.0)

Открыть только нужное; БД/Redis наружу не торчат (в prod-compose у них нет host-портов).
Порт 8000 — только для тех, кто реально обращается к API (например, IP сервера discovery).

```bash
ufw allow ssh
ufw allow from <IP_discovery> to any port 8000   # только discovery, не весь мир
ufw enable
```

## Обновление кода

```bash
cd ~/parser
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml --profile tools run --rm migrate   # если есть новые миграции
```

## Обслуживание

```bash
# Логи
docker compose -f docker-compose.prod.yml logs -f api worker

# Перечитать список прокси воркерами без рестарта (после proxy.py add/disable)
docker compose -f docker-compose.prod.yml kill -s HUP worker

# Чистка старых данных (retention) — по крону раз в сутки
docker compose -f docker-compose.prod.yml --profile tools run --rm cleanup

# Масштаб воркеров: правишь WORKER_REPLICAS в .env, затем
docker compose -f docker-compose.prod.yml up -d
```

## Важно

- **Никакого cron'а опроса трендов на парсере.** Расписание опроса Trending Now живёт в discovery
  (см. `PENDING.md` и решение в discovery `docs/decisions.md` от 2026-07-08). Парсер парсит по запросу.
- `.env` содержит секреты — в git не коммитить (он в `.gitignore`).