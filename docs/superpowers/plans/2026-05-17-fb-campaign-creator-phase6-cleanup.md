# Phase 6 — Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** Удалить старый `core/campaign_creator/`, `core/campaign_recorder/` и связанные API-routes/tests/tools. Убедиться что весь функционал переехал в новый creator (Phase 1-5).

**Architecture:** Чистое удаление. Старые миграции БД не трогаем (они уже применены в прод-окружениях).

---

## File Structure

- Delete: `core/campaign_creator/` (директория целиком)
- Delete: `core/campaign_recorder/` (директория целиком)
- Delete: `apps/api/routers/campaign_recorder.py`
- Delete: `tools/dry_run_creator.py`
- Delete: `tools/timing_percentiles.py`
- Delete: `tests/unit/test_campaign_recorder.py`
- Delete: `tests/unit/test_creo_scanner.py`
- Delete: `tests/unit/test_spec_builder_e2e.py`
- Modify: `apps/api/main.py` — убрать `include_router(campaign_recorder.router)`
- Modify: `frontend/src/pages/ScriptsPage.jsx` — убрать ссылки на старый recorder/creator если есть.
- Modify: `frontend/src/api.js` — удалить старые функции campaign_recorder/creator если есть.

---

### Task 1: Найти все импорты старых модулей

- [ ] **Step 1: Grep**

```bash
grep -rn "core\.campaign_creator\|core\.campaign_recorder\|campaign_recorder" \
  apps core tests tools frontend run.sh Makefile --include='*.py' --include='*.jsx' --include='*.js'
```

- [ ] **Step 2:** Составить список файлов, требующих правки. Сохранить в локальной заметке.

- [ ] **Step 3:** Убедиться что новый creator (Phase 4/5) уже покрывает все use-cases из старого кода. Если найдена недостающая фича — STOP и вернуться в Phase 3/4 для дописки.

---

### Task 2: Удалить API-роутер campaign_recorder

- [ ] **Step 1:**

```bash
rm apps/api/routers/campaign_recorder.py
```

- [ ] **Step 2:** Убрать импорт и `include_router` из `apps/api/main.py`.

- [ ] **Step 3:** `pytest tests/integration/ -x` → не должно быть импорт-ошибок.

- [ ] **Step 4: Commit**

```bash
git add apps/api/main.py apps/api/routers/campaign_recorder.py
git commit -m "chore(creator): remove campaign_recorder API router"
```

---

### Task 3: Удалить старые tests

- [ ] **Step 1:**

```bash
rm tests/unit/test_campaign_recorder.py
rm tests/unit/test_creo_scanner.py
rm tests/unit/test_spec_builder_e2e.py
```

- [ ] **Step 2: Run** `pytest tests/ -x` → all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "chore(creator): remove legacy tests"
```

---

### Task 4: Удалить tools

- [ ] **Step 1:**

```bash
rm tools/dry_run_creator.py
rm tools/timing_percentiles.py
```

- [ ] **Step 2:** Проверить `Makefile` и `run.sh` — убрать упоминания если есть.

- [ ] **Step 3: Commit**

```bash
git add tools Makefile run.sh
git commit -m "chore(creator): remove legacy CLI tools"
```

---

### Task 5: Удалить core/campaign_creator и core/campaign_recorder

- [ ] **Step 1:**

```bash
rm -rf core/campaign_creator core/campaign_recorder
```

- [ ] **Step 2:** Поправить любые оставшиеся импорты (из Task 1 списка).

- [ ] **Step 3: Run** `pytest tests/ -x` и `ruff check .` → all PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(creator): remove legacy core/campaign_creator and core/campaign_recorder"
```

---

### Task 6: Frontend cleanup

- [ ] **Step 1:** В `frontend/src/pages/ScriptsPage.jsx` и `frontend/src/api.js` найти упоминания старого creator/recorder. Заменить на ссылки на `/creator` (новый) или удалить.

- [ ] **Step 2:** `cd frontend && npm run build` → success.

- [ ] **Step 3: Commit**

```bash
git add frontend/
git commit -m "chore(creator-ui): remove legacy creator/recorder references"
```

---

### Task 7: Final acceptance

- [ ] `pytest tests/ -v` → all PASS.
- [ ] `cd services/browser-agent && npm test && npm run build` → all PASS.
- [ ] `cd frontend && npm run build` → success.
- [ ] `ruff check .` → clean.
- [ ] `grep -rn "campaign_creator\|campaign_recorder" apps core tests tools frontend` → пусто (кроме миграций).

---

## Acceptance criteria (из спеки, раздел 13)

- [ ] План создаёт adset из шаблона + duplicate, не падая на конфликте имён.
- [ ] Idempotency: повторный запуск того же плана на готовой кампании — все шаги skipped.
- [ ] Локализация: тот же план работает на RU и EN UI без правок.
- [ ] Recorder: записанная сессия отличается от предыдущего плана только если появились новые шаги (UI изменился).
- [ ] Чекпоинт: при появлении `/checkpoint/` worker останавливается, шлёт Telegram и помечает run как `requires_attention`.

---

## Финал

После прохождения acceptance — финальный PR в main с описанием миграции и changelog.
