import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { SessionManager } from './session-manager.js';

// Первый залив 19.08.2026 убила навигация control-страницы, и по логам нельзя
// было сказать ни кто её навигировал, ни в какую секунду (#181, #174). Здесь
// фиксируется обратное: каждая навигация вкладки кабинета оставляет запись.

interface TraceRecord {
  evt: string;
  [key: string]: unknown;
}

function captureTrace(): { records: TraceRecord[]; stop: () => void } {
  const original = console.log;
  const records: TraceRecord[] = [];
  console.log = (...args: unknown[]) => {
    const line = args.map((value) => String(value)).join(' ');
    if (line.startsWith('[trace] ')) {
      records.push(JSON.parse(line.slice('[trace] '.length)) as TraceRecord);
    }
  };
  return { records, stop: () => { console.log = original; } };
}

describe('след навигации страницы кабинета', () => {
  it('лечащая перезагрузка под ролью записана вместе с кабинетом и ролью', async () => {
    const manager = new SessionManager();
    const session = {
      id: 'session-1',
      visionProfileId: 'profile-1',
      netFailureStreak: 3,
      healLevel: 0,
    };
    (manager as any).sessions.set(session.id, session);

    let reloads = 0;
    const page = {
      isClosed: () => false,
      url: () => 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=123&nav=1',
      reload: async () => {
        reloads += 1;
        return undefined;
      },
      close: async () => undefined,
    } as any;

    const capture = captureTrace();
    let result: { action: string; ok: boolean };
    try {
      result = await manager.reloadPageAfterNetworkFailureWithinRoleLock('session-1', {
        role: 'control',
        actId: '123',
        page,
      });
    } finally {
      capture.stop();
    }

    assert.equal(reloads, 1);
    assert.equal(result.ok, true);
    const navigations = capture.records.filter((record) => record.evt === 'page_nav');
    assert.equal(navigations.length, 1, 'на навигацию приходится ровно одна запись');
    const record = navigations[0]!;
    assert.equal(record.kind, 'reload');
    assert.equal(record.role, 'control');
    assert.equal(record.act, '123');
    assert.equal(record.session, 'session-1');
    assert.equal(record.by, 'heal');
    assert.equal(
      record.url,
      'https://adsmanager.facebook.com/adsmanager/manage/ads',
      'query адреса вкладки наружу не уходит',
    );
  });

  it('не пишет сырой текст исключения, когда перезагрузка сорвалась', async () => {
    const manager = new SessionManager();
    const session = {
      id: 'session-2',
      visionProfileId: 'profile-1',
      netFailureStreak: 3,
      healLevel: 0,
    };
    (manager as any).sessions.set(session.id, session);

    const secret = 'страница вернула токен EAA-и-дальше-приватное';
    const page = {
      isClosed: () => false,
      url: () => 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=123',
      reload: async () => {
        throw new Error(secret);
      },
      close: async () => undefined,
    } as any;

    const original = { log: console.log, warn: console.warn, error: console.error };
    const lines: string[] = [];
    const sink = (...args: unknown[]) => {
      lines.push(args.map((value) => String(value)).join(' '));
    };
    console.log = sink;
    console.warn = sink;
    console.error = sink;
    let result: { action: string; ok: boolean };
    try {
      result = await manager.reloadPageAfterNetworkFailureWithinRoleLock('session-2', {
        role: 'control',
        actId: '123',
        page,
      });
    } finally {
      console.log = original.log;
      console.warn = original.warn;
      console.error = original.error;
    }

    assert.equal(result.ok, false);
    const joined = lines.join('\n');
    assert.ok(joined.length > 0, 'сорвавшаяся перезагрузка не должна проходить молча');
    assert.ok(
      !joined.includes(secret),
      'в лог уходит класс отказа, а не текст исключения со страницы',
    );
  });
});
