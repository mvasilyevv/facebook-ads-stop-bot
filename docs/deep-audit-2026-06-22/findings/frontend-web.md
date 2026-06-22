# Аудит: Frontend Web (`frontend/src/`) — 2026-06-22

> Только новые находки. Уже закрытые (CRIT-1 naive SUM, CRIT-2 observer:runtime, Round 9-11) не дублируются.

---

## CRIT-1 — Offer delete — полный no-op в production

**Файл:** `frontend/src/routes/offers/index.tsx:267`

```ts
// ЗАГЛУШКА — функция ничего не делает
async function deleteOfferFn(_offerId: string) {
  // deleteOffer state уже содержит id
}
```

`ConfirmDialog` (строка 258) вызывает `deleteOfferFn(deleteOffer.id)` → заглушка → оффер **не удаляется**. Пользователь видит диалог подтверждения, вводит код оффера, нажимает OK — ничего не происходит.

`OfferDeleteManager` (строки 341–367) с реальным `useDeleteOffer()` определён, экспортируется, но **нигде не рендерится** в `OffersPage`.

Тест `offers.test.tsx` проверяет `OfferDeleteManager` в изоляции — не ловит, что компонент не смонтирован.

**Влияние:** удаление офферов через UI сломано. Данные не теряются (бэкенд soft delete), но операция недоступна.

**Фикс:** Удалить `OfferDeleteManager` и заглушку. Вместо этого вызывать `useDeleteOffer()` (уже импортирован на строке 22) напрямую в `OffersPage` (хук должен быть на уровне компонента, не внутри callback). Вызов `mutation.mutateAsync(deleteOffer.id)` вместо `deleteOfferFn(deleteOffer.id)`.

---

## HIGH-1 — WS URL содержит API-ключ в query-param (browser logs / nginx access log)

**Файл:** `frontend/src/lib/websocket/useDashboardSocket.ts`

```ts
const url = `${wsBase}/ws?api_key=${apiKey}`;
```

Ключ виден в: nginx access_log, browser DevTools Network, потенциально в Referer-заголовках. Комментарий в коде отмечает это как вынужденный workaround (браузер не поддерживает custom headers при WebSocket upgrade).

**Влияние:** на VPS с shared nginx логами — ключ в plaintext в файлах.

**Фикс (если security важен):** реализовать endpoint `POST /api/v1/ws-token` → одноразовый short-lived токен (TTL 60с, Redis) → `?token=<short_token>` вместо постоянного API-ключа. Либо — при деплое убрать WS path из nginx `access_log`.

---

## HIGH-2 — Hard-delete объявлений без feature flag — доступен в production

**Файл:** `apps/api/routers/v1/ads_admin.py`

```python
@router.post("/dashboard/ads/bulk-delete")
async def bulk_delete_ads(...):
    # hard DELETE с CASCADE
```

Роутер auto-discovered (`pkgutil.iter_modules`) и регистрируется без `require_dev_tools` gate. Любой клиент с валидным `X-API-Key` может вызвать hard DELETE.

На фронтенде `ads_admin.py` не используется (фронт зовёт `bulk-disable`, не `bulk-delete`). Но endpoint доступен через curl / Postman.

**Влияние:** необратимое удаление production-записей FbAd с CASCADE.

**Фикс:** добавить `Depends(require_dev_tools)` на endpoint, либо удалить роутер из `apps/api/routers/v1/`.

---

## MID-1 — Hardcoded CPL/frequency thresholds игнорируют offer_rules

**Файл:** `frontend/src/components/domain/ads/adHelpers.ts`

```ts
export function isCplBad(v: number)  { return v > 30; }   // hardcoded $30
export function isFreqBad(v: number) { return v > 4; }    // hardcoded 4
export function isRoasBad(v: number) { return v < 1; }    // hardcoded 1
```

Бэкенд хранит per-offer пороги в `offer_rules.cpa_threshold` / `offer_rules.frequency_threshold`. Фронтенд их игнорирует — индикаторы в `AdDrawer` и `AdsTable` (красный цвет/иконка) показывают неправильный сигнал для вертикалей с другим CPL.

**Влияние:** у гэмблинг-офферов нормальный CPL может быть $50+; красная подсветка мешает принятию решений.

**Фикс:** `AdSnapshot` уже содержит (или должен содержать через `offer_rules` join в `snapshot.py`) `cpa_threshold` — прокидывать его в хелперы. Если поля нет в снапшоте, добавить в `build_ad_snapshot` JOIN.

---

## MID-2 — Dashboard spendSeries использует raw кумулятивные значения

**Файл:** `frontend/src/routes/index.tsx:128` (примерная строка по контексту)

```ts
const spendSeries = chartQ.data.map(b => Number(b.spend ?? 0));
```

Каждый бакет в `GET /dashboard/chart-data` содержит `SUM(spend)` по снапшотам метрик за интервал. Снапшоты кумулятивны (растут в течение cabinet-day) → SUM внутри дня завышает spend в несколько раз (известный CRIT-1 паттерн, здесь на клиенте).

При этом headline (`stats.current_day_spend`) корректен. **Визуальный конфликт:** спарклайн показывает рост в 5-10x, headline — реальную цифру.

В `lib/utils/spendTotal.ts` реализована правильная `cumulativeSpendTotal` — **не используется**.

**Влияние:** визуальный график spend/day некорректен, вводит в заблуждение при анализе дневного тренда.

**Фикс:** либо использовать `cumulativeSpendTotal` (клиентская дедупликация: последнее значение per ad per day, sum across days), либо на бэкенде переключить `chart-data` endpoint с SUM на `DISTINCT ON` через `latest_per_ad_window_cte` (уже реализован в `core/dashboard/metric_aggregation.py`).

---

## MID-3 — Cold deep-link `ads/$fbAdId` создаёт AdSnapshot с hardcoded `alert_state: "normal"`

**Файл:** `frontend/src/routes/ads/$fbAdId.tsx`

При холодной загрузке (прямой URL, кэш пуст):
```ts
const syntheticAd: AdSnapshot = {
  ...timelineData,
  alert_state: "normal",  // не отражает реальный FSM
  // …
};
```

**Влияние:** пользователь открывает ссылку на объявление в `stop_sent` — видит статус `normal`, не знает что оно под стопом. При следующем poll данные исправятся, но первый рендер обманчив.

**Фикс:** добавить `GET /api/v1/ads/{fb_ad_id}` endpoint (или реиспользовать существующий `GET /dashboard/ads?fb_ad_id=` с limit=1), вместо синтеза из timeline.

---

## MID-4 — Unsafe type casts для extended AdSnapshot полей

**Файлы:**
- `AdDrawer.tsx`: `const adExt = ad as AdSnapshot & { creative_thumb_url?, creative_image_url?, adset_pixel_id?, adset_daily_budget?, adset_lifetime_budget?, adset_budget_remaining?, learning_stage? }`
- `AdsTable.tsx`: `(ad as AdSnapshot & { creative_thumb_url? }).creative_thumb_url`
- `adHelpers.ts`: `(ad as AdSnapshot & { ad_account_id? }).ad_account_id`

Поля существуют на бэкенде (`build_ad_snapshot` в `snapshot.py`), но не входят в OpenAPI schema → не генерируются в `@fb/shared/api/generated`.

**Влияние:** при изменении бэкенда (переименование поля) TypeScript не предупредит; runtime будет `undefined` без ошибки компиляции.

**Фикс:** добавить поля в `AdSnapshotOut` pydantic-схему (бэкенд), регенерировать `pnpm gen:api`. Либо добавить `AdSnapshotExtended` интерфейс в `@fb/shared`.

---

## LOW-1 — God-компоненты превышают лимит 500 строк

| Файл | Строк | Проблема |
|------|-------|---------|
| `components/domain/ads/AdDrawer.tsx` | 638 | Tabs: Overview/Timeline/Tasks; логика disable внутри |
| `routes/ads/index.tsx` | 531 | Fetch + keyboard nav + bulk action + URL sync в одном компоненте |
| `components/domain/ads/AdsTable.tsx` | 414 | Виртуализация + колонки + creative thumb + keyboard |
| `components/domain/ads/FilterBar.tsx` | 410 | Все фильтры в одном компоненте |

`CLAUDE.md` прямо запрещает файлы >500 строк в новом коде.

**Фикс:** `AdDrawer` → вынести `AdDrawerOverview`, `AdDrawerTimeline`, `AdDrawerTasks`; `AdsPage` → вынести `AdsKeyboardNav` и `AdsPageContent`.

---

## LOW-2 — Zustand API key реагирует на смену только после переподключения WS

**Файл:** `frontend/src/lib/websocket/useDashboardSocket.ts`

```ts
const apiKey = useAuthStore.getState().apiKey; // читается один раз при монтировании
const url = `${wsBase}/ws?api_key=${apiKey}`;
```

Если пользователь меняет API key в Settings (через `useAuthStore.setState`), WS продолжает использовать старый ключ до ручного рефреша страницы.

**Влияние:** после ротации ключа live-invalidation ломается без уведомления.

**Фикс:** подписаться на `useAuthStore` через selector в `useDashboardSocket` (триггер reconnect при изменении `apiKey`), либо disconnect → connect при смене ключа.

---

## Статистика покрытия

- Всего тестов vitest: ~331 (`frontend/`)
- `offers.test.tsx` тестирует `OfferDeleteManager` в изоляции — CRIT-1 не покрыт сценарием "компонент не смонтирован"
- Нет тестов на `cumulativeSpendTotal` использование в Dashboard
- Нет E2E тестов WS reconnect
