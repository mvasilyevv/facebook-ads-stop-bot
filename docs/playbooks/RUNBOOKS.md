# Production runbooks

Актуальные сценарии для safety-first production contour. Команды ниже не
выполняют денежные действия и не изменяют данные, если это явно не указано.
Любой manual pause/activate выполняется через operator UI или `CommandService`,
а не прямым SQL или вызовом Meta.

Пути предполагают установку в `/opt/fb-agent/current` и state directory
`/opt/fb-agent/shared`.

## Первичная диагностика

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh status
sudo /opt/fb-agent/current/scripts/platform-compose.sh ready
sudo /opt/fb-agent/current/scripts/platform-desktop-compose.sh status
sudo /opt/fb-agent/current/scripts/platform-compose.sh logs api
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
sudo /opt/fb-agent/current/scripts/platform-desktop-compose.sh status
sudo /opt/fb-agent/current/scripts/platform-desktop-compose.sh logs browser-agent
sudo systemctl status fb-agent-desktop-heal.timer
sudo journalctl -u fb-agent-desktop-heal.service -n 100 --no-pager
sudo grep 'operation="desktop_healer"' \
  /var/lib/node_exporter/textfile_collector/fb-agent-host-operations.prom
```

Порядок действий:

1. Проверить фактическую готовность Kasm/Vision desktop, а не только открытый
   TCP-порт.
2. Открыть нужный Ads Manager cabinet и убедиться, что Facebook session жива.
3. Проверить, что actor показывает правильный `ad_account_id`, stage, lease и
   свежий snapshot.
4. Если desktop исправен, перезапустить только browser-agent:

   ```bash
   sudo /opt/fb-agent/current/scripts/platform-desktop-compose.sh restart
   ```

5. Не перезапускать весь application color: scan и control pages независимы.
6. Для action со статусом `UNKNOWN` дождаться reconciliation. Не создавать
   вторую задачу с другим idempotency key.

## Worker offline или stale

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh status
sudo /opt/fb-agent/current/scripts/platform-compose.sh logs <service>
curl -fsS https://app.adpulse.su/system-readyz
```

Проверить Prometheus alerts `FBWorkerMetricsAbsent` и
`FBWorkerMetricsTargetDown`: отсутствие серии и недоступность target имеют
разные причины. Для локального endpoint каждый worker слушает собственный
metrics port; карта портов — в [../worker_metrics.md](../worker_metrics.md).

Перезапускать весь color не нужно. Если требуется рестарт одного container,
использовать active-color Compose через поддерживаемый wrapper:

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh compose restart <service>
sudo /opt/fb-agent/current/scripts/platform-compose.sh ready
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
   sudo /opt/fb-agent/current/scripts/platform-compose.sh logs telegram_update_worker
   sudo /opt/fb-agent/current/scripts/platform-compose.sh logs telegram_delivery_worker
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

Для read-only проверки очередей:

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh infra exec -T postgres \
  psql -U fb_stop_bot -d fb_stop_bot -c "
    SELECT status, count(*)
    FROM notification_deliveries
    GROUP BY status ORDER BY status;
  "
```

Не помещать bot token в URL, shell history, ticket, exception или лог.

## Money action зависла или стала UNKNOWN

Сначала найти action в UI по correlation ID. `202` означает только `queued`.

Read-only снимок очереди:

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh infra exec -T postgres \
  psql -U fb_stop_bot -d fb_stop_bot -c "
    SELECT id, task_type, lane, status, priority, available_at,
           deadline_at, lease_owner, lease_token, lease_expires_at,
           correlation_id
    FROM task_queue
    WHERE lane = 'money'
      AND status IN ('pending', 'retrying', 'running')
    ORDER BY priority DESC, available_at, created_at, id
    LIMIT 50;
  "
```

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
sudo /opt/fb-agent/current/scripts/platform-compose.sh infra ps
sudo /opt/fb-agent/current/scripts/platform-compose.sh infra logs --tail=200 postgres
sudo /opt/fb-agent/current/scripts/platform-compose.sh infra logs --tail=200 redis
```

После рестарта PostgreSQL consumers перечитывают БД; потеря `LISTEN/NOTIFY` не
теряет committed work. Проверить queue age, expired leases и reconciliation,
затем `platform-compose.sh ready`.

## Backup и восстановление

Production backup — только pgBackRest с continuous WAL и off-host repository.
Реплика не является backup.

```bash
sudo /opt/fb-agent/current/scripts/pgbackrest-admin.sh \
  --release-env /opt/fb-agent/shared/active-release-images.env \
  --app-env /opt/fb-agent/shared/active-app.env \
  --backup-env /opt/fb-agent/shared/pgbackrest.env full

sudo /opt/fb-agent/current/scripts/pgbackrest-restore-drill.sh \
  --release-env /opt/fb-agent/shared/active-release-images.env \
  --app-env /opt/fb-agent/shared/active-app.env \
  --backup-env /opt/fb-agent/shared/pgbackrest.env
```

Restore drill использует отдельные временные container/network/volume и не
монтирует production volume. Полное удаление схемы не является способом
восстановления. Любой реальный restore или PITR требует maintenance window,
зафиксированного target time и подтверждения владельца.

Состояние systemd-задач хранится независимо от journal:

```bash
sudo grep -E 'operation="(pgbackrest_full|pgbackrest_diff|restore_drill)"' \
  /var/lib/node_exporter/textfile_collector/fb-agent-host-operations.prom
sudo systemctl status fb-agent-pgbackrest-full.timer \
  fb-agent-pgbackrest-diff.timer fb-agent-restore-drill.timer
```

`status=-1` означает незавершённый запуск, `0` — последний запуск завершился
ошибкой, `1` — подтверждённый успех. Нельзя гасить `FBPgBackRestBackupStale`
или `FBRestoreDrillOverdue` ручной правкой `.prom`: требуется новый успешный
backup/isolated restore.

## Failed deployment или rollback

Release state и reconciliation являются источником истины; не переключать
Caddy вручную поверх незавершённого journal.

```bash
sudo systemctl status fb-agent-release-reconcile.service
sudo journalctl -u fb-agent-release-reconcile.service -n 200 --no-pager
sudo /opt/fb-agent/current/scripts/platform-compose.sh status
sudo grep -E 'operation="release_(boot_reconcile|reconcile|rollback)"' \
  /var/lib/node_exporter/textfile_collector/fb-agent-host-operations.prom
```

Release script сам возвращает traffic и singleton leases к последнему
зафиксированному color в пределах общего 180-секундного deadline. PostgreSQL
никогда не downgrade-ится. Если reconciliation помечен critical, остановить
повторные release-запуски и сохранить journal/evidence для расследования.
`FBReleaseRollbackFailed` снимается только успешной reconciliation, которая
запишет recovery; `/opt/fb-agent/shared/rollback-failed.json` остаётся
forensic-маркером и не удаляется ради «зелёного» статуса.

Полный release contract: [../../deploy/bluegreen/README.md](../../deploy/bluegreen/README.md).

## Запрещённые операции

- production schema wipe или `DROP SCHEMA`;
- прямое изменение money task status/lease/fencing token;
- повтор create/duplicate после `UNKNOWN` без reconciliation;
- прямой Telegram send из worker/business code;
- включение второго money consumer;
- production build на VPS или запуск image по mutable tag;
- ручной Caddy switch в обход release journal;
- вывод секретов в URL или диагностику.
