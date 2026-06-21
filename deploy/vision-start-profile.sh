#!/usr/bin/env bash
# Автостарт боевого Vision-профиля (FB) через Vision API после старта Vision.
# Идемпотентно: если профиль уже с CDP-портом — ничего не делает.
# Ставится в /usr/local/bin/, вызывается из vision-autostart.service.
#
# Берёт VISION_X_TOKEN / VISION_FOLDER_ID / VISION_PROFILE_ID из /opt/fb_agent/.env.
set -u
ENV_FILE=/opt/fb_agent/.env
API=http://127.0.0.1:3030
TOKEN=$(grep "^VISION_X_TOKEN=" "$ENV_FILE" | cut -d= -f2-)
FOLDER=$(grep "^VISION_FOLDER_ID=" "$ENV_FILE" | cut -d= -f2-)
PROFILE=$(grep "^VISION_PROFILE_ID=" "$ENV_FILE" | cut -d= -f2-)

# Ждём готовности Vision API (до 120с).
for i in $(seq 1 60); do
  curl -sf --max-time 5 -H "X-Token: $TOKEN" "$API/list" >/dev/null 2>&1 && break
  sleep 2
done

# Даём Vision досинкать профили из облака после холодного старта.
sleep 20

# Уже запущен с портом? — выходим.
if curl -s --max-time 8 -H "X-Token: $TOKEN" "$API/list" 2>/dev/null | grep -qE "\"port\": *[0-9]"; then
  echo "vision-start-profile: профиль уже запущен"
  exit 0
fi

echo "vision-start-profile: стартую профиль $PROFILE (folder $FOLDER)"
curl -s --max-time 30 -H "X-Token: $TOKEN" "$API/start/$FOLDER/$PROFILE" 2>/dev/null
echo
