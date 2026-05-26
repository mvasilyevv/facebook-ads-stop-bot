# Meta Marketing API + Meta MCP — карта возможностей для арбитража трафика

Дата: 2026-05-25. Текущая стабильная версия Marketing API — **v28.0** (используется по умолчанию серверами вроде [serkanhaslak/meta-mcp](https://github.com/serkanhaslak/meta-mcp); минимально поддерживаемая — v22.0, всё ниже отключено [9 сентября 2025](https://releasebot.io/updates/meta/facebook-marketing-api)).

Структура: 1–5 — возможности по жизненному циклу, 6 — сравнение источников (Marketing API vs 5 MCP-серверов), 7 — подводные камни.

---

## 1. Создание (Creation)

### 1.1. Campaign — `POST /act_{AD_ACCOUNT_ID}/campaigns`

Документация: [Ad Campaign reference](https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group).

**Обязательные поля:** `name`, `objective`, `status`, `special_ad_categories`.

**Objectives (ODAX, 2026):** только outcome-based:
- `OUTCOME_AWARENESS`
- `OUTCOME_TRAFFIC`
- `OUTCOME_ENGAGEMENT`
- `OUTCOME_LEADS`
- `OUTCOME_SALES`
- `OUTCOME_APP_PROMOTION`

Старые `BRAND_AWARENESS`, `LINK_CLICKS`, `CONVERSIONS`, `APP_INSTALLS` и т.д. **отключены для создания** новых ad sets/ads начиная с v21.0 (см. [release notes](https://releasebot.io/updates/meta/facebook-marketing-api) и [мэппинг в pipeboard README](https://github.com/pipeboard-co/meta-ads-mcp/blob/main/README.md)).

**`special_ad_categories`** — массив; обязателен для рекламы в категориях `CREDIT`, `EMPLOYMENT`, `HOUSING`, `ISSUES_ELECTIONS_POLITICS`, `ONLINE_GAMBLING_AND_GAMING`, `FINANCIAL_PRODUCTS_SERVICES`. Для не-регулируемых офферов передавайте пустой массив `[]`. Если категория попадает в Special, требуется также `special_ad_category_country` — массив ISO-кодов.

**Bid strategy** (на уровне campaign или adset): `LOWEST_COST_WITHOUT_CAP` (Highest Volume), `LOWEST_COST_WITH_BID_CAP`, `COST_CAP`, `LOWEST_COST_WITH_MIN_ROAS` (для последнего нужен `bid_constraints={"roas_average_floor": 20000}` — значение в 10000 = 1.0 ROAS). См. [Bid Strategies](https://developers.facebook.com/docs/marketing-api/bidding/overview/bid-strategy/).

**Advantage+ Shopping/App campaigns:** `smart_promotion_type` устарел с v25.0 (Q1 2026); кампании автоматически становятся Advantage+ при определённой комбинации настроек бюджета/аудитории/плейсментов. С 18 февраля 2025 (v25.0+) ASC/AAC создаются только через унифицированный API; через 90 дней (≈ май 2026) — отключение для всех версий ([детали](https://ppc.land/meta-launches-unified-api-structure-for-advantage-campaigns/)).

### 1.2. Ad Set — `POST /act_{AD_ACCOUNT_ID}/adsets`

Документация: [Ad Account Adsets](https://developers.facebook.com/docs/marketing-api/reference/ad-account/adsets/).

**Ключевые поля:**
- `campaign_id`, `name`, `status`
- `daily_budget` или `lifetime_budget` (в минимальных единицах валюты, для USD — центы)
- `billing_event` (`IMPRESSIONS`, `LINK_CLICKS`, `THRUPLAY`)
- `optimization_goal` (зависит от objective: `OFFSITE_CONVERSIONS`, `LINK_CLICKS`, `LEAD_GENERATION`, `LANDING_PAGE_VIEWS`, `REACH`, `IMPRESSIONS`, `THRUPLAY`, `APP_INSTALLS` и др.)
- `bid_strategy`, `bid_amount` (cents, обязателен для `LOWEST_COST_WITH_BID_CAP`/`COST_CAP`/`TARGET_COST`), `bid_constraints` (для min ROAS)
- `targeting` — большой объект (см. ниже)
- `start_time`, `end_time` (ISO 8601, опц.)
- `attribution_spec` или `attribution_setting`
- `promoted_object` (для конверсий: `pixel_id` + `custom_event_type` или `application_id`)

**Targeting spec** включает:
- `geo_locations` (`countries`, `regions`, `cities`, `zips`, `geo_markets`, `electoral_districts`, `location_types`)
- `age_min`, `age_max`, `genders`
- `interests` (массив `{id, name}`), `behaviors`, `life_events`, `industries`
- `custom_audiences`, `excluded_custom_audiences`
- `flexible_spec` (логические AND нескольких inclusion-блоков)
- `publisher_platforms` (`facebook`, `instagram`, `audience_network`, `messenger`), `facebook_positions`, `instagram_positions`, `device_platforms`
- `targeting_automation.advantage_audience` (Advantage Audience flag, в 2026 — по умолчанию on для Sales/Leads)

**Attribution windows** (важно: с 12 января 2026 урезано — см. [PPC.land](https://ppc.land/meta-restricts-attribution-windows-and-data-retention-in-ads-insights-api/)):
- Поддерживаются: `1d_click`, `7d_click`, `1d_view`
- **Удалены:** `7d_view`, `28d_view`
- `28d_click` — частично (только в `use_unified_attribution_setting`)

**Dayparting:** `adset_schedule` — массив `{start_minute, end_minute, days[]}` (требуется `lifetime_budget`).

### 1.3. Ad — `POST /act_{AD_ACCOUNT_ID}/ads`

**Поля:** `adset_id`, `name`, `creative={creative_id}` (или `{creative_spec}`), `status`, `tracking_specs` (опц., URL pixel/app events для атрибуции).

### 1.4. Ad Creative — `POST /act_{AD_ACCOUNT_ID}/adcreatives`

Документация: [Ad Creative](https://developers.facebook.com/docs/marketing-api/reference/ad-creative/).

**Виды:**
- `link_ad` — одиночный image/video + ссылка
- `video_ad`
- `carousel_ad` — `child_attachments[]` (до 10 карточек)
- `dynamic_product_ad` — `template_url`, `product_set_id`
- `collection_ad` + `instant_experience` (через `canvas_id`)

**Ключевые параметры:**
- `object_story_spec` — описание поста: `page_id`, `instagram_user_id` (с v22.0+ вместо `instagram_actor_id` — последний deprecated), затем один из: `link_data`, `video_data`, `photo_data`, `template_data`
- `asset_feed_spec` — для Dynamic Creative ([docs](https://developers.facebook.com/docs/marketing-api/ad-creative/asset-feed-spec/)). Лимиты: ≤10 images, ≤10 videos, ≤5 bodies, ≤5 titles, ≤5 CTAs, всего ≤30 ассетов
- `degrees_of_freedom_spec` — управление Advantage+ Creative ([reference](https://developers.facebook.com/docs/marketing-api/reference/ad-creative-degrees-of-freedom-spec/)). С 2026 «Standard Enhancements» bundle **удалён** — фичи (`visual_touch_ups`, `text_improvements`, `add_overlays`, `image_background_gen`) включаются индивидуально

### 1.5. Загрузка медиа

- **Images:** `POST /act_{id}/adimages` — multipart с `filename` или `bytes` (base64). Возврат: `{hash, url}`. Hash используется в `object_story_spec.link_data.image_hash` или в `asset_feed_spec.images[].hash`
- **Videos:** `POST /act_{id}/advideos` — поддерживает chunked upload через `upload_phase=start|transfer|finish` для файлов >50 MB. Возврат: `video_id`. Статус кодирования — через `GET /{video_id}?fields=status` (нужно ждать `ready` перед использованием в креативе)
- **Видео-thumbnails:** автогенерация, `GET /{video_id}/thumbnails`

### 1.6. Custom Audiences — `POST /act_{id}/customaudiences`

Документация: [Custom Audiences](https://developers.facebook.com/docs/marketing-api/audiences/reference/custom-audience).

**Подтипы (`subtype`):**
- `CUSTOM` / `CUSTOMER_LIST` — загрузка хэшированных PII (SHA-256 в lowercase: `EMAIL`, `PHONE`, `FN`, `LN`, `DOB`, `MADID`, `EXTERN_ID`, `FI`, `ST`, `CT`, `ZIP`, `COUNTRY`). Загрузка пачками — `POST /{audience_id}/users` с `payload={schema:[...], data:[[...], ...]}`
- `WEBSITE` — на основе `pixel_id` + `rule` (URL contains / events)
- `ENGAGEMENT` — Page/IG/Video/Lead form engagement
- `APP` — на основе app events
- `LOOKALIKE` — `origin_audience_id` + `lookalike_spec={type:"similarity", country:"US", ratio:0.01-0.10}`

**Минимумы:** ≥100 матченных пользователей чтобы аудитория стала usable, ≥1000 для нормальной работы. Source-аудитория для lookalike — минимум 100 (рекомендуется 1000–50000).

**Подводный камень:** raw email/phone отбрасываются API. Хэшировать обязательно (SHA-256, lowercase, trim) даже если документация говорит «оба варианта». См. [Meta hashing docs](https://www.facebook.com/business/help/112061095610075).

---

## 2. Управление и редактирование (Management)

### 2.1. Status changes

`POST /{campaign_id|adset_id|ad_id}` с `status=PAUSED|ACTIVE|ARCHIVED|DELETED`. Работает на всех трёх уровнях. **Учитывайте, что DELETED — необратимо**; для временной остановки используйте PAUSED.

### 2.2. Budget editing

- На уровне adset: `daily_budget` или `lifetime_budget`
- **CBO (Campaign Budget Optimization, теперь Advantage Campaign Budget):** бюджет на уровне campaign, распределяется автоматически между ad sets. Включается через `campaign.daily_budget` (без бюджета на ad set)
- **ABO:** бюджет на adset, кампания без budget

**Правило 20%:** редактирование бюджета >20% сбрасывает learning phase (см. [Meta Business Help](https://www.facebook.com/business/help/316478108955072)).

### 2.3. Targeting editing

Можно менять через `POST /{adset_id}` с `targeting={...}`. Любое изменение `targeting`, `optimization_goal`, `bid_strategy`, `placements`, или замена creative — **триггерит сброс learning phase** (нужно набирать заново ~50 conversions / 7 days). «Косметические» правки (имя, статус-маркеры) — не триггерят.

### 2.4. Bulk operations — Batch API

`POST /` с параметром `batch=[{"method":"POST","relative_url":"act_X/campaigns","body":"..."}, ...]`. **До 50 sub-requests** за один вызов. Документация: [Async and Batch Requests](https://developers.facebook.com/docs/marketing-api/asyncrequests/). Сильно экономит rate-limit бюджет.

### 2.5. Duplication — Ad Copies API

Есть отдельные endpoint'ы:
- `POST /{campaign_id}/copies` — копировать кампанию
- `POST /{adset_id}/copies` — копировать ad set ([docs](https://developers.facebook.com/docs/marketing-api/reference/ad-campaign/copies))
- `POST /{ad_id}/copies` — копировать ad (с мая 2025 поддерживает изменение creative-полей при копировании, [blog post](https://developers.facebook.com/blog/post/2025/05/28/you-can-now-change-creative-fields-when-duplicating-ads-with-ad-copies-api/))

Параметры: `deep_copy=true|false`, `status_option=PAUSED|ACTIVE|INHERITED_FROM_SOURCE`, `rename_options`. **Подводный камень:** при cross-account duplication pixel_id переназначается на pixel destination-аккаунта; если такого нет — silent fail в полях targeting.

---

## 3. Реакция и автоматизация (Reaction)

### 3.1. Meta Automated Rules — `POST /act_{id}/adrules_library`

Документация: [Ad Rules Engine](https://developers.facebook.com/docs/marketing-api/ad-rules), [reference](https://developers.facebook.com/docs/marketing-api/reference/ad-account/adrules_library/), [specs](https://developers.facebook.com/docs/marketing-api/ad-rules/ad-rules-specs).

**Структура:**
- `evaluation_spec` — фильтры (operator: `GREATER_THAN`, `LESS_THAN`, `IN_RANGE`, `EQUAL`, `CONTAIN`), поля (`spend`, `cpc`, `cpa`, `ctr`, `cpm`, `frequency`, `roas`, `impressions`, `lifetime_budget`...), time_preset
- `execution_spec` — `execution_type` ∈ {`PAUSE`, `UNPAUSE`, `CHANGE_BUDGET`, `ROTATE`, `NOTIFICATION`, `MESSAGE_ONLY_NOTIFICATION`, `CHANGE_BID`}
- `schedule_spec` — `schedule_type` ∈ {`SEMI_HOURLY` (30 мин), `HOURLY`, `DAILY`, `CUSTOM`}

**Минимальный интервал проверки — 30 минут** (`SEMI_HOURLY`). Это критическое отличие от собственного observer'а: ваш бот в FB_Agent сканирует таблицу с минутным интервалом (refresh кнопка). Native Automated Rules **не годятся для real-time STOP-логики** на быстро деградирующих офферах.

**Альтернатива observer'у?** Частично. Подходит для:
- Долгосрочных правил (suspend если CPM > X за 24h)
- Резервного safety net (auto-pause если spend >$N без conversions)

Не подходит для:
- Sub-30-min реакции
- Сложных условий (multi-rule с FSM)
- Кастомных метрик/лидов из CRM

### 3.2. Webhooks — `ad_account` subscription

Документация: [Webhooks for Ad Accounts](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-ad-accounts/).

**Подписываемые поля:**
- `ad_campaign_group` — изменения кампаний (status, budget, name)
- `ad_campaign` — изменения ad set
- `ad_creative` — статус approval creative
- `ad_account` — billing, account status
- `ads_insights` — обновления performance метрик (но не tick-by-tick, latency остаётся)

Регистрация: app dashboard → Webhooks → ad_account → callback URL + verify_token + subscribe specific fields. Безопасность через `X-Hub-Signature-256` header (HMAC-SHA256 с app secret).

**Что НЕ ловится через webhooks:**
- Спендинг по дням/часам (нужен polling `/insights`)
- Конкретные конверсии (нужен Conversions API в обратную сторону или CAPI events log)
- Изменения targeting (только сам факт изменения, не diff)

### 3.3. Insights latency

- **Spend, impressions, clicks** — задержка обычно 15-30 минут, но в пиковые часы (12-18 EST) может расти до 1-2 часов
- **Conversions (pixel/CAPI):** 1-3 часа для большинства event types
- **`actions` field** в /insights — самые «свежие» отстают ~30 мин
- **Unique counts (`unique_actions`, `cost_per_unique_action_type`):** доступны только за последние 13 месяцев с 12 января 2026
- **Frequency breakdowns:** retention 6 месяцев
- Webhooks для `ads_insights` присылают «обновление» — но это сигнал «данные ребилдятся», не «вот свежая цифра». Всё равно нужен GET-запрос

См. [Limits & Best Practices](https://developers.facebook.com/docs/marketing-api/insights/best-practices/) и [data freshness reference](https://developers.facebook.com/docs/marketing-api/reference/ads-dataset-data-freshness/).

### 3.4. Async reports

`POST /act_{id}/insights` без блокировки: метод запускает job и возвращает `report_run_id`. Поллить `GET /{report_run_id}` пока `async_status != "Job Completed"` и `async_percent_completion < 100`. После — `GET /{report_run_id}/insights` для результатов.

**Когда обязателен:** тяжёлые отчёты (большой date range × несколько breakdowns × много объектов). Marketing Mix Modeling breakdowns с 2026 — **только async** ([PPC.land](https://ppc.land/meta-restricts-attribution-windows-and-data-retention-in-ads-insights-api/)). Async job живёт до 1 часа; `report_run_id` истекает через 30 дней.

---

## 4. Аналитика (Analysis)

### 4.1. `/insights` — основные поля для арбитража

Endpoint: `GET /{ad_id|adset_id|campaign_id|act_id}/insights` ([docs](https://developers.facebook.com/docs/marketing-api/insights/)).

**Ключевые `fields` для арбитража:**
| Поле | Назначение |
|---|---|
| `spend` | Расход |
| `impressions`, `reach`, `frequency` | Охват |
| `clicks`, `inline_link_clicks` | Клики (last — только кликабельные ссылки, важно для CTR на лидген-формы) |
| `cpc`, `cpm`, `ctr`, `inline_link_click_ctr` | Производные |
| `actions[]` | Список конверсий по типам (`action_type`: `lead`, `purchase`, `complete_registration`, `app_install`, `link_click`...) |
| `action_values[]` | Стоимость конверсий (выручка), для ROAS |
| `cost_per_action_type[]` | CPA по action_type |
| `unique_actions[]`, `cost_per_unique_action_type[]` | Уникальные (13-мес лимит) |
| `purchase_roas[]` | Готовый ROAS (если pixel/CAPI настроены) |
| `conversions[]`, `conversion_values[]` | Custom conversions |
| `quality_ranking`, `engagement_rate_ranking`, `conversion_rate_ranking` | Auction ranking (`ABOVE_AVERAGE`/`AVERAGE`/`BELOW_AVERAGE_*`) — критично для оценки fatigue креативов |
| `video_p25/50/75/100_watched_actions`, `video_avg_time_watched_actions` | Video metrics |

### 4.2. Breakdowns

[Документация](https://developers.facebook.com/docs/marketing-api/insights/breakdowns/).

**Поддерживаемые:** `age`, `gender`, `country`, `region`, `dma`, `impression_device`, `publisher_platform`, `platform_position`, `device_platform`, `product_id`, `hourly_stats_aggregated_by_audience_time_zone`, `hourly_stats_aggregated_by_advertiser_time_zone`.

**Ограничения по комбинированию:**
- Одиночные breakdowns — почти всегда ОК
- Парные (день + placement) — в 3-5× больше шанс timeout
- Тройные — timeout 10-15× вероятнее
- Не все пары разрешены (матрица недокументирована); неподдерживаемые → generic error
- Hourly breakdowns — retention 6 мес, обязательно `time_range` ≤ 1 день за запрос
- `product_id` нельзя сочетать с большинством других

**Совет:** строить «по очереди» (по одному breakdown), сшивать на своей стороне.

### 4.3. Time ranges

- `date_preset`: `today`, `yesterday`, `this_week`, `last_7d`, `last_14d`, `last_28d`, `last_30d`, `last_90d`, `this_month`, `last_month`, `lifetime`, `maximum`
- `time_range={since:"YYYY-MM-DD", until:"YYYY-MM-DD"}` (until inclusive)
- `time_increment`: `1`/`7`/`monthly`/`all_days`/`hourly` (1 = daily). Лимит при `hourly` — 1 день
- `time_ranges=[{...},{...}]` — параллельные windows в одном запросе

### 4.4. Attribution windows

`action_attribution_windows=["1d_click","7d_click","1d_view"]`. С 12 января 2026: `7d_view` и `28d_view` **удалены**. Поле `value` всегда возвращает `7d_click` атрибуцию по умолчанию (т.е. отдельные windows нужно добавлять явно).

`use_unified_attribution_setting=true` — использует тот attribution setting, что был задан при создании ad set (предпочтительно для согласованности с Ads Manager UI).

iOS 14+ ограничения: SKAdNetwork events приходят с задержкой/агрегацией; используйте Aggregated Event Measurement (AEM) события (до 8 на домен). См. [Conversions API guide](https://developers.facebook.com/docs/marketing-api/conversions-api/).

### 4.5. Custom Conversions

Создаются: `POST /act_{id}/customconversions` с `name`, `pixel_id`, `event_source_url`, `rule={url:{contains:"thank-you"}}`, `custom_event_type` (`PURCHASE`, `LEAD`, `COMPLETE_REGISTRATION`...). Возврат: `custom_conversion_id` (например `fb_pixel_custom_conv:123`).

Чтение в /insights — через `actions` filter: `action_type=offsite_conversion.custom.{custom_conversion_id}`. Например: `actions:offsite_conversion.custom.123456789`. Можно фильтровать `action_type=lead` или `action_type=purchase` для агрегированных стандартных событий.

---

## 5. AI-генерация (Advantage+ / Generative AI)

### 5.1. Advantage+ Creative

[Get Started](https://developers.facebook.com/docs/marketing-api/creative/advantage-creative/get-started/), [reference](https://developers.facebook.com/docs/marketing-api/creative/advantage-creative/).

Управляется через `degrees_of_freedom_spec` в adcreative. Subfields (caмые важные):
- `creative_features_spec.standard_enhancements.enroll_status` — `OPT_IN`/`OPT_OUT` (исторически — bundle Standard Enhancements; **с 2026 удалён**, теперь индивидуальные флаги)
- `creative_features_spec.image_background_gen.enroll_status` — AI background generation
- `creative_features_spec.image_uncrop.enroll_status` — image expansion
- `creative_features_spec.text_optimizations.enroll_status` — авто-варианты headline/primary text
- `creative_features_spec.text_generation_by_prompt.enroll_status` — генерация текста по prompt (в 2026 в open beta)
- `creative_features_spec.music.enroll_status` — авто-музыка для видео
- `creative_features_spec.visual_touch_ups.enroll_status`
- `creative_features_spec.product_extensions.enroll_status` — добавление product card к креативу

**По умолчанию:** с февраля 2026 новые кампании в Sales/Leads/App Promotion стартуют со **всеми** enhancements `OPT_IN`. Нужно явно прописывать `OPT_OUT` если не хотите.

**С марта 2026:** обязательная disclosure для ads с AI-generated/modified content.

### 5.2. Advantage+ Shopping Campaigns / Advantage+ App Campaigns

[Документация ASC](https://developers.facebook.com/docs/marketing-api/advantage-shopping-campaigns/).

**Старый способ (deprecated):** `smart_promotion_type=AUTOMATED_SHOPPING_ADS` / `SMART_APP_CAMPAIGN`. С v25.0 — недоступен. С мая 2026 — будет отключён для всех версий.

**Новый способ:** кампания становится Advantage+, если ad set имеет одновременно: 
- `targeting_automation.advantage_audience=1` (Advantage Audience on)
- `advantage_plus_placements=OPT_IN` (или `placements_automation` settings)
- CBO бюджет на campaign-уровне

Меньше явных полей, больше «auto» — оптимизатор Meta сам выбирает audience expansion, plаcements, креативы из asset_feed.

### 5.3. Generated background / image expansion

Доступно через `degrees_of_freedom_spec` (см. 5.1). Через UI — Ads Manager Creative Hub также позволяет prompted background generation; через API доступен только enroll-флаг, **не сам prompt для background** — Meta генерирует на основе анализа изображения автоматически. Кастомные prompts пока только в UI.

### 5.4. Meta AI Sandbox

UI-инструмент в Ads Manager (Generative AI для копирайтинга, image-to-image). **API нет** на 2026. Сгенерированные ассеты можно скачать вручную и потом залить через `/adimages`.

### 5.5. Рекомендации и opportunity scores

- **Recommendations API:** `GET /{adset_id}/recommendations` или `GET /{campaign_id}/recommendations` возвращает массив объектов вида `{code, message, recommendation_data, importance}`. Коды — например `AUTOMATIC_PLACEMENTS`, `CAMPAIGN_BUDGET_OPTIMIZATION_ELIGIBLE`, `DETAILED_TARGETING_EXPANSION`. Полезно для подсветки «слабых» ad sets
- **Opportunity Score:** `ads_get_opportunity_score` доступен через официальный MCP (см. ниже); напрямую — поле `account_score` на ad account level (требует особых permissions)
- **Learning phase status:** в `/insights` отсутствует напрямую; читается из `/{adset_id}?fields=learning_stage_info` → `{status: "LEARNING"|"SUCCESS"|"FAIL", attribution_windows, conversions}`. Поле статусно показывает «осталось набрать N events»

---

## 6. Сравнение: Marketing API vs MCP-серверы

Источники тулсетов:
- **Официальный Meta MCP** — `mcp.facebook.com/ads`, 29 tools, [анонс Meta](https://www.facebook.com/business/news/meta-ads-ai-connectors), [tool list](https://www.facebook.com/business/help/1456422242197840)
- **pipeboard-co/meta-ads-mcp** — 29 tools, BSL 1.1, [README](https://github.com/pipeboard-co/meta-ads-mcp/blob/main/README.md)
- **serkanhaslak/meta-mcp** — 77 tools across 24 modules, ISC, [README](https://github.com/serkanhaslak/meta-mcp/blob/main/README.md)
- **brijr/meta-mcp** — 25 tools, MIT, [README](https://github.com/brijr/meta-mcp)
- **gomarble-ai/facebook-ads-mcp-server** — ~20 read-only tools + insights, MIT, [README](https://github.com/gomarble-ai/facebook-ads-mcp-server)

Легенда: ✅ полный · ⚠️ частичный (что именно) · ❌ нет · 🧪 beta

| Операция | Marketing API | Official Meta MCP | pipeboard | serkanhaslak | brijr | gomarble |
|---|---|---|---|---|---|---|
| List ad accounts | ✅ | ✅ `ads_get_ad_accounts` | ✅ `get_ad_accounts` | ✅ `get_ad_accounts` | ✅ `get_ad_accounts` | ✅ `list_ad_accounts` |
| List/get campaigns | ✅ | ✅ `ads_get_ad_entities` | ✅ | ✅ | ✅ | ✅ |
| **Create campaign** (ODAX) | ✅ | ✅ `ads_create_campaign` | ✅ `create_campaign` | ✅ `create_campaign` | ✅ `create_campaign` | ❌ |
| **Create ad set** (с таргетингом) | ✅ | ✅ `ads_create_ad_set` | ✅ `create_adset` | ✅ `create_adset` | ✅ `create_ad_set` | ❌ |
| **Create ad** | ✅ | ✅ `ads_create_ad` | ✅ `create_ad` | ✅ `create_ad` | ✅ `create_ad` | ❌ |
| Update entity (status/budget/bid) | ✅ | ✅ `ads_update_entity` | ✅ `update_*` | ✅ `update_*` | ✅ `update_campaign` (огранич.) | ❌ |
| Pause/activate | ✅ | ✅ `ads_activate_entity` | ✅ (через update) | ✅ (через update) | ✅ `pause_campaign`/`resume_campaign` | ❌ |
| **Insights basic** (spend/CPM/CTR) | ✅ | ✅ `ads_insights_performance_trend` | ✅ `get_insights` (с `action_attribution_windows`) | ✅ `get_insights` + `get_account_insights` | ✅ `get_insights` | ✅ `get_*_insights` |
| **Insights с breakdowns** | ✅ | ⚠️ предустановленные breakdowns | ✅ (через `breakdown` параметр) | ✅ | ✅ | ✅ |
| **Insights anomaly detection** | ❌ (своя логика) | ✅ `ads_insights_anomaly_signal` 🧪 | ❌ | ❌ | ❌ | ❌ |
| Industry benchmarks | ⚠️ закрытый endpoint | ✅ `ads_insights_industry_benchmark`, `ads_insights_auction_ranking_benchmarks` 🧪 | ❌ | ❌ | ❌ | ❌ |
| Async reports | ✅ | ❌ (sync only) | ❌ | ✅ `create_async_report` | ❌ | ❌ |
| **Upload image/video** | ✅ | ⚠️ только через public URL (не file) | ✅ `upload_ad_image` (только image, путь к файлу) | ✅ `upload_image` + `upload_video` (file paths) | ⚠️ `create_ad_creative` берёт URL изображения | ❌ |
| **Create ad creative** | ✅ | ✅ часть `ads_create_ad` | ✅ `create_ad_creative` (+ dynamic headlines/descriptions) | ✅ `create_creative` | ✅ `create_ad_creative` (поддерживает внеш. URLs) | ❌ |
| **Advantage+ Creative** (degrees_of_freedom) | ✅ | ⚠️ через `ads_update_entity`, но конкретные feature flags не названы в манифесте | ⚠️ через `dynamic_creative_spec` | ⚠️ часть `create_creative` payload | ⚠️ часть `create_ad_creative` | ❌ |
| Dynamic Creative (asset_feed_spec) | ✅ | ⚠️ ограничено | ✅ (headlines[]/descriptions[]) | ✅ | ✅ | ❌ |
| Carousel/Catalog ads | ✅ | ✅ полный catalog API (10 tools) | ⚠️ через generic creative spec | ⚠️ через generic creative spec | ⚠️ ограничено | ❌ |
| **Custom audience upload** (хэшированный list) | ✅ | ❌ нет в манифесте | ❌ | ✅ `create_custom_audience` + `add_audience_users` + `remove_audience_users` | ✅ `create_custom_audience` | ❌ |
| Lookalike audience | ✅ | ❌ | ❌ | ✅ `create_lookalike_audience` | ✅ `create_lookalike_audience` | ❌ |
| **Automated Rules create** (adrules_library) | ✅ | ❌ | ❌ | ✅ `create_ad_rule` + `update_ad_rule` | ❌ | ❌ |
| **Webhooks subscribe** | ✅ (через App Dashboard / Subscriptions API) | ❌ | ❌ | ❌ | ❌ | ❌ |
| Conversions API send event (CAPI) | ✅ | ⚠️ диагностика, не send | ❌ | ✅ `send_conversion_event` | ❌ | ❌ |
| Pixel/CAPI health diagnostics | ✅ (`/datasets`, `/{pixel}/stats`) | ✅ полный (4 tools: `ads_get_dataset_*`, `ads_get_errors`) | ❌ | ⚠️ только `list_pixels`/`get_pixel` | ❌ | ❌ |
| Custom conversions CRUD | ✅ | ❌ (только чтение через диагностику) | ❌ | ✅ `create_custom_conversion` + list/get/delete | ❌ | ❌ |
| Batch API | ✅ | ❌ | ❌ | ✅ `batch_request` (up to 50) | ❌ | ❌ |
| Copy campaign/adset/ad | ✅ | ❌ | ❌ | ✅ `copy_campaign`/`copy_adset`/`copy_ad` | ❌ | ❌ |
| Ad previews | ✅ (`/{ad_id}/previews`) | ❌ | ❌ | ✅ `get_ad_previews` + `generate_ad_preview` | ❌ | ❌ |
| Lead gen forms / leads retrieval | ✅ | ❌ | ❌ | ✅ `get_lead_forms` + `get_leads` | ❌ | ❌ |
| Targeting search (interests/behaviors/geo) | ✅ | ❌ | ✅ `search_interests`, `search_behaviors`, `search_geo_locations`, `search_demographics` + `validate_interests`, `get_interest_suggestions` | ✅ полный набор (4+ tools) | ❌ | ❌ |
| Reach/delivery estimate | ✅ | ❌ | ❌ | ✅ `get_reach_estimate`, `get_delivery_estimate` | ❌ | ❌ |
| Account activity log | ✅ | ❌ | ❌ | ✅ `get_account_activities` | ❌ | ✅ `get_activities_by_adaccount`/`adset` |
| Opportunity score | ⚠️ ограничен permissions | ✅ `ads_get_opportunity_score` | ❌ | ❌ | ❌ | ❌ |
| Help center contextual | ❌ | ✅ `ads_get_help_article` | ❌ | ❌ | ❌ | ❌ |
| Budget schedules (high-demand) | ✅ | ❌ | ✅ `create_budget_schedule` | ✅ `create_budget_schedule` + `get_budget_schedules` | ❌ | ❌ |

### Вывод по сравнению для вашего use-case (арбитраж, бот FB_Agent):

- **Marketing API напрямую** — единственный полный путь. Все advanced features есть.
- **Official Meta MCP** — силён в Catalog (e-commerce) и диагностике pixel/CAPI, но **отсутствуют** custom audiences (создание), automated rules, webhooks, batch, copies, leadgen. Для арбитражного бота — недостаточно. Плюс — `ads_insights_anomaly_signal` интересен как альтернатива part of observer.
- **serkanhaslak/meta-mcp** — **самый полный сторонний MCP** (77 tools), единственный с automated rules, custom audiences с upload, copies, batch, async reports, conversions API send, custom conversions CRUD, ad previews, leadgen. Для бота с арбитражной логикой — **наиболее подходящий «универсальный» вариант**, если будете строить агента.
- **pipeboard** — фокус на «remote MCP» + хорошая targeting search, dynamic creative; но нет automated rules, custom audiences upload, webhooks.
- **brijr/meta-mcp** — компактный, удобный для базовых сценариев; для арбитражного бота слабоват.
- **gomarble** — почти read-only (нет create campaign/adset/ad), полезен только как «дашборд» для аналитики.

---

## 7. Известные подводные камни

### Что регулярно ломается / меняется

1. **Версионирование API.** Меняется раз в 3 мес, deprecation через ~24 мес. v22.0 — минимум с 9 сентября 2025. v23.0/v24.0 нужно обновить **до 10 февраля 2026**. На 2026 актуальны v25.0–v28.0; `META_API_VERSION` дефолт в serkanhaslak — `v28.0`. См. [versions list](https://developers.facebook.com/docs/marketing-api/marketing-api-changelog/versions/).

2. **Attribution windows debacle (январь 2026).** Удалены `7d_view`, `28d_view`. Старые отчёты с этими windows ломаются молча — API возвращает данные с поддерживаемыми windows без warning. **Действие:** в коде явно проверьте `action_attribution_windows` параметр, переведите бизнес-логику на `1d_click`+`7d_click`+`1d_view`.

3. **Историческое retention.** Unique counts (`unique_actions`, `cost_per_unique_action_type`) — 13 мес максимум. Hourly breakdowns — 13 мес. Frequency breakdowns — 6 мес. До этих лимитов раньше можно было поднять historic за 24+ мес. См. [PPC.land summary](https://ppc.land/meta-restricts-attribution-windows-and-data-retention-in-ads-insights-api/).

4. **ODAX миграция.** Старые objectives (`CONVERSIONS`, `LINK_CLICKS`, `BRAND_AWARENESS`, `LEAD_GENERATION`, `APP_INSTALLS`, `VIDEO_VIEWS`, `REACH`, `MESSAGES`, `EVENT_RESPONSES`) **не работают** для создания. Существующие campaigns с ними доживают, но `dynamic_creative` с ними тоже постепенно умирает.

5. **Advantage+ Shopping/App auto-migration.** С 18 февраля 2026 нельзя создавать ASC/AAC через `smart_promotion_type`. С ~19 мая 2026 — все Marketing API versions заблокированы для создания. Старые работают.

6. **`instagram_actor_id` deprecated** в пользу `instagram_user_id` (v22.0+). Старое поле формально работает, но `object_story_spec` с ним всё чаще вызывает silent errors при создании creative с IG placements.

7. **`promotions` deprecated** → `promotion_details`.

8. **Standard Enhancements bundle удалён.** Раньше один флаг включал «всё AI». Теперь — каждое включается отдельно в `creative_features_spec`.

### Расхождения API vs Ads Manager UI

1. **Conversions count differs.** Классика. Причины:
   - Разные attribution windows по умолчанию (UI часто `use_unified_attribution_setting`, API возвращает 7d_click если не указано иное)
   - Pixel + CAPI deduplication: UI делает её, для API сырой `actions` приходит с дубликатами если событие ID не передан с одинаковым `event_id`
   - Custom conversions vs стандартные events — иногда UI агрегирует, API — нет
   - Action breakdowns (`action_breakdowns=action_type,action_target_id`) — без них UI и API расходятся в `purchase` цифрах
2. **Spend rounding.** API возвращает `spend` в строке с 2 десятичными, UI округляет на лету; вечерний пересчёт может изменить «вчерашний» spend.
3. **ROAS.** UI использует `purchase_roas` действие, API — то же поле, но если CAPI шлёт `value`, а pixel — нет, расхождение получается.
4. **Frequency.** UI часто показывает за `last_7d`, API без указания time_range — `lifetime`. Не одно и то же.
5. **Reach.** UI deduplicates через cross-device, API возвращает adset-level reach без de-dup. Особенно расходится для CBO кампаний.
6. **Quality/Engagement/Conversion Ranking.** Появляются с задержкой 1-3 дня в API; UI обновляет чуть быстрее. У ad sets с малым объёмом impressions поля могут быть `null` в API дольше.

### Что считается deprecated в 2026, что отключают

- Любые **non-ODAX objectives** для новых создаваемых сущностей
- **ASC/AAC через smart_promotion_type** — финальное отключение в мае 2026
- **7d_view / 28d_view attribution windows** — отключены с января 2026
- **instagram_actor_id, promotions** — заменены, формально доживают
- **Page Insights metrics** — большая часть deprecated до 15 июня 2026 ([release notes](https://releasebot.io/updates/meta/facebook-marketing-api))
- **Real-time MMM breakdowns** — теперь только async
- **Standard Enhancements bundle** — удалён, заменён индивидуальными флагами

### Версионирование — best practice

- Закрепите `META_API_VERSION` явно в коде (например `v28.0`), не используйте «latest»
- Готовьтесь к ежегодному upgrade (90 days после релиза новой версии — старые начинают возвращать `400` на новые поля)
- Тестируйте создание campaign/adset/ad/creative на тестовом ad account при upgrade

### Rate limits и retry

- **Business Use Case (BUC) rate limiting** — Meta считает не запросы, а три метрики: call count, CPU time, total time. Header `x-business-use-case-usage` показывает % использования по каждой
- Throttle при >75% — обязателен, иначе block на 1+ час
- Insights API имеет отдельный header `x-fb-ads-insights-throttle`
- Транзиентные ошибки: 429 (rate limit), 500, 502, 503 — retry с exponential backoff
- Code 17 (User request limit reached) — пауза 60+ сек
- Code 32 (Page request limit reached) — пауза на час+

### Webhook gotchas

- Verify-token нужно валидировать на subscribe (`hub.challenge`)
- Сигнатура `X-Hub-Signature-256` — HMAC-SHA256 с **app secret**, не access token
- Webhooks **best-effort delivery**: возможны повторы и пропуски при сбоях. Нужна идемпотентность на вашей стороне
- Подписка на `ads_insights` НЕ присылает свежие метрики в payload — только сигнал «данные изменились», после которого делайте GET

### Custom Audience upload — практика

- Хэшировать **обязательно** на стороне клиента (SHA-256, lowercase, trim, никаких dashes в phone)
- `payload.schema` — массив с порядком полей; `payload.data` — массив массивов
- Status: `processing` после `POST` → `ready` через 30-120 минут
- Минимум 100 матченных пользователей для usable, 1000 для нормальной работы lookalike
- Lookalike: ratio 0.01–0.10 (1–10%). Маленький ratio = более похожие, меньше людей

### iOS 14+ / privacy

- AEM (Aggregated Event Measurement) — до 8 событий на домен (приоритизировать в Events Manager)
- App campaigns — через SKAdNetwork, события приходят агрегированно с задержкой
- 1-day view attribution для iOS opt-out — практически 0

---

## Резюме для бота FB_Agent

1. **Marketing API напрямую** покрывает 100% — это основной путь. Никакой MCP не заменит её для production-grade арбитражного бота.
2. **Официальный Meta MCP** — недостаточен для вашего use-case: нет automated rules, custom audiences upload, webhooks, batch, copies. Использовать как «копилот» в чате, не как production-зависимость.
3. **serkanhaslak/meta-mcp** — самый полный сторонний (77 tools), единственный с automated rules + audience upload + copies + async reports. Если строите AI-агента поверх бота — рассмотрите как orchestration layer.
4. **Нативные Meta Automated Rules** имеют минимальный интервал 30 минут — **не заменяют ваш observer** для STOP-логики. Подходят только как safety net.
5. **Webhooks** есть только для metadata изменений, не для real-time spend/conversions. **Polling /insights** остаётся обязательным (latency 15-30 мин — 3 часа в зависимости от метрики).
6. **ODAX** обязательна — все ваши новые campaigns должны использовать `OUTCOME_*` objectives.
7. **Attribution windows** упростите до `1d_click` + `7d_click` + `1d_view`; не закладывайтесь на view-through > 1 день.
8. **Advantage+ Creative** включён по умолчанию с 2026 для Sales/Leads/App — если хотите контроль, явно ставьте `OPT_OUT` в `degrees_of_freedom_spec.creative_features_spec.*`.
