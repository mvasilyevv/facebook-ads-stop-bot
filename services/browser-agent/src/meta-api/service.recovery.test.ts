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

  // Прод 18.08.2026: оператор зашёл в Facebook заново, а канал остался мёртвым.
  // Токен читается из HTML вкладки на каждом вызове, и вкладка держала дологиновый
  // рендер со старым токеном. Re-sniff того же DOM не помогает — нужен reload.
  it('перечитывает вкладку и повторяет GET один раз при отказе токена', async () => {
    let graphCalls = 0;
    const page = {
      waitForFunction: async () => undefined,
      isClosed: () => false,
      // Считаем только graph-fetch evaluate (args с .endpoint): служебные
      // evaluate abort-биндинга к делу не относятся.
      evaluate: async (_fn: any, args: any) => {
        if (!args?.endpoint) return undefined;
        graphCalls += 1;
        if (graphCalls === 1) {
          return {
            status_code: 400,
            response_json: JSON.stringify({
              error: {
                code: 190,
                type: 'OAuthException',
                message:
                  'Error validating access token: The session has been invalidated because '
                  + 'the user changed their password.',
              },
            }),
          };
        }
        return {
          status_code: 200,
          response_json: JSON.stringify({ data: [{ id: '1', name: 'Lucky game' }] }),
        };
      },
    } as unknown as Page;
    const session = { id: 'session-1', visionProfileId: 'profile-1', netFailureStreak: 0, healLevel: 0 };
    let reloads = 0;
    const manager = {
      getPreferredSession: () => session,
      getSession: () => session,
      ensureInteractivePage: async () => page,
      reloadPageAfterNetworkFailureWithinRoleLock: async () => {
        reloads += 1;
        return { action: 'reload', ok: true };
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
      endpoint: '/act_123/promote_pages',
      query_params: { fields: 'id,name' },
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

    assert.equal(reloads, 1);
    assert.equal(graphCalls, 2);
    assert.equal(response.status_code, 200);
  });

  // Лечение адресное: отказ прав — не протухший токен, перечитывать вкладку
  // бессмысленно, а повтор скрыл бы настоящую причину от вызывающего.
  // (Мутации под это лечение не попадают в принципе: любой не-GET уходит в
  // money-путь, а лечение стоит за !moneyControl.)
  it('не перечитывает вкладку, когда отказ не про токен', async () => {
    let graphCalls = 0;
    const page = {
      waitForFunction: async () => undefined,
      isClosed: () => false,
      evaluate: async (_fn: any, args: any) => {
        if (!args?.endpoint) return undefined;
        graphCalls += 1;
        return {
          status_code: 403,
          response_json: JSON.stringify({
            error: { code: 200, type: 'GraphMethodException', message: 'Permissions error' },
          }),
        };
      },
    } as unknown as Page;
    const session = {
      id: 'session-1',
      visionProfileId: 'profile-1',
      netFailureStreak: 0,
      healLevel: 0,
    };
    let reloads = 0;
    const manager = {
      getPreferredSession: () => session,
      getSession: () => session,
      ensureInteractivePage: async () => page,
      reloadPageAfterNetworkFailureWithinRoleLock: async () => {
        reloads += 1;
        return { action: 'reload', ok: true };
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
      endpoint: '/act_123/promote_pages',
      query_params: { fields: 'id,name' },
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

    assert.equal(graphCalls, 1);
    assert.equal(reloads, 0);
    assert.equal(response.status_code, 403);
  });
});
