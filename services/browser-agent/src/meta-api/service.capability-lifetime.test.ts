import { describe, it, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import * as grpc from '@grpc/grpc-js';

import type { SessionManager } from '../session-manager.js';
import { _resetPageLocks } from '../page-lock.js';
import {
  BROWSER_OPERATION_REJECTION_METADATA_KEY,
  createMetaApiServiceHandlers,
} from './service.js';

// Одноразовый грант execute_graph_call живёт 40 секунд (MAX_TTL_SECONDS_BY_RPC
// в operation-capability.ts). Money-операция стоит в FIFO мьютексе
// control-страницы кабинета (page-lock.ts): пока держит другая операция,
// новая ждёт своей очереди. Эти тесты фиксируют два инварианта:
//
// 1. Часы гранта, потраченные на ожидание в очереди, не должны молча убивать
//    операцию до того, как она вообще коснулась страницы/сети — это должно
//    репортиться как доказанный pre-send отказ с кодом причины, а не как
//    голый DEADLINE_EXCEEDED без объяснения.
// 2. Один и тот же capability_expired обязан отвечать по-разному в
//    зависимости от того, состоялось ли необратимое списание гранта
//    (_consumeOperationCapability) до истечения: до списания — семья
//    доказанных pre-send отказов; после — исход остаётся неоднозначным,
//    потому что fetch мог уже уйти в Meta.

// Часы здесь заморожены намеренно. Прежняя редакция брала окно гранта в одну
// настоящую секунду от Date.now(): на загруженном раннере она истекала раньше,
// чем операция доходила до переверификации свежести, — тогда consume и fetch
// не вызывались вовсе, тест вис на своей точке синхронизации и отменялся по
// времени (`# cancelled 2` при `# fail 0`). Момент истечения обязан задаваться
// явно, а не выжидаться: расширение окна секундами лечит не причину, а частоту.
const FROZEN_NOW_MS = 1_760_000_000_000;
const FROZEN_NOW_SECONDS = Math.floor(FROZEN_NOW_MS / 1_000);
// Грант живёт 10 секунд модельного времени: это внутри 40-секундного потолка
// MAX_TTL_SECONDS_BY_RPC (иначе переверификация отвергнет его как unbounded) и
// заметно раньше 30-секундного дедлайна вызова, поэтому тик ниже будит ровно
// таймер капабилити и никакой другой.
const GRANT_TTL_SECONDS = 10;
const TICK_PAST_EXPIRY_MS = GRANT_TTL_SECONDS * 1_000 + 1;

/**
 * Замораживает и часы, и таймеры до сборки запроса: дедлайн вызова считается
 * от того же модельного времени, что и срок гранта, поэтому их порядок задан
 * числами, а не тем, что успел раннер.
 */
function freezeClock(t: TestContext): void {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: FROZEN_NOW_MS });
}

function trailerReason(error: { metadata?: grpc.Metadata }): string | undefined {
  const values = error.metadata?.get(BROWSER_OPERATION_REJECTION_METADATA_KEY);
  return values && values.length > 0 ? String(values[0]) : undefined;
}

function unaryCall(request: Record<string, unknown>): EventEmitter & {
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

describe('money capability grant lifetime vs the control-page FIFO queue', () => {
  it('rejects a grant that is already stale by the time work starts, even past a bypassed entry check', async (t) => {
    freezeClock(t);
    _resetPageLocks();
    const session = { id: 'session-1', visionProfileId: 'profile-1' };
    let pageTouched = false;

    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      // Единственная проверка, оставленная активной, — переверификация
      // свежести в момент захвата control-lock (см. executeGraphCallV5Handler).
      // Подпись мокнута как всегда проходящая, ровно как и в остальных тестах
      // капабилити: она не зависит от времени и здесь не в фокусе.
      verifyOperationCapability: () => undefined,
      getControlPage: () => {
        pageTouched = true;
        return {} as any;
      },
      assertGraphOperationOwnership: async () => undefined,
      consumeOperationCapability: async () => undefined,
    });

    const call = unaryCall({
      session_id: 'session-1',
      vision_profile_id: 'profile-1',
      // Грант подписан на короткое окно и уже истёк: именно так выглядит
      // операция, чью FIFO-очередь пережила её TTL.
      capability_expires_at: FROZEN_NOW_SECONDS - 5,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '{}',
      timeout_ms: 30_000,
    });

    const error = await new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(call, (err: unknown) => resolve(err));
    });

    assert.equal(error.code, grpc.status.PERMISSION_DENIED);
    assert.equal(trailerReason(error), 'capability_expired');
    // Страница кабинета не открывалась: отказ доказанно случился до неё.
    assert.equal(pageTouched, false);
  });

  it('an expiry proven before the grant is consumed answers like the rest of the pre-send family', async (t) => {
    freezeClock(t);
    _resetPageLocks();
    const session = { id: 'session-1', visionProfileId: 'profile-1' };
    let consumeStarted!: () => void;
    const consumeStartedPromise = new Promise<void>((resolve) => {
      consumeStarted = resolve;
    });
    let releaseConsume!: () => void;
    const consumeGate = new Promise<void>((resolve) => {
      releaseConsume = resolve;
    });

    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => ({} as any),
      verifyOperationCapability: () => undefined,
      assertGraphOperationOwnership: async () => undefined,
      // Грант ещё не списан необратимо: до Meta ничего не ушло. Держим
      // consume подвешенным, чтобы истечение капабилити гарантированно
      // случилось до его разрешения, а не после.
      consumeOperationCapability: async () => {
        consumeStarted();
        await consumeGate;
      },
    });

    const call = unaryCall({
      session_id: 'session-1',
      vision_profile_id: 'profile-1',
      // Свеж на захвате lock (см. assertCapabilityStillFresh в handler'е);
      // истечение наступит от тика ниже — во время самого consume, а не до
      // него, и наступит независимо от загруженности машины.
      capability_expires_at: FROZEN_NOW_SECONDS + GRANT_TTL_SECONDS,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '{}',
      timeout_ms: 30_000,
    });

    const result = new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(call, (err: unknown) => resolve(err));
    });

    await consumeStartedPromise;
    // Операция стоит внутри consume — грант ещё не списан необратимо. Только
    // теперь двигаем модельные часы за срок гранта: будится таймер капабилити
    // (10 с) и не будится ни дедлайн вызова, ни таймаут fetch (оба 30 с).
    t.mock.timers.tick(TICK_PAST_EXPIRY_MS);
    try {
      const error = await result;
      assert.equal(error.code, grpc.status.PERMISSION_DENIED);
      assert.equal(trailerReason(error), 'capability_expired');
    } finally {
      // Освобождаем зависший withPageRoleLock независимо от исхода assert:
      // иначе следующий тест на том же кабинете встанет в очередь навечно.
      releaseConsume();
    }
  });

  it('an expiry after the grant is consumed stays ambiguous, exactly as before', async (t) => {
    // Регресс-замок на существующий тест поведения: как только send boundary
    // пересечён, capability_expired не должен обзаводиться кодом причины —
    // fetch мог уже уйти, и REJECTED здесь означал бы потерю доказательства.
    freezeClock(t);
    _resetPageLocks();
    const session = { id: 'session-1', visionProfileId: 'profile-1' };
    let startedResolve!: () => void;
    const started = new Promise<void>((resolve) => { startedResolve = resolve; });
    const page = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          startedResolve();
          return new Promise(() => undefined); // никогда не резолвится сам
        }
        return undefined;
      },
    };

    const handlers = createMetaApiServiceHandlers({
      getSession: () => session,
    } as unknown as SessionManager, {
      getControlPage: () => page as any,
      authorizeOperationCapability: () => undefined,
    });

    const call = unaryCall({
      session_id: 'session-1',
      vision_profile_id: 'profile-1',
      // Тем же образом: свеж на захвате lock, истекает уже во время fetch —
      // после того, как authorizeOperationCapability «списал» грант.
      capability_expires_at: FROZEN_NOW_SECONDS + GRANT_TTL_SECONDS,
      ad_account_id: '123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '{}',
      timeout_ms: 30_000,
    });

    const result = new Promise<any>((resolve) => {
      handlers.executeGraphCallV5(call, (err: unknown) => resolve(err));
    });

    await started;
    // fetch ушёл со страницы и не вернётся сам; грант уже списан. Двигаем
    // часы за срок гранта — истечение приходит после send boundary.
    t.mock.timers.tick(TICK_PAST_EXPIRY_MS);
    const error = await result;

    assert.equal(error.code, grpc.status.DEADLINE_EXCEEDED);
    assert.equal(trailerReason(error), undefined);
  });
});
