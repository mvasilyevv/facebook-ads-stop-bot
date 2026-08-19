# Гард на утечку секретов — план волны 3

> **Для агентов:** ОБЯЗАТЕЛЬНАЯ ПОД-СКИЛЛ: superpowers:subagent-driven-development
> или superpowers:executing-plans. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** инвариант «сырой текст исключения, traceback, UUID, токен бота и
секреты не уходят в operator UI, Telegram, URL, логи и breadcrumbs» перестаёт
держаться на внимательности ревьюера и начинает проверяться.

**Архитектура:** гард живёт одним файлом `tests/unit/test_secret_leak_guards.py`
и растёт задача за задачей. Каждая задача добавляет свой срез проверок и чинит
ровно то, что этот срез красит. `main` ни в одной точке не остаётся красным.

**Технологии:** Python 3.12, pytest, ast, FastAPI, httpx, grpc.

## Измерение (выполнено 19.08.2026)

Гард и два модуля из ветки `feat/no-secret-leaks` (PR #101) положены на текущий
`main` и прогнаны. Результат — не оценка, а факт:

- **33 уникальные проверки падают**, 6 параметризованных вариантов проходят.
- Ветка добавляет ровно три новых файла: `core/safe_diagnostics.py` (81 строка),
  `core/public_identifiers.py`, `tests/unit/test_secret_leak_guards.py`
  (857 строк). Всё остальное в PR — правки существующих модулей.
- За тремя структурными проверками стоит основной объём: `exc_info=True` —
  32 места в 14 файлах; `repr(exc)`/`str(exc)` — 59 мест в 28 файлах;
  `logger.exception` — 92 места; `correlation_id=str(` — 7 мест.

Число «100 файлов» из описания PR устарело и в расчёт не берётся.

## Global Constraints

- Код, тесты и комментарии — по-русски; имена типов, API-полей и технических
  идентификаторов остаются английскими.
- Исходный текст проверок берётся ДОСЛОВНО из `git show
  pr101:tests/unit/test_secret_leak_guards.py` (ref `pr101` уже есть локально).
  Переписывать проверку под удобство реализации запрещено: это подгонка ответа.
  Если проверка кажется неверной — остановись и доложи, не правь молча.
- `main` не остаётся красным ни после одной задачи: срез проверок добавляется
  вместе с правками, которые его зеленят.
- **Диагностику не глушить.** Сырое сообщение заменяется осмысленным, а не
  пустотой: оператор и разработчик обязаны понимать, что произошло. Класс
  ошибки и машинный код остаются, тело — нет.
- Ветку не создавать: коммиты прямо в `main`, сообщение по-русски,
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Коммитить только файлы своей задачи. В дереве лежат чужие изменения
  (`frontend/e2e/__audit__/1440px/*.png`) — не трогать, не коммитить.
- ЗАПРЕТ БЕЗ ИСКЛЮЧЕНИЙ: `git stash` не вызывать. В репозитории лежит чужой
  stash-entry от другой сессии. Предупреждение самого pre-commit про stashing —
  его внутренний механизм, реагировать не надо.
- Временные правки для мутационных доказательств возвращать точечной обратной
  заменой; `git checkout`/`git restore` не использовать.
- `pytest` только на изолированной БД; боевая `:5433` под запретом.
- **Проверь, покрыт ли меняемый модуль integration-тестами:**
  `grep -rl "<имя_модуля>" tests/integration/`. Прогнать их локально нельзя —
  им нужна БД, а Docker на машине не поднят, — поэтому найденные файлы надо
  ВЫЧИТАТЬ глазами и сказать в отчёте, какие утверждения поедут от правки.
  19.08 задача 2 изменила `normalize_web_app_base`, прогнала только
  `tests/unit` и уронила два integration-теста уже в CI: они фиксировали
  старое сквозное поведение «что положили, то и вернули».
- Ничего не менять в боевых рекламных кабинетах, сканирование не включать.
- Окружение: `.venv/bin/pytest`, `.venv/bin/ruff`, всегда с
  `PYTHONDONTWRITEBYTECODE=1`.

---

### Task 1: Основание — два модуля и их собственные проверки

**Files:**
- Create: `core/safe_diagnostics.py`
- Create: `core/public_identifiers.py`
- Create: `tests/unit/test_secret_leak_guards.py`

**Interfaces:**
- Produces: `safe_exception_diagnostic(exc) -> str` и
  `redact_sensitive_text(value) -> str` из `core.safe_diagnostics`;
  `public_uuid(value)` и `parse_public_uuid(value)` из
  `core.public_identifiers`. Все последующие задачи опираются на них.

**Контекст.** Задача чисто добавляющая: два новых модуля и три проверки,
которые тестируют только их. Поведение существующего кода не меняется, поэтому
`main` остаётся зелёным.

- [ ] **Шаг 1: Перенести модули дословно**

```bash
git show pr101:core/safe_diagnostics.py > core/safe_diagnostics.py
git show pr101:core/public_identifiers.py > core/public_identifiers.py
```

- [ ] **Шаг 2: Создать файл гарда только с проверками основания**

Взять из `git show pr101:tests/unit/test_secret_leak_guards.py` заголовок файла
(docstring и импорты, нужные перечисленным ниже проверкам) и ДОСЛОВНО три
проверки:

- `test_safe_diagnostic_never_formats_exception_message_or_traceback`
- `test_public_text_redactor_covers_credentials_capabilities_queries_and_uuid`
- `test_public_uuid_is_opaque_round_trip_and_raw_uuid_is_not_accepted_as_public`

Импорты, не нужные этим трём проверкам, не переносить — они потянут модули,
которые задача не трогает.

- [ ] **Шаг 3: Прогнать**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_secret_leak_guards.py -q
```
Ожидается: все проверки проходят.

- [ ] **Шаг 4: Доказать, что гард умеет краснеть**

Временно заменить в `core/safe_diagnostics.py` тело `redact_sensitive_text` на
`return str(value or "")`, прогнать команду из шага 3, убедиться в падении,
вернуть точечной обратной заменой и убедиться, что снова зелено и
`git diff --stat core/safe_diagnostics.py` пуст.

- [ ] **Шаг 5: Полный прогон и линтер**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit -q --timeout=60
.venv/bin/ruff check core tests && .venv/bin/ruff format --check core tests
```
Ожидается: всё зелёное.

- [ ] **Шаг 6: Коммит**

```bash
git add core/safe_diagnostics.py core/public_identifiers.py tests/unit/test_secret_leak_guards.py
git commit -m "feat(core): ограниченная диагностика вместо сырого текста

Инвариант «сырые ошибки, UUID и токены не уходят наружу» держался на
внимательности ревьюера. core/safe_diagnostics даёт класс ошибки и машинный
код без тела, core/public_identifiers — непрозрачный публичный идентификатор
вместо внутреннего UUID. Правка чисто добавляющая: поведение существующего
кода не меняется, дальше модули разложатся по местам утечки.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Задачи 2–7: срез гарда плюс починка того, что он красит

Каждая задача устроена одинаково, отличаются только область и список проверок.
Общая процедура:

1. Добавить в `tests/unit/test_secret_leak_guards.py` перечисленные проверки
   ДОСЛОВНО из `git show pr101:tests/unit/test_secret_leak_guards.py` вместе с
   нужными им импортами.
2. Прогнать файл гарда и убедиться, что новые проверки КРАСНЫЕ, а ранее
   добавленные остались зелёными.
3. Починить продуктовый код так, чтобы срез позеленел. Правки брать за образец
   из `git show pr101 -- <файл>`, но применять к текущему коду, а не
   переносить diff вслепую: с 15.08 эти модули переписывались.
4. Диагностику не глушить: заменять сырое сообщение осмысленным.
5. Прогнать `tests/unit` целиком, `ruff check`, `ruff format --check`.
6. Коммит с файлами только своей задачи.

Если проверка требует поведения, которого в текущем `main` нет и которое
выходит за область задачи, — остановись и доложи, не расширяй область молча.

#### Task 2: Telegram

Проверки: `test_telegram_renderer_redacts_facts_and_button_label`,
`test_telegram_renderer_rejects_navigation_url_side_channels`,
`test_web_app_base_rejects_url_credentials_and_history_leaks`,
`test_telegram_gateway_blocks_safe_line_bypass_for_text_and_labels`,
`test_telegram_gateway_rejects_webhook_secret_query`,
`test_telegram_diagnostics_do_not_return_remote_error_or_url_query`.

Затрагиваемые модули: `core/telegram/notification_renderer.py`,
`core/telegram/gateway.py`, `core/telegram/web_app_url.py`,
`apps/api/routers/v1/settings_telegram.py`.

Особое внимание: карточка инцидента редактируется в существующем message slot —
изменение текста не должно ломать идемпотентность доставки.

#### Task 3: Meta API

Проверки: `test_meta_error_drops_raw_graph_and_grpc_messages`,
`test_meta_audit_boundary_omits_arbitrary_payload_and_sanitizes_endpoint`,
`test_meta_audit_records_query_keys_and_error_codes_without_values`,
`test_meta_adapter_parse_warning_does_not_log_raw_value`.

Затрагиваемые модули: `core/meta_api/errors.py`, `core/meta_api/audit.py`,
`core/meta_api/adapters.py`.

Особое внимание: `core/meta_api/errors.py` содержит распознавание разлогина
(`_LOGIN_REQUIRED_MESSAGE_RE`), от которого зависит алерт о мёртвом канале.
Классификация ошибки обязана продолжать работать по тексту Meta — редактируется
то, что уходит наружу, а не то, по чему принимается решение.

#### Task 4: Operator API

Проверки: `test_operator_incident_copy_redacts_untrusted_text_fields`,
`test_operator_attention_incident_redacts_copy_and_internal_uuid`,
`test_operator_api_sources_do_not_project_raw_correlation_uuid`,
`test_vision_operator_response_never_projects_browser_identity`,
`test_vision_public_diagnostics_ignore_raw_probe_and_configuration_details`.

Затрагиваемые модули: `apps/api/routers/v1/operator.py`,
`apps/api/routers/v1/settings_vision.py`.

Особое внимание: `correlation_id=str(` встречается в 7 местах. Сверься с
`packages/shared/` и `frontend/openapi.json` — если меняется форма поля в
ответе API, контракт и оба фронта обязаны поехать следом, а `pnpm gen:api`
прогнаться.

#### Task 5: Воркеры и внешние клиенты

Проверки: `test_campaign_worker_log_handle_never_contains_run_uuid`,
`test_duplicate_status_never_exposes_worker_last_error`,
`test_observer_metric_failure_log_uses_opaque_ad_id_and_safe_error`,
`test_ai_diagnostics_redacts_log_input_and_provider_output`,
`test_ai_diagnostics_timeout_log_redacts_alert_key`,
`test_syntx_analysis_error_drops_provider_message_and_chat_uuid`,
`test_provider_error_does_not_expose_external_response_body`,
`test_external_client_start_logs_strip_url_credentials_and_query`,
`test_recurring_incident_failure_log_redacts_incident_key`,
`test_autostop_card_uses_affected_count_instead_of_meta_ids`.

Затрагиваемые модули: `apps/campaign_creator_worker/main.py`,
`core/adset_duplicates/service.py`, `core/observer/writers.py`,
`core/ai_assistant/diagnostics.py`, `core/ai_assistant/providers.py`,
`core/syntx/client.py`, `core/syntx/errors.py`, `core/telegram/worker_notify.py`,
`core/telemetry.py`.

Особое внимание: `test_autostop_card_uses_affected_count_instead_of_meta_ids`
трогает карточку авто-стопа — money-путь. Инвариант «`null` — неизвестно, `0` —
подтверждённый ноль» обязан сохраниться: количество затронутых объявлений не
превращается в ноль оттого, что его не удалось узнать.

#### Task 6: Файловые пути и постбек

Проверки: `test_campaign_tool_paths_do_not_expose_server_root_or_secret_filename`,
`test_creative_uniquify_response_does_not_expose_server_path`,
`test_campaign_folder_list_replaces_raw_validation_error`,
`test_open_folder_resolves_public_relative_path_inside_creative_root`,
`test_postback_response_does_not_echo_provider_or_internal_ids`.

Затрагиваемые модули: `apps/api/routers/v1/tools.py`,
`core/creatives/folder_opener.py`, `apps/api/routers/postback.py`.

Особое внимание: `open_folder` резолвит путь внутри корня креативов — правка
обязана оставаться защитой от выхода за корень, а не только косметикой ответа.

#### Task 7: Структурные инварианты логирования

Проверки: `test_logs_never_enable_tracebacks`,
`test_loggers_never_receive_raw_exception_or_persisted_error`,
`test_external_boundary_loggers_have_no_raw_exception_formatting`.

Идёт ПОСЛЕДНЕЙ: задачи 2–6 уберут часть нарушителей, и объём этой задачи к её
началу будет меньше измеренного.

Найти оставшиеся места:

```bash
grep -rn 'exc_info=True' --include='*.py' apps core fbctl
grep -rnE 'repr\(exc\)|str\(exc\)' --include='*.py' apps core
grep -rn 'logger\.exception' --include='*.py' apps core
```

Правило замены — одно на все места: сырой объект исключения в аргументах
логгера заменяется на `safe_exception_diagnostic(exc)`; `exc_info=True`
убирается; `logger.exception(...)` становится `logger.error(...)` с той же
безопасной диагностикой. Текст сообщения на русском сохраняется, теряется
только тело исключения.

Это механическая правка большого объёма — прогоняй `tests/unit` не только в
конце, но и после каждого десятка файлов: часть тестов утверждает конкретные
строки логов.

---

## Условие выхода волны

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_secret_leak_guards.py -q`
проходит целиком, и число проверок в файле совпадает с числом в
`git show pr101:tests/unit/test_secret_leak_guards.py`. Расхождение означает,
что срез потеряли по дороге.

## Что не входит в волну

Три вопроса из описания PR #101 кодом не закрываются и остаются владельцу:
постбек AdSet.pro идёт GET с токеном в query (нужен переход провайдера на POST
с подписью в заголовке); одноразовый ticket стола остаётся в cross-origin
launch URL; navigation capability Telegram сидит в `?nav=` и убирается только
вместе с изменением handoff во фронте и TMA.
