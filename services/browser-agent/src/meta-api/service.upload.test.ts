// M12 (аудит): money-критичный сборщик чанков UploadVideo. Регресс на класс
// «гонка прошла сквозь тесты»: порядок/повтор чанков → битое видео или дубль аплоада,
// а ад с пустым/битым крео тратит бюджет. Тестируем uploadVideoHandler через инъекцию
// uploadVideoSingle/getPage (без реального браузера и POST в Meta).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import { withPageRoleLock, _resetPageLocks } from '../page-lock.js';
import { createMetaApiServiceHandlers } from './service.js';

interface UploadCall {
  adAccountId: string;
  filename: string;
  fileBytes: Buffer;
}

// Совпадает с реальным возвратом uploadVideoSingle (deps требует typeof uploadVideoSingle).
type UploadResult = { ok: boolean; videoId: string; error: string; durationMs: number };
type UploadImpl = (
  page: Page,
  opts: UploadCall,
  operation?: { signal?: AbortSignal; operationId?: string },
) => Promise<UploadResult>;

function uploadOk(videoId: string): UploadResult {
  return { ok: true, videoId, error: '', durationMs: 1 };
}

// Фейковый sessionManager: handler зовёт getPreferredSession()/getSession() — сессия
// пустышка (getPage заинъектен, поэтому её содержимое не важно).
const fakeSessionManager = {
  getSession: () => ({ id: 'session-upload', visionProfileId: 'profile-upload' }),
  getPreferredSession: () => ({ id: 'session-upload', visionProfileId: 'profile-upload' }),
} as unknown as SessionManager;

function makeUploadHandler(uploadImpl: UploadImpl, uploads: UploadCall[]) {
  const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
    getInteractivePage: () => ({}) as unknown as Page,
    uploadVideoSingle: async (_page, opts, operation) => {
      uploads.push(opts as UploadCall);
      return uploadImpl(_page as Page, opts as UploadCall, operation);
    },
    authorizeOperationCapability: () => undefined,
  });
  return handlers.uploadVideo as (call: EventEmitter, cb: (err: unknown, res: any) => void) => void;
}

function chunk(over: Record<string, unknown>): Record<string, unknown> {
  return {
    session_id: 'session-upload',
    vision_profile_id: 'profile-upload',
    capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
    chunk_bytes: Buffer.alloc(0),
    ...over,
  };
}

const tick = () => new Promise((r) => setImmediate(r));

describe('uploadVideoHandler — сборка чанков', () => {
  // Базовый путь: 3 чанка по порядку + is_last → один аплоад с конкатенацией по порядку.
  it('собирает чанки по порядку и грузит ОДНИМ аплоадом', async () => {
    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('vid_42'), uploads);
    const call = new EventEmitter();
    const res = await new Promise<any>((resolve) => {
      handler(call, (_e, r) => resolve(r));
      call.emit('data', chunk({ ad_account_id: '1', filename: 'v.mp4', chunk_bytes: Buffer.from('AAA') }));
      call.emit('data', chunk({ chunk_bytes: Buffer.from('BBB') }));
      call.emit('data', chunk({ chunk_bytes: Buffer.from('CCC'), is_last_chunk: true }));
    });
    assert.equal(uploads.length, 1, 'ровно один аплоад');
    assert.equal(uploads[0]!.fileBytes.toString(), 'AAABBBCCC', 'байты собраны по порядку');
    assert.equal(uploads[0]!.adAccountId, 'act_1');
    assert.equal(res.ok, true);
    assert.equal(res.video_id, 'vid_42');
    assert.equal(res.chunks_processed, 3);
  });

  // Финиш по 'end' (без is_last_chunk на последнем чанке) — тоже собирает и грузит.
  it('финиширует по end без is_last_chunk', async () => {
    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('vid_7'), uploads);
    const call = new EventEmitter();
    const res = await new Promise<any>((resolve) => {
      handler(call, (_e, r) => resolve(r));
      call.emit('data', chunk({ ad_account_id: 'act_1', chunk_bytes: Buffer.from('XY') }));
      call.emit('data', chunk({ chunk_bytes: Buffer.from('Z') }));
      call.emit('end');
    });
    assert.equal(uploads.length, 1);
    assert.equal(uploads[0]!.fileBytes.toString(), 'XYZ');
    assert.equal(res.ok, true);
  });

  // ГОНКА: чанки, приехавшие пока идёт аплоад, НЕ запускают второй аплоад.
  it('чанки во время аплоада не вызывают второй аплоад (re-entry guard)', async () => {
    const uploads: UploadCall[] = [];
    let resolveUpload!: (v: UploadResult) => void;
    const handler = makeUploadHandler(
      () => new Promise<UploadResult>((r) => { resolveUpload = r; }),
      uploads,
    );
    const call = new EventEmitter();
    const done = new Promise<any>((resolve) => handler(call, (_e, r) => resolve(r)));

    call.emit('data', chunk({ ad_account_id: 'act_1', chunk_bytes: Buffer.from('AAA'), is_last_chunk: true }));
    await tick(); // даём processChunks дойти до await uploadVideoSingle
    assert.equal(uploads.length, 1, 'аплоад стартовал один раз');

    // Лишние чанки во время незавершённого аплоада (имитация гонки).
    call.emit('data', chunk({ chunk_bytes: Buffer.from('XXX') }));
    call.emit('data', chunk({ chunk_bytes: Buffer.from('YYY'), is_last_chunk: true }));
    await tick();
    assert.equal(uploads.length, 1, 'второй аплоад НЕ стартовал');

    resolveUpload(uploadOk('vid_1'));
    const res = await done;
    assert.equal(res.ok, true);
    assert.equal(uploads.length, 1, 'после ответа аплоад так и один');
  });

  // Пустое видео (0 байт собрано) → ошибка, аплоад не зовём (битый ад не уйдёт в Meta).
  it('пустое видео (0 байт) → ошибка без аплоада', async () => {
    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('nope'), uploads);
    const call = new EventEmitter();
    const res = await new Promise<any>((resolve) => {
      handler(call, (_e, r) => resolve(r));
      call.emit('data', chunk({ ad_account_id: 'act_1', chunk_bytes: Buffer.alloc(0), is_last_chunk: true }));
    });
    assert.equal(res.ok, false);
    assert.match(res.error, /0 байт|пустое/i);
    assert.equal(uploads.length, 0, 'аплоад пустого видео не должен стартовать');
  });

  // Первый чанк без ad_account_id → ошибка (нельзя резолвить кабинет).
  it('первый чанк без ad_account_id → ошибка', async () => {
    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('x'), uploads);
    const call = new EventEmitter();
    const res = await new Promise<any>((resolve) => {
      handler(call, (_e, r) => resolve(r));
      call.emit('data', chunk({ chunk_bytes: Buffer.from('AAA'), is_last_chunk: true }));
    });
    assert.equal(res.ok, false);
    assert.match(res.error, /ad_account_id/i);
    assert.equal(uploads.length, 0);
  });

  // Стрим закрыт без единого чанка → ошибка.
  it('end без чанков → ошибка', async () => {
    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('x'), uploads);
    const call = new EventEmitter();
    const res = await new Promise<any>((resolve) => {
      handler(call, (_e, r) => resolve(r));
      call.emit('end');
    });
    assert.equal(res.ok, false);
    assert.match(res.error, /без единого chunk/i);
  });
});

describe('MetaApiService — money control isolation and upload cancellation', () => {
  it('image exact-profile rejection is FAILED_PRECONDITION before upload I/O', async () => {
    let uploadCalls = 0;
    const manager = {
      getSession: () => ({
        id: 'session-upload',
        visionProfileId: 'different-profile',
      }),
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadImage: async () => {
        uploadCalls += 1;
        return {
          ok: true,
          imageHash: 'must-not-upload',
          url: '',
          error: '',
          durationMs: 1,
        };
      },
    });
    const call = new EventEmitter() as EventEmitter & {
      request: Record<string, unknown>;
    };
    call.request = {
      session_id: 'session-upload',
      vision_profile_id: 'profile-upload',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '123',
      filename: 'image.jpg',
      content_type: 'image/jpeg',
      file_bytes: Buffer.from('image'),
    };

    const result = await new Promise<{ error: any; response: any }>((resolve) => {
      handlers.uploadImage(call, (error: unknown, response: unknown) => {
        resolve({ error, response });
      });
    });

    assert.equal(result.error.code, 9);
    assert.match(result.error.message, /exact session\/profile identity/i);
    assert.equal(result.response, undefined);
    assert.equal(uploadCalls, 0);
  });

  it('video exact-session rejection is FAILED_PRECONDITION before upload I/O', async () => {
    let uploadCalls = 0;
    const manager = {
      getSession: () => {
        throw new Error('session disappeared');
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadVideoSingle: async () => {
        uploadCalls += 1;
        return uploadOk('must-not-upload');
      },
    });
    const call = new EventEmitter();
    const result = new Promise<{ error: any; response: any }>((resolve) => {
      handlers.uploadVideo(call, (error: unknown, response: unknown) => {
        resolve({ error, response });
      });
    });
    call.emit('data', chunk({
      ad_account_id: '123',
      chunk_bytes: Buffer.from('video'),
      is_last_chunk: true,
    }));

    const settled = await result;
    assert.equal(settled.error.code, 9);
    assert.match(settled.error.message, /exact session\/profile identity/i);
    assert.equal(settled.response, undefined);
    assert.equal(uploadCalls, 0);
  });

  it('token absence is a bounded image precondition, not a normal response', async () => {
    const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadImage: async () => ({
        ok: false,
        imageHash: '',
        url: '',
        error: 'TOKEN_NOT_FOUND_IN_PAGE',
        durationMs: 1,
      }),
    });
    const call = new EventEmitter() as EventEmitter & {
      request: Record<string, unknown>;
    };
    call.request = {
      session_id: 'session-upload',
      vision_profile_id: 'profile-upload',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '123',
      filename: 'image.jpg',
      content_type: 'image/jpeg',
      file_bytes: Buffer.from('image'),
    };

    const result = await new Promise<{ error: any; response: any }>((resolve) => {
      handlers.uploadImage(call, (error: unknown, response: unknown) => {
        resolve({ error, response });
      });
    });

    assert.equal(result.error.code, 9);
    assert.match(result.error.message, /no Meta token/i);
    assert.equal(result.response, undefined);
  });

  it('token absence is a bounded video precondition, not a normal response', async () => {
    const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadVideoSingle: async () => ({
        ok: false,
        videoId: '',
        error: 'TOKEN_NOT_FOUND_IN_PAGE',
        durationMs: 1,
      }),
    });
    const call = new EventEmitter();
    const result = new Promise<{ error: any; response: any }>((resolve) => {
      handlers.uploadVideo(call, (error: unknown, response: unknown) => {
        resolve({ error, response });
      });
    });
    call.emit('data', chunk({
      ad_account_id: '123',
      chunk_bytes: Buffer.from('video'),
      is_last_chunk: true,
    }));

    const settled = await result;
    assert.equal(settled.error.code, 9);
    assert.match(settled.error.message, /no Meta token/i);
    assert.equal(settled.response, undefined);
  });

  it('image upload without an account is rejected before session or page use', async () => {
    let sessionCalls = 0;
    let pageCalls = 0;
    const manager = {
      getSession: () => {
        sessionCalls += 1;
        return { id: 'must-not-resolve' };
      },
      getPreferredSession: () => {
        sessionCalls += 1;
        return { id: 'must-not-resolve' };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getInteractivePage: () => {
        pageCalls += 1;
        return {} as Page;
      },
    });
    const call = new EventEmitter() as EventEmitter & { request: Record<string, unknown> };
    call.request = {
      session_id: 'session-upload',
      ad_account_id: '',
      file_bytes: Buffer.from('image'),
    };

    const error = await new Promise<any>((resolve) => {
      handlers.uploadImage(call, (err: unknown) => resolve(err));
    });

    assert.equal(error.code, 3);
    assert.match(error.message, /requires explicit ad_account_id/);
    assert.equal(sessionCalls, 0);
    assert.equal(pageCalls, 0);
  });

  it('legacy URL-only image upload is rejected before authority or browser I/O', async () => {
    let sessionCalls = 0;
    let pageCalls = 0;
    let verifyCalls = 0;
    let consumeCalls = 0;
    let uploadCalls = 0;
    const manager = {
      getSession: () => {
        sessionCalls += 1;
        return { id: 'must-not-resolve' };
      },
      getPreferredSession: () => {
        sessionCalls += 1;
        return { id: 'must-not-resolve' };
      },
    } as unknown as SessionManager;
    const handlers = createMetaApiServiceHandlers(manager, {
      getInteractivePage: () => {
        pageCalls += 1;
        return {} as Page;
      },
      verifyOperationCapability: () => {
        verifyCalls += 1;
      },
      consumeOperationCapability: async () => {
        consumeCalls += 1;
      },
      uploadImage: async () => {
        uploadCalls += 1;
        return { ok: true, imageHash: 'must-not-upload', url: '', error: '', durationMs: 1 };
      },
    });
    const call = new EventEmitter() as EventEmitter & { request: Record<string, unknown> };
    call.request = {
      session_id: 'session-upload',
      vision_profile_id: 'profile-upload',
      ad_account_id: '123',
      file_bytes: Buffer.alloc(0),
      image_url: 'https://legacy.example/image.jpg',
      name: 'legacy',
    };

    const result = await new Promise<{ error: any; response: any }>((resolve) => {
      handlers.uploadImage(call, (error: unknown, response: unknown) => {
        resolve({ error, response });
      });
    });

    assert.equal(result.error, null);
    assert.equal(result.response.ok, false);
    assert.match(result.response.error, /file_bytes пусты/);
    assert.deepEqual(
      { sessionCalls, pageCalls, verifyCalls, consumeCalls, uploadCalls },
      { sessionCalls: 0, pageCalls: 0, verifyCalls: 0, consumeCalls: 0, uploadCalls: 0 },
    );
  });

  it('долгий video upload не блокирует concurrent pause mutation', async () => {
    let releaseUpload!: (value: UploadResult) => void;
    let uploadStarted!: () => void;
    const uploadStartedPromise = new Promise<void>((resolve) => { uploadStarted = resolve; });
    const controlPage = {
      waitForFunction: async () => true,
      evaluate: async (_fn: any, args: any) => {
        if (args && typeof args === 'object' && 'endpoint' in args) {
          return { status_code: 200, response_json: '{"success":true}' };
        }
        return undefined;
      },
    } as unknown as Page;
    const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
      getControlPage: () => controlPage,
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadVideoSingle: async () => {
        uploadStarted();
        return new Promise<UploadResult>((resolve) => { releaseUpload = resolve; });
      },
    });

    const uploadCall = new EventEmitter();
    const uploadDone = new Promise<any>((resolve) => {
      handlers.uploadVideo(uploadCall, (_error: unknown, response: any) => resolve(response));
    });
    uploadCall.emit('data', chunk({
      ad_account_id: 'act_123',
      filename: 'long.mp4',
      chunk_bytes: Buffer.from('video'),
      is_last_chunk: true,
    }));
    await uploadStartedPromise;

    const pauseCall = new EventEmitter() as EventEmitter & {
      request: Record<string, unknown>;
      getDeadline: () => Date;
    };
    pauseCall.request = {
      session_id: 'session-upload',
      vision_profile_id: 'profile-upload',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: 'act_123',
      method: 'POST',
      endpoint: '/987654321',
      query_params: { status: 'PAUSED' },
      body_json: '',
      timeout_ms: 30_000,
    };
    pauseCall.getDeadline = () => new Date(Date.now() + 30_000);
    const pauseResponse = await new Promise<any>((resolve, reject) => {
      handlers.executeGraphCallV5(pauseCall, (error: unknown, response: any) => {
        if (error) reject(error);
        else resolve(response);
      });
    });

    assert.equal(pauseResponse.status_code, 200);
    releaseUpload(uploadOk('vid-long'));
    assert.equal((await uploadDone).ok, true);
  });

  for (const mode of ['cancelled', 'deadline'] as const) {
    it(`${mode} aborts in-flight video upload and emits no later success`, async () => {
      let uploadStarted!: () => void;
      const uploadStartedPromise = new Promise<void>((resolve) => { uploadStarted = resolve; });
      let observedSignal: AbortSignal | undefined;
      const callbacks: Array<{ error: any; response: any }> = [];
      const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
        getInteractivePage: () => ({}) as Page,
        authorizeOperationCapability: () => undefined,
        uploadVideoSingle: async (_page, _params, operation) => {
          observedSignal = operation?.signal;
          uploadStarted();
          await new Promise<void>((resolve) => {
            operation?.signal?.addEventListener('abort', () => resolve(), { once: true });
          });
          return uploadOk('must-not-confirm');
        },
      });
      const call = new EventEmitter() as EventEmitter & { getDeadline?: () => Date };
      if (mode === 'deadline') call.getDeadline = () => new Date(Date.now() + 25);
      handlers.uploadVideo(call, (error: unknown, response: unknown) => {
        callbacks.push({ error, response });
      });
      call.emit('data', chunk({
        ad_account_id: 'act_456',
        chunk_bytes: Buffer.from('video'),
        is_last_chunk: true,
      }));
      await uploadStartedPromise;
      if (mode === 'cancelled') call.emit('cancelled');
      else await new Promise((resolve) => setTimeout(resolve, 50));
      await tick();

      assert.equal(observedSignal?.aborted, true);
      assert.equal(callbacks.length, 1);
      assert.equal(callbacks[0].response, undefined);
      assert.equal(callbacks[0].error.code, mode === 'cancelled' ? 1 : 4);
    });
  }

  it('unary image deadline aborts upload and cannot emit a late success', async () => {
    let observedSignal: AbortSignal | undefined;
    let observedAccountId = '';
    let uploadStarted!: () => void;
    const uploadStartedPromise = new Promise<void>((resolve) => { uploadStarted = resolve; });
    const callbacks: Array<{ error: any; response: any }> = [];
    const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
      getInteractivePage: () => ({}) as Page,
      authorizeOperationCapability: () => undefined,
      uploadImage: async (_page, params, operation) => {
        observedSignal = operation?.signal;
        observedAccountId = params.adAccountId;
        uploadStarted();
        await new Promise<void>((resolve) => {
          operation?.signal?.addEventListener('abort', () => resolve(), { once: true });
        });
        return { ok: true, imageHash: 'late-hash', url: '', error: '', durationMs: 1 };
      },
    });
    const call = new EventEmitter() as EventEmitter & {
      request: Record<string, unknown>;
      getDeadline: () => Date;
    };
    call.request = {
      session_id: 'session-upload',
      vision_profile_id: 'profile-upload',
      capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
      ad_account_id: '789',
      filename: 'image.jpg',
      content_type: 'image/jpeg',
      file_bytes: Buffer.from('image'),
    };
    call.getDeadline = () => new Date(Date.now() + 25);
    const pending = handlers.uploadImage(call, (error: unknown, response: unknown) => {
      callbacks.push({ error, response });
    });
    await uploadStartedPromise;
    await pending;

    assert.equal(observedSignal?.aborted, true);
    assert.equal(observedAccountId, 'act_789');
    assert.equal(callbacks.length, 1);
    assert.equal(callbacks[0].error.code, 4);
    assert.equal(callbacks[0].response, undefined);
  });

  it('cancel while queued behind interactive work never starts the upload step', async () => {
    _resetPageLocks();
    let releaseBlock!: () => void;
    let blockStarted!: () => void;
    const blockStartedPromise = new Promise<void>((resolve) => { blockStarted = resolve; });
    const blocker = withPageRoleLock('session-upload', 'interactive', '999', async () => {
      blockStarted();
      await new Promise<void>((resolve) => { releaseBlock = resolve; });
    });
    await blockStartedPromise;

    const uploads: UploadCall[] = [];
    const handler = makeUploadHandler(async () => uploadOk('must-not-start'), uploads);
    const call = new EventEmitter();
    const callbacks: Array<{ error: any; response: any }> = [];
    handler(call, (error: unknown, response: unknown) => {
      callbacks.push({ error, response });
    });
    call.emit('data', chunk({
      ad_account_id: 'act_999',
      chunk_bytes: Buffer.from('video'),
      is_last_chunk: true,
    }));
    await tick();
    call.emit('cancelled');
    releaseBlock();
    await blocker;
    await tick();

    assert.equal(uploads.length, 0);
    assert.equal(callbacks.length, 1);
    assert.equal(callbacks[0].error.code, 1);
    assert.equal(callbacks[0].response, undefined);
  });
});
