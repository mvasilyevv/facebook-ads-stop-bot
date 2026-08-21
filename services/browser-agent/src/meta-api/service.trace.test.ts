import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import { createMetaApiServiceHandlers } from './service.js';

// Инцидент 19.08.2026: два залива порвались внутри браузерного слоя, а в его
// логах за два часа — ноль строк. Эти тесты держат обратное: каждый вызов
// оставляет ровно одну запись, и по ней видно, ушло что-нибудь наружу или нет.

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

function graphPage(result: { status_code: number; response_json: string }): Page {
  return {
    waitForFunction: async () => undefined,
    isClosed: () => false,
    evaluate: async (_fn: unknown, args: any) => {
      if (!args?.endpoint) return undefined;
      return result;
    },
  } as unknown as Page;
}

function handlersFor(page: Page): ReturnType<typeof createMetaApiServiceHandlers> {
  const session = {
    id: 'session-1',
    visionProfileId: 'profile-1',
    netFailureStreak: 0,
    healLevel: 0,
  };
  const manager = {
    getPreferredSession: () => session,
    getSession: () => session,
    ensureInteractivePage: async () => page,
    reloadPageAfterNetworkFailureWithinRoleLock: async () => ({ action: 'reload', ok: true }),
  } as unknown as SessionManager;
  return createMetaApiServiceHandlers(manager);
}

async function runGraphCall(
  handlers: ReturnType<typeof createMetaApiServiceHandlers>,
  request: Record<string, unknown>,
): Promise<{ error: unknown; result: unknown }> {
  return await new Promise((resolve) => {
    handlers.executeGraphCallV5(
      unaryCall(request),
      (error: unknown, result: unknown) => resolve({ error, result }),
    );
  });
}

const READ_REQUEST = {
  session_id: '',
  method: 'GET',
  endpoint: '/act_123',
  query_params: { fields: 'timezone_offset_hours_utc' },
  body_json: '',
  timeout_ms: 30_000,
};

describe('след вызова Meta', () => {
  it('ответ Meta записан как CONFIRMED вместе с кабинетом, путём и страницей', async () => {
    const capture = captureTrace();
    try {
      await runGraphCall(
        handlersFor(graphPage({ status_code: 200, response_json: '{"id":"1"}' })),
        READ_REQUEST,
      );
    } finally {
      capture.stop();
    }

    const calls = capture.records.filter((record) => record.evt === 'meta_call');
    assert.equal(calls.length, 1, 'на вызов приходится ровно одна запись');
    const record = calls[0]!;
    assert.equal(record.rpc, 'execute_graph_call');
    assert.equal(record.outcome, 'CONFIRMED');
    assert.equal(record.status_code, 200);
    assert.equal(record.act, '123');
    assert.equal(record.method, 'GET');
    assert.equal(record.endpoint, '/act_123');
    assert.equal(record.money, false);
    assert.equal(record.role, 'interactive');
    assert.equal(record.session, 'session-1');
    assert.ok(typeof record.duration_ms === 'number');
  });

  it('сетевой отказ внутри страницы остаётся UNKNOWN, а не нулевым ответом', async () => {
    const capture = captureTrace();
    try {
      await runGraphCall(
        handlersFor(graphPage({
          status_code: 0,
          response_json: JSON.stringify({
            error: { code: -2, type: 'NetworkError', message: 'failed to fetch' },
          }),
        })),
        READ_REQUEST,
      );
    } finally {
      capture.stop();
    }

    const calls = capture.records.filter((record) => record.evt === 'meta_call');
    assert.equal(calls.length, 1);
    assert.equal(calls[0]!.outcome, 'UNKNOWN');
    assert.ok(
      !('status_code' in calls[0]!),
      'ответа не было — кода ответа в записи быть не должно',
    );
  });

  it('отказ до внешней границы записан как REJECTED с кодом причины', async () => {
    const capture = captureTrace();
    try {
      await runGraphCall(
        handlersFor(graphPage({ status_code: 200, response_json: '{}' })),
        { ...READ_REQUEST, query_params: { method: 'post' } },
      );
    } finally {
      capture.stop();
    }

    const calls = capture.records.filter((record) => record.evt === 'meta_call');
    assert.equal(calls.length, 1);
    assert.equal(calls[0]!.outcome, 'REJECTED');
    assert.equal(calls[0]!.reason, 'graph_method_override');
    assert.ok(!('status_code' in calls[0]!));
  });

  it('загрузка картинки записана исходом, а не текстом ответа Meta', async () => {
    const responseLeak = 'Невалидный JSON: <html>сессия оператора и кусок ответа</html>';
    const session = {
      id: 'session-upload',
      visionProfileId: 'profile-upload',
      netFailureStreak: 0,
      healLevel: 0,
    };
    const manager = {
      getSession: () => session,
      getPreferredSession: () => session,
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getInteractivePage: () => ({}) as Page,
      verifyOperationCapability: () => undefined,
      consumeOperationCapability: async () => undefined,
      uploadImage: (async () => ({
        ok: false,
        imageHash: '',
        url: '',
        error: responseLeak,
        durationMs: 12,
      })) as any,
    });

    const capture = captureTrace();
    try {
      await new Promise<void>((resolve) => {
        handlers.uploadImage(
          unaryCall({
            session_id: 'session-upload',
            vision_profile_id: 'profile-upload',
            capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
            ad_account_id: '123',
            filename: 'image.jpg',
            content_type: 'image/jpeg',
            file_bytes: Buffer.from('image'),
          }),
          () => resolve(),
        );
      });
    } finally {
      capture.stop();
    }

    const calls = capture.records.filter((record) => record.evt === 'meta_call');
    assert.equal(calls.length, 1);
    const record = calls[0]!;
    assert.equal(record.rpc, 'upload_image');
    assert.equal(record.act, '123');
    assert.equal(record.endpoint, '/act_123/adimages');
    assert.equal(record.money, true);
    // Грант списан, отправка началась, ответ невнятен — исход неизвестен, и
    // округлять его до отказа нельзя: в кабинете могла остаться картинка.
    assert.equal(record.outcome, 'UNKNOWN');
    assert.ok(
      !JSON.stringify(record).includes('html'),
      'кусок ответа Meta в след не попадает',
    );
  });

  it('в записи нет сырого текста исключения', async () => {
    const capture = captureTrace();
    try {
      await runGraphCall(
        handlersFor(graphPage({ status_code: 200, response_json: '{}' })),
        { ...READ_REQUEST, ad_account_id: 'не-число' },
      );
    } finally {
      capture.stop();
    }

    const calls = capture.records.filter((record) => record.evt === 'meta_call');
    assert.equal(calls.length, 1);
    assert.equal(calls[0]!.outcome, 'REJECTED');
    const serialized = JSON.stringify(calls[0]);
    assert.ok(
      !serialized.includes('must be an explicit numeric account id'),
      'причина пишется кодом, а не текстом исключения',
    );
  });
});
