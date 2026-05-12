# Campaign Recorder + Auto-Creator — обзор и разбивка по агентам

> **Для агентов:** используй superpowers:subagent-driven-development или superpowers:executing-plans.

**Цель:** Записать ручную сессию создания кампании в Ads Manager через CDP-инжект JS, проанализировать паттерны, затем автоматически воспроизводить создание кампаний с checkpoint-паузами.

**Архитектура:** CDP-подключение к Vision anti-detect → инжект JS-слушателей → JSON-запись событий → анализатор паттернов → Playwright-runner с пошаговым исполнением и подтверждениями из UI.

**Стек:** Python 3.12, Playwright async, FastAPI, SQLAlchemy 2.x async, React 19 + Vite.

---

## Фазирование

| Фаза | Что | Файл плана |
|------|-----|-----------|
| 1A | Бэкенд Recorder (CDP + JS + writer + analyzer + API) | `2026-05-11-campaign-recorder-phase1-backend.md` |
| 1B | Фронтенд Recorder (кнопки записи + отчёт) | `2026-05-11-campaign-recorder-phase1-frontend.md` |
| 2A | Бэкенд Creator (БД + steps + runner + API) | `2026-05-11-campaign-recorder-phase2-backend.md` |
| 2B | Фронтенд Creator (запуск + прогресс + подтверждения) | `2026-05-11-campaign-recorder-phase2-frontend.md` |

---

## Разбивка по агентам

**Агент 1 — Фаза 1A (бэкенд recorder):**
- Новые файлы: `core/campaign_recorder/cdp_session.py`, `event_injector.py`, `session_writer.py`, `analyzer.py`
- Новый роутер: `apps/api/routers/campaign_recorder.py`
- Новые схемы в `apps/api/schemas.py`
- Тесты: `tests/unit/test_campaign_recorder.py`
- Зависимости: только от `core/browser/lock.py`, `core/config.py`

**Агент 2 — Фаза 1B (фронтенд recorder):**
- Ждёт: Агент 1 (нужны API-эндпоинты)
- Модифицирует: `frontend/src/pages/ScriptsPage.jsx`, `frontend/src/api.js`
- Добавляет: submodule "Запись" в секцию "Кампании"

**Агент 3 — Фаза 2A (бэкенд creator):**
- Ждёт: Агент 1 (нужны реальные записи для понимания DOM)
- Новые файлы: `core/campaign_creator/steps/`, `runner.py`, `recovery.py`
- Миграция: новая таблица `CampaignCreatorTask`
- Новый роутер: `apps/api/routers/campaign_creator.py`

**Агент 4 — Фаза 2B (фронтенд creator):**
- Ждёт: Агент 3
- Модифицирует: `frontend/src/pages/ScriptsPage.jsx`
- Добавляет: submodule "Автосоздание" с прогрессом и кнопками подтверждения

---

## Порядок запуска

```
Агент 1 (1A) → Агент 2 (1B) → [анализ записей] → Агент 3 (2A) → Агент 4 (2B)
```

Фазы 1A и первичный анализ — первый приоритет. Без реальных данных из записи Steps для creator писать преждевременно.
