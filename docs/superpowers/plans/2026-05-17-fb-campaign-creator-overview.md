# FB Campaign Creator v2 — Implementation Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans` для каждой фазы. Все шаги внутри файлов фаз идут с `- [ ]` чекбоксами.

**Goal:** Переписать движок создания FB-кампаний с нуля как браузер-резидентный TS-агент с декларативным планом, идемпотентными шагами, антибот-гуманизацией и поддержкой локализации UI.

**Spec:** [`docs/superpowers/specs/2026-05-17-fb-campaign-creator-design.md`](../specs/2026-05-17-fb-campaign-creator-design.md)

**Architecture:** Browser-side TS bundle (`services/browser-agent/src/creator/`), инжектируемый через Playwright `addInitScript`. Python (`apps/creator_worker`, `apps/creator_recorder`) — тонкий оркестратор. Связь браузер↔Python через `expose_binding('fbAgentEmit', ...)`. Хранилище — Postgres (Plan, PlanRun).

**Tech Stack:** TypeScript 5.7, Playwright (CDP), Python 3.12 (FastAPI, SQLAlchemy 2.x async, Alembic), React 19 (Vite). Тестирование: `node --test` для TS, `pytest` для Python.

---

## Phase order (each is self-contained working software)

| # | File | Что появляется в конце фазы |
|---|------|------------------------------|
| 1 | [`2026-05-17-fb-campaign-creator-phase1-infra.md`](2026-05-17-fb-campaign-creator-phase1-infra.md) | БД-модели Plan/PlanRun + Alembic-миграция, `core/creator_bridge/` (bundle loader, runner), пустой TS-каркас `creator/index.ts` с `window.__fbAgent` |
| 2 | [`2026-05-17-fb-campaign-creator-phase2-core.md`](2026-05-17-fb-campaign-creator-phase2-core.md) | `humanizer.ts` (CDP-нативные клик/набор/скролл), `fiber.ts` (чтение React fiber), `locator.ts` (structural lookup), `registry.ts`, `executor.ts`, `steps/base.ts` |
| 3 | [`2026-05-17-fb-campaign-creator-phase3-steps.md`](2026-05-17-fb-campaign-creator-phase3-steps.md) | Все enum'ы (`enums/*.ts`) + все ~25 шагов (`steps/*.ts`) с детектом/idempotency/execute и юнит-тестами |
| 4 | [`2026-05-17-fb-campaign-creator-phase4-recorder-executor.md`](2026-05-17-fb-campaign-creator-phase4-recorder-executor.md) | `recorder.ts` (match-by-step), `apps/creator_recorder/` CLI, `apps/creator_worker/` (Vision + CDP + page.evaluate), e2e-тест на staging-форме |
| 5 | [`2026-05-17-fb-campaign-creator-phase5-api-frontend.md`](2026-05-17-fb-campaign-creator-phase5-api-frontend.md) | `apps/api/routers/creator.py` (CRUD plans, POST /plans/run, GET /enums/<name>), `frontend/src/pages/CreatorPage.jsx` |
| 6 | [`2026-05-17-fb-campaign-creator-phase6-cleanup.md`](2026-05-17-fb-campaign-creator-phase6-cleanup.md) | Удаление `core/campaign_creator/`, `core/campaign_recorder/`, старых routes/tests/tools |

## Cross-cutting rules (применяются во всех фазах)

- **TDD:** failing test → minimal implementation → passing test → commit. Каждая бите-задача — 2-5 минут.
- **Идемпотентность встроена в base step** (Phase 2). Любой новый шаг наследует `BaseStep` и переопределяет только `detect`/`execute`/`match`.
- **Локаторы — структурные** (data-testid → fiber-role → aria → нормализованный текст fallback). Никакого поиска по точным лейблам.
- **Все клики/ввод — через `humanizer.ts`.** Прямой `el.click()`/`el.value=` запрещён (lint-rule в Phase 2).
- **Комментарии — по-русски** только если действительно нужны. Сообщения логов/ошибок — по-русски (CLAUDE.md).
- **Старый код не правим** — он будет удалён в Phase 6. Изменения только в новых путях.

## Завершение

После Phase 6 выполнить acceptance criteria из спеки (раздел 13), затем создать финальный PR в main.
