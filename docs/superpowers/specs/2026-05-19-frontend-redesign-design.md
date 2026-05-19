# FB_Agent — редизайн фронтендов (Desktop + Mini App)

**Дата:** 2026-05-19
**Стиль:** Neo Control Room
**Скоуп:** `frontend/` (React 19 + Vite + Tailwind 3.4) и `frontend-mini/` (React 19 + Vite + raw CSS, Telegram Mini App)

---

## 1. Цель

Сменить визуальный язык обоих фронтов на единую эстетику Neo Control Room: тёмный операционный фон (#0E1116), оранжевые акценты (#FF6B00), Inter для заголовков + JetBrains Mono для лейблов/идентификаторов, LED-индикаторы статусов, левый цветной бар на KPI-карточках. Сохранить плотность Bloomberg-уровня без терминальной агрессии — это пульт оператора, а не торговый терминал.

Добавить трёхуровневую AI-интеграцию (глобальный брифинг + per-panel + inline в алертах), кэш в Postgres с TTL по типу блока, opt-in триггер. Разделить операционные графики (Dashboard) и исторические (Аналитика).

## 2. Визуальный язык — токены

```
Цвета
  --bg            #0E1116
  --surface       #14181E
  --surface-2     #1A1F26
  --border        #232A33
  --text          #E8EBEE
  --text-dim      #8A929D
  --text-muted    #5A6270
  --accent        #FF6B00       (brand/CTA, left-bar)
  --accent-soft   #FF6B0022
  --ok            #5AFF6A
  --warn          #FFB020
  --stop          #FF3B3B
  --info          #5CE6FF

Типографика
  Display/UI    Inter 13–32
  Mono/labels   JetBrains Mono 10–14 (uppercase, tracking .08em)
  KPI numerals  Inter SemiBold 38, tabular-nums

Геометрия
  radius        10px (карточки), 6px (чипы)
  border        1px solid --border
  shadow        inset 0 1px 0 #ffffff08
  left-bar      4px solid --accent для KPI

LED
  10px круг, glow box-shadow 0 0 8px currentColor, pulse 2s для LIVE
```

## 3. Архитектура навигации

### Desktop (`frontend/`)
Верхняя навигация: **Dashboard · Аналитика · Объявления · Офферы · Настройки**

### Mini App (`frontend-mini/`)
Главный экран + подстраницы (Офферы, Скрипты, Настройки уже есть). Добавляем AI-брифинг на главный.

## 4. Dashboard — состав блоков

Сверху вниз:

1. **AI-брифинг (opt-in, глобальный)** — карточка с кнопкой `✦ Сгенерировать брифинг`. По клику разворачивается анализ всего операционного среза (KPI + офферы + алерты), кэш в Postgres с TTL = 5 мин. Показывает время последней генерации и стоимость токенов.
2. **KPI-стрип + Pacing** — 4 плиты с левым оранжевым баром:
   - `SPEND·TODAY` — $X, дельта к плану, мини-sparkline
   - `LEADS·DEPS` — N·M, дельта к вчера
   - `ACTIVE ADS` — N, дельта к вчера
   - `WARN·STOP` — N/M, дельта за сутки
   Под стрипом — кардиограмма pacing (SVG): факт vs план, бейджи overspend/underspend.
3. **Офферы + Алерты** — 2 колонки (60/40):
   - Левая: таблица топ офферов (code / spend / leads·deps / CPL / status LED). Шапка панели: `▸ ТОП ОФФЕРЫ` + `✦ Анализ` кнопка.
   - Правая: лента алертов (время, оффер, правило, кнопка `Отключить`). Шапка: `▸ АЛЕРТЫ` + `✦ Анализ`. Каждый алерт имеет inline-кнопку `✦` для персонального разбора.
4. **Статус системы** — узкая полоса внизу: Observer (LED + время цикла), Disable worker (LED + длина очереди), Enable worker (LED + длина очереди), последний скан.

## 5. Аналитика — отдельная вкладка

4 блока:

1. **Heatmap алертов** — 24×7 сетка, цвет = плотность сработавших стоп-правил. Дрилл по клику на ячейку → список алертов.
2. **Причины стопов (donut)** — распределение стопов по 6 правилам. Легенда с долями.
3. **CPL·CPD timeline** — линейный график за период, переключатель CPL/CPD, дрилл по клику на точку → офферы дня.
4. **История решений** — лента всех алертов и действий (отключения, восстановления, override). Фильтры: оффер, правило, статус.

На каждом блоке — `✦ Анализ` кнопка с per-block кэшем.

## 6. Mini App — главный экран

1. KPI-стрип (адаптивный 2×2) — те же 4 плиты, упрощённые.
2. AI-брифинг — одна кнопка сверху, opt-in.
3. Топ офферы — карточками (не таблицей).
4. Лента алертов с кнопкой `Отключить` (telegram-native confirm).

Без графиков и Аналитики — только операционка.

## 7. AI-бэкенд

Новая таблица:

```sql
ai_cache (
  id uuid pk,
  block_type text,        -- 'briefing' | 'offers' | 'alerts' | 'pacing' | 'heatmap' | 'reasons' | 'cpl_timeline' | 'history' | 'alert_inline'
  scope_key text,         -- 'global' | offer_id | alert_id
  payload_hash text,      -- hash от входных данных, чтобы инвалидировать при изменении
  content text,           -- markdown ответа
  tokens_in int, tokens_out int,
  created_at timestamptz,
  expires_at timestamptz
)
```

TTL по типу:
- briefing: 5 мин
- offers/alerts panel: 5 мин
- alert_inline: 30 мин (привязан к конкретному alert_id)
- pacing: 10 мин
- analytics блоки (heatmap/reasons/cpl/history): 1 час

API: `POST /api/ai/analyze` — `{block_type, scope_key, force_refresh?}` → возвращает кэш или генерирует. Модель: claude-opus-4.7 через прокси (роутер уже есть).

## 8. Компоненты — конкретика

### Новые / переработать
- `components/kpi/KPIPlate.jsx` — левый бар + numeric + sparkline
- `components/charts/PacingCardiogram.jsx` — SVG, бейджи overspend
- `components/ai/AIBriefingCard.jsx` — opt-in, streaming-friendly
- `components/ai/AIPanelButton.jsx` — кнопка `✦ Анализ` для шапки панели
- `components/ai/AIInlineButton.jsx` — `✦` рядом с алертом
- `components/system/SystemStatusBar.jsx` — нижняя полоса воркеров
- `components/analytics/AlertsHeatmap.jsx`
- `components/analytics/StopReasonsDonut.jsx`
- `components/analytics/CPLTimeline.jsx`
- `components/analytics/DecisionsHistoryFeed.jsx`
- `pages/AnalyticsPage.jsx` — новая вкладка

### Стили
- Новый файл `frontend/src/styles/tokens.css` с CSS-переменными темы Neo Control Room
- Обновить `tailwind.config.js`: extend.colors с токенами, extend.fontFamily с Inter/JetBrains Mono
- `index.css` подключает JetBrains Mono + Inter с Google Fonts

### Mini App
- `frontend-mini/src/styles.css` переписать на те же токены
- `pages/DashboardPage.jsx` — добавить AIBriefingCard, перевёрстать KPI как плиты с баром

## 9. Тесты и верификация

- Snapshot-тесты на KPIPlate, PacingCardiogram, AIBriefingCard
- Mock API для `/api/ai/analyze` в тестах (без реального вызова opus)
- Visual smoke: `npm run build` для обоих фронтов проходит без warnings
- Ручная проверка в браузере: оба фронта на новых стилях, AI-кнопки кликаются, кэш работает (повторный клик не дёргает API)

## 10. Что НЕ делаем в этом скоупе

- Не трогаем бэкенд воркеров (Observer/Disable/Enable) — только API получает `/ai/analyze`
- Не меняем существующую логику FSM / правил
- Не делаем стриминг ответа в этой итерации (синхронный response с кэшем)
- Не делаем role-based UI (один пользователь)

## 11. Порядок реализации (для writing-plans)

1. Токены и подключение шрифтов
2. AIBriefingCard + AIPanelButton + AIInlineButton + бэкенд `/ai/analyze` + таблица `ai_cache`
3. KPIPlate + PacingCardiogram → DashboardPage
4. Перенос Офферов+Алертов на новый стиль + AI-кнопки в шапках
5. SystemStatusBar
6. AnalyticsPage с 4 блоками
7. Mini App переезд на новые токены + KPI + AIBriefing
8. Cleanup и удаление старых стилей/компонентов
