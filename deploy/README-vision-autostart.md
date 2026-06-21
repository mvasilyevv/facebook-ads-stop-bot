# Автозапуск Vision на 24/7-сервере (headless через Xvfb)

Чтобы канал авто-стопа работал круглосуточно (в т.ч. переживал перезагрузку
сервера), Vision запускается headless на виртуальном дисплее Xvfb через systemd —
без NoMachine-десктопа и ручного входа.

## Цепочка автозапуска

```
ребут → xvfb.service (:99) → vision-wm.service (xfwm4 — рамки окон) →
        vision.service (Vision на :99, логин из keyring, синк профилей) →
        vision-autostart.service (стартует боевой профиль через Vision API /start) →
        fb-browser-agent.service (gRPC :50051, CDP)
```

При включённом сканировании observer держит постоянную сессию; probe
(`meta_api:channel:health`) показывает статус канала.

## Установка на новом сервере

```bash
# 1. Зависимости
apt-get install -y xvfb x11vnc dbus-x11 xdotool scrot

# 2. Скрипт автостарта профиля
cp deploy/vision-start-profile.sh /usr/local/bin/
chmod +x /usr/local/bin/vision-start-profile.sh

# 3. Юниты systemd (vision-wm = оконный менеджер xfwm4 → рамки окон Vision)
cp deploy/xvfb.service deploy/vision-wm.service deploy/vision.service \
   deploy/vision-autostart.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xvfb.service vision-wm.service vision.service vision-autostart.service

# 4. В /opt/fb_agent/.env должны быть заданы:
#    VISION_X_TOKEN, VISION_API_URL, VISION_PROFILE_ID, VISION_FOLDER_ID
```

## Важные нюансы

- **Логин в Vision-аккаунт** хранится в системном keyring (Vision линкуется с
  libsecret). Headless-сессия его подхватывает — но если keyring при первой
  настройке создавался с паролем, после чистой переустановки ОС нужно один раз
  залогиниться в Vision (через x11vnc на :99 или NoMachine), чтобы токен лёг в
  keyring. Профили синкаются из облака автоматически.
- **VISION_FOLDER_ID** — id папки Vision, где лежит боевой профиль. Vision API
  отдаёт folder_id только для уже ЗАПУЩЕННОГО профиля (`GET /list`), поэтому он
  вынесен в `.env`. Узнать заново: запустить профиль (клик «Старт» в GUI или
  `xdotool` на :99), затем `curl -H "X-Token: $TOKEN" http://127.0.0.1:3030/list`.
- **Посмотреть/управлять Vision через NoMachine:** в `/usr/NX/etc/node.cfg` задано
  `PhysicalDisplays :99` — NoMachine при подключении к «physical desktop» цепляется
  к существующему :99 с Vision (xfwm4 даёт рамки окон), а НЕ создаёт пустой
  виртуальный desktop. После правки node.cfg: `/usr/NX/bin/nxserver --restart`.
  В клиенте подключайся к **physical desktop** (не «new virtual desktop»).
  Xvfb :99 запущен без `-auth` (открытый локальный доступ) → cookie не нужен.
- **Посмотреть headless-экран** (отладка, без NoMachine): на сервере `x11vnc -display
  :99 -localhost -rfbport 5900`, затем с ноута `ssh -L 5900:localhost:5900 root@<srv>`
  и VNC-клиент на `localhost:5900`.
- **Снимок экрана** :99: `DISPLAY=:99 sudo -u mark HOME=/home/mark scrot -o /tmp/v.png`.

## Проверка после ребута

```bash
systemctl is-active xvfb vision vision-autostart fb-browser-agent
curl -s -H "X-Token: $(grep ^VISION_X_TOKEN= /opt/fb_agent/.env|cut -d= -f2-)" \
     http://127.0.0.1:3030/list        # профиль должен быть с "port": <число>
curl -s -X POST http://127.0.0.1:8100/api/vision/reconnect   # {"status":"reconnected"}
```
