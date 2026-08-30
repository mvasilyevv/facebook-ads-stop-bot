// #229: скан перезагружает свою вкладку, и `page.reload()` убивает execution
// context под `page.evaluate` пробы готовности. Что проба без кабинета берёт
// scan-страницу под scan-замком и потому не влезает в середину reload, уже
// закреплено в service.control.test.ts.
//
// Здесь закрепляется вторая половина того же инварианта: money-проба роли
// control к тому же кабинету НЕ ждёт scan-замок. Иначе цена отсутствия мигания
// — задержка money-пути на время чужой перезагрузки, то есть та же потеря
// готовности, только под другим именем.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { _resetPageLocks, withPageRoleLockForSession } from '../page-lock.js';
import { createMetaApiServiceHandlers } from './service.js';

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function fakePage(tag: string): Page {
  return { __tag: tag, isClosed: () => false } as unknown as Page;
}

function healthCall(request: Record<string, unknown>): EventEmitter & {
  request: Record<string, unknown>;
  getDeadline: () => Date;
} {
  const call = new EventEmitter() as EventEmitter & {
    request: Record<string, unknown>;
    getDeadline: () => Date;
  };
  call.request = request;
  call.getDeadline = () => new Date(Date.now() + 30_000);
  return call;
}

function sessionWithPages(): BrowserSession {
  return {
    id: 'session-1',
    visionProfileId: 'profile-1',
    scanPages: new Map<string, Page>([['123', fakePage('scan:123')]]),
    controlPages: new Map<string, Page>([['123', fakePage('control:123')]]),
    interactivePages: new Map<string, Page>(),
  } as unknown as BrowserSession;
}

function handlersFor(
  session: BrowserSession,
  events: string[],
  seenPages: Array<Page>,
) {
  const manager = {
    getSession: () => session,
    getPreferredSession: () => session,
  } as unknown as SessionManager;
  return createMetaApiServiceHandlers(manager, {
    // Проба не должна ходить в браузер — важно лишь, КОГДА её пустили и на
    // какую страницу.
    checkMetaApiHealth: (async (page: Page) => {
      events.push('probe:evaluate');
      seenPages.push(page);
      return {
        healthy: true,
        currentUrl: 'https://adsmanager.facebook.com/',
        tokenPresent: true,
        tokenLength: 32,
        detail: 'ok',
        probePerformed: false,
        probeOk: false,
        probeStatusCode: 0,
        probeDurationMs: 0,
        probeDetail: 'not_performed',
      };
    }) as any,
    getControlPage: ((s: BrowserSession, actId: string) => {
      events.push('probe:control-page');
      return (s.controlPages as Map<string, Page>).get(actId)!;
    }) as any,
  });
}

describe('readiness probe vs scan reload (#229)', () => {
  it('проба control-роли не задерживается сканом того же кабинета', async () => {
    _resetPageLocks();
    const session = sessionWithPages();
    const events: string[] = [];
    const seenPages: Page[] = [];
    const handlers = handlersFor(session, events, seenPages);

    const scanReload = withPageRoleLockForSession(session, 'scan', '123', async () => {
      events.push('scan-reload:start');
      await sleep(40);
      events.push('scan-reload:end');
    });

    await sleep(5);
    const probeDone = new Promise<Record<string, any>>((resolve, reject) => {
      void handlers.checkMetaApiHealth(
        healthCall({
          session_id: 'session-1',
          expected_vision_profile_id: 'profile-1',
          operation_role: 'control',
          ad_account_id: '123',
        }),
        (err: unknown, res: Record<string, any>) => (err ? reject(err) : resolve(res)),
      );
    });

    await probeDone;
    await scanReload;

    // money-путь не ждёт скан: control-замок независим от scan-замка.
    assert.deepEqual(events, [
      'scan-reload:start',
      'probe:control-page',
      'probe:evaluate',
      'scan-reload:end',
    ]);
    assert.equal((seenPages[0] as any).__tag, 'control:123');
  });
});
