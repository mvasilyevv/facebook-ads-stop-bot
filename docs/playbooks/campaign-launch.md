# Playbook: Залив FB-кампании (gambling)

> Источник правды агента `fb`. Читать ПЕРЕД заливом. Правки — только с апрува байера.

## Статус
- ✅ **работает:** Graph API Batch — проверено боевым заливом GH_AVI 1×5×1 (`scripts/create_gh_avi_api.py`):
  кампания + 5 адсетов + 5 креативов + 5 ads, PAUSED. page_id/картинки/start_time/опт.текста — всё через API.
- ✅ **DELETE через API работает** (чистка осиротевших). `#10 Permission Denied` бывает транзиентно
  сразу после создания объекта — повторить через паузу.
- ⚠️ **грабли:** Vision-автопилот зависит от селекторов Ads Manager (дрейфуют). Batch НЕ атомарен
  (partial-fail → осиротевшие PAUSED-объекты, чистить через `delete_objects.py`).
- 🔧 **сломано:** Vision-автопилот шаг `set_budget` (старый ABO-селектор «Бюджет группы»). Для залива
  используем Метод 1 (API) — он от UI не зависит.

## Стандартные правила (ВСЕГДА)
- **Уточнять `act_id` кабинета** перед каждым заливом (явно подтвердить с байером). Текущий: `act_26943307705301002`.
- **Событие пикселя — «Покупка» (Purchase/FTD).** Objective `OUTCOME_SALES`, optimization `OFFSITE_CONVERSIONS`,
  `promoted_object={pixel_id, custom_event_type: PURCHASE, smart_pse_enabled: false}`. Не TRAFFIC даже на холодном пикселе.
- **Дата в имени = СЛЕДУЮЩИЙ день** (today+1). Имя: `MV | <GEO> | <SLOT> | adset.pro | DD.MM`.
- **`start_time` адсетов — ОБЯЗАТЕЛЕН** = следующие сутки 00:00 в TZ кабинета. Без него Meta ставит момент
  создания (баг «старт сегодня в 00:32»). TZ узнать: `GET /act_X?fields=timezone_name,timezone_offset_hours_utc`.
  Текущий кабинет — `America/Hermosillo` (UTC-7, без перехода на лето) → `YYYY-MM-DDT00:00:00-07:00`.
- **Страница — ВЫБОР БАЙЕРА, не первую из `promote_pages` вслепую.** Выгрузить все (`inspect_setup.py`:
  `promote_pages` + `/me/accounts` с категорией/правами), показать, байер выбирает. (Текущая: Game star `103053722121477`.)
- `special_ad_categories = ["NONE"]` (gambling-whitelist; прямое соглашение с Meta — см. `creative-gen.md`).
- **ABO или CBO — выбор под задачу.** **ABO = 1-N-1** (1 объявление на адсет; бюджет на адсете → чистые FTD
  по вариантам). `bid_strategy` при ABO — **на АДСЕТЕ** (`LOWEST_COST_WITHOUT_CAP`), НЕ на кампании.
  Дефолт теста = ABO $2.99/адсет, если не сказано иначе.
- **Статус — PAUSED** → байер ревьюит в Ads Manager → сам unpause. ACTIVE сами не ставим.
- **Гео: Антарктида + целевая страна** (`geo_locations.countries: ["<ISO>", "AQ"]`). Антарктиду — всегда.
- **Advantage+ «оптимизация текста»** (`text_optimizations`) — для гемблы `OPT_OUT` (контроль формулировок),
  если байер не сказал вкл. С v22.0 каждая creative-фича opt-in/out поштучно (бандл депрекейтнут).
- Vision-браузер в **РУССКОЙ локали** (для Метода 2 — гео/CTA/события по-русски).

## Методология теста (важно — не путать варианты)
- **Этап 1 — тест КРЕАТИВОВ.** N адсетов = N разных картинок при **ЕДИНОМ тексте** (primary/headline/desc).
  Имена адсетов — по коду креатива (`CR001 | <визуал>`). Ищем лучший ВИЗУАЛ по FTD.
- **Этап 2 — тест УГЛОВ по тексту.** Берём победивший креатив, на нём N адсетов с РАЗНЫМ primary text.
  Отдельный залив после данных.
- Антипаттерн: 5×3 (несколько ads в адсете) при ABO — показы/обучение делятся, атрибуция по вариантам грязнится.

## Метод 1 — Graph API Batch (основной)
Шаблон: `scripts/create_gh_avi_api.py` (режимы: без флага = spec-print; `--go` = боевое). Без UI-селекторов.
- Канал: `MetaApiClient.execute_graph_call` (gRPC → browser-agent → `page.evaluate(fetch)` изнутри Vision-сессии;
  НЕ httpx — токен session-bound, META_PLAN §1).
- **page_id:** задаётся константой по выбору байера (НЕ авто-первая). Подтвердить имя: `GET /{page_id}?fields=name`.
- **Картинки:** `MediaUploader.upload_image(act, bytes) → image_hash`. Хэши переиспользуемы между перезаливами
  (одинаковый файл → одинаковый хэш, повторно грузить не обязательно).
- **Порядок создания (проверено боевым; БЕЗ JSONPath):**
  1. **Кампания — отдельным 1-entry batch**, берём `campaign_id` из ответа. ⚠️ Операция, на которую ссылаются
     JSONPath-ом (`{result=campaign:$.id}`), возвращает `null` в ответе батча (был ложный `missing_sub_result`) —
     поэтому кампанию создаём отдельно. При ABO кампания — БЕЗ бюджета и БЕЗ `bid_strategy`
     (стратегия на кампании без её бюджета → отказ `subcode 1885737 «В кампании нет бюджета»`).
  2. **Адсеты** — batch с РЕАЛЬНЫМ `campaign_id` (без JSONPath).
  3. **Креативы** — batch.
  4. **Ads** — batch (по реальным `adset_id` + `creative_id`).
- **Эталонное тело адсета (ABO):** `billing_event: IMPRESSIONS`, `optimization_goal: OFFSITE_CONVERSIONS`,
  `bid_strategy: LOWEST_COST_WITHOUT_CAP` (на адсете), `daily_budget`, `destination_type: WEBSITE`,
  `promoted_object: {pixel_id, custom_event_type: PURCHASE, smart_pse_enabled: false}`,
  `attribution_spec: [{event_type: CLICK_THROUGH, window_days: 1}]` (если байер не задал иначе),
  `targeting: {geo_locations: {countries: [GEO, AQ], location_types: [home, recent]}, age_min, age_max,
  targeting_automation: {advantage_audience: 1}}`, `start_time`, `status: PAUSED`.
- **Тело креатива:** `object_story_spec{page_id, link_data{message, link, image_hash, name=headline, description,
  call_to_action{PLAY_GAME}}}`, `url_tags`, `degrees_of_freedom_spec.creative_features_spec.text_optimizations.enroll_status`.
- Хелперы: `core/meta_api/mutations/_batch_helpers.py` (`make_batch_entry`/`build_batch_payload`/`parse_batch_response`;
  `_encode_value` сохраняет JSONPath refs и корректно кодирует вложенные JSON-объекты `targeting`/`promoted_object`).

### Эталон ABO в кабинете (референс полей)
Чужие рабочие кампании `14.05 MZ/ZM Artemteam ABO 1-3-1` (`inspect_abo.py`): OUTCOME_SALES, 1 ад/адсет,
адсеты названы числами, `promoted_object` с `smart_pse_enabled:false`, attribution CLICK/VIEW, targeting с
Advantage+ audience + `[GEO, AQ]` + home/recent + age 18-65. Сверяться при сомнениях в полях.

## Структура папки креативов
Канон: `creo_folder/{1..N}/файлы`. Для **Этапа 1 (тест креативов)** берём **1-ю копию** каждого формата
(`{a}/GH_AVI_CR00a_1.jpeg`) → N адсетов × 1 ад. Uniquify-копии 2–3 — в запас/масштаб.
⚠️ Uniquify кладёт копии в `<OFFER>_<CRxxx>_..._3copies/{1,2,3}/` — перед заливом разложить в канон.

## Трекинг (url_tags объявления)
`sub2=MV&sub3={format_code}&sub4={cabinet_id}&sub5={{campaign.name}}&sub6={{adset.name}}&sub7={{ad.name}}`.
sub3 = код креатива (`GH_AVI_CR001`), sub6 = имя адсета. `{{...}}` — FB-макросы, оставлять как есть.

## Метод 2 — Vision-автопилот (fallback / когда нужен UI-сабмит)
Шаблон: `scripts/run_creator_gh_avi.py` (`--print` / `--run`). Кликает Ads Manager как человек.
`core/campaign_creator/`: `build_campaign_spec_from_folder` → `build_plan` → `open_page(client)` → `PlanRunner`.
- Пауза observer на сборку: `UPDATE observer_config SET is_scanning_enabled=false` ДО, вернуть `true` в finally.
- 🔧 Селекторы дрейфуют (`set_budget` сломан). Использовать только если API-путь недоступен.

## Инструменты (scripts/)
- `create_gh_avi_api.py` — залив (spec-print / `--go`).
- `inspect_setup.py` — таймзона кабинета + все страницы + creative-поля (выбор страницы, проверка enhancements).
- `inspect_abo.py [OBJECTIVE]` — разбор ABO-кампаний кабинета (эталон полей).
- `check_campaigns.py "<needle>"` — дубль/осиротевшие перед заливом.
- `verify_campaign.py <id>` — постфактум: start_time/страница/опт.текста/структура. Гонять ПЕРЕД unpause.
- `delete_objects.py <id...>` — чистка (campaign/adset/ad).

## Подъём стека (нужен — Vision-сессия)
- `./run.sh --no-tunnel` (фон). Docker (PG :5433, Redis :6380) + browser-agent gRPC :50051 + воркеры + Vision.
- ⚠️ Пустой `telegram_config` → run.sh падает на poller BACKOFF → `python scripts/restore_secrets.py`, рестарт.
- Готовность: `nc -z localhost 50051`; `redis-cli -p 6380 GET observer:runtime` (был успешный скан).

## Перед перезаливом / чистка
- Проверить дубль: `check_campaigns.py "GH | AVI"`. Осиротевшие/старую кампанию — удалить (`delete_objects.py`),
  иначе дубль (MEMORY: dry-run cleanup). Картинки переиспользуются — заново не грузить.

## После залива
- `verify_campaign.py <id>` → убедиться в start_time/странице/полях → отдать байеру на ревью → он сам unpause.
- Observer мониторит (стоп-правила + авто-стоп через Meta API). `status: live` в реестре оффера.
- Через ~неделю — `creative_report` по FTD (разрез `sub3`/`sub6`) → лучший креатив → Этап 2.
