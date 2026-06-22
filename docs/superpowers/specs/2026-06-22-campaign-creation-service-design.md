# Сервис создания кампаний — дизайн (2026-06-22)

Статус: **утверждён в брейнсторминге** (разделы 1–7 согласованы по одному с владельцем).
Это PROD-фича (тратит рекламный бюджет). Money-safe гейт — всё создаётся при PAUSED-кампании.

## 0. Цель

Полнофункциональный сервис создания FB-кампаний из UI (desktop + mobile): загрузил концепты
креативов один раз → сервис сам уникализирует их под число adset'ов → собирает и заливает кампанию
через Marketing API. Заменяет ручной флоу (ручная уникализация + раскладка по папкам + YAML +
`fb_launch.py`), переиспользуя проверенный движок залива.

Границы scope: только **создание FB-кампаний**. PWA/трекер-кампании AdSet.pro — отдельный канал
(собираются в AdSet.pro UI), здесь не делаем. Трекинг-ссылка (`destination_link`) — вход.

## 1. Архитектура (Подход A — outbox + выделенный воркер)

```
UI (desktop + mini)
  ├─ upload концептов ─► POST /tools/campaigns/upload ─► server media store (per-run dir)
  ├─ build config (preset + per-run) ─► POST /tools/campaigns/validate ─► dry-run spec preview
  └─ launch ─► POST /tools/campaigns/launch
                  │ INSERT campaign_run(status=queued) + task_queue(task_type='campaign_create')
                  ▼
         campaign_creator_worker  (claim FOR UPDATE SKIP LOCKED → исполнение → прогресс в campaign_run)
                  ├─ core/creatives/        : 1 концепт → N уникальных копий (фото/видео ffmpeg)
                  └─ core/campaign_builder/  : движок из fb_launch.py (campaign→adsets→upload→creatives→ads)
                                               канал ExecuteGraphCall (Vision) + MediaUploader, статус по launch_state
                  ▼
         campaign_run.status=succeeded + созданные Meta-ID → байер ревьюит в Ads Manager, снимает паузу
```

Ключевые решения:
- Логику `scripts/fb_launch.py` выносим в переиспользуемый пакет **`core/campaign_builder/`** (pydantic
  `CampaignConfig` + движок сборки). CLI `fb_launch.py` остаётся рабочим, импортирует из builder — без форка логики.
- Тяжёлое (ffmpeg-уникализация, аплоады) — только в воркере, не в API-запросе (latency-tolerant правило).
- Канал — `ExecuteGraphCall` через Vision (как fb_launch), `MediaUploader` для медиа.
- **proto-фикс:** добавить `ad_account_id` в `ExecuteGraphCall` (proto + browser-agent + регенерация pb2),
  чтобы залив адресовал явно заданный кабинет, а не «активную вкладку Vision» (надёжно для мульти-кабинета).

## 2. Модель конфига кампании

Деление: **preset** (стабильное, переиспользуется) / **run** (меняется каждый залив). Дефолты — по SOP/памяти.

| Группа | Поля | Preset/Run | Дефолт |
|---|---|---|---|
| Идентичность | `act_id`, `page_id`, `pixel_id`, `tz_offset` | preset | — |
| Оффер | `offer_code`, `byer_tag` | preset/run | — |
| Цель | `objective`, `optimization_goal`, `custom_event_type` | preset | OUTCOME_SALES / OFFSITE_CONVERSIONS / **PURCHASE** |
| Спецкатегории | `special_ad_categories` | preset | ["NONE"] |
| Назначение/CTA | `destination_link`, `cta`, `text_optimizations` | run/preset | PLAY_GAME / OPT_OUT |
| Дата | `start_date` | run | **сегодня+1** |
| Текст ad | `ad_text.mode` (none/text) + тексты | run | none |
| Бюджет | `level` (CBO/ABO), `daily_cents`, `bid_strategy` | run | campaign / LOWEST_COST_WITHOUT_CAP, **hard-cap валидация** |
| Таргет | `countries` (**+AQ авто**), `age_min/max`, `advantage_audience` | run | 18–65 / true |
| Атрибуция | `click_through_days`, `view_through_days` | preset | 1 / 1 |
| Трекинг | `url_tags` (sub2…sub7) | preset-шаблон | по SOP |
| Структура | список кампаний → adset'ов (`kind` image/video, число adset'ов N) | run | — |
| Креативы | загруженные концепты + код-нейминг `OFFER_CRxxx`, маппинг концепт→кампания | run | авто-нейминг |
| Уникализация | `copies_per_concept` (default = N), advanced ads/adset | run | авто (= числу концептов) |
| Нейминг | шаблон `{byer} \| {offer} \| {type} \| adset.pro \| {date}` | preset-шаблон | по SOP |
| **launch_state** | `campaign_paused` (дети активны) / `all_paused` | run | **campaign_paused** |

Продвинутое (плейсменты, гео-сплит, lookalike/custom, dayparting) — **в беклог** (YAGNI, владелец отклонил для v1).

### launch_state (money-инвариант)
- `campaign_paused` (дефолт): кампания PAUSED, adset'ы+ads ACTIVE. Подтверждено поведением Meta:
  `effective_status` детей = CAMPAIGN_PAUSED → **спенда нет**, при этом модерация/ревью креативов идёт →
  байер снимает паузу одним тумблером, всё стартует мгновенно.
- `all_paused`: всё PAUSED (как сейчас в fb_launch).
- В обоих спенда нет, пока кампания на паузе.

## 3. Автоуникализация

Вход: K концептов (фото/видео) + N adset'ов + `copies_per_concept` (default = N).
```
для каждого концепта C:
    variants = uniquify(C, copies=N)         # seed = hash(concept_id, i) → детерминированно (идемпотентный retry)
        фото  → core/creatives/uniquifier.py (uniquify_image_bytes)
        видео → core/creatives/video_uniquifier.py (ffmpeg)
    variants[i] → adset i
итог: adset i = K ads (1 на концепт), креатив = уникальная копия i; нейминг OFFER_CRxxx + суффикс копии
```
Переиспользуем существующий `core/creatives/service.uniquify_creatives(copies=…)`. Работает в воркере,
результат во временную папку запуска → `MediaUploader` (UploadImage / UploadVideo chunked).

## 4. Воркер и обработка ошибок

`apps/campaign_creator_worker/` — `task_type='campaign_create'`, heartbeat `worker:heartbeat:campaign_creator`,
claim FOR UPDATE SKIP LOCKED, graceful shutdown, фоновый heartbeat-таск (как у прочих воркеров).

Статусы: `queued → uniquifying → uploading → creating → succeeded | failed`. Прогресс + созданные Meta-ID
пишутся в `campaign_run` (jsonb `progress` + `status`). UI стримит (WS/поллинг).

Классификация (зеркало существующих воркеров):
- Permanent (валидация конфига, Meta permission, превышен budget-cap, policy-reject) → `mark_failed`, без retry.
- Transient (сеть, rate-limit, session unavailable) → `requeue` + backoff.
- Partial-create (Batch API не атомарен) → `mark_failed` + лог created_ids, **без retry** (дубли); cleanup-кнопка.

Money-инварианты: `idempotency_key` (offer+date+хеш структуры) против двойного залива; кампания PAUSED →
кривой запуск не тратит; budget hard-cap (как `set_adset_budget`: $100k/день, $1M lifetime).

## 5. API (`apps/api/routers/v1/campaigns_create.py`, за X-API-Key)

| Endpoint | Назначение |
|---|---|
| `GET/POST/PUT/DELETE /tools/campaigns/presets` | CRUD пресетов |
| `POST /tools/campaigns/upload` | multipart-загрузка концептов → media store, refs + превью |
| `POST /tools/campaigns/validate` | dry-run spec (структура/нейминг/число ads) без исполнения |
| `POST /tools/campaigns/launch` | создать campaign_run + задачу → run_id |
| `GET /tools/campaigns/runs` / `runs/{id}` | список / детали+прогресс+Meta-ID+ошибки |
| `POST /tools/campaigns/runs/{id}/clone` | клон запуска в черновик |
| `POST /tools/campaigns/runs/{id}/cancel` | отмена в очереди |
| `POST /tools/campaigns/runs/{id}/cleanup` | снести созданные Meta-объекты при partial-fail |

`CampaignConfig` (pydantic из `campaign_builder`) — единый контракт API↔воркер. openapi → `gen:api` для фронтов.

## 6. UI (desktop `frontend/` + mobile `frontend-mini/`)

Мастер-визард «Создание кампаний», шаги:
1. Старт: новый / из пресета / клон запуска.
2. Идентичность + оффер (из пресета, редактируемо).
3. Цель / бюджет / таргет / атрибуция / назначение.
4. Структура: кампании (static/video) → число adset'ов N.
5. Креативы: drag&drop концептов, привязка концепт→кампания, copies (advanced — ads/adset).
6. Превью: dry-run spec (что создастся, число ads, нейминг) + выбор `launch_state`.
7. Запуск → прогресс (live) → созданные Meta-ID.

Плюс экран «История запусков» (статус, клон, cleanup). Mini — те же шаги вертикально, тач ≥44px, загрузка
с телефона. Типы из `gen:api`, статус-лейблы и форматтеры из `@fb/shared`. Desktop ≤500 строк/компонент
(декомпозиция визарда по шагам).

## 7. Тестирование (полный цикл)

- **Unit:** валидация `CampaignConfig` (pydantic, edge); builder spec-генерация; число копий уникализации =
  N; распределение variant[i]→adset i; классификация ошибок; `idempotency_key` детерминизм; launch_state→
  статусы объектов; budget hard-cap.
- **Integration (нужен Postgres, не на живой :5433):** presets CRUD; launch создаёт run+task; воркер claim+
  исполнение с **замоканными** ExecuteGraphCall/MediaUploader; partial-fail → mark_failed + created_ids; cancel/clone.
- **Frontend (vitest):** шаги визарда, валидация, upload-флоу, превью-spec, история/клон.
- **Money-семантика:** созданные объекты PAUSED по launch_state; спенд-гейт; никаких реальных Meta-вызовов в тестах.

## 8. План реализации (волны, worktree, TDD)

Ветка `feat/campaign-creation`. Между волнами — интеграция + ревью оркестратором, в main без go владельца не мержим.

- **Волна 1 (фундамент, параллельно):**
  1. Data layer — модели `campaign_preset`, `campaign_run` + Alembic-миграция.
  2. `core/campaign_builder/` — извлечь `CampaignConfig` + движок из `fb_launch.py` (CLI остаётся рабочим).
  3. proto-фикс — `ad_account_id` в `ExecuteGraphCall` (proto + browser-agent + pb2 регенерация).
- **Волна 2 (зависит от В1):** uniquify-интеграция в builder; `campaign_creator_worker`; API-роутер.
- **Волна 3 (зависит от В2-контракта):** frontend web визард; frontend-mini визард.
- **Волна 4:** integration + frontend тесты, E2E dry-run, верификация, ревью перед merge.

Каждый «сотрудник» (агент) — полный цикл: код + тесты (TDD) + ruff + self-review. Integration-тесты пишутся,
но не гоняются на живой БД (нужен изолированный Postgres — финальная верификация отдельным гейтом).

## Решения (журнал)
- Раскладка: K концептов × N adset (default), advanced ads/adset — отдельный UI. 
- Хранение: пресеты + история запусков (клон).
- Источник креативов: загрузка через UI (drag&drop), работает с телефона/удалённого хоста.
- Мобильный: полный конструктор (не лайт).
- Архитектура: A (outbox + выделенный воркер).
- launch_state: кампания PAUSED + дети ACTIVE (default) — модерация проходит, старт одним тумблером.
- Адресация кабинета: чиним proto (`ad_account_id` явно).
- Продвинутый конфиг (плейсменты/гео-сплит/lookalike/dayparting): беклог.
