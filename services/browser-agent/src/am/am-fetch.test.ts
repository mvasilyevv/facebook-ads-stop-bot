// Тесты чистой логики am-fetch: выбор постера видео + retry-on-transient метрик +
// детект разлогина/чекпоинта (MID X-16: слепой канал = слитый бюджет).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  acquireGraphContext,
  invalidateGraphContext,
  isLoginRequiredResponse,
  pickPreferredThumb,
  retryTransient,
  runAmScanWithContext,
} from './am-fetch.js';

test('GraphContext cache is isolated by session and ad account', async () => {
  const sessionId = 'session-two-cabinets';
  const requestedAccounts = ['111', '222'];
  let reloads = 0;
  let requestListener: ((request: { url(): string }) => void) | undefined;
  const page = {
    on: (event: string, listener: (request: { url(): string }) => void) => {
      if (event === 'request') requestListener = listener;
    },
    off: (event: string, listener: (request: { url(): string }) => void) => {
      if (event === 'request' && requestListener === listener) requestListener = undefined;
    },
    reload: async () => {
      const account = requestedAccounts[reloads];
      reloads += 1;
      requestListener?.({
        url: () =>
          `https://adsmanager-graph.facebook.com/v22.0/act_${account}/am_tabular?access_token=token-${account}`,
      });
    },
  };

  invalidateGraphContext(sessionId, '111');
  invalidateGraphContext(sessionId, '222');
  const first = await acquireGraphContext(page as any, sessionId, { expectedActId: '111' });
  const second = await acquireGraphContext(page as any, sessionId, { expectedActId: '222' });
  const firstAgain = await acquireGraphContext(page as any, sessionId, {
    expectedActId: '111',
  });

  assert.equal(first.ctx.actId, 'act_111');
  assert.equal(second.ctx.actId, 'act_222');
  assert.equal(firstAgain.ctx.accessToken, 'token-111');
  assert.equal(firstAgain.sniffed, false);
  assert.equal(reloads, 2);
});

// Предпочитаем кадр с is_preferred=true (Meta помечает «главный» кадр видео).
test('pickPreferredThumb: берёт is_preferred', () => {
  const uri = pickPreferredThumb([
    { uri: 'https://cdn/a.jpg', is_preferred: false, width: 1280 },
    { uri: 'https://cdn/b.jpg', is_preferred: true, width: 640 },
  ]);
  assert.equal(uri, 'https://cdn/b.jpg');
});

// Без is_preferred — самый широкий кадр (максимальное качество для drawer).
test('pickPreferredThumb: без preferred — самый широкий', () => {
  const uri = pickPreferredThumb([
    { uri: 'https://cdn/small.jpg', width: 320 },
    { uri: 'https://cdn/big.jpg', width: 1280 },
    { uri: 'https://cdn/mid.jpg', width: 720 },
  ]);
  assert.equal(uri, 'https://cdn/big.jpg');
});

// Пустой/некорректный вход — null (best-effort, без падения).
test('pickPreferredThumb: пустой/мусорный вход → null', () => {
  assert.equal(pickPreferredThumb([]), null);
  assert.equal(pickPreferredThumb(undefined), null);
  assert.equal(pickPreferredThumb(null), null);
  assert.equal(pickPreferredThumb('x'), null);
  assert.equal(pickPreferredThumb([{ width: 100 }]), null); // нет uri
});

// retryTransient: успех с первого раза — без повторов и без ожидания.
test('retryTransient: success сразу → 1 вызов', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return { ok: true } as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => !x.ok },
  );
  assert.equal(calls, 1);
  assert.equal(r.ok, true);
});

// retryTransient: транзиент, затем success — останавливается на успехе.
test('retryTransient: transient → success на 2-й попытке', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return (calls < 2 ? { __amError: true } : { ok: true }) as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => Boolean(x.__amError) },
  );
  assert.equal(calls, 2); // initial + 1 retry
  assert.equal(r.ok, true);
});

// retryTransient: всё время транзиент — исчерпали попытки, вернули последний результат.
test('retryTransient: исчерпание → последний transient-результат', async () => {
  let calls = 0;
  const r = await retryTransient(
    async () => {
      calls += 1;
      return { __amError: true, n: calls } as Record<string, unknown>;
    },
    { delaysMs: [0, 0], isTransient: (x) => Boolean(x.__amError) },
  );
  assert.equal(calls, 3); // initial + 2 retries (delaysMs.length=2)
  assert.equal(r.__amError, true);
});

// --- isLoginRequiredResponse: детект разлогина/чекпоинта ---

// fetch увёл на login.php (redirected=true) — сессия протухла, нужен ре-логин.
test('isLoginRequiredResponse: redirect на login.php → true', () => {
  assert.equal(
    isLoginRequiredResponse({
      __amError: true,
      status: 200,
      redirected: true,
      finalUrl: 'https://www.facebook.com/login.php?next=...',
      contentType: 'text/html',
      body: '<!doctype html><html>...',
    }),
    true,
  );
});

// Редирект на checkpoint (verify identity) — тоже разлогин-класс.
test('isLoginRequiredResponse: redirect на checkpoint → true', () => {
  assert.equal(
    isLoginRequiredResponse({
      __amError: true,
      redirected: true,
      finalUrl: 'https://www.facebook.com/checkpoint/?next',
    }),
    true,
  );
});

// HTML вместо JSON (Meta отдала login-страницу) без явного redirect-флага.
test('isLoginRequiredResponse: HTML-тело вместо JSON → true', () => {
  assert.equal(
    isLoginRequiredResponse({
      __amError: true,
      status: 200,
      redirected: false,
      contentType: 'text/html; charset=utf-8',
      body: '<!DOCTYPE html><html><head><title>Log in to Facebook</title>',
    }),
    true,
  );
});

// Graph error 190 с login-subcode 463 (session expired) → true.
test('isLoginRequiredResponse: 190 + subcode 463 → true', () => {
  assert.equal(
    isLoginRequiredResponse({
      error: { code: 190, error_subcode: 463, type: 'OAuthException', message: 'Session expired' },
    }),
    true,
  );
});

// Graph error 190 с login-текстом, но без subcode → true (эвристика по message).
test('isLoginRequiredResponse: 190 с текстом про re-login → true', () => {
  assert.equal(
    isLoginRequiredResponse({
      error: { code: 190, type: 'OAuthException', message: 'The user must log in again.' },
    }),
    true,
  );
});

// Обычный сетевой блип (__amError без redirect/HTML) — НЕ разлогин (это транзиент).
test('isLoginRequiredResponse: сетевой блип → false', () => {
  assert.equal(
    isLoginRequiredResponse({ __amError: true, message: 'Failed to fetch' }),
    false,
  );
});

// 190 без login-признаков (короткоживущий токен протух) — НЕ login_required (re-sniff чинит).
test('isLoginRequiredResponse: 190 без login-subcode/текста → false', () => {
  assert.equal(
    isLoginRequiredResponse({
      error: { code: 190, type: 'OAuthException', message: 'Error validating access token' },
    }),
    false,
  );
});

// Rate-limit (code 17) — канал жив, не разлогин.
test('isLoginRequiredResponse: rate-limit code 17 → false', () => {
  assert.equal(
    isLoginRequiredResponse({ error: { code: 17, message: 'User request limit reached' } }),
    false,
  );
});

// Нормальный JSON-ответ без ошибок и пустой вход → false.
test('isLoginRequiredResponse: чистый ответ / пустой вход → false', () => {
  assert.equal(isLoginRequiredResponse({ data: [] }), false);
  assert.equal(isLoginRequiredResponse(null), false);
  assert.equal(isLoginRequiredResponse(undefined), false);
});

test('gRPC AbortSignal прерывает in-page fetch текущего am-скана', async () => {
  const abort = new AbortController();
  let startedResolve!: () => void;
  const started = new Promise<void>((resolve) => { startedResolve = resolve; });
  let finishFetch!: (value: Record<string, unknown>) => void;
  let abortEvaluations = 0;
  const page = {
    evaluate: async (_fn: any, args: any) => {
      if (args && typeof args === 'object' && 'url' in args) {
        startedResolve();
        return new Promise<Record<string, unknown>>((resolve) => { finishFetch = resolve; });
      }
      if (typeof args === 'string') {
        abortEvaluations += 1;
        finishFetch?.({ __amError: true, __amCancelled: true, message: 'AbortError' });
      }
      return undefined;
    },
  };
  const pending = runAmScanWithContext(
    page as any,
    {
      accessToken: 'token',
      actId: 'act_123',
      apiVersion: 'v22.0',
      graphOrigin: 'https://adsmanager-graph.facebook.com',
    },
    { campaignIds: [], datePreset: 'today' },
    { signal: abort.signal, operationId: 'scan-cancel-test' },
  );

  await started;
  abort.abort('grpc_cancelled');

  await assert.rejects(pending, /cancelled/);
  assert.ok(abortEvaluations >= 1);
});
