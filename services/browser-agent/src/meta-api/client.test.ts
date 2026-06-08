// H-8 (BA-3): executeGraphCall error-mapping. Канал мутаций (pause_ad/activate_ad/
// budget/create) опирается на классификацию error.code (190 → токен протух,
// -1/-2/-3 → token/network/page-evaluate). Регресс на разбор error-блока Meta.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { executeGraphCall } from './client.js';

// Мок Playwright Page: page.evaluate(fn, args) → вызываем evalImpl(args) (fn игнорируем).
function mockPage(evalImpl: (args: any) => any): any {
  return {
    evaluate: async (_fn: any, args: any) => evalImpl(args),
  };
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
