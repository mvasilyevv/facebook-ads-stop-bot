# Campaign Recorder + Auto-Creator — дизайн

**Дата:** 2026-05-11  
**Статус:** утверждён

## Проблема

Создание кампаний в Ads Manager — монотонная ручная работа. Существующий модуль `campaign_scripts/planner.py` уже строит полный план (название, адсеты, объявления, URL-параметры), но исполнение остаётся на пользователе.

## Цель

1. **Фаза 1** — записать одну ручную сессию создания кампании через CDP-инжект JS, проанализировать паттерны и найти что можно автоматизировать.
2. **Фаза 2** — на основе реальных данных из записи построить Playwright-воркер, который создаёт кампании автоматически с паузами на критичных шагах.

---

## Архитектура

### Новые модули

#### `core/campaign_recorder/`
- **`cdp_session.py`** — подключение к Vision anti-detect через CDP (переиспользует паттерн из `disable_worker`)
- **`event_injector.py`** — JS-сниппет, инжектируется в страницу через CDP, слушает события: `click`, `input`, `change`, `select`, `focus`
- **`session_writer.py`** — пишет события в `recordings/<timestamp>_<offer_code>.json`
- **`analyzer.py`** — читает JSON-сессию, строит отчёт: количество шагов, повторяющиеся паттерны, надёжность атрибутов элементов

#### `core/campaign_creator/`
- **`steps/`** — каждый шаг отдельный класс: `CreateCampaignStep`, `SetAdSetGeoStep`, `UploadMediaStep`, `SetUrlParamsStep`, и т.д.
- **`runner.py`** — выполняет шаги последовательно; перед каждым опасным шагом создаёт `CampaignCreatorCheckpoint` в БД и ждёт подтверждения из UI
- **`recovery.py`** — при падении продолжает с последнего сохранённого checkpoint

### Новая таблица БД

**`CampaignCreatorTask`** — аналог существующего `DisableTask`:
- `id`, `status`, `current_step`, `checkpoint_data`, `offer_code`, `creative_folder`, `cabinet_id`
- Статусы: `pending` → `running` → `waiting_confirmation` → `running` → `done` / `failed`

### Новые API-эндпоинты (`apps/api/routers/campaign_creator.py`)

```
POST /api/campaign-recorder/start   — стартует CDP-сессию + инжект JS
POST /api/campaign-recorder/stop    — останавливает запись, сохраняет JSON
GET  /api/campaign-recorder/analyze — возвращает отчёт по последней записи

POST /api/campaign-creator/start    — создаёт CampaignCreatorTask, запускает воркер
POST /api/campaign-creator/{id}/confirm  — подтверждает checkpoint
GET  /api/campaign-creator/{id}/status   — текущий шаг и статус
```

### Frontend

Расширение существующей страницы Campaign Scripts:
- Кнопка «Начать запись» + индикатор активной записи
- Кнопка «Стоп и анализ» — останавливает запись, показывает отчёт
- Новая секция запуска: выбор оффера + папки → кнопка «Создать автоматически»
- Прогресс шагов с кнопками «Подтвердить» на checkpoint'ах

---

## Формат записанного события

```json
{
  "ts": 1715430000.123,
  "type": "click",
  "tag": "button",
  "id": "",
  "classes": ["_5f4c", "x1y1"],
  "data_attrs": {"data-surface": "/am/create/campaign"},
  "xpath": "//div[@role='main']//button[3]",
  "text": "Создать",
  "value": null,
  "x": 340,
  "y": 210
}
```

---

## Жизненный цикл

```
[Запись]
Пользователь → «Начать запись» в UI
→ API подключается к Vision CDP
→ инжектируется JS-слушатель
→ пользователь вручную создаёт кампанию в Ads Manager
→ «Стоп» → JSON сохранён в recordings/

[Анализ]
→ Analyzer строит отчёт
→ вместе смотрим: какие шаги, какие атрибуты стабильны
→ пишем Steps на основе реальных данных

[Запуск]
Пользователь → выбирает оффер + папку → «Создать автоматически»
→ CampaignCreatorTask создаётся в БД
→ runner идёт по шагам
→ на опасных шагах — пауза + кнопка «Подтвердить» в UI
→ статус «Готово»
```

---

## Фазирование

| Фаза | Что строим | Когда |
|------|-----------|-------|
| 1 | `campaign_recorder` (CDP + JS + writer + analyzer) + API эндпоинты recorder + UI кнопки записи | Первый спринт |
| 2 | `campaign_creator` (steps + runner + recovery) + CampaignCreatorTask + UI прогресс | После анализа первой записи |

---

## Ключевые решения

- **CDP вместо расширения** — инфраструктура уже есть в `disable_worker`, не нужно ничего устанавливать в Vision
- **Фаза записи отдельно от фазы исполнения** — сначала данные, потом автоматизация на основе реальных селекторов
- **Checkpoint-паузы** — аналог подтверждений в Telegram для disable-задач, но через Frontend UI
- **Шаги как отдельные классы** — легко добавлять, тестировать и отлаживать каждый шаг независимо
