# Аудит мульти-кабинетности (2026-06-09)

Независимый проход по коду M1–M5 (`MULTI_CABINET_PLAN.md`) и его стыкам со старой системой.
Фокус: money-пути, гонки, контракты writer↔reader, обратная совместимость.

## Исправлено в этом раунде

### HIGH-1 — глобальный allowlist кампаний ослеплял чужие кабинеты ✅
`observer_config.campaign_ids` — глобальный singleton, набранный из ОДНОГО кабинета.
`campaign.id` уникальны per кабинет → при скане кабинета B фильтр
`am_tabular campaign.id IN (ids кабинета A)` отсекал ВСЁ: пустой скан, FSM молчит,
горящие объявления кабинета B невидимы. **Money-критично.**
Фикс: в мульти-каб режиме allowlist игнорируется (`campaign_ids=[]` при заданном
`ad_account_id`) + warning в лог раз в цикл; скоупинг остаётся через `owner_tag`
(am-резолв работает per кабинет через GraphContext этого кабинета).
`apps/observer_worker/main.py::_run_account_scan`.

### HIGH-2 — mini-форма офферов получала 422 ✅
Backend требует `ad_account_ids` (min 1) на POST /offers, но `frontend-mini` его не
отправлял → создание офферов из TMA сломано. Фикс: поле «Рекламные кабинеты» +
валидация/нормализация (зеркало web-формы) в `frontend-mini/src/routes/offers/index.tsx`,
типы в `lib/api.ts`, синхронизирован test-helper + 2 теста.

### LOW-1 — ложный error в summary success-цикла ✅
`_aggregate_cycle_summary` брал `error` из любого account-summary, включая
`empty_reason` («no_active_ads») от пустых кабинетов. Теперь — только от кабинетов
с `outcome="error"`.

## Найдено, требует решения (НЕ исправлено)

### HIGH-3 — слияние одноимённых кампаний из разных кабинетов
`fb_campaigns` имеет `UNIQUE(campaign_name)`, upsert идёт `ON CONFLICT (campaign_name)`.
Кампания, задублированная во второй кабинет с тем же именем (типичный сценарий),
сливается в ОДНУ строку каталога: `fb_campaign_id` первого кабинета залипает
(COALESCE отбрасывает ID второго), `ad_account_id` прыгает между сканами
(последний выигрывает), ads обоих кабинетов цепляются к одной кампании.

Последствия: деньги НЕ страдают — pause/enable идут точно по `ad_id`, токен общий
для всех кабинетов, мутация сработает даже из «не той» вкладки. Страдает аналитика
(history/campaigns смешивает два кабинета в одну строку) и точность роутинга вкладки.

Рекомендуемый фикс (отдельным раундом, money-path writers):
upsert по `fb_campaign_id` (partial unique `ix_fb_campaigns_fb_id_unique` уже есть;
am_tabular всегда отдаёт `campaign_id`) с fallback на имя для строк без ID.
Альтернатива — composite `UNIQUE (campaign_name, ad_account_id)`, но миграция
сложнее (NULL-ы legacy-строк, конфликт с существующим constraint'ом).

### MID-1 — инвалидация токена только для одного кабинета
EAAB-токен общий на сессию, но при 190 инвалидируется только ключ
`session:act_<текущий>`. Остальные кабинеты держат тот же протухший токен →
по одному лишнему 190 + re-sniff (reload) на кабинет. Самовосстанавливается за
один цикл; улучшение — `invalidateGraphContext` по префиксу `session_id`.

### MID-2 — ad_account_id не виден в UI диагностики
`scan_runs.ad_account_id` пишется, но `GET /observer/scan-runs` (ScanRunOut) и
фронтовые вьюхи его не отдают/не рендерят. Без него диагностировать «какой кабинет
упал» можно только по логам. Прокинуть поле в API + колонку в Settings → Observer.

### LOW-2 — лог-спам про офферы без кабинетов
Warning «офферы без ad_account_ids не сканируются» пишется каждый цикл (~90с).
Дедуп (раз в сутки / при изменении списка) + алерт в TG ops-топик.

### LOW-3 — пауза между кабинетами не прерывается shutdown'ом
`asyncio.sleep(3)` между кабинетами игнорирует shutdown_event. На 2-3 кабинетах
несущественно; при росте N — перейти на `_wait_interruptible`.

### LOW-4 — «Кампании для сканирования» в Settings не действует в мульти-кабе
`ListCampaigns` работает по primary-вкладке (один кабинет), а сам allowlist в
мульти-каб режиме игнорируется (HIGH-1). UI-фичу стоит скрыть/задисейблить при
непустом scan set, чтобы не вводить в заблуждение.

## Проверено — ОК

- **FSM**: ключ — `fb_ad_id` (глобально уникален) → состояния кабинетов не смешиваются.
- **observer:runtime контракт**: новые поля (`current_account_id`, `accounts_done/total`)
  аддитивны; `read_observer_runtime` берёт именованные поля + `raw` — старые читатели целы
  (контрактные тесты не assert'ят точный набор ключей).
- **alert_dispatcher**: per `scan_id` — каждый кабинетный скан диспатчит свои алерты.
- **Идемпотентность авто-стопа**: `auto:pause_ad:{fb_ad_id}:{token}` не зависит от
  кабинета — дублей задач при мульти-скане нет.
- **cabinet_scheduler / bulk**: batch по точным `ad_id` — кабинетонезависим (токен общий),
  autostart продолжает покрывать все кабинеты.
- **mutation-handlers**: все 13 call-sites `execute_graph_call` получили
  `ad_account_id=payload.ad_account_id`; helpers `duplicate_campaign` — через параметр
  (payload вне scope). Существующие unit-тесты (AsyncMock, без exact-kwargs) не ломаются.
- **Миграция 0019**: nullable-колонки на partitioned `scan_runs` безопасны; `offers.ad_account_ids`
  с server_default `'{}'` + backfill через UI.
- **Legacy-режим**: пустой scan set → один скан без `ad_account_id`, поведение бит-в-бит
  старое (включая trust-on-first-use в `ensureAdsManagerPage`).
- **page-lock**: scan и mutations сериализуются per-session — «Execution context was
  destroyed» при reload-сниффе одного кабинета во время evaluate другого исключён.
- **Верификация в этом раунде**: py_compile всего затронутого Python; `tsc --noEmit`
  чистый для browser-agent, frontend, frontend-mini; 138/138 node:test browser-agent.
  pytest/vitest — на машине разработчика (песочница без PyPI/darwin-бинарей).
