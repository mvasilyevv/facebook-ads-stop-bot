// Проверка hardReloadPage: вызывает clearBrowserCache через CDPSession и затем page.reload({ waitUntil: 'networkidle' }), возвращая длительность.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { hardReloadPage } from './hard-reload.js';

function mockPage(overrides: { reload?: (...args: any[]) => any; cdpSend?: any; detach?: any; newCDPSession?: any } = {}) {
  const cdpSend = overrides.cdpSend ?? ((..._args: any[]) => Promise.resolve());
  const detach = overrides.detach ?? (() => Promise.resolve());
  const newCDPSession = overrides.newCDPSession ?? (() => Promise.resolve({ send: cdpSend, detach }));
  const reload = overrides.reload ?? (() => Promise.resolve());

  const calls = { cdpSend: [] as any[], reload: [] as any[], newCDPSessionCalled: 0, detachCalled: 0 };

  const trackedCdpSend = (...args: any[]) => { calls.cdpSend.push(args); return cdpSend(...args); };
  const trackedDetach = () => { calls.detachCalled += 1; return detach(); };
  const trackedNewCDPSession = (page: any) => {
    calls.newCDPSessionCalled += 1;
    return Promise.resolve({ send: trackedCdpSend, detach: trackedDetach });
  };
  const trackedReload = (...args: any[]) => { calls.reload.push(args); return reload(...args); };

  const page: any = {
    context: () => ({ newCDPSession: trackedNewCDPSession }),
    reload: trackedReload,
  };
  return { page, calls };
}

describe('hardReloadPage', () => {
  it('очищает кеш через CDP и перезагружает страницу при bypassCache=true', async () => {
    const { page, calls } = mockPage();
    const result = await hardReloadPage(page, true);

    assert.equal(calls.newCDPSessionCalled, 1);
    assert.deepEqual(calls.cdpSend[0], ['Network.clearBrowserCache']);
    assert.equal(calls.detachCalled, 1);
    assert.equal(calls.reload.length, 1);
    assert.deepEqual(calls.reload[0][0], { waitUntil: 'networkidle', timeout: 60_000 });
    assert.equal(result.success, true);
    assert.equal(result.errorMessage, '');
    assert.ok(result.reloadMs >= 0);
  });

  it('возвращает success=false и error при падении reload', async () => {
    const { page } = mockPage({
      reload: () => Promise.reject(new Error('navigation failed')),
    });
    const result = await hardReloadPage(page, true);
    assert.equal(result.success, false);
    assert.match(result.errorMessage, /navigation failed/);
  });

  it('пропускает clearBrowserCache, если bypassCache=false', async () => {
    const { page, calls } = mockPage();
    await hardReloadPage(page, false);
    assert.equal(calls.newCDPSessionCalled, 0);
    assert.equal(calls.reload.length, 1);
  });
});
