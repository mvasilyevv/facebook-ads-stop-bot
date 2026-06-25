# Смешанные медиа в адсете + per-offer нумерация креативов + реестр кодов

Дата: 2026-06-25
Статус: дизайн утверждён, готов к плану реализации.

## Проблема

1. **Жёсткое разделение фото/видео.** Визард создания кампаний моделирует кампанию
   как типизированную (`CampaignBlock.kind ∈ {image, video}`). Один adset не может
   держать одновременно фото- и видео-объявления, хотя Facebook это разрешает
   (ad = одно медиа, но adset = контейнер ads любого типа). Ограничение — наше
   модельное, не FB: `kind` зашит на кампанию ради защиты уникализатора (PIL для
   фото / ffmpeg для видео) от файла чужого типа.

2. **Коллизия кодов креативов между заливами.** Код креатива `{offer}_CR{NNN}`
   нумеруется с `start=1` в каждом заливе. Второй залив того же оффера снова даёт
   `CR001, CR002…` → `sub3`-атрибуция трекера коллизирует между заливами/днями.
   В БД нет ни счётчика, ни уникального индекса — ничто это не предотвращает.

3. **Нет трекинга выданных кодов.** Мы не храним, какие коды/Meta-creative-id уже
   заливали по офферу.

## Ключевые факты кодовой базы (проверено)

- Код креатива `creative_codes()` ([core/campaign_builder/naming.py:33](../../../core/campaign_builder/naming.py))
  **уже не зависит от имени файла** — имя файла влияет только на тип медиа
  (по расширению, `ref_media_kind()`). Код идёт в имя ad, имя creative и `sub3`.
- `execute.py:302` выбирает creative по `ad.media_kind` (per-ad), **а не** по
  `block.kind`. Значит для смешанных медиа execute.py менять не нужно.
- `media_kind` сейчас проставляется из `block.kind` в `uniquify.py` (строки 194, 264).
- Воркер уже строит `ConceptInput.kind` по расширению
  ([apps/campaign_creator_worker/__init__.py:260](../../../apps/campaign_creator_worker/__init__.py)),
  с лишним фолбэком `or block.kind == "video"`.
- `code_start` уже параметризован в `build_code_layout` / `build_campaign_spec`;
  сейчас всегда стартует с 1 и накапливается только между блоками одного залива.
- Персист после залива: `CampaignRun.config` (JSONB), `CampaignRun.created_meta_ids`
  (JSONB). Отдельной таблицы кодов/creative-id нет.

## Money-safety инварианты (сохранить)

- **preview == launch == retry.** Коды в превью равны созданным. Достигается тем,
  что после создания run'а `code_start` зафиксирован в `run.config`, и execute/retry
  берут коды строго оттуда.
- **Идемпотентный retry.** Коды детерминированы (`code_start` + раскладка), запись
  в реестр через `ON CONFLICT DO NOTHING`.
- **Защита уникализатора от орфанов.** `validate` отклоняет концепт с **неизвестным**
  расширением до любого POST (раньше отклонялся «чужой тип в типизированной кампании»).

## Решение

### 1. Смешанные медиа — тип per-concept (подход A)

- `core/campaign_builder/uniquify.py`:
  - `build_uniquification_plan`: `media_kind=block.kind` → `media_kind=concept.kind`.
  - `uniquify_concepts`: диспатч `if block.kind == "video"` → `if concept.kind == "video"`
    (или по `ad.media_kind`, проставленному из concept).
- `core/campaign_builder/config.py`:
  - `CampaignBlock.kind` убрать (поле и его использование).
  - Валидатор `_check`: вместо «концепт чужого типа в kind-кампании» — отклонять
    концепт с **неизвестным** расширением (`ref_media_kind(ref) is None`) до POST.
- `core/campaign_builder/builder.py`:
  - Убрать `kind` из `CampaignSpec_Block`. Если превью нужна по-кампанийная сводка
    медиа — считать счётчики (X фото / Y видео) из `concept_refs` через
    `ref_media_kind`, а не из единого `kind`.
- `apps/campaign_creator_worker/__init__.py`: убрать фолбэк `or block.kind == "video"`,
  оставить определение типа по расширению.
- `core/campaign_builder/execute.py`: **без изменений** (уже per-ad по `media_kind`).
- Семантика: кампания = N adset'ов + смешанный набор концептов. Каждый adset = K ads
  (K = все концепты блока, фото+видео), тип каждого ad — по файлу. «Чистая» кампания
  (только фото или только видео) — частный случай.

### 2. Per-offer нумерация кодов

- Новая таблица-аллокатор `offer_creative_seq(offer_code TEXT PRIMARY KEY,
  next_seq INTEGER NOT NULL DEFAULT 0)`.
- **Validate/превью** (`campaigns_create.py` validate-путь): read-only peek текущего
  `next_seq` для оффера → `build_campaign_spec(config, code_start=peek + 1)`. Без
  резерва; превью показывает реалистичные коды.
- **Launch (создание run'а)**: атомарно
  `INSERT INTO offer_creative_seq (offer_code, next_seq) VALUES (:code, :span)
   ON CONFLICT (offer_code) DO UPDATE SET next_seq = offer_creative_seq.next_seq + :span
   RETURNING next_seq`,
  где `span` = сумма `K×N` по всем блокам. `code_start = returned - span + 1`.
  Записать `code_start` в `run.config`.
- **Execute/retry**: `build_campaign_spec(config, code_start=config["code_start"])`.

### 3. Реестр выданных кодов (ledger)

- Новая таблица `campaign_creative(id UUID PK, offer_code TEXT, code TEXT, kind TEXT,
  meta_creative_id TEXT, run_id UUID, created_at TIMESTAMPTZ,
  UNIQUE(offer_code, code))`.
- Запись в `execute.py` после создания каждого creative (когда есть
  `meta_creative_id`), `ON CONFLICT (offer_code, code) DO NOTHING` (идемпотентность).

### 4. Frontend (web `frontend/` + mini `frontend-mini/`)

- **Шаг 4 (структура)**: одна кнопка «+ Кампания» (без типа), на блок — `adset_count`.
  Убрать «+ Фото-кампания»/«+ Видео-кампания» и kind-чипы.
- **Шаг 5 (концепты)**: привязка смешанных концептов к кампаниям без kind-фильтра;
  иконка типа у каждого концепта; на кампании показывать «X фото + Y видео = K ads/adset».
- `buildConfig` (оба фронта): убрать фильтр `refKind(c.ref) === block.kind` — кампания
  получает все привязанные концепты.
- **Шаг 6 (превью)**: смешанные счётчики; warning «кампания без концептов» вместо
  «видео-кампания без видео».
- Типы: убрать `kind` из `CampaignStructure` (`frontend/src/lib/api/campaigns.ts`
  и mini-аналог).

### 5. Миграция

- Аддитивная миграция (линейный head): создать `offer_creative_seq` и
  `campaign_creative`.
- **Backfill** `offer_creative_seq.next_seq` — best-effort: для каждого оффера
  суммарное число уже созданных ads из `campaign_run.created_meta_ids` (чтобы новые
  коды не наехали на коды старых заливов). Если распарсить нельзя — `0`.

## Тестирование

- **Смешанный блок**: K = (фото + видео); типы ads в спеке корректны (image/video по
  файлу); execute создаёт правильные creative-bodies.
- **Нумерация между заливами**: два последовательных залива одного оффера → коды
  второго продолжают первый, без пересечения.
- **Конкурентная аллокация**: параллельные launch'и одного оффера не выдают
  пересекающихся диапазонов (атомарный `RETURNING`).
- **Идемпотентность реестра**: повторный execute (retry) не плодит дублей
  (`ON CONFLICT`).
- **Валидатор**: неизвестное расширение → reject до POST; известное (image/video) →
  ок в одном блоке.
- **Backfill**: счётчик после миграции ≥ числа исторических кодов оффера.
- **preview == launch**: коды из validate-плана совпадают с созданными при launch
  (фиксированный `code_start` в config).

## Объём

- Backend: `uniquify.py`, `config.py`, `builder.py`, `execute.py` (запись в реестр),
  `campaigns_create.py` (аллокация + peek), worker, новые модели + миграция.
- Frontend: шаги 4/5/6 + `buildConfig` + типы на обоих фронтах.

## Вне объёма (YAGNI)

- Третий тип `mixed` как явная сущность (подход A покрывает «чистые» кампании
  частным случаем).
- Тип на уровне adset (раздельные адсеты) — пользователь выбрал смешивание в одном.
- UI для просмотра реестра креативов — только запись; чтение по запросу позже.
