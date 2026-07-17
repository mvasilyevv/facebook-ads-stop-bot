# Production deployment — FB Agent

Основной production-контур проекта — один Linux-сервер с Docker Compose, Caddy и
отдельным Vision webtop. `run.sh` предназначен для локальной разработки. Helm и
raw Kubernetes-манифесты остаются экспериментальными и не являются поддерживаемым
способом выкладки money-критичного контура.

## Архитектура

| Компонент | Размещение | Внешний доступ |
|---|---|---|
| Caddy | systemd на хосте | `80/443` |
| React-панель | Docker, `127.0.0.1:8080` | `https://app.adpulse.su/`, Basic Auth |
| Telegram Mini App | Docker, `127.0.0.1:8081` | `https://app.adpulse.su/tma/` |
| FastAPI | Docker, `127.0.0.1:8100` | `/api`, `/ws`, `/healthz`, `/readyz` |
| Postgres / Redis | Docker, loopback | не публикуются наружу |
| Vision + Guacamole | отдельный digest-pinned stack, persistent `/config` и JDBC | `https://app.adpulse.su/desktop/` |
| browser-agent | Docker, network namespace Vision | gRPC через `127.0.0.1:50051` |

Vision и browser-agent разделяют network namespace. Это принципиально: Vision
выдаёт динамический CDP-порт на loopback, и публиковать диапазон CDP наружу не
нужно. Webtop публикует `3030` и `50051` только на loopback хоста.

## Требования к серверу

- Ubuntu x86_64, минимум 4 CPU / 8 GB RAM / 40 GB SSD;
- Docker Engine и Docker Compose v2;
- Caddy 2;
- активный firewall: наружу открыты только SSH, `80`, `443` и `443/udp`;
- DNS `app.adpulse.su` указывает на сервер;
- swap 4 GB рекомендуется как страховка от пиков сборки/Vision;
- локальный `.env` с `ENCRYPTION_KEY`, Vision, Telegram и API-секретами.

## Первая установка

### 1. Vision webtop

Существующий `/opt/vision-webtop/config` не удаляется: там находятся профиль,
cookies и настройки рабочего стола.

Production workflow собирает custom webtop в GHCR и передаёт installer его
immutable `image@sha256` reference. Installer не пересобирает desktop на VPS и
не пересоздаёт его при релизах, в которых manifest и desktop secrets не менялись.
Для ручного запуска укажите тот же immutable artifact:

```bash
DESKTOP_WEBTOP_IMAGE='ghcr.io/owner/repo-vision-webtop@sha256:...' \
  sudo -E ./scripts/install-vision-webtop.sh
```

Installer идемпотентно устанавливает официальную схему Guacamole 1.6.0 в
отдельный Postgres 16, создаёт principal `adpulse-desktop` и ровно одну READ-связь
`Vision Desktop`. Частично созданная или несовместимая схема приводит к fail-fast.
После установки:

```bash
curl -fsS http://127.0.0.1:8090/desktop/ >/dev/null
curl -fsS https://app.adpulse.su/desktop-readyz
docker logs --tail=100 vision-webtop
```

При первом запуске нужно открыть `https://app.adpulse.su/desktop/` и убедиться,
что Vision доступен через единственную Guacamole-связь. Отдельного desktop-host,
Selkies fallback или второго Guacamole login в production нет.

### 2. Приложение

Из рабочей копии на операторской машине:

```bash
./scripts/deploy-server.sh --allow-vision-offline
```

По умолчанию цель — `root@62.60.150.133`, корень — `/opt/fb-agent`, публичный URL
— `https://app.adpulse.su`. Значения можно изменить:

```bash
./scripts/deploy-server.sh \
  --host root@example.org \
  --root /opt/fb-agent \
  --public-url https://app.example.org
```

Перед записью на сервер доступен честный dry-run rsync:

```bash
./scripts/deploy-server.sh --dry-run
```

Deployment:

1. использует существующий server-side `.env`, если он уже есть;
2. на первой установке генерирует сильный Postgres-пароль и TMA session secret;
3. валидирует Fernet-ключ, production-флаги, права `0600`, диск и память;
4. загружает рабочее дерево без `.git`, `.env`, `data/`, зависимостей и build output;
5. собирает образы с неизменяемым release tag;
6. делает `pg_dump` перед миграциями, если БД уже существует;
7. запускает миграции и весь стек, ждёт Docker healthchecks;
8. проверяет `/healthz` и `/readyz` и только потом переключает `current`;
9. при ошибке возвращает предыдущие образы. БД автоматически назад не
   восстанавливается, потому что такой rollback может потерять новые данные.

`--allow-vision-offline` допускается только для первоначальной инфраструктурной
выкладки и пропускает только проверку Vision API/CDP. Контейнер `vision-webtop`
уже должен быть запущен: `browser-agent` разделяет его network namespace, поэтому
при отсутствующем или остановленном контейнере Docker физически не сможет создать
`browser-agent`, и preflight завершится ошибкой даже с этим флагом. В разрешённом
degraded-режиме `/readyz` должен быть зелёным, а `/system-readyz` честно остаётся
красным до входа в Vision и готовности auto-stop контура.

### 3. Caddy и systemd

Создайте `/etc/fb-agent/caddy.env` с правами `0600`:

```dotenv
PANEL_BASIC_AUTH_USER=operator
PANEL_BASIC_AUTH_HASH='$2a$...bcrypt-hash...'
```

`API_KEY` вручную в этот файл копировать не нужно. При установке каждого release
`scripts/sync-caddy-env.py` читает его из `/opt/fb-agent/shared/.env` как данные
(без shell `source/eval`) и атомарно обновляет только server-side Caddy env.
Frontend получает доступ через BasicAuth, а Caddy добавляет ключ в upstream;
секрет не попадает в JS bundle, browser storage или WebSocket URL.

Затем:

```bash
sudo /opt/fb-agent/current/scripts/install-server-units.sh
```

Скрипт устанавливает единственный Caddy site `app.adpulse.su` с защищённым
same-origin маршрутом `/desktop/*` и устанавливает:

- `fb-agent.service` — автозапуск текущего release;
- `fb-agent-backup.timer` — ежедневный `pg_dump`;
- `fb-agent-healthcheck.timer` — host-level readiness каждые 5 минут.

## Структура релизов

```text
/opt/fb-agent/
├── current -> releases/20260713T...-<git-sha>
├── releases/                       # последние 5 релизов
├── shared/.env                     # 0600, не находится в release
└── backups/postgres/               # custom-format dump + sha256
```

Все Compose-запуски используют стабильное имя проекта `fb_agent`; поэтому volume
БД, Redis и uploads не меняются между релизами.

## Проверка после выкладки

```bash
sudo systemctl status fb-agent --no-pager
sudo /opt/fb-agent/current/scripts/server-compose.sh status
sudo /opt/fb-agent/current/scripts/server-compose.sh ready

curl -fsS http://127.0.0.1:8100/healthz
curl -fsS http://127.0.0.1:8100/readyz
curl -fsS http://127.0.0.1:8100/desktop-readyz
curl -i https://app.adpulse.su/               # 401 без Basic Auth
curl -fsS https://app.adpulse.su/tma/ >/dev/null
curl -fsS https://app.adpulse.su/healthz
```

`/system-readyz` — более строгая проверка: она учитывает Vision, browser-agent,
heartbeat money-воркеров, Meta API и включённость сканирования. Её нельзя
подменять инфраструктурной `/readyz` в процессе деплоя.

Логи:

```bash
sudo /opt/fb-agent/current/scripts/server-compose.sh logs
sudo journalctl -u fb-agent -u fb-agent-healthcheck --since today
```

## Бэкап и восстановление

Ручной бэкап:

```bash
sudo /opt/fb-agent/current/scripts/backup-postgres.sh
sudo systemctl start fb-agent-backup.service
```

Проверка dump без восстановления:

```bash
sha256sum -c /opt/fb-agent/backups/postgres/<backup>.dump.sha256
docker compose -p fb_agent exec -T postgres pg_restore --list \
  </opt/fb-agent/backups/postgres/<backup>.dump >/dev/null
```

Восстановление — отдельная аварийная операция: остановить writers, сделать ещё
один backup текущего состояния, восстановить dump в пустую БД, применить
миграции и только затем снова запускать воркеры. Пошаговый сценарий находится в
`docs/playbooks/RUNBOOKS.md`.

## Ручной rollback приложения

```bash
previous=/opt/fb-agent/releases/<release-id>
sudo ln -sfn "$previous" /opt/fb-agent/current.new
sudo mv -Tf /opt/fb-agent/current.new /opt/fb-agent/current
sudo systemctl restart fb-agent
```

Rollback кода не означает rollback схемы. Перед откатом убедитесь, что старая
версия совместима с уже применёнными миграциями.

## Локальная разработка

```bash
cp .env.example .env
make bootstrap
./run.sh
```

Этот путь использует локальные процессы/supervisord и не должен применяться как
production init system.

## Kubernetes

`helm/` и `k8s/` сохранены для исследований. Пока chart не проходит тот же набор
readiness, backup/restore и Vision co-location тестов, production поддерживается
только через Compose-процедуру выше.
