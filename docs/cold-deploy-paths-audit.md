# Аудит холодных путей production deploy

Дата: 2026-08-15. Ветка: `audit/cold-deploy-paths`.

## Метод и границы

Проверены `fbctl/`, `deploy/`, `scripts/run-migrations-locked.py`,
`migrations/env.py` и `tests/rehearsal/`. «Покрыто» ниже означает исполняемый
unit-тест или настоящую Docker/PostgreSQL-репетицию, а не статическую проверку
текста. Docker, PostgreSQL и production host локально не запускались.

Репетиция теперь до первой мутации явно доказывает, что управляемых containers,
network, volumes и release images нет (`tests/rehearsal/single_slot.py:95-154,
741-746`). Поэтому обычный hot runner больше нельзя ошибочно принять за cold
rehearsal.

## Инвентаризация

| # | Холодная ветка и место | Отличие от горячего пути | Доказательство | Статус |
|---:|---|---|---|---|
| 1 | Нет `/opt/fb-agent`, `shared` и `deploy.lock`: `fbctl/files.py:426-509`, `fbctl/controller.py:1200-1250,1370-1376` | Cold создаёт root `0755`, shared `0700` и lock `0600`; hot только открывает и проверяет inode/owner/mode | `test_first_bootstrap_creates_host_tree_and_copies_external_profile_seed`, `test_deployment_lock_creation_is_independent_of_host_umask` | покрыто |
| 2 | Нет canonical `shared/source.env` и durable secrets: `fbctl/identity.py:45-105`, `fbctl/config.py:509-540`, `fbctl/controller.py:1399-1407` | Cold генерирует cluster id, PostgreSQL password и capability secrets; hot повторно использует durable значения и запрещает неявную ротацию | `test_first_bootstrap_creates_host_tree_and_copies_external_profile_seed`, `test_bootstrap_retry_reuses_durable_identity_after_mutation_failure` | покрыто |
| 3 | Первый запуск identity migration после перехода с `shared/.env`: `fbctl/identity.py:109-181` | Cold выбирает atomic OIDC/owner по precedence и после успеха удаляет только проверенный legacy inode; hot читает canonical `source.env` | `test_bootstrap_migrates_only_identity_from_fixed_legacy_source_after_full_success`, `test_bootstrap_failure_persists_canonical_identity_then_retry_prefers_it_and_cleans_legacy` | покрыто |
| 4 | Остался частично materialized `candidate` после process death: `fbctl/bundle.py:304-318` | Cold retry удаляет весь старый каталог и извлекает bundle заново; hot обычно приходит без `candidate` | `test_interrupted_candidate_materialization_is_replaced_before_retry` | покрыто |
| 5 | Нет canonical Vision profile, есть одноразовый seed: `fbctl/vision_profile.py:96-141,178-218`, `fbctl/controller.py:1424-1436` | Cold snapshot/copy/publish меняет владельца на runtime uid/gid; hot canonical profile авторитетен и seed не копируется | `test_bootstrap_profile_accepts_a_valid_explicit_seed_when_canonical_is_absent`, `test_first_bootstrap_creates_host_tree_and_copies_external_profile_seed`; real rehearsal пишет seed | покрыто |
| 6 | После прерывания canonical profile уже есть, а seed/adoption input ещё остались: `fbctl/controller.py:1477-1485,1686-1710` | Cold retry предпочитает canonical и удаляет только неизменившийся input; hot этих одноразовых файлов уже нет | `test_canonical_retry_snapshots_and_cleans_only_the_original_leftover_seed`, `test_bootstrap_imports_only_verified_candidate_bundle_snapshot` | покрыто |
| 7 | Нет managed network и трёх external volumes: `fbctl/controller.py:716-765`, `deploy/compose/docker-compose.infra.yml:62-73` | Cold `bootstrap` создаёт точные имена и ownership labels; hot `deploy` требует их наличия и ничего не создаёт | real `single_slot` плюс `test_bootstrap_creates_only_the_new_managed_resource_names`; precondition `test_rehearsal_proves_managed_docker_resources_are_absent` | покрыто |
| 8 | Bootstrap оборван посреди создания Docker resources: `fbctl/controller.py:740-765` | Retry инспектирует каждый resource и создаёт только отсутствующие; hot требует полный набор | `test_bootstrap_retry_creates_only_resources_missing_after_interruption` | покрыто |
| 9 | После смены имён есть только legacy network/volumes: `fbctl/controller.py:503-520`, constants `107-128` | Cold создаёт новый контур, старый только сообщает; hot обычно видит уже новый контур | `test_preflight_reports_legacy_resources_without_mutating_or_failing`, `test_bootstrap_creates_only_the_new_managed_resource_names` | покрыто |
| 10 | Нет infra/app/desktop containers: `fbctl/controller.py:452-467,842-899` | Cold Compose создаёт containers; hot пересоздаёт/поднимает существующий project | real `single_slot`; precondition `test_rehearsal_proves_managed_containers_and_release_images_are_absent` | покрыто |
| 11 | Нет release images в local Docker store: `fbctl/controller.py:420-428` | Cold `pull` реально скачивает digest; hot обычно подтверждает уже cached digest | real `single_slot` с новым explicit image precondition | покрыто |
| 12 | Нет ни одного container и host ports свободны: `fbctl/controller.py:541-700` | Cold inventory пуст; hot должен отличить собственный runtime от чужого container/process | real cold precondition и существующие port-owner/reuse unit-тесты | покрыто |
| 13 | Новый PostgreSQL volume: нет schema и `alembic_version`: `scripts/run-migrations-locked.py:70-154`, `migrations/env.py:86-168` | Cold принимает только действительно пустой target и выполняет baseline; hot принимает только известного предка/head и сверяет sentinels/artifacts | real PostgreSQL в `single_slot`; `test_fresh_target_guard_accepts_empty_database`, `test_direct_alembic_cli_imports_project_migration_contracts` | покрыто |
| 14 | Прерванная migration оставила пустую version table либо partial/unversioned schema: те же guards | Retry принимает empty version table без app objects, но fail-closed отвергает partial schema или stamped schema без baseline objects; hot идёт от известной revision | `test_fresh_target_guard_accepts_empty_database`, `test_fresh_target_guard_rejects_unversioned_nonempty_schema` и missing-sentinel tests; живой interrupted PostgreSQL не запускался по ограничению задачи | покрыто unit |
| 15 | Нет adoption receipt/данных: `fbctl/controller.py:774-814`, `deploy/compose/docker-compose.jobs.yml:46-54` | Cold запускает importer и повторно читает receipt; hot сразу возвращается при status `0` | real first bootstrap в `single_slot`; `test_missing_adoption_receipt_requires_bundle_without_host_marker` проверяет fail-closed routine deploy | покрыто |
| 16 | Import transaction уже committed, но bootstrap умер до повторной проверки/удаления bundle: `fbctl/controller.py:792-814` | Retry должен получить `verified`, не импортировать второй раз; hot receipt уже есть | Есть только DB integration `test_first_release_adoption_allows_secret_bootstrap_and_reconciles_retry`, который в этой задаче разрешено лишь collect; bootstrap rehearsal эту аварию не инжектирует | не покрыто |
| 17 | Нет runtime config rows (Telegram/AdsetPro/Web URL): `fbctl/controller.py:816-821`, `deploy/compose/docker-compose.jobs.yml:63-65` | Cold создаёт только отсутствующие строки; hot существующая строка или tombstone авторитетны | `test_explicit_bootstrap_existing_row_or_tombstone_is_authoritative`, `test_adset_bootstrap_existing_row_is_authoritative`, `test_web_app_url_bootstrap_writes_once_and_existing_tombstone_wins`; real bootstrap выполняет job | покрыто |
| 18 | Нет `vision_config` row и одноразового transport secret: `fbctl/controller.py:823-840,1408-1452`, `deploy/compose/docker-compose.jobs.yml:67-72` | Cold создаёт singleton и удаляет plaintext env; hot проверяет exact match и не перезаписывает | `test_vision_bootstrap_creates_once_and_rejects_mismatch`, `test_bootstrap_vision_secrets_never_enter_canonical_runtime_env`; real bootstrap выполняет job | покрыто |
| 19 | Нет active `runtime` symlink: `fbctl/controller.py:1068-1164` | Первая promotion не имеет previous payload; hot атомарно меняет pointer и чистит previous | `test_deploy_orders_webhook_before_workers_and_promotes_after_all_evidence`, promotion failure/post-commit retry tests; real rehearsal | покрыто |
| 20 | Нет desktop readiness state/active link: `fbctl/controller.py:858-880` | Cold пишет immutable credential state и symlink; hot сверяет существующий state на конфликт и обновляет pointer | fresh deploy unit и real rehearsal | покрыто |
| 21 | Нет Caddyfile, env, site/drop-in dirs и log files: `fbctl/controller.py:1601-1683` | Cold создаёт весь host state и единственный import; hot сохраняет существующий Caddyfile и атомарно обновляет managed files | `test_fresh_caddy_provisioning_creates_every_host_file_and_is_repeatable`, включая искусственно оставленное partial состояние | покрыто unit |
| 22 | Нет Caddy system user/group или активного `caddy.service`: `fbctl/controller.py:1634-1638,1669-1683` | Cold host требует внешней установки/первого старта service; hot только reload | README requirements этого не требуют, а rehearsal всегда `rehearsal=True` и целиком пропускает provisioning | сломано |
| 23 | Нет публичных TLS certificates: `fbctl/controller.py:1669-1683`, public smoke `1047-1066` | Первый reload инициирует ACME и следующий deploy ждёт public HTTPS; hot использует сохранённые certs | Ни unit, ни rehearsal не запускают реальный Caddy/DNS/ACME; локально это безопасно не воспроизводится | не покрыто |
| 24 | Пустой `/config`: нет `.vnc/kasmvnc.yaml` и `.kasmpasswd`: `deploy/vision-webtop/entrypoint.sh:66-83`, `deploy/vision-webtop/kasmvnc.yaml:72-81` | Cold entrypoint создаёт dirs/config/password file; hot перезаписывает managed config/password | `test_entrypoint_installs_managed_config_into_the_mounted_profile` проверяет только текст; `single_slot` заменяет `DESKTOP_WEBTOP_IMAGE` stub image и entrypoint не исполняет | не покрыто |
| 25 | Первый source содержит Kasm password длиннее допустимого tool limit: `fbctl/config.py:641-644` | До первого container такого runtime evidence нет; hot использует уже сохранённый пароль | Новый red/green `test_fresh_source_rejects_kasm_password_above_tool_limit` и boundary `...accepts...at_tool_limit` | сломано, исправлено |
| 26 | После смены network name monitoring agent ещё не создан/сидит в старой сети: `DEPLOYMENT.md:60-70`, `deploy/monitoring/docker-compose.agent.yml:54-92`, `fbctl/bundle.py:32-43` | Cold/rename требует отдельного `compose up`; hot fbctl вообще не владеет monitoring lifecycle | Rehearsal не включает monitoring. Документированная команда требует файлы, которых нет в self-contained control bundle и на checkout-free host | сломано |
| 27 | Bootstrap оборван между preflight, pull, resources, infra, migrate, adoption, runtime config, Vision config, Caddy или consume-inputs: `fbctl/controller.py:1399-1493` | Retry обязан сохранить durable identity, убрать temp/candidate и продолжить; hot bootstrap обычно больше не вызывается | Расширенный `test_bootstrap_retry_reuses_durable_identity_after_mutation_failure` плюс отдельные resource/candidate/Caddy partial tests | покрыто unit |
| 28 | Первый запуск central monitoring: нет project network, шести data volumes, containers, `.env.monitoring` и webhook token: `deploy/monitoring/docker-compose.monitoring.yml:21-195`, `deploy/monitoring/README.md:30-43` | Cold operator вручную создаёт env/token, а Compose создаёт internal network/volumes/containers; hot переиспользует persisted volumes | Ни `fbctl`, ни `single_slot`, ни unit-тест не исполняют этот release; есть только Compose/config static gates | не покрыто |
| 29 | Первый single-host monitoring overlay: нет `browser_agent_metrics_data`, central services ещё не присоединены к external platform network: `deploy/monitoring/docker-compose.local-app.yml:4-40` | Cold Compose создаёт metrics volume и подключает Prometheus/Alloy к уже созданной `fb_agent_platform`; hot сохраняет volume/network attachments | Rehearsal не запускает central monitoring или overlay | не покрыто |
| 30 | Нет host Docker daemon config: `deploy/daemon.json:1-7` | Cold provisioning должен установить/merge/restart daemon; hot Docker уже запущен с policy | Файл не входит в bundle, нигде не вызывается и не описан; Compose services имеют собственные logging limits, поэтому это orphan provisioning asset, а не доказанный runtime fix | не покрыто |

## Подтверждённые дефекты

### 1. KasmVNC password за пределом документированного диапазона — не подтвердилось

Гипотеза была такая: `canonicalize_source` принимает пароль длиннее 128
символов, а `kasmvncpasswd` его отвергает, поэтому desktop не станет healthy.
Основание — документация KasmVNC, где для `vncpasswd` указан диапазон 6–128:
<https://www.kasmweb.com/kasmvnc/docs/master/man/vncpasswd.html>.

Проверка на живом бинарнике из нашего образа гипотезу **не подтвердила**:

```text
len128 rc=0
len129 rc=0
```

Пароли на 128 и 129 символов принимаются одинаково, и сохранённые хеши
различаются — то есть обрезки, из-за которой Caddy слал бы один пароль, а Kasm
хранил другой, тоже нет. Плюс fbctl генерирует 32 символа сам, так что путь
недостижим и в теории.

Верхняя граница не добавлена: это был бы предохранитель от несуществующей
поломки, а документация разошлась с поведением сборки.

### 2. Clean-host Caddy contract не содержит prerequisite — не исправлено

- `README.md:151-156` перечисляет Python/Node/Docker/PostgreSQL, но не Caddy.
- `fbctl/controller.py:1634-1638` уже требует существующих `caddy` user/group, а
  `1669-1683` выполняет `systemctl reload caddy`, не install/enable/start.
- На хосте, удовлетворяющем опубликованным требованиям, bootstrap после DB jobs
  остановится на `Caddy system user is missing`; при наличии пользователя, но
  неактивном unit — на `systemctl reload caddy`.
- Официальная инструкция отдельно создаёт user/unit и для первого старта
  выполняет `systemctl enable --now caddy`: <https://caddyserver.com/docs/running>.

Это граница host provisioning, а не локальный Python-дефект. Автоматически
устанавливать пакет/service без утверждённого host contract в этом изменении
нельзя; нужен отдельный выбор: добавить точный prerequisite/preflight либо
сделать `fbctl` владельцем install/enable.

### 3. Monitoring recreation после rename невозможно выполнить из shipped bundle — не исправлено

- `DEPLOYMENT.md:22-24` обещает checkout-free host.
- `DEPLOYMENT.md:60-70` после rename требует
  `-f deploy/monitoring/docker-compose.agent.yml`.
- `fbctl/bundle.py:32-43` не включает ни этот Compose-файл, ни
  `deploy/monitoring/alloy/agent.alloy`, который bind-mount'ится в
  `docker-compose.agent.yml:67-70`.
- Central runbook аналогично начинает с несуществующего в новой topology
  `/opt/fb-agent/current/deploy/monitoring` (`deploy/monitoring/README.md:30-43`)
  и тоже не имеет отдельного shipped monitoring artifact.
- На практике команда либо сразу не найдёт Compose-файл, либо при ручной копии
  одного YAML не получит Alloy config/env. Первый bootstrap/deploy при этом
  останется green, а telemetry будет отсутствовать — ровно failure mode,
  описанный в `DEPLOYMENT.md:63-65`.

Compose разрешает relative bind paths относительно project/Compose directory,
поэтому одного YAML без соседнего `alloy/agent.alloy` недостаточно:
<https://docs.docker.com/reference/compose-file/services/>.

Нужен отдельный ownership design: включить monitoring agent assets/command в
control bundle либо выпускать и размещать отдельный immutable monitoring bundle.

## Что добавлено в репетицию и unit coverage

- `tests/rehearsal/single_slot.py`: fail-before-mutation проверки отсутствия
  managed containers, network, volumes и всех release digests.
- `tests/unit/test_single_slot_rehearsal.py`: positive/negative tests этих
  preconditions без живого Docker.
- `tests/unit/test_fbctl.py`:
  - root/shared/lock + generated identity + external Vision seed на действительно
    отсутствующем managed root;
  - partial Docker resources после прерывания;
  - stale partial candidate после process death;
  - fresh и partial Caddy filesystem provisioning с повтором;
  - retry после каждого bootstrap seam до Caddy и input consumption;
  - Kasm password upper boundary.

Единственное падение, которое подтвердило продуктовый дефект до изменения кода,
— Kasm password `129`. Первые варианты Caddy/root тестов также падали из-за
ошибок самих test harness (`stat` import и отсутствующий parent temp path); они
не считаются красным доказательством продукта.

## Внешние контракты

- Compose `external: true` network и volume не создаёт и падает при их
  отсутствии, поэтому `fbctl._ensure_bootstrap_resources` стоит в правильном
  месте: <https://docs.docker.com/reference/compose-file/networks/> и
  <https://docs.docker.com/reference/compose-file/volumes/>.
- Alembic поддерживает `upgrade(..., "head")` на общем connection, а version
  table создаётся при первом versioned run; project guards дополнительно
  разрешают только empty target или известного предка:
  <https://alembic.sqlalchemy.org/en/latest/api/commands.html>.
- User Kasm config `~/.vnc/kasmvnc.yaml` переопределяет global config, поэтому
  managed copy в mounted `/config/.vnc` необходим:
  <https://kasmweb.com/kasmvnc/docs/latest/configuration.html>.
- Caddy получает/обновляет certificates автоматически только при корректных
  DNS, открытых `80/443`, writable persistent data directory и работающем
  service: <https://caddyserver.com/docs/automatic-https>.

## Остаточные непокрытые риски

1. Настоящий `vision-webtop` entrypoint на пустом volume. Нужен отдельный Docker
   rehearsal shard без подмены `DESKTOP_WEBTOP_IMAGE` stub'ом.
2. Настоящий first-start Caddy service и ACME. Нужен disposable VM/domain или
   локальный ACME стенд; текущая Docker rehearsal намеренно это пропускает.
3. Crash между adoption commit и receipt re-check. Есть DB integration на
   idempotence, но нет bootstrap failpoint; в этой задаче integration можно было
   только собрать (`--collect-only`).
4. Central monitoring, single-host overlay и application-host agent cold
   start/recreation. Они отсутствуют в control bundle и вне ownership `fbctl`,
   поэтому текущая rehearsal физически до них не дотягивается.
5. `deploy/daemon.json` не имеет install/merge/restart path и теста. Нужно либо
   удалить orphan asset, либо определить отдельный host-provisioning contract.

## Проверки этой ветки

- `ruff check .` — green.
- `ruff format --check .` — green (`761 files already formatted`).
- Изменённый cold-path набор — `21 passed`.
- `pytest tests/integration --collect-only -q` — `809/812 collected`, `3
  deselected`; живой PostgreSQL не запускался.
- Полный `pytest tests/unit -q` — `2743 passed, 3 skipped`, но три старых
  socket-теста упали исключительно на запрете sandbox bind'ить
  `127.0.0.1` (`EPERM`):
  `test_port_probe_asks_the_same_way_the_runtime_binds`,
  `test_telegram_rehearsal_stub_executes_deployed_gateway_contract`,
  `test_gateway_uses_configured_origin_for_real_webhook_round_trip`.
- Повтор всего unit-набора без этих трёх недоступных sandbox cases — `2743
  passed, 3 skipped, 3 deselected`.
