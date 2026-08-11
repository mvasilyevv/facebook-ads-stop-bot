# Production runbooks

Актуальные сценарии для safety-first production contour. Команды ниже не
выполняют денежные действия и не изменяют данные, если это явно не указано.
Любой manual pause/activate выполняется через operator UI или `CommandService`,
а не прямым SQL или вызовом Meta.

Пути предполагают control bundle `/opt/fb-agent/runtime/fbctl` и state directory
`/opt/fb-agent/shared`. Прямой `docker compose` используется только при
диагностике самого `fbctl`.

## Первичная диагностика

```bash
sudo /opt/fb-agent/runtime/fbctl doctor
sudo /opt/fb-agent/runtime/fbctl status
```

В operator UI сначала проверить:

- состояние и `as_of` каждого источника;
- ranked attention feed;
- выполняемые, `failed` и `unknown` actions;
- notification diagnostics;
- correlation ID инцидента.

`partial`, `stale`, `unavailable` и `unknown` — самостоятельные состояния. Их
нельзя трактовать как исправный источник или подтверждённый ноль.

## Browser/Vision недоступен

Симптомы: source `unavailable`, actor кабинета не обновляет snapshot, gRPC
deadline/connection error, Meta action завершилась `UNKNOWN`.

```bash
sudo /opt/fb-agent/runtime/fbctl status
sudo /opt/fb-agent/runtime/fbctl logs vision-webtop --lines 200
sudo /opt/fb-agent/runtime/fbctl logs browser-agent --lines 200
```

Порядок действий:

1. Проверить фактическую готовность Kasm/Vision desktop, а не только открытый
   TCP-порт.
2. Открыть нужный Ads Manager cabinet и убедиться, что Facebook session жива.
3. Проверить, что actor показывает правильный `ad_account_id`, stage, lease и
   свежий snapshot.
4. Если desktop исправен, перезапустить только browser-agent:

   ```bash
   sudo /opt/fb-agent/runtime/fbctl restart browser-agent
   ```

5. Не перезапускать весь application runtime: scan и control pages независимы.
6. Для action со статусом `UNKNOWN` дождаться reconciliation. Не создавать
   вторую задачу с другим idempotency key.

## Worker offline или stale

```bash
sudo /opt/fb-agent/runtime/fbctl status
sudo /opt/fb-agent/runtime/fbctl logs <service> --lines 200
```

Проверить Prometheus alerts `FBWorkerMetricsAbsent` и
`FBWorkerMetricsTargetDown`: отсутствие серии и недоступность target имеют
разные причины. Для локального endpoint каждый worker слушает собственный
metrics port; карта портов — в [../worker_metrics.md](../worker_metrics.md).

Перезапускать весь runtime не нужно. Если требуется рестарт одного container,
использовать поддерживаемую команду:

```bash
sudo /opt/fb-agent/runtime/fbctl restart <service>
sudo /opt/fb-agent/runtime/fbctl status
```

Для `autopause_worker` после рестарта обязательно подтвердить, что существует
ровно один владелец money lease. Обычный `meta_api` не должен claim-ить
`lane=money`.

## Telegram updates или delivery не работают

Telegram принимает updates только webhook-путём. Не включать polling и не
вызывать Bot API вручную из business code.

1. Открыть Settings → Telegram → Diagnostics. Проверить webhook readiness,
   последний committed update, backlog inbox/replies/deliveries, dead letters и
   recipient state.
2. Проверить оба worker:

   ```bash
   sudo /opt/fb-agent/runtime/fbctl logs telegram_update_worker --lines 200
   sudo /opt/fb-agent/runtime/fbctl logs telegram_delivery_worker --lines 200
   ```

3. Проверить ingress через Caddy и API logs. `204` допустим только после commit
   строки `telegram_updates_inbox`.
4. `401` означает отозванный bot token и должен открыть critical incident.
   Ввести новый token через защищённые Settings и повторно применить webhook
   штатным configurator во время release.
5. `403` отключает конкретного recipient; не включать его обратно без
   подтверждения владельца.
6. `429` переносит `scheduled_at` на полный `retry_after`. Не рестартовать worker
   ради ускорения и не создавать duplicate delivery.
7. Invalid HTML остаётся dead letter. Исправить renderer/template и явно
   переиздать snapshot; скрытой text fallback-отправки нет.
8. Если пользователь удалил редактируемую карточку, ожидается событие
   `incident_snapshot_reissued`, а не молчаливое новое сообщение.

Для read-only проверки очередей использовать Settings → Telegram → Diagnostics
и машинный снимок `sudo /opt/fb-agent/runtime/fbctl status --json`. Прямой SQL
не является штатным production interface.

Не помещать bot token в URL, shell history, ticket, exception или лог.

## Money action зависла или стала UNKNOWN

Сначала найти action в UI по correlation ID. `202` означает только `queued`.

Read-only снимок очереди доступен в Actions и через
`sudo /opt/fb-agent/runtime/fbctl status --json`. Прямой SQL не используется как
операционный обход `CommandService`.

Дальше:

- `pending`: проверить `available_at`, deadline и наличие active money lease;
- `running`: проверить lease expiry, owner identity и browser deadline;
- `UNKNOWN`: сверить фактический Meta status через reconciliation;
- stale fencing token: не завершать задачу вручную и не переписывать token;
- create/duplicate: после неоднозначного ответа сначала искать side effect в
  Meta, затем принимать ручное решение.

Запрещено переводить task в success прямым SQL. Если нужна отмена, использовать
поддерживаемую operator action: она записывает причину, CAS и notification event.

## Snapshot stale или отсутствуют данные

1. Проверить `state`, `as_of`, freshness, sources и issues каждой секции.
2. Сопоставить actor кабинета с последним `cabinet_runtime` snapshot.
3. Убедиться, что кабинет явно настроен. Пустой scan set должен завершиться
   fail-closed skip; произвольная открытая вкладка не сканируется.
4. Проверить раздельно scan-page и control-page. Ошибка scan не должна блокировать
   pause/activate.
5. После WS sequence gap клиент должен сделать одно snapshot reconciliation.
   Частые полные refetch указывают на ошибку sequence/revision contract.

Нельзя заполнять gaps нулями или показывать stale source зелёным.

## PostgreSQL или очередь недоступны

PostgreSQL — обязательный control plane. При его недоступности money actions и
notification commits должны fail closed. Redis может быть недоступен: система
продолжает DB polling в degraded режиме.

```bash
sudo /opt/fb-agent/runtime/fbctl status
sudo /opt/fb-agent/runtime/fbctl logs postgres --lines 200
sudo /opt/fb-agent/runtime/fbctl logs redis --lines 200
```

После рестарта PostgreSQL consumers перечитывают БД; потеря `LISTEN/NOTIFY` не
теряет committed work. Проверить queue age, expired leases и reconciliation,
затем проверить `/readyz` и operator snapshot.

## Failed deployment

`fbctl deploy` печатает точный `step`, на котором остановился. До полной
готовности safety-контура money workers остаются выключенными. Infra и уже
завершённая forward migration сохраняются. Исправить причину и повторить тот же
manifest.

```bash
sudo /opt/fb-agent/runtime/fbctl doctor
sudo /opt/fb-agent/runtime/fbctl deploy --manifest release.json
```

Не запускайте удалённые old release scripts и не переключайте старый runtime.
Автоматического rollback нет. PostgreSQL backup/restore automation также
намеренно отсутствует по решению owner.

## Запрещённые операции

- production schema wipe или `DROP SCHEMA`;
- прямое изменение money task status/lease/fencing token;
- повтор create/duplicate после `UNKNOWN` без reconciliation;
- прямой Telegram send из worker/business code;
- включение второго money consumer;
- production build на VPS или запуск image по mutable tag;
- запуск удалённых blue/green, rollback или backup scripts;
- вывод секретов в URL или диагностику.
