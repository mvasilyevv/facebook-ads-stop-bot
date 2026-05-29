# PROJECT STATUS — FB Stop Bot

> Единый источник правды по состоянию проекта. Обновляется по итогам раундов.
> Дата: 2026-05-29 · Коммитов: 440 · Тесты: **1055 passed / 0 failed** · ruff clean.
> Подробности — в `CLAUDE.md` (архитектура) + `META_INTEGRATION_PLAN.md` (план) + `docs/*audit*.md` (аудиты).

## TL;DR

- **Backend — production-ready.** 12 воркеров + FastAPI (61 endpoint) + Node.js gRPC. Прошёл 5 аудитов (security ×2, test-coverage, code-quality, test-quality) и 4 cleanup-раунда. Все CRIT/HIGH закрыты.
- **Frontend — в процессе.** Старый `frontend/` (7 страниц) работает со свежим API. Новый `frontend-v2/` (TS, editorial-дизайн): foundation + Dashboard готовы, 5 страниц — placeholder.
- **Не проверено вживую:** Marketing API latency, воркеры на реальном Vision/Meta-аккаунте (нет live-доступа в сессиях).

---

## 1. Этапы META_INTEGRATION_PLAN

| Этап | Что | Статус |
|---|---|---|
| 0 | Подготовка | ✅ |
| 1 | PoC + MetaApiService (browser-agent gRPC) | ✅ (кроме live-замера latency — task BL-7) |
| 2 | `core/meta_api/` Python-обвязка | ✅ |
| 3 | AI-ассистент (15 tools, draft-first) | ✅ |
| 4 | Ad Library | ✅ (on-demand `/spy`, App-канал отброшен — Meta требует Identity Confirmation) |
| 5 | Mutations + API-creator (10 handlers) | ✅ |
| 6 | AdSet.pro (оказался MCP-сервером) | ✅ (Волна 3 закрыта; aggregator/outgoing postback — BL-8) |
| 7 | Frontend миграция | 🔄 **в процессе** (Dashboard готов, 5 страниц + WS-каналы) |
| 8 | Multi-account | ⏸ отложен (до 2-го кабинета) |
| 9 | Технический долг | 🔄 частично (Round 10/11 закрыли CRIT/HIGH; LOW — BL-9..12) |

---

## 2. Функциональные блоки — что делает, работает ли

### Python-воркеры (12) — `apps/*`
| Блок | Назначение | Состояние |
|---|---|---|
| observer_worker | scan → FSM → метрики → outbox → TG-алерты | ✅ работает (heartbeat пофикшен R11) |
| disable_worker / enable_worker | toggle ad через gRPC, retry backoff | ✅ (heartbeat пофикшен R11) |
| telegram_poller | `/start /help /spy /ask` + inline-кнопки | ✅ |
| meta_api_worker | Marketing API mutations (outbox) | ✅ |
| reconciler_worker | stuck tasks → retrying, stale drafts cancel | ✅ |
| cleanup_worker | retention + партиции | ✅ |
| health_watchdog | мониторинг `worker:heartbeat:*` | ✅ (R11 выровнял имена — было сломано) |
| enable_recommendation_worker | рекомендации re-enable | ✅ |
| digest_scheduler | ежедневный TG-дайджест 09:00 | ✅ |
| creator_worker / creator_recorder | Vision-создание кампаний (plan_run / запись) | ⚠️ код есть, не активны в текущей сборке |

### Сервисы
| Блок | Назначение | Состояние |
|---|---|---|
| FastAPI (`apps/api/`) | 61 endpoint в 17 v1-роутерах + health/postback/WS | ✅ smoke-проверен в браузере |
| WebSocket `/ws/dashboard` | real-time push из Redis pubsub | ⚠️ работает, но воркеры публикуют только `scan_finished` (BL-1) |
| Node.js gRPC browser-agent | Vision + Scanner + Creator + MetaApi | ✅ (внешний процесс) |
| MCP-сервер (`apps/mcp_server/`) | 15 tools + 4 resources для Claude Desktop | ✅ stdio |

### Core-модули (`core/`)
observer (FSM/pipeline/writers), rules (6 стоп-правил), tasks (unified outbox), meta_api (client+10 mutations+upload), telegram, dashboard (snapshot+metric_aggregation), adset_pro (MCP-клиент), ai_assistant, enable_reco, crypto, pubsub, control (pubsub_listener) — **все ✅**.

---

## 3. Frontend

| Что | Стек | Состояние |
|---|---|---|
| `frontend/` (старый) | React 19 + JSX + Tailwind 3 | ✅ 7 страниц работают со свежим API (smoke-тест прошёл) |
| `frontend-v2/` (новый) | TS + Vite + Tailwind 4 + TanStack + Zustand | 🔄 foundation + **Dashboard/Ads/Offers/History/Settings готовы** (5/6); placeholder: Drafts |
| `frontend-mini/` (TMA) | React + JSX | ⏸ не трогали, дублирует логику старого |

Дизайн v2: editorial-monochrome (см. `docs/frontend_v2_design.md` + мокапы). Dark-only, desktop 1280+.

---

## 4. Что НЕ работает / работает частично

| Проблема | Эффект | Задача |
|---|---|---|
| WS-каналы `alert_created`/`task_changed`/`health_updated` не публикуются воркерами | Real-time отдаёт только scan-события | BL-1 |
| frontend-v2: 5 страниц placeholder | Нет полного UI на новом фронте | BL-2..6 |
| Marketing API latency не замерен | Неизвестна реальная задержка API | BL-7 (нужен live-аккаунт) |
| dev-only tools endpoints (`/tools/*`) без prod-блокировки | В проде доступны | BL-9 |
| creator_worker/recorder не активны | Авто-создание кампаний через Vision выкл | по запросу |

---

## 5. Low / tech-debt (из `docs/backend_code_quality_audit.md`)

Костяк качественный, осталось косметическое:
- Копипаста: JOIN-цепочка `FbAd→FbAdset→FbCampaign→Offer` ×7, `_task_row_to_out` ×4, CSV-status-expand ×2 → вынести в helpers.
- `history.py` 692 строки (>500 design-rule) → split по endpoint'ам.
- `OfferOut`: поля затипизированы литералом `None` вместо `str | None`.
- `upload.py`: 6× `# type: ignore` на приватный `_stub` → публичный аксессор.
- `offers.py`: unique-конфликт через `str(exc)` вместо SQLSTATE 23505.
- Frontend↔v2 shape: ряд полей отдаётся `null` (Offer.country_code, OfferRule JSONB и др.) — нужна миграция если фронт начнёт требовать.

---

## 6. BACKLOG — готовые к заведению таски

> Формат: `BL-N · [приоритет] · scope · оценка`. P1=функционально важно, P2=tech-debt, P3=отложено/ждёт внешнего.

- ~~**BL-1** WS real-time publish~~ ✅ done (caller-side, 9 тестов).
- ~~**BL-2** frontend-v2 Ads~~ ✅ done (таблица+фильтры+bulk+drawer, 6 тестов).
- ~~**BL-3** frontend-v2 Offers~~ ✅ done (CRUD+rules, 9 тестов).
- ~~**BL-4** frontend-v2 History~~ ✅ done (период+summary+timeline, 9 тестов).
- ~~**BL-5** frontend-v2 Settings~~ ✅ done (4 tabs, 6 тестов).
- **BL-6 · P1 · frontend-v2 Drafts** — подтверждение AI-mutations (ACL). Последняя страница. ~1.5ч.
- **BL-7 · P3 · latency-замер Marketing API** — на активном кабинете (блок: live-аккаунт).
- **BL-8 · P3 · AdSet.pro Волна 4** — tracker_aggregate per (ad,country,day) + outgoing postback + key rotation.
- **BL-9 · P2 · prod-блок dev-tools** — env-флаг `DEV_TOOLS_ENABLED`, иначе 403 на `/tools/*`.
- **BL-10 · P2 · вынос копипасты** — helpers для JOIN/task_serializer/status-expand/decimal.
- **BL-11 · P2 · split history.py** — 692 строки → по endpoint'ам.
- **BL-12 · P2 · frontend↔v2 shape migration** — Alembic под недостающие поля Offer/OfferRule/AdMetrics.
- **BL-13 · P3 · backtest** — `scripts/backtest_rules.py`, после накопления данных (MEMORY 2026-06-08).
- **BL-14 · P3 · light mode + frontend-mini** — вторая тема + прокачка TMA.

**Рекомендованный порядок:** BL-1 (быстро оживит WS) → BL-2..6 (frontend-v2 страницы, можно параллелить sonnet'ами) → BL-10/11/9 (tech-debt) → BL-7/8/12/13/14 (по мере надобности/внешних блокеров).

---

## 7. Документы-ссылки

| Файл | Что |
|---|---|
| `META_INTEGRATION_PLAN.md` | Master-план этапов 0-9 |
| `CLAUDE.md` | Архитектура, design-rules, история раундов |
| `DB_REDESIGN.md` | v2-схема БД |
| `docs/frontend_compatibility_audit.md` | 70 endpoints × v2-модели |
| `docs/frontend_v2_design.md` + `docs/frontend_v2_mockups/` | Дизайн нового фронта |
| `docs/backend_test_audit_round_8.md` | Аудит покрытия (Round 9 закрыл) |
| `docs/backend_code_quality_audit.md` | Аудит качества (Round 10 закрыл) |
| `docs/test_quality_audit.md` | Почему пропустили CRIT (Round 11 закрыл) |
