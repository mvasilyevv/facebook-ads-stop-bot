// Operations on one concrete page serialize; scan and control roles are
// deliberately independent so scan reload cannot delay a money mutation.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { withPageLock, withPageRoleLock, _resetPageLocks } from './page-lock.js';

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

  it('scan и control одного кабинета не блокируют друг друга', async () => {
    _resetPageLocks();
    const events: string[] = [];
    await Promise.all([
      withPageRoleLock('s1', 'scan', '123', async () => {
        events.push('scan:start');
        await sleep(30);
        events.push('scan:end');
      }),
      withPageRoleLock('s1', 'control', '123', async () => {
        events.push('control:start');
        await sleep(5);
        events.push('control:end');
      }),
    ]);
    assert.deepEqual(events, ['scan:start', 'control:start', 'control:end', 'scan:end']);
  });

  it('interactive upload и control mutation одного кабинета не блокируют друг друга', async () => {
    _resetPageLocks();
    const events: string[] = [];
    await Promise.all([
      withPageRoleLock('s1', 'interactive', '123', async () => {
        events.push('upload:start');
        await sleep(30);
        events.push('upload:end');
      }),
      withPageRoleLock('s1', 'control', '123', async () => {
        events.push('pause:start');
        await sleep(5);
        events.push('pause:end');
      }),
    ]);
    assert.deepEqual(events, ['upload:start', 'pause:start', 'pause:end', 'upload:end']);
  });

  it('две control-операции одного кабинета остаются сериализованы', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const op = (tag: string, ms: number) =>
      withPageRoleLock('s1', 'control', '123', async () => {
        events.push(`${tag}:start`);
        await sleep(ms);
        events.push(`${tag}:end`);
      });
    await Promise.all([op('pause', 20), op('activate', 1)]);
    assert.deepEqual(events, ['pause:start', 'pause:end', 'activate:start', 'activate:end']);
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
