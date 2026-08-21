# Что даёт Meta и что берём мы (17.08.2026)

Источник — официальный `facebook-python-business-sdk` (ветка main, файлы
`adobjects/campaign.py`, `adobjects/adset.py`, `adobjects/targeting.py`,
`adobjects/adcreativelinkdata.py`). Это тот же перечень, что принимает Marketing API.
Публичные reference-страницы `developers.facebook.com` рендерятся JS и внешнему читателю
не отдаются — SDK читается как обычный текст и врать не может.

Наша сторона — `core/campaign_builder/builder.py`, `core/campaign_builder/config.py`,
`core/campaign_drafts/contracts.py`.

## Сводка охвата

| Уровень | Полей у Meta | Используем | Доля |
|---|---|---|---|
| Кампания: `objective` | 21 значение (6 актуальных `OUTCOME_*`) | 1 — `OUTCOME_SALES` | 1 из 6 |
| Кампания: `bid_strategy` | 4 значения | 1 — `COST_CAP` | 1 из 4 |
| Кампания: `special_ad_categories` | 7 значений | 1 — `NONE` (зашито) | 1 из 7 |
| Группа: полей объекта | 102 | ~12 | 12% |
| Группа: `optimization_goal` | 33 значения | 1 — `OFFSITE_CONVERSIONS` | 1 из 33 |
| Группа: `billing_event` | 11 значений | 1 — `IMPRESSIONS` (зашито) | 1 из 11 |
| Таргетинг: ключей spec | 106 | 6 | 6% |
| Креатив: полей `link_data` | 44 | 6 | 14% |

## Значения, которые стоит знать поимённо

**`objective`** — актуальные ODAX: `OUTCOME_AWARENESS`, `OUTCOME_TRAFFIC`,
`OUTCOME_ENGAGEMENT`, `OUTCOME_LEADS`, `OUTCOME_APP_PROMOTION`, `OUTCOME_SALES`.
Ещё 15 значений — легаси (`CONVERSIONS`, `LINK_CLICKS`, `VIDEO_VIEWS`, `REACH`,
`BRAND_AWARENESS`, `POST_ENGAGEMENT`, `LEAD_GENERATION`, `APP_INSTALLS`, `MESSAGES`,
`PAGE_LIKES`, `EVENT_RESPONSES`, `OFFER_CLAIMS`, `PRODUCT_CATALOG_SALES`, `STORE_VISITS`,
`LOCAL_AWARENESS`), новые кампании на них не заводятся.

**`bid_strategy`** — `COST_CAP`, `LOWEST_COST_WITHOUT_CAP`, `LOWEST_COST_WITH_BID_CAP`,
`LOWEST_COST_WITH_MIN_ROAS`.

**`special_ad_categories`** — `NONE`, `CREDIT`, `EMPLOYMENT`, `HOUSING`,
`ISSUES_ELECTIONS_POLITICS`, `FINANCIAL_PRODUCTS_SERVICES`, **`ONLINE_GAMBLING_AND_GAMING`**.
Последняя — ровно та вертикаль, в которой работает проект; мы объявляем `NONE`.

**`optimization_goal`** — 33 значения. Из тех, что могли бы пригодиться помимо нашего
`OFFSITE_CONVERSIONS`: `VALUE`, `LANDING_PAGE_VIEWS`, `LINK_CLICKS`, `THRUPLAY`,
`QUALITY_LEAD`, `APP_INSTALLS_AND_OFFSITE_CONVERSIONS`, `IN_APP_VALUE`, `REACH`,
`IMPRESSIONS`, `CONVERSATIONS`, `SUBSCRIBERS`.

## ГЛАВНОЕ: что кабинеты используют на самом деле

Счёт выше — это множество возможного, и оно почти целиком шум. Прогон по живым кабинетам
17.08 (read-only Graph API через CDP боевой Vision-сессии; **55 кампаний и 360 групп** в
`1855748448431929`, `1386439193678186`, `996458953428822`) даёт другую картину.

| Поле | Что реально стоит | Что шлём мы |
|---|---|---|
| `objective` | **55 из 55** `OUTCOME_SALES` | `OUTCOME_SALES` — совпадает |
| `buying_type` | **55 из 55** `AUCTION` | по умолчанию `AUCTION` — совпадает |
| `special_ad_categories` | **55 из 55** пусто | `NONE` — совпадает |
| `optimization_goal` | **360 из 360** `OFFSITE_CONVERSIONS` | совпадает |
| `billing_event` | **360 из 360** `IMPRESSIONS` | совпадает |
| `bid_strategy` | **41** `LOWEST_COST_WITHOUT_CAP`, **12** `COST_CAP` | только `COST_CAP` |
| `destination_type` | 333 `UNDEFINED`, 27 `WEBSITE` | всегда `WEBSITE` |

Шесть из семи зашитых значений **совпадают с практикой** — то есть замки, которые я насчитал
по SDK, в основном мнимые. Настоящий разрыв ровно один: **три четверти живых кампаний идут на
`LOWEST_COST_WITHOUT_CAP`, который наш билдер выпустить не может.**

### Таргетинг: используется 10 ключей из 106, а не 106

Сколько групп из 360 используют ключ:

| Ключ | Групп | Шлём? |
|---|---|---|
| `age_min`, `age_max`, `geo_locations` | 360 | да |
| `targeting_optimization` (`expansion_all`) | **360** | **нет** |
| `targeting_automation` | 360 | частично — только `advantage_audience` |
| `age_range` | **351** | **нет** |
| `brand_safety_content_filter_levels` | **345** | **нет** |
| `genders` | 60 | да |
| `user_age_unknown` | 35 | нет |
| `targeting_relaxation_types` | 9 | нет |

Ни одна из 360 групп не использует `custom_audiences`, `interests`, `flexible_spec`, `cities`,
`regions`, `locales`, `device_platforms` и остальные 96 ключей. Планировать их поддержку не
нужно — это была бы работа в пустоту.

**И обратное, более важное: `publisher_platforms` не использует НИ ОДНА группа из 360, а мы
его шлём.** Наши группы получают фиксированный список площадок там, где рабочие группы
кабинета отдают выбор площадок автоматике. Это не недостающая фича, а лишняя — и она делает
наши группы не такими, как те, что реально крутятся.

Пример живой группы (кабинет `1855748448431929`):

```json
{
 "age_min": 25, "age_max": 65, "age_range": [25, 55], "genders": [1],
 "geo_locations": { "countries": ["AQ", "NG"], "location_types": ["home", "recent"] },
 "targeting_optimization": "expansion_all",
 "brand_safety_content_filter_levels": ["FACEBOOK_RELAXED", "AN_RELAXED"],
 "targeting_automation": { "advantage_audience": 1, "individual_setting": { "age": 1, "gender": 1 } }
}
```

Кабинет `1282495953856981` из этого прогона выпал: `Object with ID does not exist, cannot be
loaded due to missing permissions` — либо ID неверен, либо у профиля нет доступа.

## Полное множество возможного (справочно)

Ниже — то, что API допускает в принципе. Практическую ценность имеет только раздел выше;
этот нужен, когда появится задача выйти за текущий шаблон.

## Таргетинг: 6 ключей из 106

Отправляем: `geo_locations` (только `countries` + `location_types`), `age_min`, `age_max`,
`genders`, `publisher_platforms`, `targeting_automation.advantage_audience`.

Не отправляем ничего из этого (выборка по значимости, полный список — 106 ключей в
`targeting.py`):

- **Аудитории:** `custom_audiences`, `excluded_custom_audiences`, `connections`,
  `excluded_connections`, `friends_of_connections`, `dynamic_audience_ids`,
  `product_audience_specs`, `prospecting_audience`.
- **Интересы и поведение:** `interests`, `behaviors`, `flexible_spec`, `exclusions`,
  `life_events`, `industries`, `income`, `net_worth`, `family_statuses`,
  `relationship_statuses`, `education_statuses`, `work_positions`, `work_employers`.
- **Гео мельче страны:** `cities`, `regions`, `zips`, `radius`, `country_groups`,
  `excluded_geo_locations`.
- **Плейсменты поимённо:** `facebook_positions`, `instagram_positions`,
  `messenger_positions`, `audience_network_positions`, `threads_positions`,
  `whatsapp_positions`, `device_platforms`, `user_os`, `user_device`, `connected_tv`,
  `wireless_carrier`.
- **Прочее:** `locales` (языки), `brand_safety_content_filter_levels`,
  `excluded_publisher_list_ids`, `targeting_relaxation_types`, `exclude_reached_since`.

## Креатив: 6 полей из 44

Отправляем: `link`, `call_to_action`, `image_hash` (или `picture`), `message`, `name`,
`description`.

Не отправляем в том числе: **`caption`** (отображаемая ссылка — задача A1 плана),
`child_attachments` и `carousel_*` (карусель), `format_option`, `image_crops`,
`image_overlay_spec`, `customization_rules_spec` (варианты под плейсмент),
`static_fallback_spec`, `app_link_spec`, `page_welcome_message`, `offer_id`,
`retailer_item_ids`, `use_flexible_image_aspect_ratio`.

## Что видно только глазами, а не в SDK

SDK перечисляет возможное, но не говорит, что Meta считает нормой сегодня. Прогон по мастеру
создания в кабинете `1234567890123456` (17.08) показал:

- Для цели «Продажи» Meta по умолчанию включает **кампанию Advantage+** и **объявления из
  каталога Advantage+** с уже подставленным каталогом. Наш билдер собирает классическую
  кампанию, то есть по умолчанию другой тип, чем предлагает Meta сегодня.
- На уровне группы в этом флоу появляются «Стратегия жизненного цикла клиента», «Правила
  определения ценности», «Цель по результативности» (это `optimization_goal` под другим
  именем) и «Модель атрибуции».
- На уровне кампании из дополнительных настроек доступен только «Лимит затрат для кампании»
  (`spend_cap`), спец-категорий в этом флоу не спрашивают.
- У названия кампании и группы есть **«Создать шаблон»** — родной механизм заготовок Meta,
  прямой аналог наших пресетов.
- Открытие мастера сразу создаёт **черновик** кампании в кабинете, даже без единой правки.

Отсюда рабочее разделение: полноту допустимого берём из SDK, а «что Meta считает дефолтом»
— разовым прогоном по UI, потому что в SDK этого нет.
