# Кабинет пробы готовности браузера — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Проба готовности browser-agent перестаёт выводить кабинет из случайного состояния браузера и открывает вкладку только для кабинета, настроенного в активных офферах, не трогая чужие вкладки.

**Architecture:** Кабинет становится явным входом gRPC-запроса `CheckMetaApiHealthRequest.ad_account_id`. Python-сторона резолвит его детерминированно из `offer_ad_accounts` активных офферов (`resolve_scan_account_ids`). Вызов без кабинета больше ничего не создаёт: он переиспользует живую вкладку Ads Manager, а при её отсутствии честно отвечает `no_ads_manager_page`.

**Tech Stack:** Python 3 (SQLAlchemy async, pytest), Node.js + TypeScript (`@grpc/grpc-js`, `@grpc/proto-loader`, Playwright, `node --test`), protobuf.

## Контекст: что именно сломано

`services/browser-agent/src/meta-api/service.ts:514` берёт кабинет из адреса главной вкладки:

```ts
const actId = extractAdAccountId(session.primaryPage?.url?.()) ?? '';
```

и передаёт его в `_getInteractivePage` → `ensureInteractivePage` → `ensureRolePage`, который **создаёт вкладку**, если её нет. `apps/health_watchdog/main.py:108` зовёт эту пробу каждые 2 секунды (`HEALTH_WATCHDOG_BROWSER_READINESS_SEC` по умолчанию `"2"`).

Итог на проде 17.08.2026: вкладка кабинета `1855748448431929`, которого нет ни в `ad_accounts`, ни в `offer_ad_accounts`, ни в `observer_config`, воскресала через 2 секунды после каждого закрытия. Кабинет разлогинен, поэтому каждый цикл уходил на `business.facebook.com/business/loginpage/`. Замер: с замороженным browser-agent вкладка не вернулась за 20 секунд, с живым — возвращается за 2.

Это прямо противоречит инварианту, уже записанному в `core/observer/accounts.py:6-7`:

> Пустой scan set всегда останавливает цикл fail-closed: текущая вкладка браузера никогда не используется как неявная account identity.

## Global Constraints

- Готовность канала `meta_api` — money-путь: `core/tasks/queue.py:1015-1039` (`_BROWSER_READY_CLAIM_SQL`) допускает claim задач только при `state = 'ready'` с живым `readiness_expires_at`. Любое изменение обязано сохранять возможность публиковать `ready` при **выключенном** сканировании, иначе ручной стоп/запуск из UI и Telegram перестанет работать.
- Money-путь: сначала инвариант и regression test, потом реализация.
- `null` означает unknown, `0` — подтверждённый ноль.
- Raw exception, traceback, UUID и секреты не попадают в operator UI, Telegram, URL и логи.
- Комментарии и названия тестов по-русски там, где это помогает оператору; имена типов, полей API и proto — английские.
- Никогда не запускать pytest против боевой БД (`:5433`): integration-фикстуры сносят `offers`/`offer_rules`.
- Один архитектурный слой за коммит; каждая задача заканчивается зелёными узкими тестами.
- Миграций БД в этом плане нет: `browser_channel_readiness.reason_code` — существующая колонка, новых значений `state` не вводим (`ck_browser_channel_readiness_state` разрешает только `ready|unavailable|incompatible|profile_mismatch|maintenance`).
- Генерируемые Python-stubs правятся только через `python scripts/generate_grpc_stubs.py`, вручную `*_pb2.py` не редактируются.
- TypeScript-сторона читает proto динамически через `@grpc/proto-loader` — codegen для TS не нужен.

---

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `proto/v1/meta_api.proto` | Контракт gRPC | +поле `ad_account_id = 4` в `CheckMetaApiHealthRequest` |
| `clients/python_grpc/v1/meta_api_pb2.py`, `.pyi`, `_pb2_grpc.py` | Сгенерированные stubs | Регенерация |
| `services/browser-agent/src/session-manager.ts` | Владение вкладками и ролями | +`findLiveAdsManagerPage(browser)` — чистый поиск без побочных эффектов |
| `services/browser-agent/src/meta-api/service.ts` | gRPC-обработчики Meta API | Обработчик health перестаёт угадывать кабинет |
| `core/meta_api/client.py` | Python-клиент browser-agent | `check_health(..., ad_account_id)` |
| `core/meta_api/browser_readiness.py` | Публикация готовности канала | Резолв кабинета из активных офферов + новый reason code |
| `apps/health_watchdog/main.py` | Циклы наблюдения | Лог перехода готовности (тишина ≠ живой цикл) |
| `services/browser-agent/src/session-manager.test.ts` | Тесты владения вкладками | +тесты `findLiveAdsManagerPage` и «чужую вкладку не трогаем» |
| `services/browser-agent/src/meta-api/service.control.test.ts` | Тесты health-обработчика | Переписать под явный кабинет |
| `tests/unit/test_browser_readiness.py` | Тесты готовности | +контракт кабинета и поведение без офферов |

---

### Task 1: `findLiveAdsManagerPage` — поиск вкладки без побочных эффектов

**Files:**
- Modify: `services/browser-agent/src/session-manager.ts:175-194` (рядом с `findPreferredPrimaryPage`)
- Test: `services/browser-agent/src/session-manager.test.ts`

**Interfaces:**
- Consumes: существующие приватные хелперы модуля `safeBrowserContexts(browser)`, `safeContextPages(context)`, `isPageClosed(page)`, `safePageUrl(page)`, `isAdsManagerUrl(url)`.
- Produces: `export function findLiveAdsManagerPage(browser: Browser | null): Page | null` — первая живая вкладка Ads Manager или `null`. Ничего не создаёт, не навигирует, не закрывает. Task 2 импортирует её в `meta-api/service.ts`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `services/browser-agent/src/session-manager.test.ts`. Импорт `findLiveAdsManagerPage` добавить в существующий `import { ... } from "./session-manager.js";` в шапке файла (там же, где импортируется `findPreferredPrimaryPage`).

```ts
// Проба готовности читает токен с уже открытой вкладки кабинета.
test("findLiveAdsManagerPage возвращает живую вкладку Ads Manager", () => {
  const inboxPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const adsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123",
  };
  const browser = {
    contexts: () => [{ pages: () => [inboxPage, adsPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), adsPage);
});

// В отличие от findPreferredPrimaryPage здесь НЕТ отката на первую попавшуюся
// вкладку: выдать чужую вкладку за Ads Manager значит соврать о готовности канала.
test("findLiveAdsManagerPage не подменяет Ads Manager чужой вкладкой", () => {
  const inboxPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const browser = {
    contexts: () => [{ pages: () => [inboxPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), null);
});

// Закрытая вкладка не является доказательством живого канала.
test("findLiveAdsManagerPage игнорирует закрытые вкладки", () => {
  const closedAdsPage = {
    isClosed: () => true,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123",
  };
  const openAdsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=456",
  };
  const browser = {
    contexts: () => [{ pages: () => [closedAdsPage, openAdsPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), openAdsPage);
});

// Без браузера проба обязана честно ответить «нет страницы», а не упасть.
test("findLiveAdsManagerPage без браузера возвращает null", () => {
  assert.equal(findLiveAdsManagerPage(null), null);
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run:
```bash
cd services/browser-agent && npm test
```
Expected: FAIL — TypeScript-сборка не проходит, `"findLiveAdsManagerPage" is not exported by "./session-manager.js"`.

- [ ] **Step 3: Реализовать хелпер**

Вставить в `services/browser-agent/src/session-manager.ts` сразу после `findPreferredPrimaryPage` (после строки 194):

```ts
/** Живая вкладка Ads Manager без побочных эффектов: ничего не создаёт и не навигирует.
 *
 * Отличие от findPreferredPrimaryPage: здесь нет отката на первую попавшуюся
 * вкладку. Проба здоровья без явно названного кабинета должна честно ответить
 * «нет страницы», а не выдать за Ads Manager чужую вкладку оператора.
 */
export function findLiveAdsManagerPage(browser: Browser | null): Page | null {
  if (!browser) {
    return null;
  }

  for (const context of safeBrowserContexts(browser)) {
    for (const page of safeContextPages(context)) {
      if (isPageClosed(page)) {
        continue;
      }
      if (isAdsManagerUrl(safePageUrl(page))) {
        return page;
      }
    }
  }

  return null;
}
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run:
```bash
cd services/browser-agent && npm test
```
Expected: PASS, четыре новых теста `findLiveAdsManagerPage ...` зелёные.

- [ ] **Step 5: Проверить линт**

Run:
```bash
cd services/browser-agent && npm run lint
```
Expected: без ошибок.

- [ ] **Step 6: Коммит**

```bash
git add services/browser-agent/src/session-manager.ts services/browser-agent/src/session-manager.test.ts
git commit -m "feat(browser-agent): поиск живой вкладки Ads Manager без побочных эффектов"
```

---

### Task 2: Проба здоровья получает кабинет явно и ничего не выдумывает

**Files:**
- Modify: `proto/v1/meta_api.proto:115-130`
- Modify: `services/browser-agent/src/meta-api/service.ts:506-530`
- Test: `services/browser-agent/src/meta-api/service.control.test.ts:600-646`

**Interfaces:**
- Consumes: `findLiveAdsManagerPage(browser)` из Task 1; существующие в `service.ts` `resolveHealthSession`, `withPageRoleLock`, `_getInteractivePage`, `_checkMetaApiHealth`, `extractAdAccountId`, `BROWSER_CONTRACT_VERSION`, `bindGrpcAbort`.
- Produces: контракт `CheckMetaApiHealthRequest.ad_account_id` (строка, 1..32 цифр либо пусто) и ответ с `detail = 'no_ads_manager_page'`, который Task 3 и Task 4 считают известным состоянием.

- [ ] **Step 1: Добавить поле в proto**

В `proto/v1/meta_api.proto` внутри `message CheckMetaApiHealthRequest`, после поля `expected_vision_profile_id = 3;` (строка 129) добавить:

```proto
  // Кабинет, в котором выполняется проба. Задаётся вызывающим явно.
  // Пусто = переиспользовать любую уже открытую вкладку Ads Manager и НЕ
  // открывать новую: адрес текущей вкладки не является account identity.
  string ad_account_id = 4;
```

- [ ] **Step 2: Написать падающие тесты обработчика**

В `services/browser-agent/src/meta-api/service.control.test.ts` заменить тест, который сейчас проверяет `assert.deepEqual(requestedActs, ['123'])` (около строк 600-646), на четыре теста ниже. `unarycall`-хелпер и импорты уже есть в файле.

```ts
  it('health probe uses the cabinet from the request, never the current tab', async () => {
    const requestedActs: string[] = [];
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      // Случайная вкладка чужого кабинета: identity из неё браться не должна.
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: (_session, actId) => {
        requestedActs.push(actId);
        return {} as any;
      },
      checkMetaApiHealth: async (_page, options) => {
        assert.equal(options?.fullProbe, true);
        return {
          healthy: true,
          currentUrl: 'https://adsmanager.facebook.com/?act=2108857220005012',
          tokenPresent: true,
          tokenLength: 200,
          detail: 'ok',
          probePerformed: true,
          probeOk: true,
          probeStatusCode: 200,
          probeDurationMs: 10,
          probeDetail: 'ok',
        };
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: true,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '2108857220005012',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.deepEqual(requestedActs, ['2108857220005012']);
    assert.equal(response.healthy, true);
    assert.equal(response.browser_contract_version, 5);
    assert.equal(response.session_id, 'session-exact');
    assert.equal(response.vision_profile_id, 'profile-exact');
  });

  it('health probe without a cabinet reuses a live tab and creates nothing', async () => {
    let createdPages = 0;
    const adsPage = {
      isClosed: () => false,
      url: () => 'https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=2108857220005012',
    };
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [adsPage] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        createdPages += 1;
        return {} as any;
      },
      checkMetaApiHealth: async (page) => {
        assert.equal(page, adsPage as any);
        return {
          healthy: true,
          currentUrl: 'https://adsmanager.facebook.com/?act=2108857220005012',
          tokenPresent: true,
          tokenLength: 200,
          detail: 'ok',
          probePerformed: false,
          probeOk: false,
          probeStatusCode: 0,
          probeDurationMs: 0,
          probeDetail: 'not_performed',
        };
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: false,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(createdPages, 0);
    assert.equal(response.healthy, true);
  });

  it('health probe without a cabinet and without a tab answers no_ads_manager_page', async () => {
    let createdPages = 0;
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => {
        createdPages += 1;
        return {} as any;
      },
      checkMetaApiHealth: async () => {
        throw new Error('проба не должна запускаться без страницы');
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.checkMetaApiHealth(
        unaryCall({
          session_id: '',
          full_probe: false,
          expected_vision_profile_id: 'profile-exact',
          ad_account_id: '',
        }),
        (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
      );
    });

    assert.equal(createdPages, 0);
    assert.equal(response.healthy, false);
    assert.equal(response.detail, 'no_ads_manager_page');
    assert.equal(response.probe_performed, false);
    assert.equal(response.browser_contract_version, 5);
    assert.equal(response.session_id, 'session-exact');
  });

  it('health probe rejects a malformed cabinet id instead of guessing', async () => {
    const session = {
      id: 'session-exact',
      visionProfileId: 'profile-exact',
      primaryPage: { url: () => 'https://adsmanager.facebook.com/?act=1855748448431929' },
      browser: { contexts: () => [{ pages: () => [] }] },
    };
    const fakeManager = {
      getSessionForVisionProfile: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(fakeManager, {
      getInteractivePage: () => ({} as any),
      checkMetaApiHealth: async () => {
        throw new Error('проба не должна запускаться при кривом кабинете');
      },
    });

    await assert.rejects(
      new Promise<any>((resolve, reject) => {
        handlers.checkMetaApiHealth(
          unaryCall({
            session_id: '',
            full_probe: false,
            expected_vision_profile_id: 'profile-exact',
            ad_account_id: 'act_123abc',
          }),
          (error: unknown, value: unknown) => (error ? reject(error) : resolve(value)),
        );
      }),
    );
  });
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run:
```bash
cd services/browser-agent && npm test
```
Expected: FAIL — `createdPages` равен 1 вместо 0 и `detail` не равен `no_ads_manager_page`, потому что обработчик по-прежнему берёт кабинет из `primaryPage` и создаёт вкладку.

- [ ] **Step 4: Реализовать обработчик**

В `services/browser-agent/src/meta-api/service.ts` добавить `findLiveAdsManagerPage` в существующий импорт из `../session-manager.js`, затем заменить строки 514-522 (от `const actId = ...` до конца вызова `_getInteractivePage`) на:

```ts
      // Кабинет пробы называет вызывающий. Адрес текущей вкладки — случайное
      // состояние браузера, а не identity: раньше проба брала act отсюда и
      // каждые 2 секунды воскрешала вкладку произвольного кабинета, которую
      // оператор не мог закрыть (прод, 17.08.2026).
      const requestedAct = String(req.ad_account_id || '').trim();
      if (requestedAct && !/^\d{1,32}$/.test(requestedAct)) {
        throw new Error('ad_account_id must be 1..32 digits');
      }
      const fullProbe = Boolean(req.full_probe);
      const reusablePage = requestedAct
        ? null
        : findLiveAdsManagerPage(session.browser ?? null);

      if (!requestedAct && !reusablePage) {
        // Без явного кабинета проба ничего не открывает: отсутствие живой
        // вкладки Ads Manager — честный отказ, а не повод создать вкладку.
        if (grpcAbort.controller.signal.aborted) return;
        callback(null, {
          healthy: false,
          current_url: '',
          token_present: false,
          token_length: 0,
          detail: 'no_ads_manager_page',
          probe_performed: false,
          probe_ok: false,
          probe_status_code: 0,
          probe_duration_ms: 0,
          probe_detail: 'not_performed',
          browser_contract_version: BROWSER_CONTRACT_VERSION,
          session_id: session.id,
          vision_profile_id: session.visionProfileId,
        });
        return;
      }

      const lockAct = requestedAct || extractAdAccountId(reusablePage?.url?.()) || '';

      const result = await withPageRoleLock(session.id, 'interactive', lockAct, async () => {
        const page = requestedAct
          ? await _getInteractivePage(
            session,
            requestedAct,
            grpcAbort.controller.signal,
          )
          : (reusablePage as any);
        return _checkMetaApiHealth(page, {
          fullProbe,
          signal: grpcAbort.controller.signal,
        });
      });
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run:
```bash
cd services/browser-agent && npm test
```
Expected: PASS, все четыре новых теста зелёные, остальные тесты `service.control.test.ts` не сломаны.

- [ ] **Step 6: Линт**

Run:
```bash
cd services/browser-agent && npm run lint
```
Expected: без ошибок.

- [ ] **Step 7: Коммит**

```bash
git add proto/v1/meta_api.proto services/browser-agent/src/meta-api/service.ts services/browser-agent/src/meta-api/service.control.test.ts
git commit -m "fix(browser-agent): проба здоровья не выводит кабинет из текущей вкладки"
```

---

### Task 3: Python-клиент передаёт кабинет в пробу

**Files:**
- Modify: `core/meta_api/client.py:2733-2760`
- Modify: `core/meta_api/browser_readiness.py:36-42`
- Regenerate: `clients/python_grpc/v1/meta_api_pb2.py`, `meta_api_pb2.pyi`, `meta_api_pb2_grpc.py`
- Test: `tests/unit/test_browser_readiness.py`

**Interfaces:**
- Consumes: поле `CheckMetaApiHealthRequest.ad_account_id` из Task 2.
- Produces: `MetaApiClient.check_health(*, full_probe: bool = False, expected_profile_id: str | None = None, ad_account_id: str | None = None) -> dict[str, Any]` и такой же расширенный `BrowserReadinessProbeClient` Protocol. Task 4 вызывает `check_health(..., ad_account_id=...)`.

- [ ] **Step 1: Написать падающий контрактный тест**

Добавить в `tests/unit/test_browser_readiness.py`. Импорты `inspect`, `readiness`, `meta_client` в файле уже есть; добавить в шапку `from clients.python_grpc.v1 import meta_api_pb2`.

```python
def test_check_health_contract_carries_explicit_cabinet() -> None:
    """Проба готовности обязана называть кабинет явно, а не наследовать вкладку.

    Инцидент 17.08.2026: кабинет брался из адреса текущей вкладки, и проба
    каждые 2 секунды воскрешала вкладку кабинета, которого нет ни в одном оффере.
    """
    protocol_params = inspect.signature(
        readiness.BrowserReadinessProbeClient.check_health
    ).parameters
    client_params = inspect.signature(meta_client.MetaApiClient.check_health).parameters

    assert "ad_account_id" in protocol_params
    assert "ad_account_id" in client_params
    request = meta_api_pb2.CheckMetaApiHealthRequest(ad_account_id="2108857220005012")
    assert request.ad_account_id == "2108857220005012"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_browser_readiness.py::test_check_health_contract_carries_explicit_cabinet -q
```
Expected: FAIL — `ValueError: Protocol message CheckMetaApiHealthRequest has no "ad_account_id" field`.

- [ ] **Step 3: Регенерировать stubs**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/generate_grpc_stubs.py
```
Expected: перезаписаны `clients/python_grpc/v1/meta_api_pb2.py`, `meta_api_pb2.pyi`, `meta_api_pb2_grpc.py`; `git diff --stat` показывает только эти файлы среди сгенерированных.

- [ ] **Step 4: Расширить клиент и Protocol**

В `core/meta_api/client.py` заменить сигнатуру и построение запроса в `check_health`:

```python
    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
```

и

```python
        req = meta_api_pb2.CheckMetaApiHealthRequest(
            session_id=self.session_id,
            full_probe=full_probe,
            expected_vision_profile_id=(expected_profile_id or "").strip(),
            # Пусто = переиспользовать живую вкладку Ads Manager и не открывать новую.
            ad_account_id=(ad_account_id or "").strip(),
        )
```

В `core/meta_api/browser_readiness.py` заменить Protocol:

```python
class BrowserReadinessProbeClient(Protocol):
    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]: ...
```

- [ ] **Step 5: Убедиться, что тест проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_browser_readiness.py -q
```
Expected: PASS, все тесты файла зелёные.

- [ ] **Step 6: Коммит**

```bash
git add proto/v1/meta_api.proto clients/python_grpc/v1/meta_api_pb2.py clients/python_grpc/v1/meta_api_pb2.pyi clients/python_grpc/v1/meta_api_pb2_grpc.py core/meta_api/client.py core/meta_api/browser_readiness.py tests/unit/test_browser_readiness.py
git commit -m "feat(meta-api): кабинет пробы здоровья передаётся явно"
```

---

### Task 4: Кабинет пробы берётся из активных офферов

**Files:**
- Modify: `core/meta_api/browser_readiness.py:426-500`
- Test: `tests/unit/test_browser_readiness.py`

**Interfaces:**
- Consumes: `check_health(..., ad_account_id=...)` из Task 3; существующая `core.observer.accounts.resolve_scan_account_ids(engine) -> list[str]` (sorted DISTINCT кабинеты активных офферов).
- Produces: `async def resolve_readiness_ad_account_id(engine: AsyncEngine) -> str | None` и поведение: при пустом наборе кабинетов готовность становится `unavailable` с `reason_code="no_configured_cabinet"`, а browser-agent вообще не дёргается.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/unit/test_browser_readiness.py`. В шапку добавить `import uuid` и `from datetime import datetime, timezone` (если их там ещё нет).

```python
class _FakeFence:
    """Заглушка BrowserOperationFence: аренда всегда наша и не теряется."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeFence":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def assert_held(self) -> None:
        return None


class _RecordingProbeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def check_health(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {
            "healthy": True,
            "browser_contract_version": 5,
            "vision_profile_id": "profile-1",
            "session_id": "session-1",
            "detail": "ok",
        }


def _install_readiness_fakes(monkeypatch, *, accounts: list[str]) -> list[dict]:
    """Общая обвязка: фенс, identity, часы и запись публикаций."""
    published: list[dict] = []
    observed_at = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(readiness, "BrowserOperationFence", _FakeFence)
    monkeypatch.setattr(
        readiness,
        "load_vision_readiness_identity",
        _async_return(
            readiness.VisionReadinessIdentity(
                config_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
                profile_id="profile-1",
                config_updated_at=observed_at,
            )
        ),
    )
    monkeypatch.setattr(readiness, "_database_clock", _async_return(observed_at))
    monkeypatch.setattr(readiness, "resolve_scan_account_ids", _async_return(accounts))

    async def _persist(engine, *, identity, observation, writer_instance, ttl_seconds):
        published.append({"kind": "persist", "state": observation.state})
        return observation.state == "ready"

    async def _invalidate(engine, *, writer_instance, state="unavailable", reason_code):
        published.append({"kind": "invalidate", "state": state, "reason_code": reason_code})

    monkeypatch.setattr(readiness, "persist_browser_readiness", _persist)
    monkeypatch.setattr(readiness, "invalidate_browser_readiness", _invalidate)
    return published


def _async_return(value):
    async def _call(*args, **kwargs):
        return value

    return _call


@pytest.mark.asyncio
async def test_readiness_probe_uses_cabinet_from_active_offers(monkeypatch) -> None:
    """Кабинет пробы — детерминированный первый кабинет активных офферов."""
    published = _install_readiness_fakes(
        monkeypatch, accounts=["2108857220005012", "3570379159805007"]
    )
    client = _RecordingProbeClient()

    result = await readiness.probe_and_publish_browser_readiness(
        MagicMock(),
        client,
        writer_instance=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
    )

    assert result is True
    assert len(client.calls) == 1
    assert client.calls[0]["ad_account_id"] == "2108857220005012"
    assert published == [{"kind": "persist", "state": "ready"}]


@pytest.mark.asyncio
async def test_readiness_probe_without_offers_never_touches_browser(monkeypatch) -> None:
    """Нет настроенного кабинета — нет пробы и нет вкладки, а не выдуманный кабинет."""
    published = _install_readiness_fakes(monkeypatch, accounts=[])
    client = _RecordingProbeClient()

    result = await readiness.probe_and_publish_browser_readiness(
        MagicMock(),
        client,
        writer_instance=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
    )

    assert result is False
    assert client.calls == []
    assert published == [
        {
            "kind": "invalidate",
            "state": "unavailable",
            "reason_code": "no_configured_cabinet",
        }
    ]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_browser_readiness.py -q -k readiness_probe
```
Expected: FAIL — `AttributeError: <module 'core.meta_api.browser_readiness'> does not have the attribute 'resolve_scan_account_ids'`.

- [ ] **Step 3: Реализовать резолв кабинета**

В `core/meta_api/browser_readiness.py` добавить импорт рядом с остальными импортами `core`:

```python
from core.observer.accounts import resolve_scan_account_ids
```

Добавить функцию перед `probe_and_publish_browser_readiness`:

```python
async def resolve_readiness_ad_account_id(engine: AsyncEngine) -> str | None:
    """Кабинет пробы готовности — первый кабинет активных офферов.

    Детерминированный выбор из конфигурации, а не из состояния браузера.
    None означает, что настроенного кабинета нет: подтверждать готовность
    money-канала не на чем, и открывать наугад чужую вкладку нельзя.
    """
    accounts = await resolve_scan_account_ids(engine)
    return accounts[0] if accounts else None
```

В `probe_and_publish_browser_readiness` сразу после блока `if identity is None:` вставить:

```python
            probe_account_id = await resolve_readiness_ad_account_id(engine)
            if probe_account_id is None:
                await invalidate_browser_readiness(
                    engine,
                    writer_instance=writer_instance,
                    reason_code="no_configured_cabinet",
                )
                return False
```

и передать кабинет в пробу:

```python
                probe = await client.check_health(
                    full_probe=False,
                    expected_profile_id=identity.profile_id,
                    ad_account_id=probe_account_id,
                )
```

Добавить `"resolve_readiness_ad_account_id"` в `__all__` (в алфавитном порядке, после `"probe_and_publish_browser_readiness"`).

- [ ] **Step 4: Проверить отсутствие циклического импорта**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 python -c "import core.meta_api.browser_readiness"
```
Expected: без вывода и без `ImportError`. Если появится цикл — перенести `from core.observer.accounts import resolve_scan_account_ids` внутрь тела `resolve_readiness_ad_account_id` (так уже сделано для `load_observer_config` в `apps/health_watchdog/main.py:546`). В этом случае тесты Step 1 перестанут работать: `monkeypatch.setattr(readiness, "resolve_scan_account_ids", ...)` больше нечего подменять — тогда патчить надо `core.observer.accounts.resolve_scan_account_ids`.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_browser_readiness.py -q
```
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add core/meta_api/browser_readiness.py tests/unit/test_browser_readiness.py
git commit -m "fix(readiness): кабинет пробы берётся из активных офферов"
```

---

### Task 5: Переход готовности виден в логах

**Files:**
- Modify: `apps/health_watchdog/main.py:1246-1265`
- Test: `tests/unit/test_health_watchdog.py`

**Interfaces:**
- Consumes: `probe_and_publish_browser_readiness(...) -> bool` (без изменений сигнатуры).
- Produces: `browser_readiness_loop` логирует только смену состояния, не каждый тик.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/unit/test_health_watchdog.py`:

Модуль в этом файле импортирован как `hw`, а не `watchdog`; `asyncio`, `logging`, `SimpleNamespace` и `pytest` уже импортированы в шапке — добавлять ничего не нужно.

```python
@pytest.mark.asyncio
async def test_browser_readiness_loop_logs_only_transitions(monkeypatch, caplog) -> None:
    """Цикл раз в 2 секунды не спамит лог, но смену состояния показывает.

    Урок 01.07: молчание в логах неотличимо от зависшего воркера, поэтому
    переход публикуется явно; лог на каждом тике при этом залил бы всё остальное.
    """
    results = iter([True, True, False, False])
    calls = 0
    stop = asyncio.Event()

    async def _probe(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        value = next(results)
        if calls >= 4:
            stop.set()
        return value

    monkeypatch.setattr(hw, "probe_and_publish_browser_readiness", _probe)

    with caplog.at_level(logging.INFO, logger=hw.logger.name):
        await hw.browser_readiness_loop(
            SimpleNamespace(),
            stop=stop,
            engine=SimpleNamespace(),
            interval=0.001,
            ttl_seconds=6,
        )

    transitions = [r.message for r in caplog.records if "browser readiness" in r.message]
    assert calls == 4
    assert transitions == [
        "browser readiness: ready",
        "browser readiness: not ready",
    ]
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_health_watchdog.py -q -k readiness_loop_logs
```
Expected: FAIL — `assert [] == ['browser readiness: ready', 'browser readiness: not ready']`, потому что цикл пока ничего не логирует.

- [ ] **Step 3: Реализовать лог перехода**

В `apps/health_watchdog/main.py` заменить тело `browser_readiness_loop`:

```python
    """Continuously publish bounded v5/profile evidence for task scheduling."""
    # Публикуем только смену состояния: цикл идёт раз в 2 секунды, и лог на
    # каждом тике утопил бы остальные записи. Причина недоступности видна в
    # browser_channel_readiness.reason_code и в снимке оператора.
    last_published: bool | None = None
    while not stop.is_set():
        published_ready = await probe_and_publish_browser_readiness(
            engine,
            meta_client,
            writer_instance=_BROWSER_READINESS_WRITER_INSTANCE,
            ttl_seconds=ttl_seconds,
        )
        if published_ready != last_published:
            logger.info(
                "browser readiness: %s",
                "ready" if published_ready else "not ready",
            )
            last_published = published_ready
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.1, interval))
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_health_watchdog.py -q
```
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add apps/health_watchdog/main.py tests/unit/test_health_watchdog.py
git commit -m "feat(health-watchdog): переход готовности браузера виден в логах"
```

---

### Task 6: Чужие вкладки остаются нетронутыми

**Files:**
- Test: `services/browser-agent/src/session-manager.test.ts`

**Interfaces:**
- Consumes: `SessionManager.ensureInteractivePage(session, { actId })` и локальный хелпер `makeSession(overrides)` (`services/browser-agent/src/session-manager.test.ts:167`).
- Produces: regression-тест инварианта «health-путь переиспользует существующую вкладку и никогда её не навигирует и не закрывает».

- [ ] **Step 1: Написать regression-тест**

Добавить в `services/browser-agent/src/session-manager.test.ts`:

```ts
// Вкладку кабинета мог открыть оператор руками. Проба готовности обязана её
// переиспользовать: перезагрузка сбросила бы его фильтры и выделение.
test("ensureInteractivePage переиспользует вкладку кабинета, не навигируя и не закрывая её", async () => {
  const manager = new SessionManager();
  let gotoCalls = 0;
  let closeCalls = 0;
  const adsPage = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=2108857220005012",
    goto: async () => {
      gotoCalls += 1;
    },
    close: async () => {
      closeCalls += 1;
    },
  };
  const browser = {
    isConnected: () => true,
    contexts: () => [{ pages: () => [adsPage] }],
  };
  const session = makeSession({ browser });

  const page = await manager.ensureInteractivePage(session, {
    actId: "2108857220005012",
  });

  assert.equal(page, adsPage as any);
  assert.equal(gotoCalls, 0);
  assert.equal(closeCalls, 0);
});
```

- [ ] **Step 2: Запустить тест**

Run:
```bash
cd services/browser-agent && npm test
```
Expected: PASS сразу — инвариант уже выполняется, потому что health-путь не передаёт `amColumnsQs`, и ветка перенавигации (`session-manager.ts:851-878`) не срабатывает. Тест фиксирует это, чтобы будущая правка не начала переоткрывать чужую вкладку.

Если тест падает — это настоящий дефект: остановиться и разобрать причину, а не подгонять тест.

- [ ] **Step 3: Коммит**

```bash
git add services/browser-agent/src/session-manager.test.ts
git commit -m "test(browser-agent): health-путь не трогает чужую вкладку кабинета"
```

---

### Task 7: Полные гейты и проверка на проде

**Files:**
- Изменений кода нет; задача — доказательства.

**Interfaces:**
- Consumes: всё, что сделано в Task 1-6.
- Produces: зелёные гейты и наблюдаемое поведение на проде.

- [ ] **Step 1: Backend-гейты**

Run:
```bash
ruff check .
```
Expected: `All checks passed!`

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit -q
```
Expected: PASS без новых падений.

- [ ] **Step 2: Integration-тесты на изолированной БД**

Поднять одноразовый PostgreSQL и прогнать integration-набор против него — боевую БД `:5433` не трогать.

Run:
```bash
docker run --rm -d --name fb-agent-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test -p 55432:5432 postgres:16
```
Expected: печатается id контейнера.

Run:
```bash
DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/test PYTHONDONTWRITEBYTECODE=1 pytest tests/integration -q
```
Expected: PASS. До этой работы уже падали 7 тестов, не связанных с готовностью браузера (offers `name=code`, дефолты settings, идемпотентность TMA) — они не считаются регрессией. Новых падений быть не должно, и ни одно падение не должно упоминать `browser_readiness`, `check_health` или `meta_api`.

Run:
```bash
docker rm -f fb-agent-test-db
```
Expected: печатается имя контейнера.

- [ ] **Step 3: Гейты browser-agent**

Run:
```bash
cd services/browser-agent && npm run lint && npm run build && npm test
```
Expected: линт чист, сборка проходит, все тесты зелёные.

- [ ] **Step 4: Проверка контракта proto ↔ stubs**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/generate_grpc_stubs.py && git status --porcelain clients/python_grpc
```
Expected: пустой вывод `git status` — сгенерированные файлы совпадают с закоммиченными.

- [ ] **Step 5: Релиз**

Мерж в `main` и деплой выполняет владелец: `push` в `main` не выкатывает, деплой требует `workflow_dispatch`. См. блок «Что требуется от владельца».

- [ ] **Step 6: Проверка на проде после выката**

Убедиться, что вкладка воскресает только для кабинета оффера:

```bash
ssh root@62.60.150.133 'docker exec fb_agent_desktop-vision-webtop-1 python3 /tmp/tabtest.py'
```
Expected: вкладка возвращается, и её адрес содержит один из кабинетов активных офферов (`2108857220005012` или `3570379159805007`), а не посторонний кабинет.

Убедиться, что money-канал остался готов:

```bash
ssh root@62.60.150.133 "docker exec fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c 'select state, reason_code, readiness_expires_at > now() as fresh from browser_channel_readiness'"
```
Expected: `state = ready`, `fresh = t`.

Убедиться, что лог показывает переход, а не тишину:

```bash
ssh root@62.60.150.133 'docker logs --since 10m fb_agent_app-health_watchdog-1 2>&1 | grep "browser readiness"'
```
Expected: одна строка `browser readiness: ready`.

---

## Что требуется от владельца

1. **Подтвердить поведение при выключенном сканировании.** Ты просил создавать вкладку «при включении сканирования». План делает это **независимо от тумблера скана**, и вот почему: готовность канала `meta_api` гейтит выдачу money-задач (`core/tasks/queue.py:1015-1039`). Если при выключенном скане проба перестанет подтверждать готовность, перестанет работать ручной стоп и запуск объявлений из веб-интерфейса и Телеграма. Поэтому одна вкладка кабинета оффера держится всегда — но это законный кабинет из офферов, а не случайный. Скажи, если хочешь наоборот: тогда при выключенном скане ручные money-команды будут вставать в очередь и ждать.
2. **Запустить деплой.** После мержа в `main` выкат не происходит сам: нужен ручной `workflow_dispatch` на workflow деплоя. Я могу подготовить всё до этой точки.
3. **Посмотреть глазами после выката.** Открой стол и убедись, что вкладка, которая возвращается, — кабинет оффера, и что закрытие остальных вкладок больше ничего не воскрешает.

## Что план сознательно не делает

- Не меняет частоту пробы (2 секунды). После правки проба перестаёт создавать вкладки, и частота больше не является проблемой.
- Не трогает `check_meta_api_channel` (полная проба раз в 300 секунд): она уходит без кабинета и после правки просто переиспользует живую вкладку. Отдельного кабинета ей не нужно.
- Не удаляет `session.primaryPage` и `session.lastAdsManagerUrl`: они остаются нужны сканеру и self-heal, из пробы здоровья убирается только их использование как identity.
- Не добавляет ротацию кабинетов при недоступности первого: если первый кабинет активных офферов не открывается, готовность честно падает в `unavailable`. Ротация — отдельная задача, если это когда-нибудь понадобится.
