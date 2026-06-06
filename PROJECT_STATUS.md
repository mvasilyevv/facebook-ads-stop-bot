# PROJECT STATUS — FB Stop Bot

> Единый источник правды по состоянию проекта. Обновляется по итогам раундов.
> Тесты backend: **1214 passed / 0 failed** · frontend: **77 passed** · ruff/typecheck/lint clean.
> Подробности — в `CLAUDE.md` (архитектура) + `META_INTEGRATION_PLAN.md` (план) + `docs/*audit*.md` (аудиты).

## TL;DR

- **Backend — production-ready.** 14 воркеров + FastAPI (61 endpoint) + Node.js gRPC. Прошёл 5 аудитов (security ×2, test-coverage, code-quality, test-quality) + cleanup-раунды. Все CRIT/HIGH закрыты.
- **Frontend — готов.** Новый `frontend/` (TS + Vite + Tailwind 4 + TanStack): 6 страниц, русский UI, проверены в браузере. Старый `frontend-legacy/` удалён (мёртвый код).
- **БД** переименована `fb_stop_bot_v2 → fb_stop_bot` (данные сохранены).
- **Проверено вживую (2026-05-29):** Marketing API latency (BL-7, insights 2–4с / list ~1с); **Marketing API mutations enable/disable 24/24 объявлений** на живом кабинете (Этап 5 валидирован, act через API — 48 операций 0 промахов); observer DOM scan-канал (парсинг + валидация колонок). **Подтверждено владельцем:** стоп ad-level (кампания — отдельный рубильник владельца); стоп-правила (`docs/stop_rules.md`); CPA по гео (KE_CR2 $8 / GH_CR2 $3). **Починены 2 латентных prod-бага** gate-фабрик observer/disable/enable (+4 теста).
- **Не доведено вживую:** полный observer-цикл с FSM/авто-disable (нужен column-preset + запущенный observer на реальном кабинете) — #36.

---

## Проверено вживую vs тесты (срез 2026-05-29)

> Легенда: ✅ проверено на реальном кабинете · 🧪 код+тесты зелёные, вживую НЕ проверено · 📦 сделано, не активно.

- ✅ **Вживую на кабинете:** Marketing API mutations (enable/disable 24/24, 48 операций 0 промахов), DOM scan-канал (парсинг + валидация колонок), latency (BL-7: insights 2–4с / list ~1с), Node.js gRPC browser-agent. Подтверждено владельцем: стоп ad-level, стоп-правила (`docs/stop_rules.md`), гео-CPA (KE_CR2 $8 / GH_CR2 $3).
- 🧪 **Только тесты (1214 passed), не на кабинете:** полный observer-цикл scan→FSM→авто-disable (#36); act через API в авто-режиме (#39, сами mutations — вживую); frequency-anomaly + data-driven analyzer (#37, `ad_metrics` пустая → реально не считал); автостарт по расписанию (#38); все воркеры e2e + heartbeat; FastAPI под нагрузкой; WebSocket push; MCP-сервер; AdSet.pro ingest/aggregator/outgoing (BL-8); shape-поля фронта (BL-12); фронт с реальным бэком.
- 📦 **Сделано, не активно:** creator_worker/recorder (выкл в сборке); frontend-mini TMA (BL-15).

**Главный разблокиратор — #36 (observer вживую):** переводит бóльшую часть 🧪→✅ и накапливает `ad_metrics`, без которых не работают #37-analyzer и отложенный backtest. Требует поднятого Vision-профиля + column-preset + визуальной проверки владельцем.

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
| 6 | AdSet.pro (оказался MCP-сервером) | ✅ Волна 3+4 закрыты (BL-8: aggregator + outgoing postback + key rotation) |
| 7 | Frontend | ✅ 6 страниц + русский UI |
| 8 | Multi-account | ⏸ отложен (до 2-го кабинета) |
| 9 | Технический долг | ✅ CRIT/HIGH (Round 10/11) + P2 (helpers/split/dev-tools/openapi) закрыты |

---

## 2. Функциональные блоки — что делает, работает ли

### Python-воркеры (13) — `apps/*`
| Блок | Назначение | Состояние |
|---|---|---|
| observer_worker | scan → FSM → метрики → outbox → TG-алерты | ✅ (heartbeat R11; gate-фабрика пофикшена; scan-канал live; **owner-scoping** по тегу кампаний; **act_via_api** #39 — авто-стоп через Marketing API (дефолт), DOM-резерв по флагу) |
| disable_worker / enable_worker | toggle ad через gRPC, retry backoff | ✅ (heartbeat R11; gate-фабрика пофикшена 2026-05-29) |
| telegram_poller | `/start /help /spy /ask /tools /pause /resume /autostart` + inline | ✅ |
| meta_api_worker | Marketing API mutations (outbox) | ✅ (#39 — FSM-sync `ad_alert_state` после pause_ad/activate_ad/bulk) |
| reconciler_worker | stuck tasks → retrying, stale drafts cancel | ✅ |
| cleanup_worker | retention + партиции | ✅ |
| health_watchdog | мониторинг `worker:heartbeat:*` | ✅ (R11 выровнял имена — было сломано) |
| enable_recommendation_worker | рекомендации re-enable | ✅ |
| digest_scheduler | ежедневный TG-дайджест 09:00 | ✅ |
| cabinet_scheduler | автостарт по расписанию: enable owner-кампаний по дате + scan trigger (#38) | ✅ draft-free, owner-scoped, dedup |
| creator_worker / creator_recorder | Vision-создание кампаний | ⚠️ код есть, не активны в сборке |

### Сервисы
| Блок | Назначение | Состояние |
|---|---|---|
| FastAPI (`apps/api/`) | 61 endpoint в 17 v1-роутерах + health/postback/WS | ✅ smoke-проверен в браузере |
| WebSocket `/ws/dashboard` | real-time push из Redis pubsub | ✅ воркеры публикуют alert/task/health/scan (BL-1) |
| Node.js gRPC browser-agent | Vision + Scanner + Creator + MetaApi | ✅ (внешний процесс) |
| MCP-сервер (`apps/mcp_server/`) | 15 tools + 4 resources для Claude Desktop | ✅ stdio |

### Core-модули (`core/`)
observer (FSM/pipeline/writers/runtime), rules (7 стоп-правил, frequency-anomaly opt-in), tasks (unified outbox), meta_api (client+10 mutations+upload), telegram, dashboard (snapshot+metric_aggregation), adset_pro (MCP-клиент), ai_assistant, enable_reco, control (pubsub_listener), crypto, pubsub — **все ✅**.

---

## 3. Frontend

| Что | Стек | Состояние |
|---|---|---|
| `frontend/` (основной) | TS + Vite + Tailwind 4 + TanStack + Zustand | ✅ **6 страниц + русский UI** (Dashboard/Ads/Offers/History/Settings/Drafts), browser-проверены, 77 тестов |
| `frontend-mini/` (TMA) | React + JSX | ⏸ Telegram Mini App, дублирует часть логики основного |

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

**Закрыто:** BL-1 (WS publish), BL-2..6 (frontend страницы), BL-7 (latency-замер live), BL-9 (prod-блок dev-tools), BL-10+11 (helpers + split history.py), BL-12 (OpenAPI codegen). Rename (v2→fb_stop_bot) — done.
**Сессия 2026-05-29 (запушено):** gate-фабрики fix; owner-scoping (#33-35, +мультитег); стоп-правила зафиксированы (`docs/stop_rules.md`); ADR канал observer (DOM); `/tools` каталог (#34); `/pause` `/resume` (#33); автостарт по расписанию `cabinet_scheduler` (#38); **live-валидация Marketing API mutations** (enable/disable 24/24); **#39 observer act через API** (флаг `observer_config.act_via_api`, миграции 0007+0008 — **дефолт TRUE**: API основной канал, DOM спящий резерв-фолбэк; авто-стоп и ручные кнопки идут через `meta_api_mutation pause_ad/activate_ad`; FSM-sync `ad_alert_state` в meta_api_worker; +29 тестов). **#37 frequency-anomaly** активирован (правило 7, opt-in per-offer через `offer.frequency_threshold`, фаза 1 — абсолютный порог) + **data-driven analyzer** (`core/rules/frequency_analyzer.py` — авто-расчёт порога из истории `ad_metrics` по деградации `cost_per_result`; `dry_run`-защита, пишет только в NULL — ручное не затирает; нужен накопленный `ad_metrics`); +24 теста → 1175.

**BL-8 · P3 · AdSet.pro Волна 4 — ✅ закрыто (2026-05-29):** tracker_aggregator_worker (#14) — idempotent absolute-recompute `tracker_aggregate` per (ad,country,day) из `adsetpro_postback_events`; `OutgoingPostbackSender` (httpx+tenacity retry, non-blocking dispatch); ротация ключей через `adsetpro_credentials` (БД-first + `.env`-фолбэк, Fernet/BYTEA, без рестарта; wired в `deps.py` + postback endpoint). Миграция не нужна (таблицы в 0001). +27 тестов → 1202. Tech-debt (LOW): outgoing не подключён к конкретному flow (нет URL-адресата), durable-outbox через `task_queue` — по запросу.

**Осталось:**
- **BL-12-mig · P2 · shape-миграция БД** — добавить недостающие поля Offer/OfferRule/AdMetrics (если фронт начнёт требовать; пайплайн дрейфа уже есть).
- **BL-15 · P3 · frontend-mini (TMA)** — прокачка Telegram Mini App (дублирует логику старого фронта, без тестов).
- **#36 · P3 · live observer end-to-end** — полный цикл scan→FSM→авто-disable на живом профиле (нужен column-preset). При `act_via_api=True` act идёт через Marketing API (#39).

**Не делаем (решение 2026-05-29):**
- ~~BL-13 backtest~~ — пропускаем (пользователь калибрует пороги вручную/доверяет).
- ~~BL-14 light mode~~ — пропускаем, фронт остаётся dark-only.

---

## 7. Документы-ссылки

| Файл | Что |
|---|---|
| `META_INTEGRATION_PLAN.md` | Master-план этапов 0-9 |
| `CLAUDE.md` | Архитектура, design-rules, история раундов |
| `DB_REDESIGN.md` | Схема БД |
| `docs/stop_rules.md` | Стоп-правила: 6 правил + свёртка 80/80 + гео-CPA (KE_CR2 $8 / GH_CR2 $3) + owner-тег (подтверждено 2026-05-29) |
| `docs/frontend_design.md` + `docs/frontend_mockups/` | Дизайн нового фронта |
| `docs/archive/*audit*.md` | Завершённые аудиты (Round 9/10/11 закрыли) — архив |
| `docs/playbooks/` | Операционные playbooks (залив/креативы/PWA/инциденты) |
