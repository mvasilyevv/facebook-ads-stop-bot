# Аудит сервиса создания кампаний — 2026-06-22

**Дата:** 2026-06-22
**Scope:** свежесмёрженная логика сервиса создания FB-кампаний (merge `185af2e3`), новый код:
`core/campaign_builder/` (config/builder/uniquify/execute/naming), `apps/campaign_creator_worker/`,
`apps/api/routers/v1/campaigns_create.py`, reconciler-интеграция (`apps/reconciler_worker/` + `core/tasks/queue.py`),
миграция `0025_campaign_creation`, фронты `frontend/` + `frontend-mini/` (визард создания кампаний).
**Спека намеренного поведения:** `docs/superpowers/specs/2026-06-22-campaign-creation-service-design.md`.
**Доменов проверено:** 6 (builder-config, uniquify-execute, worker, api, data-migration, frontend).
**Метод:** находки + adversarial-вердикты скептиков сведены ведущим ревьюером, каждая находка перепроверена
по исходникам. CRIT «reconciler не защищает campaign_create» подавался независимо в доменах uniquify-execute
и worker — это ОДИН корневой баг с двумя точками входа, в итоговой таблице считается один раз (CRIT).

## Итоговая таблица severity × домен (после верификации, дедуп)

| Домен | CRIT | HIGH | MID | LOW |
|---|---|---|---|---|
| builder-config | 0 | 1 | 1 | 2 |
| uniquify-execute / worker (общий money-контур) | 1 | 1 | 1 | 2 |
| api | 0 | 1 | 1 | 2 |
| data-migration | 0 | 0 | 0 | 2 |
| frontend | 1 | 1 | 3 | 2 |
| **ИТОГО** | **2** | **4** | **6** | **10** |

Корректировки относительно сырых находок:
- CRIT reconciler-дыра дедуплицирован (был в 2 доменах) → 1 CRIT.
- builder-config HIGH «spec ≠ исполнитель по числу ads/кодам» и uniquify-execute MID «spec.ads ≠ execute» —
  одно и то же рассогласование builder↔uniquify; в таблице HIGH остаётся за builder-config, дублирующий MID
  из uniquify-execute снят (поглощён).
- Frontend: 4 находки скорректированы скептиком по severity (auto-launch HIGH→MID, mini-budget HIGH→LOW,
  stale-preview HIGH→MID), все по причине «money-инвариант держится бэком, дефект чисто UX/контракт».
- Refuted: нет.

---

## Находки по severity

### CRIT-1 — `campaign_create` не защищён от авто-рекола → дубль кампании + двойной открут бюджета

- **Файлы:** `core/tasks/queue.py:333-361` (`fail_stuck_irreversible`), `:364-409` (`reconcile_stuck_running`),
  `apps/reconciler_worker/worker.py:39-66`, `apps/campaign_creator_worker/main.py:108-121`,
  `apps/campaign_creator_worker/__init__.py:43-69` (`claim`), `:106-120` (`set_run_status`).
- **Проблема:** обе защиты reconciler'а жёстко завязаны на `task_type='meta_api_mutation' AND
  payload->>'mutation_kind' IN IRREVERSIBLE_MUTATION_KINDS` (`{create_campaign, duplicate_campaign}`).
  Задача залива имеет `task_type='campaign_create'` и `payload={run_id}` (нет `mutation_kind`) →
  НЕ матчит ни `fail_stuck_irreversible` (queue.py:342-345), ни exclude-guard в `reconcile_stuck_running`
  (queue.py:387-391). Зависшая в `running` >30 мин задача `campaign_create` уводится `running→retrying`,
  второй воркер её re-claim'ит (`__init__.py:50-51` берёт `pending/retrying`). Run-guard в `process_one_task`
  (main.py:110) проверяет только `run.status IN ('succeeded','failed','cancelled')`; у недозавершённого run
  статус `uniquifying/uploading/creating` → guard НЕ срабатывает → `execute_campaign_spec` стартует заново
  с `created=_empty_ids()` (чисто in-process, без восстановления из `campaign_run.created_meta_ids`).
  `task_queue.updated_at` фиксируется на claim и НЕ обновляется во время залива (`set_run_status` пишет в
  `campaign_run`, не в `task_queue`), поэтому медленный залив (ffmpeg-уникализация видео + chunked upload +
  последовательные Graph-вызовы) гарантированно пробивает 30-мин окно.
- **Impact:** MONEY-CRIT. Дубль FB-кампании + двойной открут рекламного бюджета при любом крахе/деплое
  воркера (OOM/SIGKILL) ПОСЛЕ коммита кампании в Meta, но ДО `finalize`. Необратимо. `idempotency_key` UNIQUE
  стоит на `campaign_run`/`task_queue` — защищает enqueue, НЕ повторный execute одной строки. Контраст:
  meta_api путь имеет ОБА барьера (worker `_fail_irreversible` + reconciler `fail_stuck_irreversible`+exclude);
  `campaign_create` — НИ ОДНОГО. Прямое нарушение заявленного money-инварианта спеки (строки 100-101) и её же
  обещания «зеркало существующих воркеров» (строки 95-99).
- **Fix:** (1) включить `campaign_create` в irreversible-контур: в `apps/reconciler_worker/worker.py` пометить
  зависшие `running` campaign_create как `failed` без retry (аналог `fail_stuck_irreversible` по
  `task_type='campaign_create'` без условия на `mutation_kind`, с TG-алертом «проверь Meta вручную») И
  исключить из `reconcile_stuck_running` (расширить guard на `task_type='campaign_create'` либо передавать
  набор необратимых task_type'ов); (2) усилить run-level guard: считать non-terminal run с непустым
  `created_meta_ids` или stage∈(uploading,creating) защищённым → `finalize_run_failed` без переисполнения;
  (3) атомарный claim-run перед `execute` (`WHERE status NOT IN (terminal, executing)`).
- **Confidence:** high (воспроизводимо по коду).

### CRIT-2 — Фронты шлют ПЛОСКИЙ CampaignConfig, бэк ждёт ВЛОЖЕННЫЙ → 422 на каждый validate/launch

- **Файлы:** `frontend/src/lib/api/campaigns.ts:97-158`, `frontend-mini/src/lib/campaignTypes.ts:112-142`,
  бэк-контракт `core/campaign_builder/config.py:170-205` (`CampaignConfig`), потребители
  `apps/api/routers/v1/campaigns_create.py:304` (validate), `:345` (launch).
- **Проблема:** бэк требует строго вложенный `CampaignConfig`: обязательные `account(Account)`, `budget(Budget)`,
  `targeting(Targeting)`, `campaigns: list[CampaignBlock(name, adsets: list[AdsetConfig(name, dir, glob)])]`.
  В `config.py` НЕТ `model_validator(mode='before')`, НЕТ `alias`/`populate_by_name` (все 4 валидатора —
  `mode='after'`, т.е. пост-конструкция, уплощения не делают). Оба фронта строят плоский объект:
  `act_id`, `page_id`, `pixel_id`, `daily_budget_cents` (web) / `daily_cents` (mini), `countries`, `age_min`,
  `age_max`, `campaigns: [{key, kind, adset_count, concept_refs}]` (без `name`, без `adsets[].dir/glob`).
  Ни `account`, ни `budget`, ни `targeting`, ни `campaigns[].name/adsets` фронты не отправляют. Вдобавок
  web использует `daily_budget_cents`, mini — `daily_cents` (расхождение web↔mini), оба не совпадают с
  бэковым `budget.daily_cents`.
- **Impact:** КАЖДЫЙ `POST /tools/campaigns/validate` и `/tools/campaigns/launch` → 422 (Pydantic не найдёт
  обязательные `account`/`targeting`/`budget`). Сервис полностью нерабочий из UI: ни dry-run, ни залив.
  Деньги не тратятся (залив не стартует), но фича мёртвая. Корень: оба фронта на рукописных типах (TODO
  «консолидировать в `@fb/shared` после `gen:api`»), дрейф от openapi не пойман — классический writer↔reader.
- **Fix:** предпочтительно — адаптировать бэк под плоский input через `@model_validator(mode='before')`
  (`act_id→account.act_id`, `daily_budget_cents→budget.daily_cents`, `countries→targeting.countries`, авто-сборка
  `CampaignBlock.adsets` из `adset_count`+`creo_root`), либо перевести оба фронта на вложенную схему из `gen:api`.
  Унифицировать `daily_budget_cents` vs `daily_cents`. Добавить контрактный тест фронт-payload→бэк-парс.
- **Confidence:** high.

---

### HIGH-1 — Превью (build_campaign_spec) считает число ads и коды иначе, чем исполнитель (uniquify) → байер апрувит залив по неверному money-превью

- **Файлы:** `core/campaign_builder/builder.py:218-252` (`_build_block`), `:255-276` (`build_campaign_spec`),
  потребитель `apps/api/routers/v1/campaigns_create.py:314-336` (validate); расходится с
  `core/campaign_builder/uniquify.py:124-148` (`build_uniquification_plan`), вызываемой в `execute.py:221`.
- **Проблема:** превью делает `creative_codes(count=copies)` ОТДЕЛЬНО на каждый adset (`builder.py:231`),
  где `copies=len(block.adsets)` (`builder.py:266`) — коды перезапускаются с CR001 в каждом adset'е, число
  ads = `copies × adsets`. Исполнитель строит `total = len(concepts) × copies` со сквозной нумерацией
  (`uniquify.py:125-126`), раскладка adset i = K ads по 1 на концепт (`uniquify.py:146-148`).
  Пример (3 концепта × 2 adset): превью показывает 4 ads с дублирующимися кодами `CR001/CR002` в ОБОИХ
  adset'ах, исполнитель создаёт 6 ads с уникальными `CR001..006`. Расхождение всегда при #концептов ≠ #adset.
  `CampaignConfig` принципиально не несёт число концептов (они только в upload-store, передаются воркеру
  через `creo_root`), поэтому `build_campaign_spec` не может посчитать K×copies — превью совпадает с
  реальностью только при K=1, и даже тогда коллизии кодов в превью остаются при copies>1. Spec-deviation:
  дизайн (строки 75-82) описывает каноническую раскладку «adset i = K ads (1 на концепт)», которую исполнитель
  реализует ВЕРНО, а builder — нет. Тест `test_campaign_builder.py:266-270` проверяет коды только `adsets[0]`
  и закрепляет per-adset перезапуск как «ожидаемое» — тест вписывает баг.
- **Impact:** Money/контракт. Money-approval экран (превью dry-run) занижает число объявлений и показывает
  фиктивную коллизию sub3-трекинга, которой в реальном заливе нет. Не CRIT, т.к. кампания заливается PAUSED
  (`launch_state=campaign_paused`, спенда нет; реальную раскладку байер видит в Ads Manager перед unpause) —
  прямого открута нет, это вводящее в заблуждение превью.
- **Fix:** единый источник раскладки. Либо превью-роутер строит spec через тот же `build_uniquification_plan`,
  либо `_build_block` принимает `concept_count` и считает сквозную нумерацию `total=K×copies`, variant[i]→adset[i].
  Семантический тест, сверяющий `ad_count`/коды `build_campaign_spec` против `build_uniquification_plan` при #конц≠#adset.
- **Confidence:** high.

### HIGH-2 — Commit-без-ack на `POST /campaigns`: transient после потерянного ack → requeue → вторая кампания

- **Файлы:** `core/campaign_builder/execute.py:226-233` (первый Graph-вызов), `:379-396` (`_raise_for_failure`),
  `:113-133` (`classify_execution_error`), маршрутизация `apps/campaign_creator_worker/main.py:193-215`.
- **Проблема:** `execute_graph_call(POST /campaigns)` может физически создать кампанию в Meta, но вернуть
  grpc timeout/UNAVAILABLE (→ `TemporaryError`) ДО `_extract_id` (execute.py:232). `created['campaigns']`
  остаётся пуст → `_has_created()==False` → `CampaignExecutionError` с `__cause__=TemporaryError` (execute.py:394-396)
  → `classify_execution_error='transient'` (execute.py:129-132) → воркер `requeue_for_retry` (main.py:197) →
  повторный execute создаёт ВТОРУЮ кампанию, если первый POST реально закоммитился (потерянный ack).
  Нарушает СОБСТВЕННЫЙ паттерн проекта: meta_api_worker для create/duplicate уводит транзиент в `mark_failed`
  без retry именно из-за lost-ack-после-коммита; новый воркер этот паттерн к шагу campaign не применяет.
- **Impact:** MONEY. Дубль кампании + двойной бюджет при потере ack на первом Graph-вызове. Вероятность ниже
  гарантированного срабатывания reconciler-окна (CRIT-1), но money-класс тот же.
- **Fix:** transient на шаге создания campaign трактовать как potential-partial → `mark_failed` без retry +
  алерт оператору на ручную проверку Meta (как для irreversible); либо client-side idempotency-заголовок на
  Graph-create. Минимум — алерт оператору при transient на шаге campaign.
- **Confidence:** med.

### HIGH-3 — Live-zombie: медленный залив переживает 30-мин stuck-таймаут → второй воркер исполняет параллельно

- **Файлы:** `apps/campaign_creator_worker/main.py:108-121` (терминальный guard), interplay с
  `apps/reconciler_worker/worker.py:35,53-66`.
- **Проблема:** единственный run-guard проверяет только `run.status IN ('succeeded','failed','cancelled')`.
  Длинный залив (chunked `UploadVideo` больших видео + последовательные `execute_graph_call` для
  adsets/creatives/ads, execute.py:239-318) может пережить `_STUCK_TIMEOUT_MIN=30` (worker.py:35), оставаясь
  живым; `reconcile_stuck_running` параллельно уводит `running→retrying` (campaign_create не исключён — см.
  CRIT-1), второй воркер re-claim'ит run в `creating/uploading` → guard пропускает → два процесса создают
  кампанию одновременно. Тест `test_campaign_creator_worker.py:260-282` сидит run сразу в `succeeded` —
  опасный mid-flight re-claim не тестируется (shape-vs-semantics). Реализуемо на типичных gambling
  видео-концептах без всякого краха воркера.
- **Impact:** MONEY. Дубль кампании при медленном заливе даже без падения воркера.
- **Fix:** дополнить guard (non-terminal run с непустым `created_meta_ids` или stage∈(uploading,creating) →
  не переисполнять, `finalize_run_failed`+алерт); поднять/сделать настраиваемым stuck-таймаут для
  `campaign_create` выше реалистичного времени залива. Тест: run в `creating` + дубль-claim → `client.calls==[]`.
- **Confidence:** high. *(Закрывается фиксом CRIT-1 целиком — это его частный случай без краша.)*

### HIGH-4 — Гонка двух launch с одним idempotency_key → голый 500 вместо идемпотентного ответа

- **Файлы:** `apps/api/routers/v1/campaigns_create.py:368-428` (`launch_campaign`), `apps/api/main.py:171-232`
  (нет handler `IntegrityError`).
- **Проблема:** read-then-insert: оба параллельных запроса проходят `SELECT campaign_run WHERE idempotency_key`
  (соперник ещё не закоммичен), оба делают `INSERT` без `ON CONFLICT`. UNIQUE-констрейнты реальны
  (`uq_campaign_run_idempotency_key` migrations/0025:166, `uq_task_queue_idempotency_key`) → второй INSERT →
  asyncpg UniqueViolation → SQLAlchemy `IntegrityError`. В `main.py` зарегистрированы только AdsetPro*/Meta*
  handlers (подтверждено grep: строки 174-229) — generic `IntegrityError` НЕ обработан → клиент получает 500.
- **Impact:** Деньги НЕ теряются (UNIQUE гарантирует, дубля залива нет — money-инвариант держится). Но
  легитимный double-submit (двойной клик визарда, retry сети) → 500 вместо идемпотентного 200/201 с тем же
  `run_id`; утечка internal-деталей; риск, что фронт покажет «залив провалился» и оператор повторит вручную.
- **Fix:** `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id` (для обоих INSERT), при пустом
  RETURNING — повторный SELECT и идемпотентный ответ. Альтернатива — `app.exception_handler(IntegrityError)→409`.
  Предпочтителен ON CONFLICT (truly-idempotent без 4xx).
- **Confidence:** high.

### HIGH-5 — web `uploadConcepts` использует CommonJS `require()` в ESM-сборке → upload без X-API-Key → 401

- **Файлы:** `frontend/src/lib/api/campaigns.ts:302-313`.
- **Проблема:** `require('@/stores/auth')` внутри IIFE с try/catch — в Vite ESM-сборке bare `require`
  не определён → ReferenceError → catch съедает → `apiKey=null` → заголовок `X-API-Key` не ставится.
  Эндпоинт `POST /api/tools/campaigns/upload` — write-метод, НЕ в `_EXEMPT_PATH_PREFIXES`
  (`api_key_auth.py:36` — только `/api/v1/postback` и `/api/tma`), при `require_api_key=True`
  (secure-by-default) middleware вернёт 401. Канонический паттерн — статический `import {useAuthStore}` +
  `useAuthStore.getState().apiKey` (как `client.ts`); mini делает то же через Bearer/`getStoredToken`
  (расхождение web↔mini).
- **Impact:** Загрузка концептов в web сломана в проде с включённым ключом. Денег не тратит, но блокирует
  upload-флоу web.
- **Fix:** заменить `require(...)` на статический `import { useAuthStore } from '@/stores/auth'` +
  `useAuthStore.getState().apiKey` синхронно (или Bearer-паттерн как в mini).
- **Confidence:** high.

---

### MID-1 — `Targeting.countries` без валидации непустоты → молчаливый `['AQ']`-фолбэк / пустой geo

- **Файл:** `core/campaign_builder/config.py:93-108`.
- **Проблема:** нет `min_length=1`. При `countries=[]` и `add_antarctica=False` → пустой
  `geo_locations.countries`; при `add_antarctica=True` (дефолт) пустой список → `['AQ']` (Антарктида-таргет,
  де-факто мёртвый, но молча). Пустой geo обычно отклоняется Meta (permanent fail после создания
  кампании+adset → осиротевшие объекты/partial-create) либо даёт непреднамеренный охват.
- **Impact:** Money/корректность: `['AQ']`-фолбэк = «валидный» по превью залив, который никогда не открутит,
  либо partial-create с осиротевшими объектами.
- **Fix:** валидатор `Targeting`: `countries` обязателен и непуст после нормализации (≥1 реальной страны
  помимо авто-AQ), отклонять на этапе pydantic до создания Meta-объектов.
- **Confidence:** high.

### MID-2 — `upload_concepts`: лимит размера только по накопленному total после полного `read()` файла → OOM

- **Файл:** `apps/api/routers/v1/campaigns_create.py:267-282`.
- **Проблема:** проверка `total_bytes > _MAX_TOTAL_UPLOAD_BYTES` срабатывает ПОСЛЕ `await upload.read()`
  каждого файла целиком в память. Per-file лимита нет, потоковой проверки нет. Один файл (напр. 10 ГБ)
  читается целиком в RAM до проверки. Маршрут освобождён от `BodySizeLimitMiddleware` (exempt `/api/tools/`).
- **Impact:** Authenticated DoS/OOM воркера API: владелец X-API-Key одним multipart-запросом с гигантским
  файлом исчерпывает память → краш API, обслуживающего money-критичные cancel/cleanup/launch. Не слив
  бюджета, но отказ контура управления.
- **Fix:** читать чанками (`while chunk := await upload.read(1<<20)`) с инкрементальной проверкой и ранним
  413; per-file cap; проверять `Content-Length` до чтения.
- **Confidence:** high.

### MID-3 — Транзиентный инвариант worker↔execute не зафиксирован явно (хрупкая опора на `_has_created`)

- **Файлы:** `apps/campaign_creator_worker/main.py:193-215`, `core/campaign_builder/execute.py:375-396`.
- **Проблема:** при transient run остаётся в non-terminal, задача в retrying; на retry execute гоняется
  заново. Безопасно ТОЛЬКО потому, что `_raise_for_failure` поднимает `PartialCreateError` при любом
  созданном объекте до transient-ветки. Нет явного инварианта «transient ⇒ ничего в Meta не создано» —
  будущая правка execute/классификатора задублирует.
- **Impact:** Латентный money-риск при будущих изменениях; сейчас прикрыт, но без явной защиты на стыке.
- **Fix:** перед requeue ассертить, что execute не накопил `created_meta_ids` (если протекли → partial→mark_failed);
  документировать контракт «transient допустим только pre-create» + тест «transient ПОСЛЕ частичного создания ⇒ partial».
- **Confidence:** med.

### MID-4 — Mini StepLaunch авто-фаерит залив в useEffect при монтировании, без явной кнопки

- **Файл:** `frontend-mini/src/routes/campaigns/StepLaunch.tsx:99-116`.
- **Проблема:** `useEffect(()=>{if(launched)return; setLaunched(true); launch.mutateAsync(...)},[])` запускает
  `POST /launch` при mount; явной кнопки «Залить» нет (контраст с web `WizardStep7Launch`). Путь назад→вперёд
  защищён (`store.runId` → `launched=useState(!!runId)=true`), реальный двойной fire требует StrictMode (dev)
  или конкурентных вкладок.
- **Impact:** UX/контроль момента запуска. Money double-spend закрыт `idempotency_key` на бэке
  (`_compute_idempotency_key` детерминирован, SELECT-then-INSERT возвращает существующий run) — денег
  не задваивает. (Скорректировано HIGH→MID: денежного импакта нет.)
- **Fix:** явная кнопка «Запустить залив» перед auto-fire (как в web); либо ограничить useEffect только
  возобновлением существующего `runId`.
- **Confidence:** high (механизм), med (severity).

### MID-5 — Web StepPreview не пересчитывает dry-run при возврате на шаг 6 с изменёнными шагами 2–5

- **Файл:** `frontend/src/components/domain/campaigns/WizardStep6Preview.tsx:62-69`.
- **Проблема:** `useEffect(()=>{if(!preview.plan) validateMut.mutate(config)},[])` — при возврате на шаг 6 с
  заполненным `preview.plan` dry-run НЕ пересчитывается; `setGoal/setStructure/setCreatives` только мёрджат,
  `plan` не сбрасывают; «Пересчитать» — ручная. Байер ревьюит старые числа/нейминг.
- **Impact:** Money-нейтрально структурно: `config = store.buildConfig()` пересобирается на каждом рендере при
  `currentStep>=6` и уходит в шаг 7 — Meta получает СВЕЖИЙ конфиг (всё PAUSED). Проблема — устаревшее
  ОТОБРАЖЕНИЕ: байер ревьюит старое, создаётся (правильно) другое. Для money-фичи, где ревью байера = гейт,
  значимо. (Скорректировано HIGH→MID.)
- **Fix:** сбрасывать `preview.plan=null` при любом изменении шагов 2–5 (onChange/goNext), либо зависимость
  useEffect от хеша конфига.
- **Confidence:** high (механизм), med (severity).

---

### LOW-1 — `_default_start_date` использует `date.today()` (локальное время хоста), а контракт заявляет UTC

- **Файл:** `core/campaign_builder/config.py:165-167`, docstring `:11`.
- **Проблема:** `date.today()` берёт локальный день хоста; docstring/SOP заявляют «today+1 (UTC)». TZ старта
  выставляется отдельно через `account.tz_offset`, но выбор календарного дня — локальный.
- **Impact:** На хосте не в UTC (или около полуночи) дата в имени кампании и start_time могут уехать на сутки.
  На прод-хосте (Ubuntu/UTC) совпадает, контракт хрупкий.
- **Fix:** `datetime.now(timezone.utc).date() + timedelta(days=1)` либо явно задокументировать TZ хоста.
- **Confidence:** med.

### LOW-2 — Регистрозависимый дедуп `AQ` + `render_name` без экранирования плейсхолдеров

- **Файлы:** `core/campaign_builder/config.py:103-108` (`geo_countries`), `core/campaign_builder/naming.py:12-30`
  (`render_name`).
- **Проблема:** (1) `'AQ' not in countries` регистрозависим, коды стран не нормализуются — `['aq']` добавит
  второй `'AQ'`. (2) `render_name` делает простой `.replace` без экранирования: offer-код с `{date}` подставится
  буквально; пустой `type_label` оставляет двойные разделители.
- **Impact:** Краевые случаи грязного ввода. Не money — Meta отклонит невалидный ISO-код, offer-коды проекта
  без скобок.
- **Fix:** нормализовать страны к `upper()` перед дедупом; зафиксировать контракт плейсхолдеров (только из
  доверенного preset) либо чистить пустые разделители.
- **Confidence:** med.

### LOW-3 — `ad.media_bytes or b""` маскирует битый креатив тихой пустотой вместо явной ошибки

- **Файл:** `core/campaign_builder/execute.py:268,281`.
- **Проблема:** при `media_bytes=None` (регрессия uniquify) в Meta уйдёт пустой файл. Сейчас `uniquify_concepts`
  всегда заполняет — мёртвая защита, но маскирует будущий баг тихим битым креативом.
- **Impact:** Низкий (PAUSED, не покрутится). Усложняет диагностику.
- **Fix:** `if not ad.media_bytes: raise CampaignExecutionError(f'ad {ad.code}: пустые байты после uniquify')`.
- **Confidence:** high.

### LOW-4 — `resolve_concepts_from_config` читает ВСЕ фото-байты в память на этапе резолва

- **Файл:** `apps/campaign_creator_worker/__init__.py:208-218`.
- **Проблема:** для image-концептов `content=path.read_bytes()` префетчит все файлы до execute, на каждый блок.
  Пиковая память = сумма всех исходников. Видео грузятся как path (лениво), фото — нет.
- **Impact:** Память воркера при крупных батчах фото. Не money/корректность.
- **Fix:** грузить байты фото лениво внутри `_uniquify_one_image` из path (умеет: `content is None and path`).
- **Confidence:** med.

### LOW-5 — Idempotent-ветка launch при отменённом run → операционный тупик (нельзя перезалить тем же ключом)

- **Файл:** `apps/api/routers/v1/campaigns_create.py:379-391`, `cancel_run`.
- **Проблема:** при найденном `cancelled` run повторный launch того же конфига вернёт `status='cancelled'` +
  task_id отменённой задачи (claim берёт только pending/retrying — не достанется). Перезалить тем же
  `idempotency_key` нельзя — UNIQUE блокирует новый run, старый терминален.
- **Impact:** Не money (дубля нет). Операционный тупик: после cancel оператор не может перезапустить тот же
  конфиг тем же ключом. Обход — другой конфиг/ключ, неочевидно из API.
- **Fix:** в existing-ветке при `status='cancelled'` разрешить пересоздание (reset run в queued + новый task)
  либо явный 409 с понятным сообщением.
- **Confidence:** med.

### LOW-6 — Миграция 0025: безусловный `drop_constraint` падает на нестандартном bootstrap

- **Файл:** `migrations/versions/0025_campaign_creation.py:174,184`.
- **Проблема:** `op.drop_constraint("ck_task_queue_task_type", "task_queue")` безусловен. Таблица `task_queue`
  и её CHECK рождаются через `apply_schema.py → create_all` + `alembic stamp head`. Инвариант «констрейнт
  существует к моменту upgrade 0025» держится только при штатном bootstrap. `alembic upgrade head` на БД, где
  `task_queue` создан без именованного констрейнта (ручной DDL/старый дамп), упадёт `ProgrammingError`.
- **Impact:** Не money: dev/ops риск падения миграции при нестандартной инициализации. Данные не портятся.
- **Fix:** `op.execute("ALTER TABLE task_queue DROP CONSTRAINT IF EXISTS ck_task_queue_task_type")` (зеркально
  в downgrade).
- **Confidence:** med.

### LOW-7 — `CampaignRun` CheckConstraint с `name="status"` — хрупкое/рассогласованное именование

- **Файл:** `core/models/campaigns/run.py:84-87`.
- **Проблема:** CHECK статуса задан `name="status"` (опирается на naming_convention → `ck_campaign_run_status`,
  совпадает с миграцией), но `name='status'` совпадает с именем колонки и читается неявно. `UniqueConstraint`
  в той же модели — полным явным именем. Стиль рассогласован.
- **Impact:** Читаемость/maintainability. Функционально корректно, autogenerate-дрейфа нет.
- **Fix:** `name="campaign_run_status"` (convention даст то же имя) либо явное полное имя.
- **Confidence:** med.

### LOW-8 — `RunStatus`/`TERMINAL_RUN_STATUSES`/`RUN_STATUS_LABELS` дублированы в web и mini вместо `@fb/shared`

- **Файлы:** `frontend/src/lib/api/campaigns.ts:227-235`, `frontend-mini/src/lib/campaignTypes.ts:9-30`.
- **Проблема:** константы статусов дублированы независимо (в web TODO «консолидировать в `@fb/shared`»). При
  добавлении нового статуса воркером обновится только один фронт.
- **Impact:** Дрейф лейблов: новый статус (напр. `cancelling`) покажется raw на одном из фронтов.
- **Fix:** вынести в `packages/shared/src/campaign.ts`, импортировать в оба фронта (выполнить TODO).
- **Confidence:** high.

### LOW-9 — web `config = currentStep>=6 ? store.buildConfig() : null` без мемоизации

- **Файл:** `frontend/src/routes/campaigns/create/index.tsx:184`.
- **Проблема:** `buildConfig()` вызывается на каждом рендере страницы на шагах 6/7, пересоздаёт объект config.
- **Impact:** Не money. Лишние ре-рендеры/пересоздание объекта.
- **Fix:** `useMemo` по шагу+ключевым полям, либо вычислять только при переходе на 6/7.
- **Confidence:** low.

### LOW-10 — mini StepConfig не валидирует нижнюю границу бюджета

- **Файл:** `frontend-mini/src/routes/campaigns/StepConfig.tsx:57-65`.
- **Проблема:** `handleNext` проверяет только верх (`>10_000_000`); ввод $0.50 → `dailyCentsNum=50` проходит.
  web наоборот блокирует `<100` (расхождение web↔mini).
- **Impact:** Не money: бэк `Budget._check` режет `daily_cents<MIN_DAILY_BUDGET_CENTS=100` → 422; hard-cap
  держится. Чисто UX-гэп (поздняя ошибка, потеря данных шага). (Скорректировано HIGH→LOW.)
- **Fix:** `if (dailyCentsNum!==null && dailyCentsNum<100) { setError('Минимальный бюджет $1.00'); return; }`.
- **Confidence:** high (механизм), high (LOW-severity).

### Доп. (frontend, med-confidence shape-test) — StepStructure тесты могут маскировать поведение

- **Файл:** `frontend-mini/src/routes/campaigns/StepStructure.tsx`, тест `campaigns.steps.test.tsx:205-243`.
- **Проблема:** тесты ожидают `camp_1/camp_2` и текст «Ключи не могут быть пустыми», но реальный компонент
  не сверён — возможны shape-tests, не проверяющие навигацию. Нет уверенности, что StepStructure блокирует
  переход без кампаний.
- **Impact:** Маскировка багов навигации шагов (паттерн Round 11).
- **Fix:** прочитать `StepStructure.tsx`, сверить ожидания тестов; тест на disabled «Далее» при пустом списке.
- **Confidence:** med. *(Учтено в общем счёте frontend как часть MID-группы; отдельной строкой в таблицу не выделено.)*

---

## Рекомендованный план

### Перед продом (БЛОКЕРЫ — money/security вперёд)

1. **CRIT-1 (money, reconciler-дыра)** — добавить `campaign_create` в irreversible-контур reconciler'а
   (fail-stuck без retry + exclude из requeue) + усилить run-level guard на non-terminal-with-progress +
   атомарный claim-run. Закрывает заодно HIGH-3 (live-zombie). **Первый приоритет.**
2. **HIGH-2 (money, commit-без-ack)** — transient на шаге campaign → potential-partial/mark_failed + алерт
   оператору. Закрывает второй money-путь к дублю кампании.
3. **CRIT-2 (фича мертва)** — выровнять контракт фронт↔бэк (предпочтительно `@model_validator(mode='before')`
   на бэке + `gen:api` для обоих фронтов) + контрактный тест payload→parse. Без этого UI не работает в принципе.
4. **MID-1 (money/осиротевшие объекты)** — валидация непустого `countries` на pydantic-уровне.
5. **HIGH-4 (контур управления)** — `ON CONFLICT` в launch (идемпотентный ответ при гонке).
6. **HIGH-5 (web upload сломан)** — убрать `require()` → статический import / Bearer.
7. **MID-2 (DoS/OOM API)** — чанковый upload + per-file cap + ранний 413.

### Tech-debt (после прода / отдельным заходом)

- HIGH-1 / MID-3: единый источник раскладки превью↔исполнитель + семантический тест; явный инвариант
  «transient ⇒ pre-create».
- MID-4 / MID-5: явная кнопка залива в mini; сброс `preview.plan` при правках шагов.
- LOW-1 (UTC-дата), LOW-2 (нормализация стран/render_name), LOW-3 (явная ошибка на пустых байтах),
  LOW-4 (ленивое чтение фото), LOW-5 (перезалив после cancel), LOW-6 (идемпотентный drop_constraint),
  LOW-7 (имя констрейнта), LOW-8 (консолидация RunStatus в `@fb/shared`), LOW-9 (useMemo), LOW-10 (mini
  budget-min), shape-test StepStructure.

---

## Вердикт готовности к проду

**НЕ ГОТОВ. Блокеры есть.**

- **CRIT money:** CRIT-1 (reconciler не защищает `campaign_create`) — гарантированный дубль кампании +
  двойной открут бюджета при любом крахе/деплое воркера или просто медленном заливе (HIGH-3 — частный случай).
  Это самый тяжёлый класс проекта, реализуемый штатно на больших видео-концептах.
- **HIGH money:** HIGH-2 (commit-без-ack на `POST /campaigns`) — второй путь к дублю кампании.
- **CRIT функциональный:** CRIT-2 — сервис нерабочий из UI (422 на каждый запрос); денег не сливает, но
  фича мёртвая.

До закрытия CRIT-1 + HIGH-2 (+MID-1 против осиротевших объектов) в прод выпускать нельзя — money-инварианты,
заявленные в спеке (`idempotency_key против двойного залива`, «зеркало существующих воркеров»), в коде
НЕ держатся для нового `campaign_create`. CRIT-2 + HIGH-5 — блокеры работоспособности фичи. Остальные HIGH/MID —
после устранения money-блокеров.
