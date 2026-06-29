// M12 (аудит): money-критичный сборщик чанков UploadVideo. Регресс на класс
// «гонка прошла сквозь тесты»: порядок/повтор чанков → битое видео или дубль аплоада,
// а ад с пустым/битым крео тратит бюджет. Тестируем uploadVideoHandler через инъекцию
// uploadVideoSingle/getPage (без реального браузера и POST в Meta).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';

import type { Page } from 'playwright';
import type { SessionManager } from '../session-manager.js';
import { createMetaApiServiceHandlers } from './service.js';

interface UploadCall {
  adAccountId: string;
  filename: string;
  fileBytes: Buffer;
}

// Совпадает с реальным возвратом uploadVideoSingle (deps требует typeof uploadVideoSingle).
type UploadResult = { ok: boolean; videoId: string; error: string; durationMs: number };
type UploadImpl = (page: Page, opts: UploadCall) => Promise<UploadResult>;

function uploadOk(videoId: string): UploadResult {
  return { ok: true, videoId, error: '', durationMs: 1 };
}

// Фейковый sessionManager: handler зовёт getPreferredSession()/getSession() — сессия
// пустышка (getPage заинъектен, поэтому её содержимое не важно).
const fakeSessionManager = {
  getSession: () => ({}),
  getPreferredSession: () => ({}),
} as unknown as SessionManager;

function makeUploadHandler(uploadImpl: UploadImpl, uploads: UploadCall[]) {
  const handlers = createMetaApiServiceHandlers(fakeSessionManager, {
    getPage: () => ({}) as unknown as Page,
    uploadVideoSingle: async (_page, opts) => {
      uploads.push(opts as UploadCall);
      return uploadImpl(_page as Page, opts as UploadCall);
    },
  });
  return handlers.uploadVideo as (call: EventEmitter, cb: (err: unknown, res: any) => void) => void;
}

function chunk(over: Record<string, unknown>): Record<string, unknown> {
  return { chunk_bytes: Buffer.alloc(0), ...over };
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
      call.emit('data', chunk({ ad_account_id: 'act_1', filename: 'v.mp4', chunk_bytes: Buffer.from('AAA') }));
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
