// Operations on one concrete page serialize; scan and control roles are
// deliberately independent so scan reload cannot delay a money mutation.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { withPageLock, withPageRoleLock, pageLockKey, pageLockKeyForSession, withPageRoleLockForSession, _resetPageLocks } from './page-lock.js';

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

// #232: ключ замка строится по Vision-профилю, а не по id логической сессии.
describe('pageLockKeyForSession: физический браузер, а не логическая сессия', () => {
  it('две сессии с одним visionProfileId получают один и тот же ключ', () => {
    const s1 = { id: 'session-1', visionProfileId: 'profile-A' };
    const s2 = { id: 'session-2', visionProfileId: 'profile-A' };
    assert.equal(
      pageLockKeyForSession(s1, 'control', '123'),
      pageLockKeyForSession(s2, 'control', '123'),
    );
  });

  it('сессии с разными visionProfileId получают разные ключи', () => {
    const s1 = { id: 'session-1', visionProfileId: 'profile-A' };
    const s2 = { id: 'session-2', visionProfileId: 'profile-B' };
    assert.notEqual(
      pageLockKeyForSession(s1, 'control', '123'),
      pageLockKeyForSession(s2, 'control', '123'),
    );
  });

  it('пустой visionProfileId — фолбэк на session.id', () => {
    const s = { id: 'session-X', visionProfileId: '' };
    assert.equal(
      pageLockKeyForSession(s, 'scan', '456'),
      pageLockKey('session-X', 'scan', '456'),
    );
  });

  it('withPageRoleLockForSession сериализует две сессии с одним профилем', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const s1 = { id: 'session-1', visionProfileId: 'profile-A' };
    const s2 = { id: 'session-2', visionProfileId: 'profile-A' };
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    await Promise.all([
      withPageRoleLockForSession(s1, 'control', '123', async () => {
        events.push('A:start');
        await sleep(30);
        events.push('A:end');
      }),
      withPageRoleLockForSession(s2, 'control', '123', async () => {
        events.push('B:start');
        await sleep(5);
        events.push('B:end');
      }),
    ]);
    assert.deepEqual(events, ['A:start', 'A:end', 'B:start', 'B:end']);
  });

  it('withPageRoleLockForSession НЕ сериализует сессии с разными профилями', async () => {
    _resetPageLocks();
    const events: string[] = [];
    const s1 = { id: 'session-1', visionProfileId: 'profile-A' };
    const s2 = { id: 'session-2', visionProfileId: 'profile-B' };
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    await Promise.all([
      withPageRoleLockForSession(s1, 'control', '123', async () => {
        events.push('A:start');
        await sleep(30);
        events.push('A:end');
      }),
      withPageRoleLockForSession(s2, 'control', '123', async () => {
        events.push('B:start');
        await sleep(5);
        events.push('B:end');
      }),
    ]);
    // Разные профили — разные ключи — параллельное выполнение.
    assert.equal(events[0], 'A:start');
    assert.equal(events[1], 'B:start');
    assert.equal(events[2], 'B:end');
    assert.equal(events[3], 'A:end');
  });
});

// #187: очередь блокировок не имела предела ожидания. Операция, чей gRPC-дедлайн
// уже истёк, дожидалась своей очереди и ОТПРАВЛЯЛА мутацию в Meta после того, как
// клиент получил DEADLINE_EXCEEDED. За зависшим сканом так встаёт авто-стоп.
describe('withPageLock: дедлайн ожидания', () => {
  it('ожидающий отваливается по дедлайну и НЕ выполняет операцию', async () => {
    _resetPageLocks();
    let mutations = 0;
    let releaseHolder!: () => void;
    const holder = withPageLock('s-deadline', () =>
      new Promise<void>((resolve) => {
        releaseHolder = resolve;
      }));

    const controller = new AbortController();
    const waiting = withPageLock(
      's-deadline',
      async () => {
        mutations += 1;
      },
      { signal: controller.signal },
    );
    controller.abort('grpc_deadline_exceeded');

    await assert.rejects(waiting, /page lock wait aborted/);
    assert.equal(mutations, 0, 'мутация не должна уйти после отказа по дедлайну');

    releaseHolder();
    await holder;
    // Держатель отпустил слот уже после отказа ожидающего — операция всё равно
    // не выполняется: отменённое ожидание не воскресает.
    await sleep(5);
    assert.equal(mutations, 0);
  });

  it('после отвалившегося ожидания очередь обслуживает следующего', async () => {
    _resetPageLocks();
    const events: string[] = [];
    let releaseHolder!: () => void;
    const holder = withPageLock('s-next', async () => {
      events.push('holder:start');
      await new Promise<void>((resolve) => {
        releaseHolder = resolve;
      });
      events.push('holder:end');
    });

    const controller = new AbortController();
    const abandoned = withPageLock('s-next', async () => {
      events.push('abandoned:run');
    }, { signal: controller.signal });
    controller.abort('grpc_deadline_exceeded');
    await assert.rejects(abandoned, /page lock wait aborted/);

    const next = withPageLock('s-next', async () => {
      events.push('next:start');
    });

    await sleep(5);
    // Взаимное исключение сохранено: следующий НЕ стартовал, пока держатель жив.
    assert.deepEqual(events, ['holder:start']);

    releaseHolder();
    await holder;
    await next;
    assert.deepEqual(events, ['holder:start', 'holder:end', 'next:start']);
  });
});
