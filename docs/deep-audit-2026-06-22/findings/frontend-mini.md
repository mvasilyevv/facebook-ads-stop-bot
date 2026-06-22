# Находки аудита: Frontend Mini + Shared Package

Дата: 2026-06-22  
Файлы: `frontend-mini/src/`, `packages/shared/src/`

---

## HIGH — Кнопка «Сканировать сейчас» не работает (404)

**Файл**: `frontend-mini/src/lib/api.ts:312`  
**Проблема**: `useTriggerScan` вызывает `POST /api/observer/scan-now` — такого эндпоинта нет. Endpoint живёт по пути `POST /api/settings/observer/scan-now` (роутер `settings_observer.py`).  
**Влияние**: Ручной запуск скана из Dashboard TMA всегда получает 404. Пользователь нажимает — видит тост «ошибка» или тихий фейл.  
**Фикс**: Изменить строку в `api.ts`:
```ts
// было:
fetchJson("/observer/scan-now", { method: "POST" })
// стало:
fetchJson("/settings/observer/scan-now", { method: "POST" })
```

---

## MID — Точки фильтра объявлений невидимы (неверные CSS-токены)

**Файл**: `frontend-mini/src/routes/ads/index.tsx:349`  
**Проблема**: Фильтр-чипы по состоянию объявления используют `background: \`var(--fsm-${f.id})\``, где `f.id` принимает значения `"warning_sent"` и `"stop_sent"`. Токены `--fsm-warning_sent` и `--fsm-stop_sent` не существуют — только `--fsm-warning` и `--fsm-stop`. Точки рендерятся прозрачными.  
Баг задокументирован в комментарии `packages/shared/src/constants/states.ts:51`, но не исправлен на стороне mini.  
**Влияние**: Визуальный дефект — пользователь не видит цветовую индикацию предупреждения/стопа в фильтрах списка объявлений.  
**Фикс**: Использовать `alertStateCssVar(f.id)` из `@fb/shared`:
```tsx
import { alertStateCssVar } from "@fb/shared";
// ...
background: alertStateCssVar(f.id)
```

---

## MID — setState в теле рендера (ThresholdsForm)

**Файл**: `frontend-mini/src/routes/offers/index.tsx:258–267`  
**Проблема**: Компонент `ThresholdsForm` вызывает `setValues(init)` и `setInitialized(true)` напрямую в теле функции рендера (не в `useEffect`). Это React anti-pattern: вызывает дополнительный рендер на каждый цикл, в React StrictMode (включён в `main.tsx`) триггерится дважды, генерирует предупреждение «Cannot update a component while rendering a different component».  
**Влияние**: Возможные артефакты состояния форм при double-render (StrictMode), потенциальные бесконечные циклы рендера при изменении `rules`.  
**Фикс**: Обернуть инициализацию в `useEffect`:
```tsx
useEffect(() => {
  if (!initialized && rules) {
    setValues(init);
    setInitialized(true);
  }
}, [rules]);
```

---

## LOW — Дублирование константы API_BASE

**Файлы**: `frontend-mini/src/lib/auth.ts:13`, `frontend-mini/src/lib/api.ts:21`  
**Проблема**: Константа `API_BASE` объявлена независимо в двух файлах. При смене базового пути нужно обновить оба места.  
**Влияние**: Tech-debt, риск рассинхронизации при рефакторинге.  
**Фикс**: Вынести в `lib/config.ts` или импортировать из одного файла.

---

## LOW — Устаревший type cast для `ad_account_ids`

**Файл**: `frontend-mini/src/routes/offers/index.tsx` (несколько мест)  
**Проблема**: Используется приведение `offer as (Offer & { ad_account_ids?: string[] })`, хотя поле `ad_account_ids?: string[]` уже присутствует в `OfferOut` в `packages/shared/src/api/generated.ts`. Cast стал избыточным после обновления OpenAPI-схемы.  
**Влияние**: Ложное ощущение нестабильности типа, запутывает код ревью.  
**Фикс**: Удалить избыточный cast, использовать `offer.ad_account_ids` напрямую.

---

## LOW — God-компоненты превышают порог 500 строк

**Файлы**:  
- `frontend-mini/src/routes/offers/index.tsx` — 644 строки  
- `frontend-mini/src/routes/settings/index.tsx` — 520 строк  

**Проблема**: Нарушают правило «Никаких файлов >500 строк в новом коде» (CLAUDE.md). `offers/index.tsx` содержит: список офферов + форму создания/редактирования + `ThresholdsForm` + `parseAccountIds` — четыре независимые области ответственности. `settings/index.tsx` содержит четыре секции настроек.  
**Влияние**: Низкое покрытие тестами (12 тестов на весь mini — подтверждено), сложно тестировать изолированно.  
**Фикс**: Разнести по компонентам: `OfferForm`, `OfferList`, `ThresholdsForm` → отдельные файлы; секции settings → `ObserverSection`, `TelegramSection`, `VisionSection`, `CabinetAutostartSection`.

---

## LOW — Read-modify-write в handleSaveTag с потенциально устаревшим кешем

**Файл**: `frontend-mini/src/routes/settings/index.tsx` (секция Observer)  
**Проблема**: `handleSaveTag` читает текущую конфигурацию из React Query cache (`queryClient.getQueryData`), патчит `owner_campaign_tag` и отправляет `PUT /settings/observer` со всеми полями. Если параллельно был изменён другой параметр (например, включено/выключено сканирование), кеш может содержать stale-данные → PUT перезапишет изменение.  
**Влияние**: Low-probability race при одновременном использовании нескольких вкладок/устройств. Не money-critical (только тег owner).  
**Фикс**: Использовать `PATCH /settings/observer/scanning` для точечных изменений, либо делать `await refetch()` непосредственно перед PUT.

---

## Примечание: alertRuleCodes всегда пуст

**Файл**: `frontend-mini/src/routes/ads/$fbAdId.tsx:141`  
**Проблема**: `alertRuleCodes` инициализируется как `[]` и не заполняется. Блок «danger-callout с правилами» (`alertRuleCodes.length > 0`) никогда не рендерится. Причина: `TmaRecentAlert` не содержит `rule_codes` (только `reason_title`) — бэкенд `tma.py` не возвращает коды правил в `/tma/ads/{id}`.  
**Влияние**: Пользователь TMA не видит, какое именно правило сработало.  
**Фикс**: Расширить `TmaRecentAlert` полем `rule_codes: string[]` в `tma.py::_load_ad_extras` + `TmaAdDetailResponse`, заполнить `alertRuleCodes` из `recent_alerts`. Не блокирующий дефект — только информационное ухудшение UX.
