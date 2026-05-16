# План: recorder как design-time источник спецификации UI

**Дата:** 2026-05-16
**Автор:** Mark + Claude (после форензик-анализа recorder↔runner)
**Контекст:** 4 дня боли с автоматизацией FB campaign creator. Текущий dry_run падает на `set_geo` после успешных 7 шагов. Корневой диагноз: recorder и runner не связаны архитектурно, а попытки чинить шаги вслепую без актуальной DOM-карты не дают сходимости.

---

## Модель работы (важно зафиксировать)

Recorder — это **спецификация UI на момент записи**, а не реплей. Поток:

1. Человек записывает ручной прогон → recorder отдаёт markdown-отчёт.
2. Разработчик читает отчёт: «вот тут стабильный anchor (role+name), вот тут изменяющаяся константа, вот тут параметр, который тянется из CampaignSpec».
3. Разработчик правит код шага в `steps/<step>.py` — переносит anchor'ы и константы. Никакой runtime-связи с recorder нет.
4. PlanRunner исполняет шаги. Если FB редизайнит UI — снова запись → отчёт → точечная правка.

Из этого следуют ограничения плана:
- **Никакого dynamic resolver, читающего `recordings/*.json` в runtime.** Recorder не зависимость, а инструмент.
- **Runner остаётся чисто статическим**, но получает робастный контракт шагов и единый локатор-движок (`human_click_label`/`human_pick_option`).
- **Recorder улучшается** для того, чтобы отчёт реально давал инженеру всё нужное (особенно для дропдаунов FB, которые сейчас в markdown теряются).

---

## Что НЕ трогаем

- `migrations/*` — без необходимости.
- `docker-compose.yml`, `run.sh`, `Makefile`.
- `frontend/` (кроме случая, если придётся подчистить `ScriptsPage` под новый формат отчёта — решим по ходу).
- Воркеры: `observer_worker`, `disable_worker`, `enable_worker`, `enable_recommendation_worker`, `telegram_poller`.
- БД-схема (кроме того, что и так уже модифицировано до этого плана).
- EN-локализация UI — не нужна. FB-аккаунт жёстко RU.

## Запрещено в ходе исполнения

- Любые commit/push/PR без явной команды.
- Запуск миграций.
- Добавление новых зависимостей без согласования.
- Правки за пределами файлов, перечисленных ниже.

---

## Порядок работ

**C2 → C1+C3 → R1+R2+R3 → D1.**

Логика: сначала закрываем текущую боль (`set_geo`), что даёт быструю проверку гипотез на живом dry-run. Потом фиксируем архитектурный контракт и реорганизуем константы. Потом улучшаем recorder, чтобы следующая итерация шагов шла по новой модели. В конце пишем регламент.

---

## Блок C2 — Починить set_geo через humanizer (1-я задача)

**Цель:** пройти `set_geo` в текущем dry-run без падения, без дальнейших правок recorder'а.

**Файлы:** `core/campaign_creator/steps/set_geo.py` (только этот).

**Что делаем:**
1. Открытие секции «Местоположения»:
   - убрать `get_by_text("Местоположения", exact=False).first` — слишком широкий локатор.
   - использовать `human_click_label("Местоположения", roles=("button","heading","link"))` через `humanizer.py`. Если humanizer возвращает фейл — fallback в `aria-label`-варианте.
   - убрать `page.mouse.wheel(0, 600)` по странице — humanizer уже умеет скроллить в нужном контейнере через `scroll_into_view_if_needed`.
2. Поиск страны:
   - локатор поля: оставить `input[placeholder="Поиск местоположений"]` (он рабочий), но обернуть в `wait_for(state="visible", timeout=4000)` с понятным сообщением.
   - выбор опции: убрать ставку на `[role="option"]` (probe пользователя подтвердил, что её больше нет). Использовать `human_pick_option(name)` который сам перебирает `listitem`/`gridcell`/`menuitem`/`button`/text+`:has-text("Страна/регион")`.
3. Удаление лишних чипов:
   - убрать JS-walk по `li` с `innerText.startsWith("Удалить")`.
   - использовать `page.locator('[aria-label^="Удалить:"]')` — у чипов FB реально такой aria-label (упомянуто в существующем docstring `set_geo.py:7`).

**Acceptance:**
- `python tools/dry_run_creator.py` с теми же параметрами, что в `logs/dry_run_20260516_133745.log`, доходит до `set_geo` и проходит его.
- Лог следующего шага (`set_age`) появляется в выводе.

**Тесты:** unit-тест `tests/unit/test_set_geo.py` (если такого нет — создать) с моком Playwright `Page`, проверяющий, что вызовы идут через `human_click_label`/`human_pick_option`, а не через прямые `page.locator(...).wait_for(8000)`.

**Risk-флаг:** перед dry-run **напомнить пользователю удалить старую кампанию** (по сохранённому feedback-memory).

---

## Блок C1 — Контракт шага через `BaseStep`

**Цель:** убрать разнобой в том, как шаги проверяют свою готовность и валидируют результат.

**Файлы:**
- `core/campaign_creator/steps/base.py` (изменение)
- `core/campaign_creator/plan_runner.py` (изменение)
- все `core/campaign_creator/steps/*.py` (минимальная адаптация: только те, у кого уже сейчас есть локальные pre-check'и — выносим их в `pre_check`)

**Что делаем:**
1. В `BaseStep` добавить **опциональные** хуки:
   ```python
   async def pre_check(self, page, ctx, params) -> None:  # default: no-op
   async def verify(self, page, ctx, params) -> None:     # default: no-op
   ```
   Контракт `pre_check`: убедиться, что нужный drawer открыт / уровень (campaign/adset/ad) активен. Если нет — поднять исключение со внятным сообщением. **Без авто-исправления.**
   Контракт `verify`: после `execute` подтвердить, что результат на DOM соответствует ожиданиям (чип появился, dropdown закрылся и т. п.).
2. `PlanRunner.execute_plan` вызывает `pre_check` → `execute` → `verify`. Если что-то падает — логирует с указанием стадии, прерывает план (как сейчас).
3. **Не добавлять** глобальный retry на уровне runner'а — это решается отдельно, тут не нужно.
4. Мигрировать `set_geo._open_locations_block` → `set_geo.pre_check` (проверка, что drawer adset открыт). Остальные шаги пока не трогаем — только добавляем заглушки.

**Acceptance:**
- Существующие unit-тесты (`tests/unit/test_step_context.py`, `test_plan_builder.py`) проходят без правок.
- Новый тест `tests/unit/test_plan_runner_lifecycle.py`: проверяет, что для шага с `pre_check` сначала вызывается он, потом `execute`, потом `verify`.

---

## Блок C3 — Реорганизация селекторов под архитектурно правильную схему

**Цель:** убрать единственный `selectors.py`-словарь, который растёт неконтролируемо, в пользу масштабируемой структуры.

**Файлы:**
- Удалить (после миграции): `core/campaign_creator/selectors.py`
- Создать: `core/campaign_creator/ui/__init__.py`
- Создать: `core/campaign_creator/ui/anchors.py` — глобальные anchor-константы (текст «Далее», «Создать новую кампанию», структурные элементы навигации).
- В каждом `steps/<step>.py` добавить локальный модуль-сосед `steps/_constants/<step>.py` с константами именно этого шага (placeholder поля поиска, aria-label чипа, label секции). Если файл шага простой — можно держать константы прямо в его начале как `_LABEL_LOCATIONS = "Местоположения"` блоком, по согласованию по ходу работы.

**Что делаем:**
1. Все RU-строки, привязанные к конкретному шагу, переезжают рядом со своим шагом.
2. `ui/anchors.py` содержит только то, что используется в ≥2 шагах (например, кнопка «Далее»).
3. Удаление `selectors.py` — только после того, как ни один импорт его не использует. Сначала переезд → grep → удаление.

**Acceptance:**
- `ruff check .` чисто.
- `grep -rn "from core.campaign_creator.selectors" core/ apps/ tests/` пусто.
- Существующие тесты проходят.

**Risk-флаг:** переезд констант может задеть тесты, которые их импортируют. Перед удалением `selectors.py` прогнать `pytest tests/unit/ -x`.

---

## Блок R1 — Денойзер: не терять выбор опций дропдауна

**Цель:** в markdown-отчёте после ввода «Кения» появлялось действие «выбрана опция Кения», а не только `fill`.

**Файлы:** `core/campaign_recorder/analyzer.py`.

**Что делаем:**
1. В `denoise` ветка click-группы (`analyzer.py:87-124`): если в группе есть `pointerdown`/`mousedown`, но нет `click`, и при этом исходник имеет осмысленный `selector_candidates` ИЛИ текст ИЛИ widget — всё равно эмитить `UserAction(kind="click", ...)` с пометкой `value="(mousedown-only)"` в `widget`.
2. Окно `_CLICK_WINDOW_S` расширить до 0.35 (FB иногда задерживает событие, особенно в порталах).
3. Условие «xpath совпадает» (`analyzer.py:95`) ослабить: если xpath отличается, но `composedPath`/`widget.role` указывает на один и тот же контейнер — считать одной группой. (Реализуется через сравнение `widget.is_self` или общего ancestor-маркера; уточнить по фактической структуре event'а.)

**Acceptance:**
- Новый unit-тест `tests/unit/test_analyzer_dropdown.py`: фикстура из реальной записи `recordings/20260516_130926_KE_CR2.json` (события 11000-11045) → денойзер выдаёт `UserAction(kind="click", label содержит "Кения")`.
- Существующие тесты денойза проходят.

---

## Блок R2 — Качественные селекторы для опций дропдауна

**Цель:** в `selector_candidates` для опции дропдауна были устойчивые варианты, а не только `xpath=/html/body/div[1]/...`.

**Файлы:** `core/campaign_recorder/event_injector.py`.

**Что делаем:**
1. В `getAccessibleName` / `shortTxt` (`event_injector.py:269,322`) добавить ветку для элементов внутри `[role="listbox"]`/`[role="menu"]`/`[role="grid"]`: брать `innerText.split('\n')[0].trim()` как имя опции, даже если общий текст >80 символов.
2. В `selectorCandidates` (`:315-370`) для таких элементов добавлять относительный селектор `[role="listbox"] >> text="Кения"` (или scoped через ближайший listbox-ancestor).
3. Фильтр стабильности (`isStableClass`/проверка `data-auto-logging-id`): отметить `data-auto-logging-id` как НЕ-стабильный (regex `^[a-f0-9]{6,12}$` или хеш-паттерн) и убрать из топа кандидатов. Оставить как low-priority fallback с пометкой `(volatile)`.

**Acceptance:**
- Unit-тест `tests/unit/test_selector_candidates.py` (если такого нет — создать) с моковым DOM-узлом из listbox → топ-кандидат это `[role="listbox"] >> text="..."`, а не xpath.
- Live-проверка: новая запись клика в дропдаун дает в JSON хотя бы один не-xpath кандидат для опции.

---

## Блок R3 — Markdown-отчёт с разделением «стабильное / нестабильное»

**Цель:** разработчик, читая отчёт, сразу понимает, какой селектор переносить в код, а какой игнорировать.

**Файлы:** `core/campaign_recorder/markdown_report.py`.

**Что делаем:**
1. В `_action_block` (`markdown_report.py:57`) для блока «Селекторы» разделить вывод на две группы:
   - **Стабильные:** role-based, aria-label, placeholder, data-testid.
   - **Нестабильные (fallback):** xpath, classes, `data-auto-logging-id`.
2. Для каждого `click` внутри открытого listbox добавлять подблок «Текст опции:» и «Контейнер: `[role=listbox]`».
3. `opened_after` уже есть — оставить, но добавить лимит «первые 6 + N more».

**Acceptance:**
- Unit-тест `tests/unit/test_markdown_report_stability.py`: на UserAction с разнотипными селекторами markdown содержит отдельные секции «Стабильные» и «Нестабильные».
- Существующий `tests/unit/test_markdown_report.py` (если есть) — адаптировать.

---

## Блок D1 — Регламент «как добавить или починить шаг»

**Цель:** убрать необходимость каждый раз заново вспоминать процесс.

**Файлы:** `docs/superpowers/specs/how-to-add-or-fix-step.md` (новый).

**Что пишем:**
1. Записать руками новый/сломанный шаг в Ads Manager через recorder.
2. Получить markdown-отчёт по записи.
3. Открыть нужный `steps/<step>.py` (или создать новый по шаблону `_template.py`).
4. Из отчёта вытащить:
   - имя секции/анкера (раздел «Стабильные»),
   - placeholder поля,
   - aria-label кнопки удаления / подтверждения.
5. Перенести как константы в `steps/_constants/<step>.py` (или в шапку файла).
6. Реализовать через `human_click_label` / `human_pick_option`. Прямые `page.locator(...).wait_for(...)` допускаются только с явным комментарием «почему здесь не подходит humanizer».
7. Прогнать `pytest tests/unit/test_<step>.py -x`.
8. Перед dry-run **удалить старую кампанию вручную** (см. memory).
9. Прогнать `tools/dry_run_creator.py` с точечным флагом по шагу (если такого флага нет — это отдельная задача, не в этом плане).

---

## Сводная таблица acceptance

| Блок | Главный артефакт | Тест |
|---|---|---|
| C2 | dry-run проходит set_geo | `tests/unit/test_set_geo.py` |
| C1 | `BaseStep.pre_check/verify`, `PlanRunner` зовёт их | `tests/unit/test_plan_runner_lifecycle.py` |
| C3 | `selectors.py` удалён, константы по шагам | существующие тесты + grep |
| R1 | mousedown-only клик попадает в action-список | `tests/unit/test_analyzer_dropdown.py` |
| R2 | топ-кандидат для опции listbox — не xpath | `tests/unit/test_selector_candidates.py` |
| R3 | markdown делит селекторы на стабильные/нестабильные | `tests/unit/test_markdown_report_stability.py` |
| D1 | `how-to-add-or-fix-step.md` существует | — |

---

## Чекпойнты

- **После C2** — показать пользователю лог нового dry-run перед переходом к C1.
- **После C3** — показать `git diff` структуры файлов перед переходом к R1.
- **После R3** — показать сэмпл нового markdown перед написанием D1.

На каждом чекпойнте: пауза, ответ пользователя, продолжение.
