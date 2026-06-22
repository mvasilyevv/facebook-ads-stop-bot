# Улучшения: кандидаты на переезд на другой язык/рантайм

Дата: 2026-06-22. Тема Фазы 2. Источники: `arch/browser-agent.md`, `00-system-map.md`, `99-risk-synthesis.md`, `findings/browser-agent.md` + чтение реального кода (`proto/v1/scanner.proto`, `core/meta_api/errors.py`, `services/browser-agent/src/meta-api/client.ts`, `core/rules/evaluator.py`).

## Краткий вывод

**Переписывать что-либо на другой язык — НЕТ. Все три кандидата на смену рантайма — `skip`.**

- **browser-agent остаётся на Node/TS.** Аргумент «один язык» иллюзорен: Playwright-Python всё равно тащит внутри Node-драйвер (Node не уходит, добавляется второй мост). Миграция 8.5k LOC прод-кода (без тестов) money-критичного канала ради косметики — отрицательный EV. Реальная боль (дрейф контракта, типобезопасность границы) лечится **точечно, БЕЗ миграции** — см. рекомендацию №3.
- **Горячие Python-пути — НЕ кандидаты на Rust/Go/C-расширение.** Данные карт однозначны: пути I/O-bound (network round-trip к Meta через `page.evaluate(fetch)`, async-SQL), не CPU-bound. `evaluator.py` — десятки сравнений Decimal на строку, без numpy/циклов на 10⁴+. Профиль латентности упирается в сеть и Postgres, не в Python-интерпретатор. Rust здесь экономит микросекунды на фоне сотен миллисекунд сети.
- **ЕСТЬ что улучшить — но это НЕ смена языка, а ужесточение уже существующего IDL.** Контракт `.proto` уже общий и кодогенерируется в обе стороны (`grpc_tools.protoc` для Python, `grpc_tools_node_protoc` для TS). Дрейф (ScannedAdRow ×3 места, Graph error code TS↔Python) живёт в **ручном маппинге и семантике ошибок**, которые proto не выражает. Это `consider` с маленьким усилием и реальной money-отдачей (рекомендация №3, частично).

---

## Таблица рекомендаций

| # | Рекомендация | Что меняем | Почему (боль) | Что даёт | Усилие | Риск | Вердикт |
|---|---|---|---|---|:--:|:--:|:--:|
| 1 | Консолидировать browser-agent на Playwright-Python | Переписать ~8.5k LOC TS → Python, выкинуть Node-сервис | «Два рантайма», дубль контракта | Почти ничего реального (Node не уходит из Playwright-Python); огромный риск на money-канале | L | high | **skip** |
| 2 | Rust/Go/C-расширение для горячих путей (evaluator, агрегации спенда) | Вынести rules/агрегации в нативный модуль | Гипотетический CPU-боттлнек | Ничего: пути I/O-bound, не CPU-bound | L | high | **skip** |
| 3a | Кодоген типизированных мапперов ScannedAdRow из `.proto` (убрать ручной слой ×3) | Генерировать TS-row-builder и Python-row-parser из единого источника | Поле задаётся руками в 3 местах → тихий NULL в БД (money-данные) | Class of bug «тихий NULL метрики» исчезает структурно, не дисциплиной | M | med | **consider** |
| 3b | Единый источник Graph-error-классификации (TS коды ↔ Python `_CODE_MAP`) | Вынести таблицу code→class в общий JSON/proto-enum, генерировать обе стороны | Рассинхрон ломает маршрутизацию requeue/mark_failed авто-стопа | Авто-стоп не «навсегда failed» при будущем рассинхроне кодов | S | low | **consider** |
| 3c | protovalidate/buf вместо самописного proto-codegen | Заменить `grpc_tools.protoc` на buf + protovalidate CEL | Хочется «строже контракт» | Не закрывает реальную боль (она в маппинге, не в схеме); ещё один инструмент в стек | M | med | **skip** |

---

## Детализация

### №1 — Консолидация browser-agent на Python (skip)

**Что предлагалось взвесить:** оставить Node (Playwright JS-native, anti-detect Vision-экосистема в JS) ИЛИ переехать на Playwright-Python ради одного языка и единого proto-контракта.

**Явный вердикт: SKIP. Оставить Node/TS.**

Разбор аргументов «за переезд» по фактам кода:

1. **«Один рантайм / операционная простота» — иллюзия.** Playwright-Python **не убирает Node**: все языковые биндинги Playwright общаются по WebSocket с одним и тем же Node.js-драйвером, который и рулит браузером ([LambdaTest](https://www.lambdatest.com/automation-testing-advisor/python/playwright-python-connect_over_cdp), [Playwright architecture](https://samedesilva.medium.com/playwright-architecture-simple-breakdown-69f64ea4de3d)). То есть после миграции в проде всё равно крутится Node-процесс (драйвер Playwright) + Python-обёртка — это **два рантайма + новый IPC-мост** вместо нынешнего чистого gRPC. Операционно НЕ проще, местами сложнее.

2. **«anti-detect Vision-экосистема в JS» — в этом коде её нет, и это снимает аргумент «за Node», но НЕ создаёт аргумент «за переезд».** Проверено: browser-agent подключается к **внешнему** Vision-профилю через `chromium.connectOverCDP` (`session-manager.ts:666`), своего браузера не запускает; в `package.json` НЕТ ни `puppeteer-extra`, ни `stealth`, ни fingerprint-либ. Anti-detect целиком в Vision (внешний сервис :3030). Node здесь — просто CDP-клиент + оркестратор `page.evaluate(fetch)`. Значит «JS-native stealth» не держит за Node — но это симметрично работает: переезд на Python ничего не выигрывает, потому что вся ценность и так снаружи.

3. **Перф Playwright не аргумент ни в какую сторону.** `connect_over_cdp` и `page.evaluate` в Python — функциональный паритет с Node (общий движок), опции догоняются по релизам ([Playwright Python release notes](https://playwright.dev/python/docs/api/class-browsertype)). Известная latency-проблема (лишний хоп через Node-драйвер на тысячах CDP-вызовов, [browser-use](https://browser-use.com/posts/playwright-to-cdp)) к этому коду **не относится**: горячий путь — один `fetch` изнутри страницы, а не тысячи мелких CDP-команд. Перф нейтрален.

4. **Цена миграции — высокая и асимметричная.** ~11.8k LOC TS, из них ~8.5k прод (без `*.test.ts`): `session-manager.ts` (702), `meta-api/upload.ts` (592, chunked resumable video), `am/am-fetch.ts` (564, снифф токена + пагинация), `humanizer.ts` (536), `am-parser`/`am-join` (money-критичный маппинг spend/leads/deposits). Это **самый money-критичный** сервис (через него идут ОБА канала — детект и авто-стоп). Переписывание = риск занести регресс в маппинг денег (точно тот класс, что MEMORY «ScannedAdRow field checklist» и находки M2 `am-fetch` пагинация). EV резко отрицательный: высокий риск на необратимых деньгах ради косметической однородности.

5. **Дубль контракта — реальная боль, но решается БЕЗ миграции.** `.proto` уже единый IDL и кодогенерируется обе стороны. Остаточный ручной дрейф (ScannedAdRow-маппинг, error-коды) чинится рекомендацией №3 за M-усилие против L-миграции. Переезд на Python НЕ убрал бы ручной маппинг сам по себе — он просто переписал бы его на другом языке.

**Вывод:** костяк browser-agent (lock-сериализация reload↔fetch, circuit-breaker, session-recovery, graceful shutdown) аудит признал здоровым (`findings/browser-agent.md` «Проверено и признано здоровым»). Открытые HIGH (R5 — per-cabinet heal-state) — это **логические фиксы внутри TS**, а не повод менять язык. Менять рантайм money-канала без единого технического выигрыша — переписывание ради переписывания.

---

### №2 — Rust/Go/C-расширение для горячих путей (skip)

**Что предлагалось взвесить:** есть ли смысл вынести `rules/evaluator`, агрегации спенда в Rust/Go/нативное расширение.

**Явный вердикт: SKIP. Обоснование данными карт, не догадкой.**

1. **Профиль нагрузки — I/O-bound, не CPU-bound.** Жизненный цикл объявления (`00-system-map.md §2`) — это сеть и БД: `RunScanCycle` = network round-trip к `adsmanager-graph.facebook.com` через `page.evaluate(fetch)`; мутации = round-trip к `graph.facebook.com`; метрики/FSM = 4 раздельные транзакции в Postgres. Латентность доминируется внешними сервисами (сотни мс сети + Postgres), не Python-интерпретатором.

2. **`evaluator.py` (610 LOC) не содержит тяжёлой арифметики.** Проверено grep'ом: нет `numpy`/`pandas`/`scipy`, нет циклов на 10⁴⁺. Это десятки сравнений `Decimal` и веток правил **на одну строку скана**. Размер батча — десятки–сотни ad'ов на цикл (am_tabular limit 5000 — практический максимум, не типичный). Даже на 5000 строк ×7 правил это микросекунды CPU на фоне сетевого скана в секунды.

3. **Агрегации спенда уже на стороне Postgres.** `core/dashboard/metric_aggregation.py` (138 LOC) — это построение SQL CTE (`latest_per_ad_window_cte` / DISTINCT ON), сама агрегация исполняется в БД. Переписывать «обёртку над SQL» на Rust бессмысленно — работу делает Postgres.

4. **Стоимость интеграции нативного модуля высокая, выгода нулевая.** PyO3/cffi/cgo-мост, кросс-компиляция под прод-хост (Ubuntu, `project-24-7-deploy`), усложнение CI/сборки, потеря единообразия отладки — всё это ради экономии, которая теряется в шуме сетевого I/O. Классический анти-паттерн преждевременной оптимизации.

**Если когда-нибудь появится реальный CPU-боттлнек** (его сейчас нет в картах) — первый шаг не Rust, а профилирование (`py-spy`) и оптимизация горячего SQL/алгоритма на Python. Нативное расширение — последнее средство, не первое.

---

### №3 — Единый IDL/codegen для контракта TS↔Python (частично consider)

**Что предлагалось:** единый источник для ScannedAdRow и ошибок, чтобы не держать руками в трёх местах.

Это **единственное направление темы, где есть реальная отдача** — но важно: оно НЕ про смену языка, а про ужесточение уже существующего proto-контракта. Разбиваю на под-части, потому что вердикты разные.

#### Контекст: что УЖЕ единое, а что дрейфует руками

`.proto` (`proto/v1/*.proto`, 808 LOC) — уже единый источник истины. Кодоген настроен обе стороны:
- Python: `grpc_tools.protoc` (Makefile:173, run.sh:713) → `clients/python_grpc/v1/*_pb2.py`.
- TS: `grpc_tools_node_protoc` (package.json «proto»-скрипт) + runtime `@grpc/proto-loader.loadSync` (index.ts:30).

**Сами proto-сообщения НЕ дрейфуют** — они генерируются из одного файла. Дрейфует то, что proto выразить не может:

1. **Ручной маппинг ScannedAdRow ×3** (`99-risk-synthesis.md §3`, MEMORY checklist): `am-join.buildScannedRow` (TS, сырой Meta-JSON → row) → `index.toProtoRow` (TS, row → proto, index.ts:411) → `client._proto_to_row` (Python, proto → dataclass). 33 поля. Пропуск поля в любом из трёх = **тихий NULL метрики в БД** = money-данные теряются. Это подтверждённый класс багов (MEMORY: «иначе тихие NULL»).

2. **Семантика Graph error code** (`99-risk-synthesis §3`, R-контракт): Python `core/meta_api/errors.py::_CODE_MAP` (таблица code→класс: 190→TokenInvalid, 803→NotFound, 4/17/32→RateLimited, отрицательные -1/-2/-3→Temporary/SessionUnavailable) vs TS `meta-api/client.ts` (разбросанные `if code===190 / code===-1||-2||-3`, client.ts:359-369). Таблицы **не имеют общего источника**. Рассинхрон ломает маршрутизацию requeue↔mark_failed авто-стопа (money: «pause_ad навсегда failed» — комментарий в errors.py:99-101 прямо про этот класс).

#### 3a — Кодоген типизированных ScannedAdRow-мапперов (consider, M)

**Что меняем:** свести три ручных слоя к двум, сгенерированным из proto. `proto → Python dataclass` уже частично решается `_pb2`-классом; ручной `_proto_to_row` можно заменить программным обходом полей дескриптора (один цикл по `DESCRIPTOR.fields`, без ручного перечисления 33 имён). На TS-стороне `toProtoRow` аналогично свести к маппингу по списку полей из дескриптора. Останется один действительно доменный слой — `buildScannedRow` (Meta-JSON → row), он по природе ручной (источник — Meta, не proto).

**Почему:** убирает 2 из 3 рукописных мест, где забытое поле = тихий NULL денег.

**Что даёт:** структурно (а не дисциплиной/ревью) закрывает класс «новое поле ScannedAdRow не доехало до БД». Меньше LOC рукописного маппинга, меньше money-data-loss багов.

**Усилие M / риск med:** нужно аккуратно покрыть тестом round-trip (Meta-JSON → row → proto → dataclass), чтобы сам кодоген-обход не занёс регресс в money-маппинг. Не делать на горячую — это money-путь.

**Альтернатива дешевле (S, если M не окупается):** оставить три места, но добавить **контрактный тест-страж** — параметризованный тест, который сверяет, что множество полей `ScannedAdRow` dataclass == множество полей proto == ключей, которые трогает `_proto_to_row`/`toProtoRow` (как анти-регресс `test_heartbeat_contract.py` из Round 11 для имён heartbeat). Это не убирает дубль, но ловит дрейф в CI. Часто это лучший cost/benefit, чем кодоген.

#### 3b — Единый источник Graph-error-классификации (consider, S)

**Что меняем:** вынести таблицу `code → семантика (permanent/temporary/token-invalid/rate-limited/not-found/session-unavailable)` в **один машинно-читаемый источник** (например `proto/v1/` enum + sidecar JSON, или просто `errors.json` в репо), генерировать/загружать обе стороны. TS перестаёт хардкодить `if code===190`, Python `_CODE_MAP` читает тот же источник.

**Почему:** сейчас две независимые таблицы кодов; рассинхрон тихо ломает money-маршрутизацию авто-стопа (requeue vs mark_failed).

**Что даёт:** добавление/изменение трактовки Graph-кода — в одном месте; авто-стоп не застревает в «навсегда failed» из-за того, что TS и Python разошлись в трактовке кода.

**Усилие S / риск low:** таблица маленькая (~25 кодов + 2 subcode-override), границы чёткие, легко покрыть тестом «обе стороны согласны по каждому коду». Самый дешёвый и безопасный пункт темы.

**Минимальная версия (если даже S дорого):** контрактный тест, который держит TS-набор обрабатываемых кодов и Python `_CODE_MAP` в синхроне (фейлит CI при расхождении) — без вынесения в общий источник.

#### 3c — protovalidate / buf вместо самописного codegen (skip)

**Что предлагалось бы:** заменить `grpc_tools.protoc` на buf CLI + protovalidate (CEL-валидация в proto, v1.0 с сентября 2025, [Buf blog](https://buf.build/blog/protovalidate-v1); рантаймы `protovalidate` PyPI и `@bufbuild/protovalidate` npm на v1.2).

**Вердикт: SKIP.**

1. **Не закрывает реальную боль.** protovalidate добавляет **валидацию значений** (диапазоны, required, CEL-правила) в схему. Но дрейф здесь — в **маппинге** (ScannedAdRow ×3) и **семантике ошибок** (code→retry-класс), которые валидация полей не выражает. Инструмент решает не ту проблему.

2. **Стоимость замены codegen-пайплайна реальна.** Текущий `grpc_tools.protoc` встроен в run.sh/Makefile/CI, есть известный gotcha с импортами (`from v1 import` → relative, run.sh:727) и MEMORY про pb2-staleness — пайплайн отлажен. Менять рабочий codegen на buf-toolchain ради фич, которые не нужны — добавить инструмент в стек без отдачи.

3. **Если когда-нибудь захочется валидации в схеме** (например, hard-cap бюджета как proto-constraint) — тогда protovalidate уместен. Сейчас таких требований в картах нет. Преждевременно.

---

## Итог темы

| Кандидат | Вердикт | Одной строкой |
|---|:--:|---|
| browser-agent → Python | **skip** | Node не уходит (Playwright-Python тащит его внутри); L-риск на money-канале без выигрыша |
| Rust/Go/нативное для hot-path | **skip** | Пути I/O-bound (сеть+Postgres), не CPU-bound; нечего ускорять |
| Кодоген ScannedAdRow-мапперов (3a) | **consider** | Убирает 2/3 ручных места «тихого NULL»; дешевле — контрактный тест-страж |
| Единый источник error-кодов (3b) | **consider** | Самый дешёвый (S/low); money: авто-стоп не «навсегда failed» при рассинхроне |
| buf/protovalidate (3c) | **skip** | Решает не ту проблему (валидация ≠ маппинг); лишний инструмент |

**Главное:** ни один блок не выигрывает от смены языка/рантайма. Реальная боль темы (дрейф контракта TS↔Python) лечится тем же gRPC/proto + контрактными тестами/кодогеном мапперов — без миграции 8.5k LOC money-кода. Честный skip на двух из трёх кандидатов, точечный consider на третьем (и то — это не «смена рантайма», а укрепление существующего).

## Источники (внешняя сверка)

- Playwright Python `connect_over_cdp`/`page.evaluate` паритет и общий Node-движок: [LambdaTest](https://www.lambdatest.com/automation-testing-advisor/python/playwright-python-connect_over_cdp), [Playwright architecture](https://samedesilva.medium.com/playwright-architecture-simple-breakdown-69f64ea4de3d), [Playwright Python release notes / BrowserType](https://playwright.dev/python/docs/api/class-browsertype).
- Latency лишнего Node-хопа только на массовых CDP-вызовах (к нашему `fetch`-пути не относится): [browser-use: Leaving Playwright for CDP](https://browser-use.com/posts/playwright-to-cdp).
- protovalidate v1.0 / buf codegen зрелость: [Buf blog: Protovalidate v1.0](https://buf.build/blog/protovalidate-v1), [protovalidate GitHub](https://github.com/bufbuild/protovalidate), [@bufbuild/protoc-gen-es](https://www.npmjs.com/package/@bufbuild/protoc-gen-es).
