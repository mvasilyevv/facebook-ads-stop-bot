// H-7 (BA-4): per-session мьютекс сериализует операции над общей primaryPage,
// чтобы scan page.reload и mutation page.evaluate(fetch) не пересекались.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { withPageLock, _resetPageLocks } from './page-lock.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('withPageLock (H-7 per-session mutex)', () => {
  it('сериализует операции на ОДНОЙ сессии — без наложения', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const op = (tag: string, ms: number) =>
      withPageLock('s1', async () => {
        events.push(`${tag}:start`);
        await sleep(ms);
        events.push(`${tag}:end`);
      });
    // A дольше B, но B не должна влезть в середину A.
    await Promise.all([op('A', 30), op('B', 5)]);
    assert.deepEqual(events, ['A:start', 'A:end', 'B:start', 'B:end']);
  });

  it('РАЗНЫЕ сессии выполняются конкурентно', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const op = (sid: string, tag: string, ms: number) =>
      withPageLock(sid, async () => {
        events.push(`${tag}:start`);
        await sleep(ms);
        events.push(`${tag}:end`);
      });
    await Promise.all([op('s1', 'A', 30), op('s2', 'B', 5)]);
    // Обе стартуют сразу (лок не общий), короткая B заканчивается раньше длинной A.
    assert.equal(events[0], 'A:start');
    assert.equal(events[1], 'B:start');
    assert.equal(events[2], 'B:end');
    assert.equal(events[3], 'A:end');
  });

  it('ошибка одной операции НЕ ломает очередь сессии', async () => {
    _resetPageLocks();
    await assert.rejects(
      () =>
        withPageLock('s1', async () => {
          throw new Error('boom');
        }),
      /boom/,
    );
    // Следующая операция на той же сессии должна нормально выполниться.
    const r = await withPageLock('s1', async () => 42);
    assert.equal(r, 42);
  });

  it('возвращает результат fn вызывающему', async () => {
    _resetPageLocks();
    assert.equal(await withPageLock('s1', async () => 'ok'), 'ok');
    // Пустой sessionId → дефолтный ключ, тоже работает.
    assert.equal(await withPageLock('', async () => 'def'), 'def');
  });
});
