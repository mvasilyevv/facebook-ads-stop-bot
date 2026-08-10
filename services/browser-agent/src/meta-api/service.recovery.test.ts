import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import { createMetaApiServiceHandlers } from './service.js';

describe('ExecuteGraphCallV5 — восстановление interactive-вкладки', () => {
  it('получает восстановленную Ads Manager page и завершает Graph GET без 503', async () => {
    const page = {
      waitForFunction: async () => undefined,
      evaluate: async () => ({
        status_code: 200,
        response_json: JSON.stringify({ timezone_offset_hours_utc: 3 }),
      }),
    } as unknown as Page;
    const session = {
      id: 'session-1',
      visionProfileId: 'profile-1',
      netFailureStreak: 0,
      healLevel: 0,
    };
    let ensureCalls = 0;
    const manager = {
      getPreferredSession: () => session,
      getSession: () => session,
      ensureInteractivePage: async () => {
        ensureCalls += 1;
        return page;
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager);

    const call = new EventEmitter() as EventEmitter & {
      request: Record<string, unknown>;
      getDeadline: () => Date;
    };
    call.request = {
      session_id: '',
      method: 'GET',
      endpoint: '/act_123',
      query_params: { fields: 'timezone_offset_hours_utc' },
      body_json: '',
      timeout_ms: 30_000,
    };
    call.getDeadline = () => new Date(Date.now() + 30_000);

    const response = await new Promise<any>((resolve, reject) => {
      handlers.executeGraphCallV5(
        call,
        (error: unknown, result: unknown) => (error ? reject(error) : resolve(result)),
      );
    });

    assert.equal(ensureCalls, 1);
    assert.equal(response.status_code, 200);
    assert.deepEqual(JSON.parse(response.response_json), { timezone_offset_hours_utc: 3 });
  });
});
