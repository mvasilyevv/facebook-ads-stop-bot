# FB Campaign Creator v2 — Design Spec

**Дата:** 2026-05-17
**Статус:** Draft → ожидает approval
**Заменяет:** `core/campaign_creator/`, `core/campaign_recorder/`

---

## 1. Цель и мотивация

Текущий движок создания FB-кампаний (`core/campaign_creator/`) не работает. Подход «записать клики → проиграть клики» оказался хрупким: любая разница в UI (другая страна, другой тип конверсии, другой язык интерфейса) ломает воспроизведение. Шаги жёстко завязаны на координаты и тексты лейблов из конкретной записи.

Цель v2 — переписать движок с нуля так, чтобы:
- шаги работали **адаптивно** (читают состояние UI перед действием, идемпотентны),
- план был **декларативным** (намерения, а не клики),
- одна запись плана покрывала **любую страну/оффер/креатив** через переменные,
- автоматизация была **максимально трастовой** (минимальный риск бана),
- добавление нового шага = один новый файл.

## 2. Архитектурное решение

Движок целиком переезжает в браузер (TypeScript, `services/browser-agent/`). Python остаётся как тонкий оркестратор: поднимает Vision-профиль, инжектит TS-бандл, передаёт план, читает прогресс из БД.

```
┌─────────────────── Python ──────────────────┐    ┌──── Браузер (Chrome via CDP) ────┐
│ apps/api               apps/creator_worker  │    │ window.__fbAgent                  │
│   POST /plans/run        polls Plan queue   │    │   ├─ registry: Map<name, Step>   │
│   GET /enums/<name>      injects bundle     │    │   ├─ executor.run(plan)          │
│   CRUD plans                                │◄──►│   ├─ recorder.start/stop         │
│ apps/creator_recorder  CLI: capture session │    │   ├─ state: fiber + DOM + XHR    │
│ core/creator_bridge/   ↕ CDP                │    │   └─ steps/* (set_geo, ...)      │
│   addInitScript                             │    └───────────────────────────────────┘
│   exposeBinding('fbAgentEmit')              │
│ core/models: Plan, PlanRun                  │
└─────────────────────────────────────────────┘
```

### Почему браузер, а не Python+Playwright

- Доступ к React fiber (`__reactProps$*`, `__reactFiber$*`) → читаем реальное состояние формы, а не угадываем по DOM.
- MutationObserver и fetch-proxy работают без CDP round-trip → шаги быстрее и без race conditions.
- Перехват GraphQL-мутаций FB как best-effort подтверждение реальных сохранений.
- Существующий `services/browser-agent/` уже стабильнее питон-парсера в observer.

## 3. Контракт Step

```ts
interface Step<I = unknown, O = unknown> {
  name: string;                                                    // 'set_geo', 'set_conversion_location', ...
  match?(ev: RecordedEvent, dom: DomState): boolean;               // используется recorder'ом
  detect(): StepState;                                              // что сейчас в DOM/fiber
  isSatisfied(state: StepState, input: I): boolean;                 // идемпотентность
  execute(state: StepState, input: I): Promise<O>;                  // выполнить действие
}
```

**Правила:**
- Каждый шаг — отдельный файл `services/browser-agent/src/creator/steps/<name>.ts`.
- Регистрация через `registerStep()`, реестр строится при загрузке бандла.
- `detect()` ищет блок **структурно** (data-testid → fiber-role → aria → последний fallback по нормализованному тексту), не по точному лейблу.
- `isSatisfied()` всегда вызывается перед `execute()` — если значение уже выставлено, шаг no-op.
- `execute()` использует только `humanizer.ts` (CDP-нативные события), никаких `el.click()` / `el.value=`.

## 4. Антибот / траст (критическое требование)

Создание кампании автоматизацией — высокий риск бана, если паттерны действий отличаются от живого пользователя. Меры:

- **Нативные события через CDP** (расширение существующего `services/browser-agent/src/humanizer.ts`):
  - `humanClick(el)`: курсор по кривой Безье + hover 80-250мс + `Input.dispatchMouseEvent` через `page.mouse`.
  - `humanType(el, text)`: focus → 40-180мс/символ + случайные паузы 200-800мс каждые 3-8 символов + редкие опечатки с backspace (5%).
  - `humanScroll(el)`: wheel-события с переменной velocity, не `scrollIntoView()`.
- **Минимальный stealth** — оставляем существующий `services/browser-agent/src/stealth.ts`, не расширяем.
- **Чтение fiber только на чтение.** Запись в реактовый стейт запрещена. Изменения — только через DOM-события.
- **Idle-паузы** между шагами 600-2500мс, между сценами (кампания → adset → ad) 3-8с — из фиксированных распределений в humanizer, не из записи.
- **GraphQL respect** — best-effort. Primary сигнал готовности — DOM/fiber, GraphQL подтверждает мутацию если уловили (мутации FB обфусцированы и батчатся, надёжность ~70%).
- **Один профиль = один аккаунт.** Профиль приходит из `PlanRun.profile_id`, переключения нет.
- **Total time ≥ 3-6 минут** на полную кампанию. Если executor успевает быстрее — добавляем паузы.
- **Реактивная остановка при checkpoint/captcha**: executor подписан на навигацию, при редиректе на `/checkpoint/` или появлении captcha-iframe — останавливается, эмитит `checkpoint_detected`, Python переводит профиль в `requires_attention` и шлёт Telegram-алерт.

## 5. Канонические значения (enum + labelMap)

Все шаги с выбором из списка (conversion_location, pixel_event, optimization_goal, attribution, currency, objective, …) работают по одной механике:

```ts
// services/browser-agent/src/creator/enums/conversion-location.ts
export enum ConversionLocation {
  WEBSITE = 'WEBSITE',
  WEBSITE_AND_CALLS = 'WEBSITE_AND_CALLS',
  APP = 'APP',
  MESSENGER = 'MESSENGER',
}

export const conversionLocationLabels: LabelMap<ConversionLocation> = {
  WEBSITE:           { ru: ['Сайт', 'Веб-сайт'], en: ['Website', 'Web site'] },
  WEBSITE_AND_CALLS: { ru: ['Сайт и звонки'],    en: ['Website and calls'] },
  APP:               { ru: ['Приложение'],        en: ['App'] },
  MESSENGER:         { ru: ['Messenger'],         en: ['Messenger'] },
};
```

- Шаг принимает enum, не строку.
- `detect()` читает выбранный лейбл → нормализует через labelMap → возвращает enum.
- `isSatisfied(state, input)` = `state.current === input.value`.
- `execute()` ищет опцию в дропдауне через labelMap (все языки/синонимы).
- API `GET /enums/<name>` отдаёт список enum'ов → frontend строит селекты на форме запуска.
- Поддержка локализации UI FB (RU/EN) — встроена через labelMap, без правок шагов.

**Если FB добавил новое значение** (отсутствует в enum):
- При записи recorder пишет `{value: 'UNKNOWN', rawLabel: '...'}` — запись сохраняется.
- При replay executor падает с `UnimplementedEnumValueError('add X to ConversionLocation')` — Telegram-алерт.
- Фикс — добавление строчки в enum + labelMap в одном TS-файле.

## 6. Recorder

**Запуск:** отдельная команда `python -m apps.creator_recorder --profile=<vision_id> --name="DRC v1"`.

**Когда используется:** только когда FB изменил UI (новое поле, переименован блок, добавился шаг визарда). Не для новой страны/оффера/креатива — это переменные плана.

**Поток:**
1. Python поднимает Vision-профиль, цепляется через CDP, инжектит бандл, вызывает `window.__fbAgent.startRecording()`.
2. Юзер вручную создаёт кампанию в Ads Manager.
3. TS-recorder подписан на `click`, `change`, `input` (debounced 800мс) в capture-фазе. Для каждого события прогоняет `step.match(ev, dom)` по всему реестру.
4. Матч → шлёт через `fbAgentEmit` в Python `{step: 'set_geo', input: {countries: ['DE']}}`.
5. Нет матча → `{step: 'unknown', raw: {selector, text, value}}`.
6. Python пишет события в `Plan.steps`.
7. Юзер заканчивает, Ctrl+C → `stopRecording()`.

**Дедуп:** серии input-событий схлопываются в одно через debounce 800мс.

**Unknown шаги:** записываются как есть. При первом replay executor падает с понятной ошибкой "реализуй шаг X". Это и есть момент, когда разработчик дописывает новый шаг.

## 7. Executor

- `creator_worker` поллит `PlanRun` со статусом `queued` (SELECT FOR UPDATE SKIP LOCKED).
- Поднимает Vision-профиль из `PlanRun.profile_id`, открывает Ads Manager.
- Инжектит бандл через `addInitScript`, биндит `fbAgentEmit`.
- Подставляет `PlanRun.variables` в `Plan.steps` (шаблонизация `{{geo}}`, `{{offer.code}}`, …).
- Вызывает `await page.evaluate("window.__fbAgent.run(plan)")`.
- Каждый шаг: `detect()` → `isSatisfied()` (если да — skip + emit `step_skipped`) → `execute()` → emit `step_finished`.
- При ошибке — emit `step_failed`, статус `PlanRun.status = failed`, Telegram-алерт.
- При `checkpoint_detected` — статус `requires_attention`, профиль помечается в БД, Telegram-алерт.

## 8. Структура файлов

```
services/browser-agent/src/
├── creator/
│   ├── index.ts                       # window.__fbAgent = { run, startRecording, stopRecording }
│   ├── executor.ts
│   ├── recorder.ts
│   ├── registry.ts                    # Map<name, Step>, registerStep()
│   ├── humanizer.ts                   # humanClick, humanType, humanScroll, idle-паузы
│   ├── fiber.ts                       # getFiber, readProps
│   ├── locator.ts                     # findBlockByRole, findBlockByTestid, fallback
│   ├── enums/                         # ConversionLocation, PixelEvent, OptimizationGoal, ...
│   ├── steps/
│   │   ├── index.ts                   # импорт + registerStep всех шагов
│   │   ├── base.ts                    # абстрактный Step с общей idempotency-логикой
│   │   ├── set_geo.ts
│   │   ├── set_age.ts
│   │   ├── set_conversion_location.ts
│   │   ├── set_pixel_event.ts
│   │   ├── set_optimization_goal.ts
│   │   ├── set_attribution.ts
│   │   ├── set_budget.ts
│   │   ├── set_schedule_start.ts
│   │   ├── set_cta.ts
│   │   ├── set_tracking_url.ts
│   │   ├── fill_texts.ts
│   │   ├── upload_creatives.ts
│   │   ├── create_campaign.ts
│   │   ├── create_adset.ts
│   │   ├── duplicate_adset.ts
│   │   ├── duplicate_ad.ts
│   │   ├── rename_adset.ts
│   │   ├── rename_ad.ts
│   │   ├── reattach_creative.ts
│   │   ├── switch_to_adset.ts
│   │   ├── click_next.ts
│   │   ├── save_draft.ts
│   │   └── unknown.ts
│   └── types.ts                       # Step, StepState, PlanContext, RecordedEvent

apps/
├── creator_worker/
│   ├── main.py                        # entrypoint
│   └── runner.py                      # Vision → CDP → page.evaluate
├── creator_recorder/
│   └── main.py                        # CLI
└── api/routers/creator.py             # POST /plans/run, GET /enums/<name>, CRUD plans

core/
└── creator_bridge/
    ├── bundle.py                      # читает services/browser-agent/dist/fb-agent.js
    ├── runner.py                      # инжект через addInitScript, биндинг fbAgentEmit
    └── models.py                      # Plan (JSONB), PlanRun

frontend/src/pages/
└── CreatorPage.jsx                    # список планов, форма запуска, прогресс PlanRun
```

## 9. БД-схема

```python
# core/models/creator.py
class Plan(Base):
    id: UUID                          # primary
    name: str                         # "DRC v1"
    schema_version: int               # формат plan.steps
    steps: JSONB                      # [{step, input}, ...] с шаблонизаторами {{var}}
    is_active: bool
    created_at, updated_at

class PlanRun(Base):
    id: UUID
    plan_id: FK → Plan
    profile_id: str                   # Vision-профиль
    variables: JSONB                  # {geo: 'DE', offer: {...}, creatives: [...]}
    status: enum                      # queued, running, success, failed, requires_attention
    started_at, finished_at
    step_log: JSONB                   # инкрементально дописывается
    error_message: str | null
```

Миграция Alembic — отдельная, старые таблицы recorder-сессий **не трогаем** (история).

## 10. Observability

- TS-executor эмитит через `fbAgentEmit`: `step_started`, `step_skipped`, `step_finished`, `step_failed`, `checkpoint_detected`.
- Python пишет инкрементально в `PlanRun.step_log`.
- Frontend (`/creator`):
  - Список планов и кнопка "Создать кампанию" (форма из enum'ов + переменных).
  - Таблица `PlanRun` с live-прогрессом, длительностью шагов, ошибками.
- Telegram алерты на `failed` и `requires_attention`.

## 11. Удаление старого

Последним коммитом в финальном PR:

- `core/campaign_creator/` — целиком
- `core/campaign_recorder/` — целиком
- `tools/dry_run_creator.py`
- `tools/timing_percentiles.py`
- `apps/api/routers/campaign_recorder.py` (заменяется на `creator.py`)
- `tests/unit/test_campaign_recorder.py`
- `tests/unit/test_creo_scanner.py`
- `tests/unit/test_spec_builder_e2e.py`

Старые миграции рекордер-сессий не трогаем.

## 12. Out of scope (намеренно)

- A/B testing разных вариантов планов — не делаем в v1.
- Параллельный запуск нескольких `PlanRun` на одном профиле — нет, один профиль = одна очередь.
- Авто-обнаружение изменений UI FB — нет, разработчик дописывает шаги вручную при поломках.
- Запись через UI (toggle в frontend) — нет, только CLI `creator_recorder`.
- Импорт планов из других источников — нет, только запись и ручная правка JSON.

## 13. Acceptance criteria

- [ ] Один план "DRC v1" запускает рабочую кампанию на любую из ≥5 стран (DE, AT, FR, IT, ES) с разными офферами и креативами.
- [ ] Полный паритет шагов со старым `core/campaign_creator/steps/` (все ~20 шагов реализованы).
- [ ] Повторный запуск того же `PlanRun` после падения на середине — продолжает с того же места (идемпотентность).
- [ ] Смена языка UI FB (RU↔EN) не требует правок плана или шагов.
- [ ] Создание полной кампании занимает ≥3 минут (антибот).
- [ ] Captcha/checkpoint → автоостановка + Telegram-алерт, без вылета процесса.
- [ ] Новый шаг добавляется одним TS-файлом + одной строкой импорта в `creator/steps/index.ts`.
- [ ] `core/campaign_creator/` и `core/campaign_recorder/` удалены из репозитория.
