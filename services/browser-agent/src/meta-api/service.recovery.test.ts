import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { createMetaApiServiceHandlers } from './service.js';

describe('ExecuteGraphCall — восстановление primary-вкладки', () => {
  it('переоткрывает last known Ads Manager page и завершает Graph GET без 503', async () => {
    const page = {
      waitForFunction: async () => undefined,
      evaluate: async () => ({
        status_code: 200,
        response_json: JSON.stringify({ timezone_offset_hours_utc: 3 }),
      }),
    } as unknown as Page;
    const session = {
      id: 'session-1',
      netFailureStreak: 0,
      healLevel: 0,
    } as BrowserSession;
    let ensureCalls = 0;
    const manager = {
      getPreferredSession: () => session,
      getSession: () => session,
      ensureAdsManagerPage: async () => {
        ensureCalls += 1;
        return page;
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getPage: () => {
        throw new Error('Основная страница браузера недоступна');
      },
    });

    const response = await new Promise<any>((resolve, reject) => {
      handlers.executeGraphCall(
        {
          request: {
            session_id: '',
            method: 'GET',
            endpoint: '/act_123',
            query_params: { fields: 'timezone_offset_hours_utc' },
          },
        },
        (error: unknown, result: unknown) => (error ? reject(error) : resolve(result)),
      );
    });

    assert.equal(ensureCalls, 1);
    assert.equal(response.status_code, 200);
    assert.deepEqual(JSON.parse(response.response_json), { timezone_offset_hours_utc: 3 });
  });
});
