# PROJECT STATUS — FB Stop Bot

> Единый источник правды по состоянию проекта. Обновляется по итогам раундов.
> Тесты backend: **1076 passed / 0 failed** · frontend: **77 passed** · ruff/typecheck/lint clean.
> Подробности — в `CLAUDE.md` (архитектура) + `META_INTEGRATION_PLAN.md` (план) + `docs/*audit*.md` (аудиты).

## TL;DR

- **Backend — production-ready.** 12 воркеров + FastAPI (61 endpoint) + Node.js gRPC. Прошёл 5 аудитов (security ×2, test-coverage, code-quality, test-quality) + cleanup-раунды. Все CRIT/HIGH закрыты.
- **Frontend — готов.** Новый `frontend/` (TS + Vite + Tailwind 4 + TanStack): 6 страниц, русский UI, проверены в браузере. Старый `frontend-legacy/` — архив.
- **БД** переименована `fb_stop_bot_v2 → fb_stop_bot` (данные сохранены).
- **Проверено вживую (2026-05-29):** Marketing API latency (BL-7, insights 2–4с / list ~1с); observer scan-канал — DOM-парсинг + валидация колонок работают на живом Ads Manager (для полного скана нужен кастомный column-preset в кабинете). **Починены 2 латентных prod-бага:** gate-фабрики observer/disable/enable звали несуществующий API (`BrowserAgentClient()` без config + `.connect()`) → упали бы на первой задаче; +4 анти-регресс теста.
- **Не доведено вживую:** полный observer-цикл с FSM/disable (нужен column-preset + согласие на действия на реальных кабинетах).

---

## 1. Этапы META_INTEGRATION_PLAN

| Этап | Что | Статус |
|---|---|---|
| 0 | Подготовка | ✅ |
| 1 | PoC + MetaApiService (browser-agent gRPC) | ✅ latency замерена live 2026-05-29 (insights 2–4с / list ~1с) |
| 2 | `core/meta_api/` Python-обвязка | ✅ |
| 3 | AI-ассистент (15 tools, draft-first) | ✅ |
| 4 | Ad Library | ✅ (on-demand `/spy`, App-канал отброшен — Meta требует Identity Confirmation) |
| 5 | Mutations + API-creator (10 handlers) | ✅ |
| 6 | AdSet.pro (оказался MCP-сервером) | ✅ (Волна 3 закрыта; aggregator/outgoing postback — BL-8) |
| 7 | Frontend | ✅ 6 страниц + русский UI |
| 8 | Multi-account | ⏸ отложен (до 2-го кабинета) |
| 9 | Технический долг | ✅ CRIT/HIGH (Round 10/11) + P2 (helpers/split/dev-tools/openapi) закрыты |

---

## 2. Функциональные блоки — что делает, работает ли

### Python-воркеры (12) — `apps/*`
| Блок | Назначение | Состояние |
|---|---|---|
| observer_worker | scan → FSM → метрики → outbox → TG-алерты | ✅ (heartbeat R11; gate-фабрика пофикшена 2026-05-29; scan-канал проверен live) |
| disable_worker / enable_worker | toggle ad через gRPC, retry backoff | ✅ (heartbeat R11; gate-фабрика пофикшена 2026-05-29) |
| telegram_poller | `/start /help /spy /ask` + inline-кнопки | ✅ |
| meta_api_worker | Marketing API mutations (outbox) | ✅ |
| reconciler_worker | stuck tasks → retrying, stale drafts cancel | ✅ |
| cleanup_worker | retention + партиции | ✅ |
| health_watchdog | мониторинг `worker:heartbeat:*` | ✅ (R11 выровнял имена — было сломано) |
| enable_recommendation_worker | рекомендации re-enable | ✅ |
| digest_scheduler | ежедневный TG-дайджест 09:00 | ✅ |
| creator_worker / creator_recorder | Vision-создание кампаний | ⚠️ код есть, не активны в сборке |

### Сервисы
| Блок | Назначение | Состояние |
|---|---|---|
| FastAPI (`apps/api/`) | 61 endpoint в 17 v1-роутерах + health/postback/WS | ✅ smoke-проверен в браузере |
| WebSocket `/ws/dashboard` | real-time push из Redis pubsub | ✅ воркеры публикуют alert/task/health/scan (BL-1) |
| Node.js gRPC browser-agent | Vision + Scanner + Creator + MetaApi | ✅ (внешний процесс) |
| MCP-сервер (`apps/mcp_server/`) | 15 tools + 4 resources для Claude Desktop | ✅ stdio |

### Core-модули (`core/`)
observer (FSM/pipeline/writers/runtime), rules (6 стоп-правил), tasks (unified outbox), meta_api (client+10 mutations+upload), telegram, dashboard (snapshot+metric_aggregation), adset_pro (MCP-клиент), ai_assistant, enable_reco, control (pubsub_listener), crypto, pubsub — **все ✅**.

---

## 3. Frontend

| Что | Стек | Состояние |
|---|---|---|
| `frontend/` (основной) | TS + Vite + Tailwind 4 + TanStack + Zustand | ✅ **6 страниц + русский UI** (Dashboard/Ads/Offers/History/Settings/Drafts), browser-проверены, 77 тестов |
| `frontend-legacy/` (архив) | React 19 + JSX + Tailwind 3 | 🗄 старый фронт, оставлен на случай отката, не развивается |
| `frontend-mini/` (TMA) | React + JSX | ⏸ не трогали, дублирует логику старого |

Дизайн: editorial-monochrome (см. `docs/frontend_design.md` + `docs/frontend_mockups/`). Dark-only, desktop 1280+. Язык UI зафиксирован в `design.md §0.5` (русский + ad-ops латиницей). Контрактные типы из OpenAPI: `make export-openapi && make gen-api-types`.

---

## 4. Что НЕ работает / работает частично

| Проблема | Эффект | Задача |
|---|---|---|
| creator_worker/recorder не активны | Авто-создание кампаний через Vision выкл | по запросу |
| Frontend ↔ backend shape: ряд полей `null` | Offer.country_code, OfferRule JSONB и др. отдаются null | BL-12 (миграция, если фронт начнёт требовать) |

---

## 5. Low / tech-debt

Костяк качественный. Что осталось (некритично):
- **BL-12 shape-миграция:** Offer.country_code/use_vision_creator/notes, OfferRule JSONB-thresholds, AdMetrics.delivery_status — отдаются `null`. OpenAPI-пайплайн (BL-12 done) выявляет дрейф; миграция БД — если фронт начнёт требовать поля.
- JOIN-цепочка `FbAd→FbAdset→FbCampaign→Offer` ×7 — НЕ вынесена (3 ортогональные оси различий, вынос рискованнее дублирования — обосновано в Round 10 отчёте).
- `upload.py`: 6× `# type: ignore` на приватный `_stub` → публичный аксессор (LOW).
- `offers.py`: unique-конфликт через `str(exc)` вместо SQLSTATE 23505 (LOW).

---

## 6. BACKLOG — готовые к заведению таски

> `BL-N · [приоритет] · scope`. P2=tech-debt, P3=отложено/ждёт внешнего.

**Закрыто:** BL-1 (WS publish), BL-2..6 (frontend страницы), BL-7 (latency-замер live 2026-05-29 → §1.1 плана), BL-9 (prod-блок dev-tools), BL-10+11 (вынос helpers + split history.py), BL-12 (OpenAPI codegen + дрейф-репорт). Rename (frontend-v2→frontend, БД v2→fb_stop_bot) — done.

**Осталось:**
- **BL-8 · P3 · AdSet.pro Волна 4** — tracker_aggregate per (ad,country,day) + outgoing postback + key rotation.
- **BL-12-mig · P2 · shape-миграция БД** — добавить недостающие поля Offer/OfferRule/AdMetrics (если фронт начнёт требовать; пайплайн дрейфа уже есть).
- **BL-13 · P3 · backtest** — `scripts/backtest_rules.py`, после накопления данных (MEMORY 2026-06-08).
- **BL-14 · P3 · light mode + frontend-mini** — вторая тема + прокачка TMA.

---

## 7. Документы-ссылки

| Файл | Что |
|---|---|
| `META_INTEGRATION_PLAN.md` | Master-план этапов 0-9 |
| `CLAUDE.md` | Архитектура, design-rules, история раундов |
| `DB_REDESIGN.md` | Схема БД |
| `docs/frontend_compatibility_audit.md` | 70 endpoints × backend-модели |
| `docs/frontend_design.md` + `docs/frontend_mockups/` | Дизайн нового фронта |
| `docs/backend_test_audit_round_8.md` | Аудит покрытия (Round 9 закрыл) |
| `docs/backend_code_quality_audit.md` | Аудит качества (Round 10 закрыл) |
| `docs/test_quality_audit.md` | Почему пропустили CRIT (Round 11 закрыл) |
