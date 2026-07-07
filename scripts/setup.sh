#!/usr/bin/env bash
set -euo pipefail

echo "=== Google Trends Parser: setup ==="

# .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[ok] .env создан из .env.example — проверь значения перед запуском"
else
    echo "[ok] .env уже существует"
fi

# docker
echo ""
echo "--- Поднимаем контейнеры ---"
docker compose up -d --build

echo ""
echo "--- Применяем миграции ---"
docker compose exec api python migrations/run.py

# proxies
echo ""
proxy_count=$(docker compose exec api python scripts/proxy.py list | grep -c '^[0-9]' || true)
if [ "$proxy_count" -eq 0 ]; then
    echo "[!] Прокси не найдены в БД."
    read -rp "    Введи URL прокси (http://user:pass@host:port, Enter — пропустить): " proxy_url
    if [ -n "$proxy_url" ]; then
        docker compose exec api python scripts/proxy.py add "$proxy_url"
        docker compose kill -s HUP worker
        echo "[ok] Прокси добавлен, воркер уведомлён"
    else
        echo "[!] Пропущено. Добавь позже: python scripts/proxy.py add <url>"
    fi
else
    echo "[ok] Прокси в БД: $proxy_count"
fi

echo ""
echo "--- Статус ---"
docker compose ps
echo ""
echo "=== Готово ==="
echo "    API:    http://localhost:8000"
echo "    Docs:   http://localhost:8000/docs"
echo "    Health: http://localhost:8000/health"
