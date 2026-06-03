# Playbook: Залив FB-кампании (gambling)

> Источник правды агента `fb`. Читать ПЕРЕД заливом. Правки — только с апрува байера.

## Статус
- ✅ **работает:** Graph API Batch (page_id авто, аплоад картинок, 3 захода) — `scripts/create_gh_avi_api.py`; стандартные правила; подъём стека.
- ⚠️ **грабли:** Vision-автопилот зависит от селекторов Ads Manager (RU-локаль) — дрейфуют при апдейтах FB. Batch НЕ атомарен (partial-fail → осиротевшие PAUSED-объекты, чистить вручную).
- 🔧 **сломано:** Vision-автопилот шаг `set_budget` — кликал лейбл «Бюджет группы», в новом ABO-UI его нет (поле дневного бюджета видно сразу). Нужна правка шага.

## Стандартные правила (ВСЕГДА)
- **Событие пикселя — «Покупка» (Purchase/FTD).** Objective `OUTCOME_SALES`, optimization `OFFSITE_CONVERSIONS`, `promoted_object={pixel_id, custom_event_type: PURCHASE}`. Не TRAFFIC, даже на холодном пикселе.
- **Дата в имени = СЛЕДУЮЩИЙ день** (today+1, старт с новых суток). Имя: `MV | <GEO> | <SLOT> | adset.pro | DD.MM`.
- `special_ad_categories = ["NONE"]` (gambling-whitelist; у нас прямое соглашение с Meta — см. `creative-gen.md`).
- **ABO или CBO — выбор под задачу** (ABO = равный бюджет на вариант → чистые FTD по форматам; CBO = FB сам перекидывает бюджет на лучшее). Без форс-дефолта. Дефолт теста = ABO $2.99/адсет, если не сказано иначе.
- **Статус — PAUSED** → байер ревьюит в Ads Manager → сам unpause. ACTIVE сами не ставим.
- **Гео: Антарктида + целевая страна** (`geo_locations.countries: ["<ISO>", "AQ"]`). Антарктиду добавляем всегда.
- Vision-браузер в **РУССКОЙ локали** → гео/CTA/события по-русски («Гана», «Играть», «Покупка»).

## Метод 1 — Graph API Batch (предпочтителен с нашим Meta-соглашением)
Шаблон: `scripts/create_gh_avi_api.py`. Без UI-селекторов → не ломается от апдейтов FB.
- Канал: `MetaApiClient.execute_graph_call` (gRPC → browser-agent → `page.evaluate(fetch)` изнутри Vision-сессии; НЕ httpx — токен session-bound, META_PLAN §1).
- page_id: авто из кабинета (`GET /act_X/promote_pages` → fallback скан `/act_X/adcreatives`).
- Картинки: `MediaUploader.upload_image(act, bytes) → image_hash` (15 объявлений = 15 хэшей; uniquify-копии md5-разные).
- Структура — **3 захода** (лимит Batch 50 entry + защита от длины URL):
  - **A:** campaign + N adsets (JSONPath adset→campaign) → парсим `campaign_id` + `adset_ids`.
  - **B:** N×M креативов (`object_story_spec{page_id, link_data{message, link, image_hash, name=headline, description, call_to_action{PLAY_GAME}}}`, `url_tags`) — чанками.
  - **C:** N×M ads (по реальным `adset_id` + `creative_id`) — чанками.
- Хелперы: `core/meta_api/mutations/_batch_helpers.py` (`make_batch_entry`/`jsonpath_ref`/`build_batch_payload`/`parse_batch_response`; `_encode_value` НЕ трогает JSONPath refs `{result=...}`).
- ⚠️ **Открытый вопрос:** покрывает ли наше Meta-соглашение API-аплоады креативов (а не только ad-policy/дисклеймеры). Проверить на первом заливе; если режет на content review — Метод 2.

## Метод 2 — Vision-автопилот (fallback / когда нужен UI-сабмит)
Шаблон: `scripts/run_creator_gh_avi.py` (`--print` = план без браузера / `--run` = боевой). Кликает Ads Manager как человек.
- `core/campaign_creator/`: `build_campaign_spec_from_folder` → `build_plan(spec)` (разворачивает 1×N×M, включая `duplicate_adset` для адсетов 2..N) → `open_page(client)` (Playwright по CDP к живому Vision) → `PlanRunner(STEP_REGISTRY).run`.
- **Пауза observer на время сборки:** `UPDATE observer_config SET is_scanning_enabled=false` ДО, вернуть `true` в finally (иначе observer сканит ту же вкладку и сбивает сборку).
- **Vision открыт на целевом кабинете** (`act_...`) — строит в том, что открыт. Раннер печатает `page.url` — свери `act=` до шагов.
- 🔧 Селекторы записаны ~2026-05, дрейфуют (`set_budget` сейчас сломан). `save_draft` в конце → PAUSED-черновик, деньги не тратятся до unpause.

## Структура папки креативов (для обоих методов)
Канон: `creo_folder/{1..N}/файлы` — **каждая числовая подпапка = ОДИН адсет**, файлы внутри = объявления адсета.
⚠️ Uniquify кладёт копии в `<OFFER>_<CRxxx>_..._3copies/{1,2,3}/` (подпапки = КОПИИ одного формата!). Перед заливом пересобрать в канон: `campaign_root/{1..N}/{3 копии формата}` (1 подпапка = 1 формат × копии). Иначе получишь «N адсетов × 1 ад».

## Трекинг (url_tags объявления)
`sub2=MV&sub3={ad_name}&sub4={cabinet_id}&sub5={{campaign.name}}&sub6={{adset.name}}&sub7={{ad.name}}`.
sub3 = код формата (`GH_AVI_CR001`, авто из имени файла без `_N`), sub6 = имя адсета = угол теста (разрез по FTD). `{{...}}` — FB-макросы, оставлять как есть.

## Подъём стека (нужен для обоих методов — Vision-сессия)
- `./run.sh --no-tunnel` (фон). Docker (PG :5433, Redis :6380) + browser-agent gRPC :50051 + воркеры + Vision.
- ⚠️ При пустом `telegram_config` run.sh падает на telegram_poller BACKOFF → `python scripts/restore_secrets.py` (из `data/secrets_backup_*.json`), потом рестарт. Секреты в БД (Fernet), переживают рестарт докера (volume pgdata).
- Готовность: `nc -z localhost 50051`; `redis-cli -p 6380 GET observer:runtime` (был успешный скан → Vision на Ads Manager нужного кабинета).

## Почему «раньше залили автоматом, сейчас тыкали руками»
Не «надо руками» — это **дрейф UI-селекторов** Vision-автопилота (FB меняет вёрстку). Лечится правкой шага (сейчас `set_budget`) ИЛИ переходом на Метод 1 (API, без селекторов). Держим оба метода рабочими — это страховка.

## После залива
- Observer мониторит кампанию (стоп-правила + авто-стоп через Meta API) — см. `CLAUDE.md`.
- `status: live` в реестре оффера. Через ~неделю — `creative_report` по FTD (разрез `sub3`/`sub6`).
