// H-8 (BA-3): executeGraphCall error-mapping. Канал мутаций (pause_ad/activate_ad/
// budget/create) опирается на классификацию error.code (190 → токен протух,
// -1/-2/-3 → token/network/page-evaluate). Регресс на разбор error-блока Meta.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { executeGraphCall, checkMetaApiHealth } from './client.js';

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
