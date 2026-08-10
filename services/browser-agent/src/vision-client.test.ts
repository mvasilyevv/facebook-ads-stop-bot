// H-5 (BA-1): VisionClient оборачивает fetch в AbortController с таймаутом —
// зависший (но живой) Vision-процесс не должен вешать attach/reconnect/maintenance
// навсегда. Проверяем: зависший fetch → reject по таймауту; успешный → данные.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { VisionClient } from './vision-client.js';

describe('VisionClient fetch timeout (H-5)', () => {
  it('request таймаутит зависший Vision-ответ (abort)', async () => {
    const orig = globalThis.fetch;
    // fetch, который никогда не отвечает, но уважает signal.abort()
    globalThis.fetch = ((_url: any, init: any) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () =>
          reject(new DOMException('The operation was aborted', 'AbortError')),
        );
      })) as unknown as typeof fetch;
    try {
      const client = new VisionClient('x-token', 'http://127.0.0.1:3030', {
        requestTimeoutMs: 40,
      });
      const start = Date.now();
      await assert.rejects(() => client.listProfiles(), /timeout/);
      // Должно отвалиться около таймаута, а не висеть.
      assert.ok(Date.now() - start < 2_000, 'request должен отвалиться по таймауту быстро');
    } finally {
      globalThis.fetch = orig;
    }
  });

  it('request возвращает данные при успешном ответе', async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({ profiles: [{ folder_id: 'f', profile_id: 'p', port: 9222 }] }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )) as unknown as typeof fetch;
    try {
      const client = new VisionClient('x-token');
      const profiles = await client.listProfiles();
      assert.equal(profiles.length, 1);
      assert.equal(profiles[0].port, 9222);
    } finally {
      globalThis.fetch = orig;
    }
  });

  it('request бросает на не-2xx ответе', async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response('nope', { status: 500 })) as unknown as typeof fetch;
    try {
      const client = new VisionClient('x-token');
      await assert.rejects(() => client.listProfiles(), /500/);
    } finally {
      globalThis.fetch = orig;
    }
  });
});
