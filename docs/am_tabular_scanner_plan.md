# Имплан: сканер на `am_tabular` вместо DOM-парсинга

Статус: **active replication подтверждён live 2026-05-30** (реверс боевого запроса). Money-критичный путь: код observer/rules/FSM не трогаем — контракт `ScannedAdRow` неизменен.

## 1. Что нашли (подтверждено live)

UI Ads Manager строит таблицу из REST на `adsmanager-graph.facebook.com`, НЕ из `/api/graphql`:

| Endpoint | Отдаёт |
|---|---|
| `GET .../v22.0/act_<id>/am_tabular` | метрики per-ad + конверсии (`level=ad`) |
| `.../light_campaigns`, `.../light_adsets`, `.../lightads` | имена + `effective_status` (+ budget) |

`am_tabular` = источник UI → real-time (без лага Marketing API), числа-строки без локали, позиционно-структурный.

### Боевой per-ad запрос (реверс, `am[3]`, метод GET)
```
GET https://adsmanager-graph.facebook.com/v22.0/act_<ID>/am_tabular
  access_token = <USER_TOKEN>          # из Vision-сессии (не httpx)
  level        = ad
  column_fields = ["anchor_events","anchor_event_attribution_setting",
       "multi_event_conversion_attribution_setting","results","objective","reach",
       "impressions","cost_per_result","spend","clicks","cpc","actions",
       "cost_per_action_type","ctr","outbound_clicks","outbound_clicks_ctr","cpm",
       "frequency","attribution_setting","conversion_annotations",
       "conversion_count_setting","ad_id"]
  filtering = [
     {"field":"ad.delivery_info","operator":"IN","value":["active","archived","completed",
        "inactive","limited","not_delivering","not_published","pending_review",
        "permanently_deleted","recently_completed","recently_rejected","rejected","scheduled"]},
     {"field":"ad.id","operator":"IN","value":["120244801468120044", ...]},   # UI пагинирует батчами ad.id
     {"field":"action_type","operator":"IN","value":["lead","omni_complete_registration",
        "omni_landing_page_view","landing_page_view", ...]}
  ]
  date_preset = today
  limit       = 5000
  action_attribution_windows = ["default","inline"]
  use_unified_attribution_setting = true
  locale = ru_RU                        # числа всё равно приходят чистыми numeric strings
```
- **column_fields — явный параметр запроса.** Отсюда ответ на главный вопрос: шлём свой список полей → получаем свои колонки **независимо от пресета колонок в UI**.
- **filtering — где UI задаёт scope.** Он батчит по `ad.id IN [...]` (виртуализация). Мы заменяем на `ad.id IN [наши ад'ы из light_*]` либо пробуем `campaign.id IN [наш список]` (#3).
- Большие батчи UI шлёт **POST** на тот же endpoint (params в теле) — нам не нужно, наш объём влезает в один GET (limit 5000).

### Формат ответа per-ad
```jsonc
data:[ {
  headers:{
    dimensions:["objective","ad_id","date_start","date_stop"],        // [1] = ad_id
    atomic_columns:[                                                   // 12, метрики на поз. 2..9
      "anchor_event_attribution_setting","multi_event_conversion_attribution_setting",
      "reach","impressions","spend","clicks","cpc","ctr","cpm","frequency",
      "attribution_setting","conversion_count_setting"],
    action_columns:[                                                   // по 2 на имя: default + inline
      "actions","actions","cost_per_action_type","cost_per_action_type",
      "outbound_clicks","outbound_clicks","outbound_clicks_ctr","outbound_clicks_ctr",
      "conversion_annotations","conversion_annotations"]
  },
  rows:[ {
    dimension_values:["OUTCOME_SALES","120244531696570044","2026-05-30","2026-05-30"], // "na"=summary→skip
    atomic_values:["na","na","8","8","0.01","2","0.005","25","1.25","1","1d_view_1d_click_1d_ev","ALL_CONVERSIONS"],
    action_values:[ {"types":["landing_page_view","lead","omni_landing_page_view","omni_complete_registration"],
                     "values":["1","1","1","1"],"breakdown":"action_type"}, ... ]   // ↔ action_columns по индексу
  } ]
} ]
```
- `atomic_values` зипуется с `atomic_columns` по индексу; `na/null/""` → None, иначе Decimal/int.
- `action_values[i]` ↔ `action_columns[i]`; берём окно **default** (первое из пары default/inline). Внутри `{types[],values[]}` — параллельные массивы → dict `action_type → value`.
- `results` / `cost_per_result` приходят как отдельные action-колонки (objective-зависимый «результат»).

## 2. Архитектура — active replication

Сами зовём `am_tabular` + `light_*` изнутри Vision-сессии через `page.evaluate(fetch)` (токен и куки сессии уже там; httpx не используем — правило Meta-доступа). Не скроллим, не парсим DOM, не зависим от виртуализации / пресета колонок / локали.

**Поток скана (зеркало UI):**
1. **resolve scope** — список campaign.id для наблюдения (конфиг #3; либо резолв по owner-tag/маске имени).
2. **GET light_campaigns + light_adsets + lightads** с **нашим** `fields=id,name,effective_status[,budget]` (filter campaign.id IN [scope]) → `Map id → {name, effective_status, budget}` **и список ad.id**.
3. **GET am_tabular** `level=ad`, `column_fields=[наш полный набор]`, `filtering=[ad.id IN [из шага 2], ad.delivery_info IN [...]]`, `date_preset` → метрики per-ad.
4. **join** по ad_id → эмитим те же `ScannedAdRow`, что DOM-парсер.

> ⚠️ **Источник имён/статуса.** В live-перехвате UI звал `light_*` с `fields=id` → вернулись только id-списки (scope для батчинга am_tabular). Имена/иерархию UI берёт из `/api/graphql` (узлы `Adgroup`: `node_id`+`name`+`ad_campaign_id`=adset+`ad_campaign_group_id`=campaign+`ad_campaign_name`). Для active replication **мы контролируем `fields`** → запрашиваем `light_*?fields=id,name,effective_status` (нужно подтвердить в Ф2, что light_* их отдаёт). Фолбэк, если не отдаёт: парсить `Adgroup`-узлы из `/api/graphql`. `am_tabular` имён не содержит (только ad_id).

**Компоненты (TS, `services/browser-agent/src/am/`):**
- `am-fetch.ts` — конструктор params + вызов через `page.evaluate(fetch)`; извлечение access_token из сессии; POST-фолбэк для больших батчей.
- `am-parser.ts` (+`.test.ts`) — чистые `parseAmTabular(body) → Map<ad_id, AmRow>`, `parseLight*(body) → Map<id, meta>`. Без сети.
- `am-join.ts` — джойн + маппинг в `ScannedAdRow` (таблица §3).
- `am-config.ts` — наблюдаемые campaign.id + наш `column_fields`.

## 3. Маппинг — зеркало текущего DOM-парсера (`ads-columns.ts`), поведение не меняется

| `ScannedAdRow` | Источник `am_tabular` | Текущий DOM (surfaceKey / needle) |
|---|---|---|
| spend, impressions, reach, clicks, cpc, ctr, cpm, frequency | atomic_values по имени колонки | те же atomic |
| deposits | **`results`** (count) | surfaceKey `results`, title «Результат» → `deposits` |
| cost_per_result | `cost_per_result` | `cost_per_result` |
| leads | actions[`lead`] | actions + needle `лид/lead` |
| cost_per_lead | cost_per_action_type[`lead`] | cost_per_action_type + needle `лид` |
| registrations | actions[`omni_complete_registration`] | actions + needle `регистрац/registration` |
| cost_per_registration | cost_per_action_type[`omni_complete_registration`] | cost_per_action_type + needle `регистрац` |
| landing_page_views | actions[`landing_page_view`] | actions + needle `целев/landing` |
| cost_per_landing_page_view | cost_per_action_type[`landing_page_view`] | cost_per_action_type + needle `целев` |
| outbound_clicks | outbound_clicks (type `outbound_click`) | surfaceKey `outbound_clicks` |
| outbound_ctr | outbound_clicks_ctr | surfaceKey `outbound_clicks_ctr` |
| ad_name, campaign_name, adset_name | light_* | DOM name-колонки |
| delivery_status | light_*.`effective_status` (норм. в коды `detectDeliveryStatus`) | DOM «Статус показа» |
| budget | light_adsets/light_campaigns | DOM «Бюджет» |

- **deposits:** `ScannedAdRow.deposits` ← Meta `results` (как сейчас DOM). **Депозиты для ПРАВИЛ — отдельный источник AdSet.pro** (`external_deposits` добирается в `core/observer/pipeline.py::build_rule_context`), он НЕ меняется (#2).
- **Конверсии валидируем эмпирически в Ф3 (shadow parity), не гаданием:** am_tabular vs DOM по одним ад'ам в одном скане. Расходящийся action_type (напр. `omni_*` vs `offsite_conversion.fb_pixel_*`) правим точечно по факту.
- Окно атрибуции: берём **default** (первый из пары default/inline в action_columns).

## 4. Фазы

- **Ф0 ✅** — реверс боевого запроса + перехват. Сырьё в `.am_capture/` (gitignored, содержит token — не коммитим).
- **Ф1 ✅** — `am/am-parser.ts` + `am/am-join.ts` + 10 unit-тестов. Урок: машинные числа am_tabular НЕ гнать через locale-эвристику parser.ts (ломает `0.005→0005`) — свои am-парсеры.
- **Ф2 ✅** — `am/am-fetch.ts` (сниф access_token + `page.evaluate(fetch)` + курсорная пагинация) + `am/am-config.ts`. proto `scan_source`+`campaign_ids`, Python-клиент прокидывает, врезано в `runScanCycle`. Имена/статус — **Graph REST** (`act/ads,campaigns,adsets?fields=name,effective_status`; решение: Marketing API только для статичных имён). Живьём: 388 ад'ов ~20с, имена/статусы ОК.
- **Ф3 ✅** — shadow parity (несколько прогонов): **0/84 расхождений** (impr/clicks/leads/reg/deposits). Бонус: вскрыт DOM-баг — мусорные deposits (155/23/25, рассинхрон колонок), а `row.deposits` rule-bearing → am_tabular чинит money-баг.
- **Ф4 ✅** — `observer_config.scan_source` (дефолт `am_tabular`) + `campaign_ids` (миграция `0011`). `load_observer_config` + observer-гейт прокидывают в `run_scan_cycle`. Settings API: GET/PUT + `PATCH /settings/observer/scan-source` и `/campaigns` (+5 тестов). DOM — фолбэк по флагу. ⚠️ **Scoping в общем кабинете:** без `campaign_ids`/`owner_campaign_tag` am-режим берёт весь кабинет (UI-фильтр игнорируется) — перед стартом observer задать allowlist.
- **Ф5 ✅** — снят debug-перехват из `index.ts`. Полный набор: 153 TS + целевые Python зелёные, ruff clean.

`ScannedAdRow` неизменен → Python (observer/pipeline/rules/FSM) не трогаем (кроме прокидывания флага источника).

## 5. Открытые вопросы / риски

1. **access_token из сессии** — прочитать токен, которым UI зовёт am_tabular (`page.evaluate`: из объекта require/`__accessToken`, либо перехватить один раз и переиспользовать в рамках сессии). Без httpx.
2. **`campaign.id` фильтр на level=ad** — подтвердить, что am_tabular принимает `campaign.id IN` (стандартно для Graph filtering). Если нет — берём ad.id из `lightads` (проверенный путь, шаг 2) и фильтруем по ним.
3. **Конверсии** — §3, валидация Ф3 (главный money-риск).
4. **`light_*` полнота** — подтвердить в Ф2, что `light_*?fields=name,effective_status` отдаёт имена/статус (live-перехват был только `fields=id`). Если нет — фолбэк на `Adgroup`-узлы из `/api/graphql`. На каком уровне budget.
5. **Пагинация** — при >5000 строк (нереально для наших кампаний) добавить after-курсор / POST-батчи.
6. **Owner-scoping** — campaign.id-allowlist строже owner-tag: чужое физически не запрашиваем. Python-фильтр `campaign_matches_owner` остаётся вторым рубежом.

## 6. Что НЕ делаем
- Marketing API (`/insights`) — не трогаем (правило).
- Источник rule-deposits (AdSet.pro) — не меняем (#2).
- Toggle/мутации в кабинете — только чтение (кабинет общий).
