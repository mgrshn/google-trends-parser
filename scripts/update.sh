#!/usr/bin/env bash
# Обновление парсера на сервере: стянуть код, пересобрать контейнеры, применить миграции.
# Запуск с сервера:  bash scripts/update.sh
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "→ git pull"
git pull

echo "→ пересборка и перезапуск контейнеров"
$COMPOSE up -d --build

echo "→ применение миграций (если есть новые)"
$COMPOSE --profile tools run --rm migrate

echo "→ проверка health"
sleep 3
if curl -fsS http://127.0.0.1:8000/health > /dev/null; then
  echo ""
  echo "════════════════════════════════════════════"
  echo "  ✅  ПАРСЕР ОБНОВЛЁН И РАБОТАЕТ (health: ok)"
  echo "════════════════════════════════════════════"
else
  echo ""
  echo "⚠️   Код обновлён, но /health не отвечает."
  echo "     Смотри логи:  docker compose -f docker-compose.prod.yml logs --tail 50 api"
  exit 1
fi
