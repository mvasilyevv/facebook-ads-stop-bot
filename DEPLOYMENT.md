# Production deployment

FB Agent использует один production slot. Краткая недоступность во время
выпуска допустима; blue/green, rollback journal, worker handoff и backup gates
не входят в runtime.

## Runtime

Стабильные Compose-проекты:

- `fb_agent_infra` — PostgreSQL и Redis;
- `fb_agent_app` — API, web, TMA и workers;
- `fb_agent_desktop` — Vision, browser-agent и брокеры RustDesk;
- `fb_agent_monitoring` — Prometheus, Loki, Tempo, Grafana и Alloy.

Caddy всегда направляет трафик на `18100` (API), `18080` (web) и `18081`
(TMA). Доступ к рабочему столу веб-канала не имеет: он идёт нативным
клиентом RustDesk через собственный брокер в приватной сети. Docker
`restart: unless-stopped` отвечает за запуск после reboot; отдельных
application systemd units нет.

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
одноразовые identity/profile/Docker/DB-ресурсы. Управляемая часть Caddy — два
site-файла, `caddy.env`, systemd drop-in, каталоги и права логов — исключение:
она сверяется с candidate-бандлом и обновляется каждым production deploy.

## Ресурсы после первого production bootstrap

После первого боевого bootstrap очищенный host не содержит ресурсов прежних
попыток: удалены network и volumes `fb_agent_safety_first_*`, четвёртый набор с
префиксом `current_*`, volume Guacamole и прежние images. Это зафиксированное
состояние host, а не действие `fbctl`: control bundle не удаляет legacy-ресурсы
и не доказывает отсутствие произвольных images.

Имена текущего контура с прежними наборами не пересекаются:

- network `fb_agent_platform`;
- volumes `fb_agent_infra_pgdata`, `fb_agent_infra_redisdata` и
  `fb_agent_app_campaign_uploads`;
- Compose-проекты `fb_agent_infra`, `fb_agent_app`, `fb_agent_desktop` и
  `fb_agent_monitoring`.

`fbctl` по-прежнему только информационно проверяет четыре точных имени
`fb_agent_safety_first_*`. Если они появятся снова, он сообщит о них и оставит
нетронутыми. Ресурсы `current_*`, Guacamole и legacy images в этот detector не
входят: их отсутствие подтверждается inventory host, а не `fbctl`.

**Контейнеры прежнего контура — отдельный случай.** Даже при разведённых именах
запущенный PostgreSQL или Redis может удерживать host-порты `5433` и `6380`.
Preflight обнаруживает это до первой мутации, называет контейнер и печатает
готовую команду. Остановить его нужно вручную; volumes и network `fbctl` не
трогает:

```bash
sudo docker stop <container>
```

## Мониторинг после смены имени сети

Проект `fb_agent_monitoring` не управляется `fbctl` и не пересоздаётся в
последовательности deploy. Пока он не пересоздан, `alloy-agent` остаётся в
прежней сети, алиас перестаёт резолвиться, и traces, логи и метрики теряются
молча — readiness при этом остаётся зелёным. После bootstrap с новым именем
сети мониторинг нужно поднять заново и убедиться, что targets снова видны:

```bash
docker compose -p fb_agent_monitoring -f deploy/monitoring/docker-compose.agent.yml up -d
```

Подготовка чистого host и точная последовательность команд описаны в разделе
«Первый запуск на чистом host». Manifest уже вложен в control bundle и не
передаётся в `deploy` отдельным аргументом.

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
  --desktop-profile-seed /opt/fb-agent/shared/desktop-profile-seed
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
9. атомарно синхронизирует управляемую Caddy-конфигурацию, валидирует общий
   Caddyfile и выполняет только `systemctl reload caddy`;
10. выполняет public smoke и только затем продвигает candidate configuration.

При ошибке `fbctl` возвращает ненулевой код и имя шага. Money workers не
запускаются до подтверждённой готовности safety-контура. Повтор той же команды
идемпотентен; автоматического rollback или запуска старого stack нет.

## Проверка конфигурации

```bash
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz doctor
```

`doctor` проверяет строгую конфигурацию без повторяющихся/неизвестных ключей,
manifest, executable modes, четыре Compose-файла, точное содержимое управляемых
Caddy-файлов, Caddy, digest-only images, browser capability isolation, порты и
свободное место. Проверка не меняет active configuration и не останавливает
runtime.

### Caddy в routine deploy

Caddy общий с другими сайтами, поэтому `fbctl` сохраняет остальное содержимое
общего Caddyfile и не трогает чужие site-файлы. Он управляет только точным import
`/etc/caddy/sites-enabled/*.caddy`, одним файлом сайта app.adpulse.su, собственным env и
drop-in; ушедший сайт рабочего стола удаляется. Panel BasicAuth-пара сохраняется из root-owned host env; `API_KEY` заново выводится из candidate configuration.

До записи на host управляемая пара валидируется в отдельном staging-каталоге.
Затем целевые файлы меняются атомарно, полный общий Caddyfile валидируется уже
на host, выполняются `systemctl daemon-reload` и `systemctl reload caddy`.
Прямой `caddy reload` запрещён: его default admin socket на этом host общий и
может остановить systemd-Caddy. Если live validation, daemon-reload или reload
падает, `fbctl` восстанавливает предыдущие файлы; после уже начатого reload он
повторно загружает восстановленную конфигурацию. Ошибка шага `sync_caddy`
происходит до public smoke и promotion, поэтому release не может завершиться
`READY` со старой Caddy-конфигурацией.

## Контракт каталога PostgreSQL

Frozen baseline создаёт и сразу проверяет собственную поверхность каталога:
extensions, functions, triggers, view и `CHECK`-ограничения перечислены в
`BASELINE_ARTIFACT_HASHES`. Проверка нужна, чтобы частичная, подменённая или
дополненная схема не получила корректный Alembic revision только за счёт stamp.

Ревизии после baseline объявляют свои артефакты отдельно, в
`POST_BASELINE_ARTIFACT_HASHES`. На голове база сверяется с суммой обоих
наборов — `HEAD_ARTIFACT_HASHES`. Разделение обязательно: один общий манифест
одновременно описывал бы и состояние сразу после baseline, и состояние на
голове, а это взаимоисключающие вещи.

Отсюда правило для новой ревизии, добавляющей function, trigger, view или
`CHECK`: её артефакты нужно внести в `POST_BASELINE_ARTIFACT_HASHES`, иначе
migration завершится с `catalog artifact drift: unexpected ...`. Это не повод
удалять объект вручную или stamp-ить БД — молча принимать незаявленные объекты
контракт не должен.

## Первый запуск на чистом host

### 1. Подготовить `shared`

`/opt/fb-agent` должен быть обычным root-owned каталогом mode `0755`, а
`/opt/fb-agent/shared` — обычным root-owned каталогом mode `0700`. Ограничение
закрывает подмену входов между preflight и их повторной проверкой под deploy
lock.

На чистой БД положить в `shared`:

- `source.env` — полный production source, root-owned regular file mode `0600`
  со `st_nlink == 1`, то есть без hard-link aliases;
- `adoption-bundle-v1.json` — проверенный adoption bundle с теми же требованиями
  к owner, типу inode, mode и `st_nlink`;
- `desktop-profile-seed` — одобренное дерево Vision, целиком `root:root`, с
  корнем mode `0700`.

Отдельные browser capability env-файлы в `shared` не нужны: `fbctl` выводит их
из `source.env` в release-specific candidate. Только для описанной выше identity
migration допускается root-owned regular file `shared/.env` mode `0600`; routine
runtime его источником не считает.

В `desktop-profile-seed` разрешены только каталоги и обычные файлы. Симлинки,
sockets, FIFO, devices и hard-linked файлы отвергаются как `unsafe entry`; все
элементы принадлежат `root:root`, не имеют group/world write, а корневой marker
`.fb-agent-vision-profile-v1` имеет mode `0600` и содержит строку
`fb-agent-vision-profile-v1` с завершающим LF. Это не даёт профилю вывести
чтение/копирование за проверенное дерево или заменить inode после snapshot.

Если `shared/vision-config` уже существует после неуспешной попытки, он
авторитетнее seed и обязан принадлежать runtime-пользователю `1000:1000`; его
корень имеет mode `0700`, а дерево — те же ограничения по типам записей и
write-битам. Bootstrap не скрывает повреждённый canonical profile переходом на
seed. При первом успешном копировании `fbctl` сам создаёт canonical profile,
назначает `1000:1000`, каталоги `0700` и файлы `0600`.

Безопасная подготовка чистого host выглядит так:

```bash
sudo install -d -o root -g root -m 0755 /opt/fb-agent
sudo install -d -o root -g root -m 0700 /opt/fb-agent/shared
sudo install -o root -g root -m 0600 /path/to/prepared-source.env \
  /opt/fb-agent/shared/source.env
sudo install -o root -g root -m 0600 /path/to/reviewed-adoption-bundle-v1.json \
  /opt/fb-agent/shared/adoption-bundle-v1.json
sudo cp -a -- /path/to/approved-desktop-profile-seed \
  /opt/fb-agent/shared/desktop-profile-seed
sudo chown -R root:root /opt/fb-agent/shared/desktop-profile-seed
sudo find /opt/fb-agent/shared/desktop-profile-seed -type d -exec chmod 0700 {} +
sudo find /opt/fb-agent/shared/desktop-profile-seed -type f -exec chmod 0600 {} +
```

До запуска проверить, что следующие команды ничего не печатают:

```bash
sudo find /opt/fb-agent/shared/desktop-profile-seed ! -type d ! -type f -print
sudo find /opt/fb-agent/shared/desktop-profile-seed -type f -links +1 -print
```

### 2. Проверить source и Caddy

`bootstrap-source-check` валидирует dotenv, allowlist ключей и bootstrap-only
Vision/Caddy значения, но не смотрит filesystem, Docker, БД или Caddyfile:

```bash
sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap-source-check --stdin \
  --project-known-legacy-source < /opt/fb-agent/shared/source.env
```

Для чистого source поле `dropped_keys` в результате должно быть пустым. Если
оно не пусто, не продолжать чистый bootstrap: либо убрать legacy-ключи из
source, либо использовать отдельно одобренный migration-переход.

В `/etc/caddy/Caddyfile` среди строк, импортирующих
`/etc/caddy/sites-enabled`, допустима только одна точная форма:

```caddyfile
import /etc/caddy/sites-enabled/*.caddy
```

Если такого импорта нет, bootstrap добавит его. Дубль или более широкий glob,
например `import /etc/caddy/sites-enabled/*`, считается конфликтом и
останавливает bootstrap. Суффикс `.caddy` ограничивает загрузку drop-in файлами
с ожидаемым расширением, а не любым файлом каталога.

Release `bootstrap-remote-preflight` дополнительно читает фиксированные
root-owned inputs и Vision profile без записи на host. Сам `bootstrap` повторно
проверяет identity и профиль под lock, затем до первой мутации проверяет все
published host-порты. Caddyfile проверяется позже, при provisioning Caddy.

### 3. Выполнить bootstrap и deploy

На чистом host с полной identity в `source.env` migration-флаги не нужны:

```bash
sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap \
  --source-env /opt/fb-agent/shared/source.env \
  --adoption-bundle /opt/fb-agent/shared/adoption-bundle-v1.json \
  --desktop-profile-seed /opt/fb-agent/shared/desktop-profile-seed
sudo python3 -B /path/to/reviewed/fbctl.pyz deploy --enable-scanning
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz doctor
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz status
```

После bootstrap поле `profile_seed_cleanup` должно быть `removed` либо
`not_applicable`, если seed уже был штатно удалён. Adoption import переносит
allowlisted конфигурацию и записывает receipt в той же транзакции PostgreSQL;
после полного успеха bundle и seed удаляются. История, runtime state и secrets
не импортируются.

### 4. Разобрать отказ

| Сообщение | Что делать |
| --- | --- |
| `owned directory with mode 700` или `not a private single-owner file` | Вернуть `shared` owner `root:root` и mode `0700`; фиксированные файлы сделать обычными single-link файлами `root:root` mode `0600`. Не заменять их симлинками. |
| `desktop profile seed ... unsafe entry` | Удалить из seed симлинки и non-file/non-directory записи, разорвать hard links, вернуть всему дереву `root:root` и убрать group/world write. Затем повторить ту же команду: этот отказ происходит до мутаций. |
| `managed Vision configuration ... unsafe entry` или `invalid ownership` | Исправлять `shared/vision-config`, а не seed: canonical profile уже выбран и должен принадлежать `1000:1000`. Не удалять его вслепую после частично успешного bootstrap. |
| `marker is invalid` | Вернуть одобренный profile seed с каноническим marker; не создавать пустой профиль ради прохождения gate. |
| `Caddyfile contains a conflicting FB Agent site import` | Заменить все imports `sites-enabled` одной точной строкой с `*.caddy` и повторить bootstrap. До этого места infra/БД уже могли быть подготовлены; повтор рассчитан на canonical retry. |
| `Host TCP port collision` | Выполнить напечатанную `sudo docker stop <container>` либо освободить названный host-порт у внешнего процесса. Volumes не удалять. |
| `adoption bundle owner contract is invalid` или `clean database bootstrap requires an adoption bundle` | Подать заново проверенный bundle, в котором единственный owner совпадает с `DESKTOP_OWNER_TELEGRAM_USER_ID`; не править receipt или БД вручную. |
| `source environment contains unsupported keys`, partial OIDC pair или owner mismatch | Снова прогнать `bootstrap-source-check`. `--project-known-legacy-source` и `--migrate-existing-bootstrap-identity` включать только для документированного migration-перехода; `--reuse-existing-caddy-credentials` нужен отдельно, если Caddy-пары нет в source. Половины OIDC-пары не смешивать. |
| `safety-first baseline catalog artifact drift: unexpected ...` | Остановить выпуск. Это несовместимость release manifest и migration, а не состояние, которое чинится на host; не удалять constraint и не stamp-ить Alembic. |

`preserved_changed_or_quarantined` в `profile_seed_cleanup` означает, что
receipt seed не совпал: каталог не удаляется вслепую, а возможный
`.fbctl-profile-cleanup-*` остаётся в `shared` для ручной проверки. Каталог
`.fbctl-profile-cleanup-unbound-*` сохраняется, если bootstrap не смог безопасно
открыть только что созданный staging-каталог; после устранения filesystem-ошибки
его также проверяют вручную.

## Operations

```bash
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz status
sudo python3 -B /opt/fb-agent/runtime/fbctl.pyz logs autopause_worker --lines 200
```

PostgreSQL backup/restore automation намеренно отсутствует по решению owner.
Удаление production volumes выполняется только по явной команде и только после
проверки нового runtime.
