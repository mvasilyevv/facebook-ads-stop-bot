// Marketing API media upload — multipart/form-data из браузерного контекста.
//
// Архитектурное обоснование совпадает с client.ts: загрузка идёт через
// page.evaluate(fetch(..., {body: FormData})) — request исходит из той же
// session-context (cookies, fingerprint, IP), что и DOM-операции, иначе
// anti-fraud Meta отвергает запрос.
//
// Здесь НЕ используем executeGraphCall: он шлёт JSON, а нам нужен multipart.
// Token извлекается из page source тем же способом, что и в client.ts.

import type { Page } from 'playwright';
import { META_API_VERSION } from './client.js';

const DEFAULT_UPLOAD_TIMEOUT_MS = 120_000;

export interface UploadImageResult {
  ok: boolean;
  imageHash: string;
  url: string;
  error: string;
  durationMs: number;
}

export interface UploadVideoInitParams {
  adAccountId: string;
  filename: string;
  fileSize: number;
}

export interface VideoStartPhase {
  uploadSessionId: string;
  videoId: string;
  startOffset: number;
  endOffset: number;
}

export interface VideoTransferPhase {
  startOffset: number;
  endOffset: number;
}

export interface UploadVideoResult {
  ok: boolean;
  videoId: string;
  error: string;
  durationMs: number;
  chunksProcessed: number;
}

/**
 * Загружает картинку в /act_X/adimages через multipart/form-data.
 * Возвращает image_hash для последующего использования в AdCreative.
 *
 * Meta endpoint: POST /v22.0/act_{id}/adimages
 *   body: FormData с полем `source` = File(bytes, filename, content_type)
 *   ответ: { images: { <filename>: { hash, url } } }
 */
export async function uploadImage(
  page: Page,
  params: {
    adAccountId: string;
    filename: string;
    contentType: string;
    fileBytes: Uint8Array | Buffer;
    timeoutMs?: number;
    imageUrl?: string;  // НОВОЕ: загрузка по URL вместо multipart
    name?: string;      // НОВОЕ: имя картинки при URL-загрузке
  },
): Promise<UploadImageResult> {
  const timeoutMs = params.timeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS;
  const t0 = Date.now();

  if (!params.adAccountId.startsWith('act_')) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: `ad_account_id должен начинаться с act_, получено ${params.adAccountId}`,
      durationMs: 0,
    };
  }

  // Ветка URL-загрузки: Meta сама скачивает картинку, multipart не нужен.
  if (params.imageUrl) {
    return uploadImageFromUrl(page, {
      adAccountId: params.adAccountId,
      imageUrl: params.imageUrl,
      name: params.name,
      timeoutMs,
    });
  }

  // Ветка multipart: проверяем наличие байтов.
  if (!params.fileBytes || params.fileBytes.length === 0) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: 'INVALID_ARGUMENT: image_url и file_bytes оба пусты — нужен один из двух',
      durationMs: 0,
    };
  }

  // page.evaluate не сериализует Buffer/Uint8Array как есть.
  // Конвертируем в base64 → внутри fetch-evaluate декодируем в Uint8Array → Blob.
  const base64 = Buffer.from(params.fileBytes).toString('base64');

  const evalArgs = {
    adAccountId: params.adAccountId,
    filename: params.filename || 'upload.jpg',
    contentType: params.contentType || 'image/jpeg',
    base64Data: base64,
    timeoutMs,
    apiVersion: META_API_VERSION,
  };

  try {
    const result = await page.evaluate(async (args) => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) {
        return { ok: false, hash: '', url: '', error: 'TOKEN_NOT_FOUND_IN_PAGE' };
      }
      const token = match[0];

      // Декодируем base64 → Uint8Array → Blob (внутри fetch).
      const binary = atob(args.base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: args.contentType });

      const form = new FormData();
      form.append('source', blob, args.filename);
      form.append('access_token', token);

      const url = `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/adimages`;

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);

      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          body: form,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        const text = await response.text();
        let parsed: any;
        try {
          parsed = JSON.parse(text);
        } catch {
          return { ok: false, hash: '', url: '', error: `Невалидный JSON: ${text.slice(0, 300)}` };
        }

        if (parsed?.error) {
          const e = parsed.error;
          return {
            ok: false,
            hash: '',
            url: '',
            error: `GRAPH_ERROR_${e.code ?? '?'}: ${e.message ?? 'unknown'}`,
          };
        }

        // Meta возвращает { images: { <filename>: { hash, url } } }.
        // Имя файла в ответе может отличаться от имени, что мы передали,
        // поэтому берём первое значение из images.
        const images = parsed?.images;
        if (!images || typeof images !== 'object') {
          return { ok: false, hash: '', url: '', error: `Нет поля images в ответе: ${text.slice(0, 300)}` };
        }
        const keys = Object.keys(images);
        if (keys.length === 0) {
          return { ok: false, hash: '', url: '', error: 'images пустой' };
        }
        const first = images[keys[0]];
        const hash = String(first?.hash ?? '');
        const imgUrl = String(first?.url ?? '');
        if (!hash) {
          return { ok: false, hash: '', url: '', error: 'hash отсутствует в ответе' };
        }
        return { ok: true, hash, url: imgUrl, error: '' };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const msg = err?.name === 'AbortError'
          ? `Timeout после ${args.timeoutMs}мс`
          : String(err?.message ?? err);
        return { ok: false, hash: '', url: '', error: msg };
      }
    }, evalArgs);

    return {
      ok: result.ok,
      imageHash: result.hash,
      url: result.url,
      error: result.error,
      durationMs: Date.now() - t0,
    };
  } catch (err: any) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: `PAGE_EVALUATE_ERROR: ${String(err?.message ?? err)}`,
      durationMs: Date.now() - t0,
    };
  }
}

/**
 * Внутренний helper: загрузить картинку по URL — Meta сама скачивает её.
 * POST /act_X/adimages?url=...&name=...&access_token=...
 * Ответ: { images: { <name>: { hash, url } } } — такой же как multipart.
 */
async function uploadImageFromUrl(
  page: Page,
  params: {
    adAccountId: string;
    imageUrl: string;
    name?: string;
    timeoutMs: number;
  },
): Promise<UploadImageResult> {
  const t0 = Date.now();

  const evalArgs = {
    adAccountId: params.adAccountId,
    imageUrl: params.imageUrl,
    name: params.name || '',
    timeoutMs: params.timeoutMs,
    apiVersion: META_API_VERSION,
  };

  try {
    const result = await page.evaluate(async (args) => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) {
        return { ok: false, hash: '', url: '', error: 'TOKEN_NOT_FOUND_IN_PAGE' };
      }
      const token = match[0];

      // URL-загрузка: query params вместо multipart body.
      const qp = new URLSearchParams();
      qp.set('url', args.imageUrl);
      qp.set('access_token', token);
      if (args.name) qp.set('name', args.name);

      const endpoint =
        `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/adimages?${qp.toString()}`;

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'include',
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        const text = await response.text();
        let parsed: any;
        try {
          parsed = JSON.parse(text);
        } catch {
          return { ok: false, hash: '', url: '', error: `Невалидный JSON: ${text.slice(0, 300)}` };
        }

        if (parsed?.error) {
          const e = parsed.error;
          return {
            ok: false,
            hash: '',
            url: '',
            error: `GRAPH_ERROR_${e.code ?? '?'}: ${e.message ?? 'unknown'}`,
          };
        }

        const images = parsed?.images;
        if (!images || typeof images !== 'object') {
          return { ok: false, hash: '', url: '', error: `Нет поля images в ответе: ${text.slice(0, 300)}` };
        }
        const keys = Object.keys(images);
        if (keys.length === 0) {
          return { ok: false, hash: '', url: '', error: 'images пустой' };
        }
        const first = images[keys[0]];
        const hash = String(first?.hash ?? '');
        const imgUrl = String(first?.url ?? '');
        if (!hash) {
          return { ok: false, hash: '', url: '', error: 'hash отсутствует в ответе' };
        }
        return { ok: true, hash, url: imgUrl, error: '' };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const msg = err?.name === 'AbortError'
          ? `Timeout после ${args.timeoutMs}мс`
          : String(err?.message ?? err);
        return { ok: false, hash: '', url: '', error: msg };
      }
    }, evalArgs);

    return {
      ok: result.ok,
      imageHash: result.hash,
      url: result.url,
      error: result.error,
      durationMs: Date.now() - t0,
    };
  } catch (err: any) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: `PAGE_EVALUATE_ERROR: ${String(err?.message ?? err)}`,
      durationMs: Date.now() - t0,
    };
  }
}

/**
 * Видео-upload-сессия: держит state между client streaming chunks.
 *
 * Lifecycle:
 *   1. start(): POST upload_phase=start с file_size → возвращает upload_session_id + video_id
 *   2. transfer(bytes): POST upload_phase=transfer с file_chunk + start_offset
 *      → возвращает следующий start_offset (если ещё не всё)
 *   3. finish(): POST upload_phase=finish → возвращает {success: true, video_id}
 *
 * Все вызовы — через page.evaluate(fetch FormData), внутри одного browser-context.
 */
export class VideoUploadSession {
  private page: Page;
  private adAccountId: string;
  private filename: string;
  private fileSize: number;
  private apiVersion: string;
  private timeoutMs: number;

  private uploadSessionId: string = '';
  private videoId: string = '';
  private currentOffset: number = 0;
  private expectedNextOffset: number = 0;
  private started: boolean = false;
  private finished: boolean = false;

  constructor(
    page: Page,
    params: UploadVideoInitParams & { timeoutMs?: number },
  ) {
    this.page = page;
    this.adAccountId = params.adAccountId;
    this.filename = params.filename;
    this.fileSize = params.fileSize;
    this.apiVersion = META_API_VERSION;
    this.timeoutMs = params.timeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS;
  }

  /** Фаза start: получить upload_session_id + video_id. */
  async start(): Promise<VideoStartPhase> {
    if (this.started) {
      throw new Error('VideoUploadSession уже запущен');
    }
    if (!this.adAccountId.startsWith('act_')) {
      throw new Error(`ad_account_id должен начинаться с act_, получено ${this.adAccountId}`);
    }
    if (this.fileSize <= 0) {
      throw new Error(`file_size должен быть > 0, получено ${this.fileSize}`);
    }

    const result: {
      ok: boolean; error: string;
      uploadSessionId: string; videoId: string;
      startOffset: number; endOffset: number;
    } = await this.page.evaluate(async (args) => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) return { ok: false, error: 'TOKEN_NOT_FOUND_IN_PAGE', uploadSessionId: '', videoId: '', startOffset: 0, endOffset: 0 };
      const token = match[0];

      const form = new FormData();
      form.append('upload_phase', 'start');
      form.append('file_size', String(args.fileSize));
      form.append('access_token', token);

      const url = `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/advideos`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);
      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          body: form,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        const text = await response.text();
        let parsed: any;
        try { parsed = JSON.parse(text); } catch { return { ok: false, error: `Невалидный JSON: ${text.slice(0, 300)}`, uploadSessionId: '', videoId: '', startOffset: 0, endOffset: 0 }; }
        if (parsed?.error) return { ok: false, error: `GRAPH_ERROR_${parsed.error.code}: ${parsed.error.message}`, uploadSessionId: '', videoId: '', startOffset: 0, endOffset: 0 };
        return {
          ok: true,
          error: '',
          uploadSessionId: String(parsed.upload_session_id ?? ''),
          videoId: String(parsed.video_id ?? ''),
          startOffset: Number(parsed.start_offset ?? 0),
          endOffset: Number(parsed.end_offset ?? 0),
        };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const msg = err?.name === 'AbortError' ? `Timeout` : String(err?.message ?? err);
        return { ok: false, error: msg, uploadSessionId: '', videoId: '', startOffset: 0, endOffset: 0 };
      }
    }, {
      adAccountId: this.adAccountId,
      fileSize: this.fileSize,
      timeoutMs: this.timeoutMs,
      apiVersion: this.apiVersion,
    });

    if (!result.ok || !result.uploadSessionId) {
      throw new Error(`UploadVideo start failed: ${result.error || 'no upload_session_id'}`);
    }
    this.uploadSessionId = result.uploadSessionId;
    this.videoId = result.videoId;
    this.currentOffset = result.startOffset;
    this.expectedNextOffset = result.endOffset;
    this.started = true;
    return {
      uploadSessionId: this.uploadSessionId,
      videoId: this.videoId,
      startOffset: result.startOffset,
      endOffset: result.endOffset,
    };
  }

  /** Фаза transfer: загрузить чанк начиная с текущего offset. */
  async transfer(chunkBytes: Uint8Array | Buffer): Promise<VideoTransferPhase> {
    if (!this.started) {
      throw new Error('VideoUploadSession не запущен — вызови start() сначала');
    }
    if (this.finished) {
      throw new Error('VideoUploadSession уже завершён');
    }
    if (!chunkBytes || chunkBytes.length === 0) {
      throw new Error('chunk пустой');
    }

    const base64 = Buffer.from(chunkBytes).toString('base64');

    const result: { ok: boolean; error: string; startOffset: number; endOffset: number } =
      await this.page.evaluate(async (args) => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) return { ok: false, error: 'TOKEN_NOT_FOUND_IN_PAGE', startOffset: 0, endOffset: 0 };
      const token = match[0];

      const binary = atob(args.base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: 'application/octet-stream' });

      const form = new FormData();
      form.append('upload_phase', 'transfer');
      form.append('upload_session_id', args.uploadSessionId);
      form.append('start_offset', String(args.startOffset));
      form.append('video_file_chunk', blob, args.filename);
      form.append('access_token', token);

      const url = `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/advideos`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);
      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          body: form,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        const text = await response.text();
        let parsed: any;
        try { parsed = JSON.parse(text); } catch { return { ok: false, error: `Невалидный JSON: ${text.slice(0, 300)}`, startOffset: 0, endOffset: 0 }; }
        if (parsed?.error) return { ok: false, error: `GRAPH_ERROR_${parsed.error.code}: ${parsed.error.message}`, startOffset: 0, endOffset: 0 };
        return {
          ok: true,
          error: '',
          startOffset: Number(parsed.start_offset ?? 0),
          endOffset: Number(parsed.end_offset ?? 0),
        };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const msg = err?.name === 'AbortError' ? `Timeout` : String(err?.message ?? err);
        return { ok: false, error: msg, startOffset: 0, endOffset: 0 };
      }
    }, {
      adAccountId: this.adAccountId,
      uploadSessionId: this.uploadSessionId,
      filename: this.filename,
      startOffset: this.currentOffset,
      base64Data: base64,
      timeoutMs: this.timeoutMs,
      apiVersion: this.apiVersion,
    });

    if (!result.ok) {
      throw new Error(`UploadVideo transfer failed (offset=${this.currentOffset}): ${result.error}`);
    }
    this.currentOffset = result.startOffset;
    this.expectedNextOffset = result.endOffset;
    return { startOffset: result.startOffset, endOffset: result.endOffset };
  }

  /** Фаза finish: подтвердить завершение → Meta склеит чанки и вернёт video_id. */
  async finish(): Promise<string> {
    if (!this.started) {
      throw new Error('VideoUploadSession не запущен');
    }
    if (this.finished) {
      return this.videoId;
    }

    const result: { ok: boolean; error: string; success: boolean; videoId: string } =
      await this.page.evaluate(async (args) => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) return { ok: false, error: 'TOKEN_NOT_FOUND_IN_PAGE', success: false, videoId: '' };
      const token = match[0];

      const form = new FormData();
      form.append('upload_phase', 'finish');
      form.append('upload_session_id', args.uploadSessionId);
      form.append('access_token', token);

      const url = `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/advideos`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);
      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          body: form,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        const text = await response.text();
        let parsed: any;
        try { parsed = JSON.parse(text); } catch { return { ok: false, error: `Невалидный JSON: ${text.slice(0, 300)}`, success: false, videoId: '' }; }
        if (parsed?.error) return { ok: false, error: `GRAPH_ERROR_${parsed.error.code}: ${parsed.error.message}`, success: false, videoId: '' };
        return {
          ok: true,
          error: '',
          success: Boolean(parsed.success),
          videoId: String(parsed.video_id ?? ''),
        };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const msg = err?.name === 'AbortError' ? `Timeout` : String(err?.message ?? err);
        return { ok: false, error: msg, success: false, videoId: '' };
      }
    }, {
      adAccountId: this.adAccountId,
      uploadSessionId: this.uploadSessionId,
      timeoutMs: this.timeoutMs,
      apiVersion: this.apiVersion,
    });

    if (!result.ok) {
      throw new Error(`UploadVideo finish failed: ${result.error}`);
    }
    if (!result.success) {
      throw new Error('UploadVideo finish: Meta вернула success=false');
    }
    // video_id известен ещё со start-фазы; finish может его не повторить,
    // но если повторил — обновляем (на всякий случай).
    const finalVideoId = result.videoId || this.videoId;
    if (!finalVideoId) {
      throw new Error('UploadVideo finish: video_id отсутствует');
    }
    this.videoId = finalVideoId;
    this.finished = true;
    return this.videoId;
  }

  get sessionId(): string { return this.uploadSessionId; }
  get id(): string { return this.videoId; }
  get isStarted(): boolean { return this.started; }
  get isFinished(): boolean { return this.finished; }
  get nextOffset(): number { return this.currentOffset; }
}
