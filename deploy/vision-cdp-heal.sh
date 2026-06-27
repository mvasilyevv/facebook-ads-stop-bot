#!/bin/sh
# Probe-driven самовосстановление Vision-канала (browser-agent), раз в 60с.
#
# Старая версия проверяла только heartbeat cdp_ready — и пропускала случай
# «CDP up, страница мертва» (реальный инцидент 2026-06-27: канал лежал, heal молчал).
# Теперь — РЕАЛЬНЫЙ probe (GET /me изнутри Vision). Если канал мёртв:
#   L1 (каждый прогон): ensure-cdp — дешёвый reconnect browser-agent к профилю.
#   L2 (после ESCALATE_AFTER подряд + кулдаун): vision-refresh-token.py --force —
#       тяжёлый recovery: свежий токен + рестарт browser-agent + start профиля +
#       ensure-cdp + probe. Чинит потерю страницы И протухший токен.
#
# Идемпотентно, с кулдауном — без флапа боевого браузера.
set -u

FAILS=/run/vision-heal.fails
LAST_HEAVY=/run/vision-heal.last-heavy
ESCALATE_AFTER=3       # ~3 прогона (≈3 мин) подряд down → тяжёлая эскалация
HEAVY_COOLDOWN=600     # тяжёлый recovery не чаще раза в 10 мин

systemctl is-active --quiet xvfb.service vision.service || {
  echo "xvfb/vision не активны — пропуск"
  exit 0
}

probe_healthy() {
  # 0 (true) если канал жив: реальный GET /me изнутри Vision через meta_api-контейнер.
  out=$(docker exec fb_agent-meta_api-1 python -c '
import asyncio,os,json
from core.meta_api.client import MetaApiClient
async def m():
    c=MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST","host.docker.internal"),port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT","50051")))
    await c.start(); print(json.dumps(await c.check_health(full_probe=True)))
asyncio.run(m())' 2>/dev/null | tail -1)
  echo "$out" | grep -q '"healthy": *true'
}

# --- здоров? сброс счётчика и выход ---
if probe_healthy; then
  rm -f "$FAILS"
  exit 0
fi

fails=$(cat "$FAILS" 2>/dev/null || echo 0)
fails=$((fails + 1))
echo "$fails" > "$FAILS"
echo "канал DOWN (подряд $fails) → L1 ensure-cdp"

# --- L1: дешёвый reconnect ---
docker exec fb_agent-api-1 sh -c \
  'curl -s --max-time 120 -X POST -H "X-API-Key: $API_KEY" http://localhost:8100/api/vision/ensure-cdp' \
  >/dev/null 2>&1 || true
sleep 4
if probe_healthy; then
  echo "L1 ensure-cdp помог — канал healthy"
  rm -f "$FAILS"
  exit 0
fi

# --- L2: тяжёлый recovery (с эскалацией + кулдауном) ---
[ "$fails" -lt "$ESCALATE_AFTER" ] && {
  echo "ещё не эскалирую (fails<$ESCALATE_AFTER)"
  exit 0
}
now=$(date +%s)
last=$(cat "$LAST_HEAVY" 2>/dev/null || echo 0)
[ $((now - last)) -lt "$HEAVY_COOLDOWN" ] && {
  echo "L2 на кулдауне ($((now - last))с) — жду"
  exit 0
}
echo "$now" > "$LAST_HEAVY"
echo "L2 ТЯЖЁЛЫЙ recovery: vision-refresh-token.py --force (свежий токен + полный рестарт)"
python3 /usr/local/bin/vision-refresh-token.py --force 2>&1 | sed 's/^/  /'
if probe_healthy; then
  echo "L2 recovered — канал healthy"
  rm -f "$FAILS"
else
  echo "L2 не поднял канал — следующий прогон попробует снова после кулдауна"
fi
