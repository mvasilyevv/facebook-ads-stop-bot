// H-8 (BA-3): executeGraphCall error-mapping. Канал мутаций (pause_ad/activate_ad/
// budget/create) опирается на классификацию error.code (190 → токен протух,
// -1/-2/-3/-4 → token/network/page-evaluate/proven-pre-send). Регресс на разбор
// error-блока Meta.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  executeGraphCall,
  checkMetaApiHealth,
  isLoginRequiredError,
  graphFetchInPage,
} from './client.js';

// Мок Playwright Page: page.evaluate(fn, args) → вызываем evalImpl(args) (fn игнорируем).
function mockPage(evalImpl: (args: any) => any): any {
  return {
    evaluate: async (_fn: any, args: any) => evalImpl(args),
  };
}

// Мок страницы для checkMetaApiHealth: различает token-extraction evaluate (без args)
// и graph-fetch evaluate из executeGraphCall (args с .endpoint). Считает реальные
// graph-вызовы (graphEvalCount) — нужно для проверки кеша probe.
function mockHealthPage(opts: {
  url?: string;
  isClosed?: boolean;
  token?: { present: boolean; length: number };
  graph?: () => { status_code: number; response_json: string };
}): { page: any; graphEvalCount: () => number } {
  let graphEvals = 0;
  const page = {
    isClosed: () => opts.isClosed ?? false,
    url: () => opts.url ?? 'https://adsmanager.facebook.com/adsmanager/manage/campaigns',
    waitForFunction: async () => true,
    evaluate: async (_fn: any, args: any) => {
      if (args && typeof args === 'object' && 'endpoint' in args) {
        graphEvals += 1;
        return opts.graph ? opts.graph() : { status_code: 200, response_json: '{"id":"123"}' };
      }
      const t = opts.token ?? { present: true, length: 200 };
      return { present: t.present, length: t.length };
    },
  };
  return { page, graphEvalCount: () => graphEvals };
}

const baseParams = { method: 'GET' as const, endpoint: '/me', queryParams: {} };

describe('executeGraphCall error-mapping (H-8)', () => {
  it('rejects method override variants before touching the browser page', async () => {
    let pageCalls = 0;
    const page = {
      waitForFunction: async () => {
        pageCalls += 1;
        return true;
      },
      evaluate: async () => {
        pageCalls += 1;
        return { status_code: 200, response_json: '{}' };
      },
    };
    const variants: Array<{
      endpoint?: string;
      queryParams?: Record<string, string>;
      bodyJson?: string;
    }> = [
      { queryParams: { method: 'post' } },
      { queryParams: { MeThOd: 'POST' } },
      { queryParams: { '%256dethod': 'post' } },
      { queryParams: { '%25252525256dethod': 'post' } },
      { queryParams: { method: 'get', METHOD: 'post' } },
      { endpoint: '/me?method=post&method=get' },
      { endpoint: '/me%253Fmethod%253Dpost' },
      { endpoint: '/me%25252525253Fmethod%25252525253Dpost' },
      { bodyJson: '{"method":"post"}' },
      { bodyJson: '{"\\u006dethod":"post"}' },
      { bodyJson: '{"%256dethod":"post"}' },
      { bodyJson: '{"%25252525256dethod":"post"}' },
      { bodyJson: '{"method":"GET","method":"POST"}' },
      { bodyJson: 'method%3Dpost' },
    ];

    for (const variant of variants) {
      await assert.rejects(
        () => executeGraphCall(page as any, {
          method: 'GET',
          endpoint: variant.endpoint ?? '/me',
          queryParams: variant.queryParams ?? {},
          bodyJson: variant.bodyJson,
        }),
        /method|semantics|query\/fragment/i,
      );
    }
    assert.equal(pageCalls, 0);
  });

  it('успешный ответ → error undefined, statusCode/responseJson проброшены', async () => {
    const page = mockPage(() => ({ status_code: 200, response_json: '{"id":"act_1"}' }));
    const r = await executeGraphCall(page, baseParams);
    assert.equal(r.statusCode, 200);
    assert.equal(r.error, undefined);
    assert.equal(r.responseJson, '{"id":"act_1"}');
    assert.ok(r.durationMs >= 0);
  });

  it('Meta error 190 (OAuth, протухший токен) → code/subcode/type/fbtrace разобраны', async () => {
    const body = JSON.stringify({
      error: {
        code: 190,
        error_subcode: 460,
        type: 'OAuthException',
        message: 'Error validating access token',
        fbtrace_id: 'TRACE1',
      },
    });
    const page = mockPage(() => ({ status_code: 400, response_json: body }));
    const r = await executeGraphCall(page, baseParams);
    assert.equal(r.statusCode, 400);
    assert.equal(r.error?.code, 190);
    assert.equal(r.error?.subcode, 460);
    assert.equal(r.error?.type, 'OAuthException');
    assert.equal(r.error?.fbtraceId, 'TRACE1');
  });

  it('token not found (code -1) пробрасывается из page.evaluate', async () => {
    const body = JSON.stringify({
      error: { code: -1, type: 'TokenNotFound', message: 'EAA-токен не найден' },
    });
    const page = mockPage(() => ({ status_code: 0, response_json: body }));
    const r = await executeGraphCall(page, baseParams);
    assert.equal(r.error?.code, -1);
    assert.equal(r.error?.type, 'TokenNotFound');
  });

  it('network error (code -2) пробрасывается', async () => {
    const body = JSON.stringify({
      error: { code: -2, type: 'NetworkError', message: 'Timeout после 30000мс' },
    });
    const page = mockPage(() => ({ status_code: 0, response_json: body }));
    const r = await executeGraphCall(page, baseParams);
    assert.equal(r.error?.code, -2);
    assert.equal(r.error?.type, 'NetworkError');
  });

  it('page.evaluate бросает (context destroyed) → code -3 PageEvaluateError', async () => {
    const page = mockPage(() => {
      throw new Error('Execution context was destroyed');
    });
    const r = await executeGraphCall(page, {
      method: 'POST',
      endpoint: '/act_1/ads',
      queryParams: {},
    });
    assert.equal(r.statusCode, 0);
    assert.equal(r.error?.code, -3);
    assert.equal(r.error?.type, 'PageEvaluateError');
    assert.match(r.error?.message ?? '', /context was destroyed/);
  });

  it('success с массивом data → без error', async () => {
    const page = mockPage(() => ({ status_code: 200, response_json: '{"data":[]}' }));
    const r = await executeGraphCall(page, {
      method: 'GET',
      endpoint: '/act_1/campaigns',
      queryParams: {},
    });
    assert.equal(r.error, undefined);
  });

  it('AbortSignal вызывает browser-side abort и сохраняет UNKNOWN-семантику (-2)', async () => {
    const abort = new AbortController();
    let notifyMainStarted!: () => void;
    const mainStarted = new Promise<void>((resolve) => { notifyMainStarted = resolve; });
    let finishGraph!: (value: { status_code: number; response_json: string }) => void;
    let abortEvaluations = 0;
    const page = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          notifyMainStarted();
          return new Promise<{ status_code: number; response_json: string }>((resolve) => {
            finishGraph = resolve;
          });
        }
        if (typeof args === 'string') {
          abortEvaluations += 1;
          finishGraph?.({
            status_code: 0,
            response_json: JSON.stringify({
              error: {
                code: -2,
                type: 'NetworkError',
                message: 'gRPC request cancelled; external result is unknown',
              },
            }),
          });
        }
        return undefined;
      },
    };

    const pending = executeGraphCall(page as any, baseParams, {
      signal: abort.signal,
      operationId: 'cancel-test',
    });
    await mainStarted;
    abort.abort('grpc_cancelled');
    const result = await pending;

    assert.ok(abortEvaluations >= 1, 'cancel дошёл до отдельного page.evaluate(abort)');
    assert.equal(result.statusCode, 0);
    assert.equal(result.error?.code, -2);
    assert.equal(result.error?.type, 'NetworkError');
  });

  // Issue 195: отмена во время прогрева токена (waitForFunction) случается ДО
  // того, как page.evaluate(graphFetchInPage) вообще вызван — fetch() не мог
  // стартовать. Это доказанный pre-send, не тот же код, что «мог дойти до Meta».
  it('отмена до page.evaluate (прогрев токена) → доказанный pre-send код, не -2', async () => {
    const abort = new AbortController();
    let evaluateCalls = 0;
    const page = {
      // Никогда не резолвится сама — единственный способ выйти отсюда это отмена.
      waitForFunction: async () => new Promise(() => undefined),
      // clearInPageFetchOperation/abortInPageFetches тоже дёргают evaluate (со
      // строковым operationId) — считаем только вызов graphFetchInPage (args-объект
      // с endpoint), как и в существующем -2 тесте выше.
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          evaluateCalls += 1;
        }
        return { status_code: 200, response_json: '{}' };
      },
    };

    const pending = executeGraphCall(page as any, baseParams, { signal: abort.signal });
    abort.abort('grpc_cancelled_during_warmup');
    const result = await pending;

    assert.equal(evaluateCalls, 0, 'page.evaluate(graphFetchInPage) не должен был вызываться');
    assert.equal(result.statusCode, 0);
    assert.notEqual(result.error?.code, -2, 'доказанный pre-send не должен склеиваться с -2');
    assert.equal(result.error?.code, -4);
    assert.equal(result.error?.type, 'CancelledBeforeSend');
  });
});

// Issue 195: graphFetchInPage исполняется внутри страницы через page.evaluate,
// но не ссылается ни на что из объемлющего модуля — поэтому его можно вызвать
// напрямую в Node (document — единственная браузерная глобаль без нативного
// аналога, стабим её вручную).
describe('graphFetchInPage: pre-send классификация отмены (issue 195)', () => {
  const inPageArgs = {
    method: 'GET',
    endpoint: '/me',
    queryParams: {},
    bodyJson: null,
    timeoutMs: 5_000,
    apiVersion: 'v22.0',
    operationId: 'presend-test',
  };

  function withStubDocument<T>(run: () => Promise<T>): Promise<T> {
    const token = `EAA${'x'.repeat(120)}`;
    (globalThis as any).document = { documentElement: { innerHTML: token } };
    return run().finally(() => {
      delete (globalThis as any).document;
      delete (globalThis as any).__fbAgentFetchAbort;
    });
  }

  // Красный на текущем коде до фикса: cancelled взведён РАНЬШЕ, чем controller.abort()
  // вызывается внутри graphFetchInPage (строго перед fetch()) — сеть не тронута,
  // но до фикса это неотличимо от отмены, поймавшей уже стартовавший fetch (-2).
  it('cancelled уже true до вызова fetch() → -4 CancelledBeforeSend, не -2', async () => {
    await withStubDocument(async () => {
      (globalThis as any).__fbAgentFetchAbort = {
        controllers: new Map(),
        cancelled: new Set([inPageArgs.operationId]),
      };
      const result = await graphFetchInPage(inPageArgs);
      const parsed = JSON.parse(result.response_json);
      assert.equal(result.status_code, 0);
      assert.notEqual(parsed.error?.code, -2, 'доказанный pre-send не должен склеиваться с -2');
      assert.equal(parsed.error?.code, -4);
      assert.equal(parsed.error?.type, 'CancelledBeforeSend');
    });
  });

  // Контрольная группа: cancelled НЕ взведён заранее — обычный сетевой сбой
  // (fetch падает сам, без всякой отмены) остаётся -2/NetworkError, как и раньше.
  // fetch подменяется, чтобы не ходить в реальную сеть из юнит-теста.
  it('обычный сетевой сбой без предварительной отмены остаётся -2 NetworkError', async () => {
    const realFetch = globalThis.fetch;
    (globalThis as any).fetch = async () => {
      throw new TypeError('Failed to fetch');
    };
    try {
      await withStubDocument(async () => {
        const result = await graphFetchInPage({
          ...inPageArgs,
          operationId: 'presend-test-network-fail',
        });
        const parsed = JSON.parse(result.response_json);
        assert.equal(result.status_code, 0);
        assert.equal(parsed.error?.code, -2);
        assert.equal(parsed.error?.type, 'NetworkError');
        assert.match(parsed.error?.message ?? '', /Failed to fetch/);
      });
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});

// Инцидент 2026-06-19: token-only health давал false-positive «healthy» при мёртвом
// сетевом канале (Failed to fetch). full_probe делает реальный GET /me — ловит это.
describe('checkMetaApiHealth full_probe (incident 2026-06-19)', () => {
  // Token-only режим (без fullProbe): сетевой probe НЕ выполняется (graphEvalCount=0).
  it('token-only: probe не выполняется, probe_performed=false', async () => {
    const { page, graphEvalCount } = mockHealthPage({});
    const r = await checkMetaApiHealth(page);
    assert.equal(r.healthy, true);
    assert.equal(r.probePerformed, false);
    assert.equal(r.probeDetail, 'not_performed');
    assert.equal(graphEvalCount(), 0);
  });

  // full_probe и реальный GET /me=200 → канал жив: probe_ok, healthy.
  it('full_probe success (200) → probe_ok=true, healthy=true', async () => {
    const { page, graphEvalCount } = mockHealthPage({
      graph: () => ({ status_code: 200, response_json: '{"id":"123"}' }),
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.healthy, true);
    assert.equal(r.probePerformed, true);
    assert.equal(r.probeOk, true);
    assert.equal(r.probeStatusCode, 200);
    assert.equal(r.probeDetail, 'ok');
    assert.equal(graphEvalCount(), 1);
  });

  // КЛЮЧЕВОЙ кейс: токен present, URL верный, но fetch падает Failed to fetch (code -2).
  // token-only вернул бы healthy=true (false-positive); full_probe ловит network-down.
  it('full_probe Failed to fetch (-2) → healthy=false, probe_network_down', async () => {
    const body = JSON.stringify({
      error: { code: -2, type: 'NetworkError', message: 'Failed to fetch' },
    });
    const { page } = mockHealthPage({
      graph: () => ({ status_code: 0, response_json: body }),
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.tokenPresent, true);
    assert.equal(r.healthy, false);
    assert.equal(r.probeOk, false);
    assert.equal(r.probeDetail, 'probe_network_down');
    assert.equal(r.detail, 'probe_network_down');
  });

  // full_probe и Meta вернула 190 (протух токен) → канал мёртв для мутаций.
  it('full_probe token invalid (190) → healthy=false, probe_token_invalid', async () => {
    const body = JSON.stringify({
      error: { code: 190, type: 'OAuthException', message: 'Error validating access token' },
    });
    const { page } = mockHealthPage({
      graph: () => ({ status_code: 400, response_json: body }),
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.healthy, false);
    assert.equal(r.probeDetail, 'probe_token_invalid');
  });

  // MID X-16: 190 с login-subcode 463 (session expired) = РАЗЛОГИН профиля, не просто
  // протухший токен. Отдельный маркер login_required → health_watchdog шлёт «нужен ре-логин».
  it('full_probe разлогин (190 + subcode 463) → healthy=false, login_required', async () => {
    const body = JSON.stringify({
      error: {
        code: 190,
        error_subcode: 463,
        type: 'OAuthException',
        message: 'Session has expired',
      },
    });
    const { page } = mockHealthPage({
      graph: () => ({ status_code: 400, response_json: body }),
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.healthy, false);
    assert.equal(r.probeDetail, 'login_required');
    assert.equal(r.detail, 'login_required');
  });

  // Meta-side rate-limit (code 17): fetch ДОШЁЛ до Meta → канал жив (healthy=true),
  // но probe_ok=false. Согласовано с autostop_alert.is_channel_down_error.
  it('full_probe rate-limit (17) → healthy=true, probe_ok=false', async () => {
    const body = JSON.stringify({
      error: { code: 17, type: 'OAuthException', message: 'User request limit reached' },
    });
    const { page } = mockHealthPage({
      graph: () => ({ status_code: 400, response_json: body }),
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.healthy, true);
    assert.equal(r.probeOk, false);
    assert.equal(r.probeDetail, 'meta_error:17');
  });

  // Кеш: два full_probe подряд в пределах TTL → реальный fetch ровно один раз.
  it('кеш: два full_probe в пределах TTL → один реальный fetch', async () => {
    const { page, graphEvalCount } = mockHealthPage({
      graph: () => ({ status_code: 200, response_json: '{"id":"123"}' }),
    });
    await checkMetaApiHealth(page, { fullProbe: true, cacheTtlMs: 60_000 });
    await checkMetaApiHealth(page, { fullProbe: true, cacheTtlMs: 60_000 });
    assert.equal(graphEvalCount(), 1);
  });

  // Нет токена → probe не нужен, healthy=false, probe не выполнялся.
  it('нет токена → token_not_found, probe не выполняется', async () => {
    const { page, graphEvalCount } = mockHealthPage({
      token: { present: false, length: 0 },
    });
    const r = await checkMetaApiHealth(page, { fullProbe: true });
    assert.equal(r.healthy, false);
    assert.equal(r.detail, 'token_not_found');
    assert.equal(r.probePerformed, false);
    assert.equal(graphEvalCount(), 0);
  });
});

// isLoginRequiredError: чистый классификатор разлогина/чекпоинта (MID X-16).
describe('isLoginRequiredError', () => {
  // login-subcode 459 (checkpoint) → разлогин.
  it('190 + subcode 459 (checkpoint) → true', () => {
    assert.equal(isLoginRequiredError({ code: 190, subcode: 459, type: 'OAuthException' }), true);
  });

  // По тексту про re-login без subcode → true.
  it('190 с текстом про log in → true', () => {
    assert.equal(
      isLoginRequiredError({ code: 190, message: 'The session has been invalidated, please log in' }),
      true,
    );
  });

  // Реальный текст Meta 18.08.2026: ни «expired», ни «log in», ни subcode — канал
  // ослеп на 4.5 часа и считался рядовым протуханием токена.
  it('190 «session has been invalidated ... changed their password» → true', () => {
    assert.equal(
      isLoginRequiredError({
        code: 190,
        message:
          'Error validating access token: The session has been invalidated because the user ' +
          'changed their password or Facebook has changed the session for security reasons.',
      }),
      true,
    );
  });

  // 190 без login-признаков (обычное протухание токена) → false (это не разлогин).
  it('190 без login-subcode/текста → false', () => {
    assert.equal(
      isLoginRequiredError({ code: 190, subcode: 0, message: 'Error validating access token' }),
      false,
    );
  });

  // Не-OAuth ошибка (rate-limit) → false.
  it('code 17 (rate-limit) → false', () => {
    assert.equal(isLoginRequiredError({ code: 17, message: 'limit reached' }), false);
  });

  // Пустой/undefined вход → false.
  it('undefined/пустой вход → false', () => {
    assert.equal(isLoginRequiredError(undefined), false);
    assert.equal(isLoginRequiredError(null), false);
  });
});
