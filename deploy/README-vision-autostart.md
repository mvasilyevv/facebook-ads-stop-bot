# Автозапуск Vision + удалённый доступ на 24/7-сервере

Чтобы канал авто-стопа работал круглосуточно (в т.ч. переживал перезагрузку
сервера), Vision запускается headless на виртуальном дисплее Xvfb через systemd.
Удалённый доступ к рабочему столу — через self-hosted RustDesk (см. ниже).

## Цепочка автозапуска

```
ребут → xvfb.service (:99) → vision-desktop.service (полный XFCE — панель + WM) →
        vision.service (Vision на :99, логин из keyring, синк профилей) →
        vision-autostart.service (стартует боевой профиль через Vision API /start) →
        fb-browser-agent.service (gRPC :50051, CDP)
```

При включённом сканировании observer держит постоянную сессию; probe
(`meta_api:channel:health`) показывает статус канала.

## Установка на новом сервере

```bash
# 1. Зависимости (Xvfb + полный XFCE + утилиты)
apt-get install -y xvfb xfce4 xfce4-goodies x11vnc dbus-x11 xdotool scrot

# 2. Скрипт автостарта профиля
cp deploy/vision-start-profile.sh /usr/local/bin/
chmod +x /usr/local/bin/vision-start-profile.sh

# 3. Юниты systemd (vision-desktop = полный рабочий стол XFCE на :99)
cp deploy/xvfb.service deploy/vision-desktop.service deploy/vision.service \
   deploy/vision-autostart.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xvfb.service vision-desktop.service vision.service vision-autostart.service

# 4. В /opt/fb_agent/.env должны быть заданы:
#    VISION_X_TOKEN, VISION_API_URL, VISION_PROFILE_ID, VISION_FOLDER_ID
```

## Удалённый доступ к рабочему столу — RustDesk (self-hosted)

NoMachine удалён (плодил пустые десктопы, кривой UX). Вместо него — собственный
RustDesk-сервер (приватный relay, без публичного сервера и логина).

```bash
# сервер RustDesk (relay + ID)
cd /opt/rustdesk-server && docker compose up -d        # см. rustdesk-server/docker-compose.yml
ufw allow 21115:21119/tcp && ufw allow 21116/udp
cat /opt/rustdesk-server/data/id_ed25519.pub           # → Key для клиентов

# RustDesk-клиент НА СЕРВЕРЕ (шарит :99), привязка к своему relay в RustDesk2.toml [options]:
#   custom-rendezvous-server = '127.0.0.1'
#   key = '<id_ed25519.pub>'
# + unattended: approve-mode = 'password', verification-method = 'use-permanent-password'
# systemd-служба rustdesk (Environment=DISPLAY=:99, XAUTHORITY=/home/mark/.Xauthority)
rustdesk --get-id            # ID для подключения
rustdesk --password <PW>     # постоянный пароль
```

**Клиент (Mac/iOS — App Store «RustDesk»):** Settings → Network → ID/Ретранслятор →
ID-сервер = `<IP сервера>`, Key = `<id_ed25519.pub>`. Затем подключение по ID + паролю.
Показывает весь рабочий стол сервера, без выбора/дублей экранов.

## Важные нюансы

- **Логин в Vision-аккаунт** хранится в системном keyring (Vision линкуется с
  libsecret). Headless-сессия его подхватывает — но если keyring при первой
  настройке создавался с паролем, после чистой переустановки ОС нужно один раз
  залогиниться в Vision (через RustDesk/x11vnc на :99), чтобы токен лёг в keyring.
- **VISION_FOLDER_ID** — id папки Vision, где лежит боевой профиль. Vision API
  отдаёт folder_id только для уже ЗАПУЩЕННОГО профиля (`GET /list`), поэтому он
  вынесен в `.env`. Узнать заново: запустить профиль (клик «Старт» в GUI или
  `xdotool` на :99), затем `curl -H "X-Token: $TOKEN" http://127.0.0.1:3030/list`.
- **RustDesk relay:** hbbs запускать с `-r <IP>:21117`, иначе peer может сообщить
  клиенту `127.0.0.1` как relay → внешний клиент не подключится («невозможно к ретранслятору»).
- **Снимок экрана** :99: `DISPLAY=:99 sudo -u mark HOME=/home/mark scrot -o /tmp/v.png`.

## Проверка после ребута

```bash
systemctl is-active xvfb vision-desktop vision vision-autostart fb-browser-agent rustdesk
curl -s -H "X-Token: $(grep ^VISION_X_TOKEN= /opt/fb_agent/.env|cut -d= -f2-)" \
     http://127.0.0.1:3030/list        # профиль должен быть с "port": <число>
curl -s -X POST http://127.0.0.1:8100/api/vision/reconnect   # {"status":"reconnected"}
```
