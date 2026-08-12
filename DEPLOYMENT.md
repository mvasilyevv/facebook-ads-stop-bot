# Production deployment

FB Agent использует один production slot. Краткая недоступность во время
выпуска допустима; blue/green, rollback journal, worker handoff и backup gates
не входят в runtime.

## Runtime

Стабильные Compose-проекты:

- `fb_agent_infra` — PostgreSQL и Redis;
- `fb_agent_app` — API, web, TMA и workers;
- `fb_agent_desktop` — Vision/KasmVNC и browser-agent;
- `fb_agent_monitoring` — Prometheus, Loki, Tempo, Grafana и Alloy.

Caddy всегда направляет трафик на `18100` (API), `18080` (web), `18081`
(TMA) и `8444` (desktop). Docker `restart: unless-stopped` отвечает за запуск
после reboot; отдельных application systemd units нет.

## Управление production

Единственная поддерживаемая командная поверхность — `fbctl`. Это
самодостаточный Python control bundle: серверу не нужен checkout репозитория и
на нём не выполняется сборка images.

```bash
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz doctor
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz status
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz deploy
```

`fbctl bootstrap` используется один раз на новом host или новой чистой БД. Он
создаёт host directories, fixed Caddy configuration, Compose network/volumes,
применяет baseline, импортирует adoption bundle и активирует desktop profile.
Обычный `deploy` не принимает adoption bundle или desktop seed и не изменяет
host provisioning.

На новом host bootstrap вызывается с явными локальными путями к подготовленным
секретам и конфигурации; manifest уже вложен в control bundle и не передаётся
в `deploy` отдельным аргументом:

```bash
sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap \
  --source-env /opt/fb-agent/shared/source.env \
  --adoption-bundle /opt/fb-agent/shared/adoption-bundle-v1.json \
  --desktop-profile-seed /opt/fb-agent/shared/vision-profile-seed
sudo python3 -B /path/to/reviewed/fbctl.pyz deploy --enable-scanning
```

Для единственного проверенного перехода со старого host identity Release
workflow до сборки images отправляет маленький deterministic preflight bundle и
source только через stdin. Проверка читает фиксированные root-owned файлы
`/opt/fb-agent/shared/source.env` и `/opt/fb-agent/shared/.env`, ничего не пишет
и не запускает Docker/БД. Реальный bootstrap повторяет ту же проверку с явным
`--migrate-existing-bootstrap-identity`.

Миграция наследует только атомарную пару `TELEGRAM_OIDC_CLIENT_ID` +
`TELEGRAM_OIDC_CLIENT_SECRET` и отдельно
`DESKTOP_OWNER_TELEGRAM_USER_ID`: explicit source → canonical retry → legacy
`.env` → проверенный owner adoption bundle. Половина OIDC-пары, неверное
значение или owner mismatch блокируют bootstrap; `TELEGRAM_CHAT_ID` и остальные
legacy-поля никогда не импортируются. Canonical `source.env` сохраняется до
Docker/DB, а исходный `.env` удаляется только после полного успеха и только если
путь всё ещё указывает на проверенный inode. Флаг недоступен routine deploy и
rehearsal.

```bash
sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap-source-check --stdin \
  --project-known-legacy-source < /opt/fb-agent/shared/source.env
sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap \
  --project-known-legacy-source --reuse-existing-caddy-credentials \
  --migrate-existing-bootstrap-identity \
  --source-env /opt/fb-agent/shared/source.env \
  --adoption-bundle /opt/fb-agent/shared/adoption-bundle-v1.json \
  --desktop-profile-seed /opt/fb-agent/shared/vision-profile-seed
```

Caddy credentials не копируются из source в canonical runtime: при их
отсутствии bootstrap использует только уже проверенную root-owned пару host.

`--enable-scanning` — осознанный первый запуск observer после готовности
desktop/browser-agent; без флага `deploy` сохраняет текущее DB-состояние
scanning. Receipt import проверяется из PostgreSQL, поэтому сброс БД не может
быть ошибочно признан уже импортированным из-за файлов на host.

CI вычисляет hashes реальных build inputs, переиспользует опубликованные
образы и формирует маленький control bundle только с `fbctl`, Compose/Caddy
конфигурацией и manifest из `image@sha256` ссылок.

Routine deploy:

1. проверяет candidate config, manifest, secrets и Compose до остановки;
2. загружает все immutable images;
3. останавливает app и desktop;
4. поднимает infra и применяет forward-only Alembic migrations;
5. проверяет desktop, точный Vision profile, Graph и browser-agent;
6. поднимает API/web/TMA и проверяет typed operator snapshot;
7. применяет и проверяет Telegram webhook до запуска delivery worker;
8. поднимает workers и ждёт heartbeats плюс `/system-readyz`;
9. выполняет public smoke и только затем продвигает candidate configuration.

При ошибке `fbctl` возвращает ненулевой код и имя шага. Money workers не
запускаются до подтверждённой готовности safety-контура. Повтор той же команды
идемпотентен; автоматического rollback или запуска старого stack нет.

## Проверка конфигурации

```bash
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz doctor
```

`doctor` проверяет строгую конфигурацию без повторяющихся/неизвестных ключей,
manifest, executable modes, четыре Compose-файла, Caddy, digest-only images,
browser capability isolation, порты и свободное место. Проверка не меняет
active configuration и не останавливает runtime.

## First release

До однократного `bootstrap` в `/opt/fb-agent/shared` должны существовать:

- `source.env` с production-конфигурацией;
- browser capability env-файлы mode `0600`;
- `adoption-bundle-v1.json` mode `0600`;
- `desktop-profile-seed` с проверенным Vision profile.

Только для описанной identity migration может дополнительно существовать
`/opt/fb-agent/shared/.env` mode `0600`, принадлежащий root. Это не второй
конфигурационный источник routine runtime.

Adoption import переносит allowlisted конфигурацию один раз и записывает receipt
в той же транзакции PostgreSQL. После успешного bootstrap bundle и seed с host
удаляются. История, runtime state и secrets не импортируются.

## Operations

```bash
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz status
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz logs autopause_worker --lines 200
```

PostgreSQL backup/restore automation намеренно отсутствует по решению owner.
Удаление production volumes выполняется только по явной команде и только после
проверки нового runtime.
