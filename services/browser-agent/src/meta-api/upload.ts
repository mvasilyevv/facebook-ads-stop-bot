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
import { randomUUID } from 'crypto';
import { META_API_VERSION } from './client.js';
import {
  bindAbortSignalToPage,
  clearInPageFetchOperation,
  raceWithAbort,
} from '../in-page-abort.js';

const DEFAULT_UPLOAD_TIMEOUT_MS = 120_000;

export interface UploadOperationOptions {
  signal?: AbortSignal;
  operationId?: string;
}

export interface UploadImageResult {
  ok: boolean;
  imageHash: string;
  url: string;
  error: string;
  durationMs: number;
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
  },
  options: UploadOperationOptions = {},
): Promise<UploadImageResult> {
  const timeoutMs = params.timeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS;
  const t0 = Date.now();

  if (options.signal?.aborted) {
    return { ok: false, imageHash: '', url: '', error: 'cancelled', durationMs: 0 };
  }

  if (!params.adAccountId.startsWith('act_')) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: `ad_account_id должен начинаться с act_, получено ${params.adAccountId}`,
      durationMs: 0,
    };
  }

  if (!params.fileBytes || params.fileBytes.length === 0) {
    return {
      ok: false,
      imageHash: '',
      url: '',
      error: 'INVALID_ARGUMENT: file_bytes пусты',
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
    operationId: options.operationId ?? `image:${randomUUID()}`,
  };
  const abortBinding = bindAbortSignalToPage(page, evalArgs.operationId, options.signal);

  try {
    const result = await raceWithAbort(page.evaluate(async (args) => {
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

      const root = globalThis as typeof globalThis & {
        __fbAgentFetchAbort?: {
          controllers: Map<string, Set<AbortController>>;
          cancelled: Set<string>;
        };
      };
      const state = root.__fbAgentFetchAbort ??= {
        controllers: new Map<string, Set<AbortController>>(),
        cancelled: new Set<string>(),
      };
      const controller = new AbortController();
      const controllers = state.controllers.get(args.operationId) ?? new Set<AbortController>();
      controllers.add(controller);
      state.controllers.set(args.operationId, controllers);
      if (state.cancelled.has(args.operationId)) controller.abort('grpc_cancelled');
      const timeoutId = setTimeout(() => controller.abort('deadline_exceeded'), args.timeoutMs);

      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          body: form,
          signal: controller.signal,
        });
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
        const msg = err?.name === 'AbortError'
          ? `Timeout после ${args.timeoutMs}мс`
          : String(err?.message ?? err);
        return { ok: false, hash: '', url: '', error: msg };
      } finally {
        clearTimeout(timeoutId);
        controllers.delete(controller);
        if (controllers.size === 0) state.controllers.delete(args.operationId);
      }
    }, evalArgs), options.signal);

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
  } finally {
    abortBinding.dispose();
    void clearInPageFetchOperation(page, evalArgs.operationId);
  }
}

/**
 * Загружает видео в /act_X/advideos ОДНИМ multipart-POST (source=File) — как картинки.
 * Возвращает video_id (поле `id` в ответе Meta).
 *
 * Почему single-POST, а не chunked resumable (upload_phase=start/transfer/finish):
 * Meta v22 отвергает upload_phase=start как 'Invalid parameter' (GRAPH_ERROR_100).
 * Single-POST `source` поддерживается для видео до ~1GB и проверен живьём (200 + id).
 */
export async function uploadVideoSingle(
  page: Page,
  params: {
    adAccountId: string;
    filename: string;
    fileBytes: Uint8Array | Buffer;
    timeoutMs?: number;
  },
  options: UploadOperationOptions = {},
): Promise<{ ok: boolean; videoId: string; error: string; durationMs: number }> {
  const timeoutMs = params.timeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS;
  const t0 = Date.now();

  if (options.signal?.aborted) {
    return { ok: false, videoId: '', error: 'cancelled', durationMs: 0 };
  }

  if (!params.adAccountId.startsWith('act_')) {
    return {
      ok: false,
      videoId: '',
      error: `ad_account_id должен начинаться с act_, получено ${params.adAccountId}`,
      durationMs: 0,
    };
  }
  if (!params.fileBytes || params.fileBytes.length === 0) {
    return { ok: false, videoId: '', error: 'INVALID_ARGUMENT: file_bytes пусты', durationMs: 0 };
  }

  const base64 = Buffer.from(params.fileBytes).toString('base64');
  const operationId = options.operationId ?? `video:${randomUUID()}`;
  const abortBinding = bindAbortSignalToPage(page, operationId, options.signal);

  try {
    const result: { ok: boolean; videoId: string; error: string } = await raceWithAbort(page.evaluate(
      async (args) => {
        const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
        if (!match) return { ok: false, videoId: '', error: 'TOKEN_NOT_FOUND_IN_PAGE' };
        const token = match[0];

        const bytes = Uint8Array.from(atob(args.base64Data), (c) => c.charCodeAt(0));
        const form = new FormData();
        form.append('source', new File([bytes], args.filename, { type: 'video/mp4' }));
        form.append('access_token', token);

        const url = `https://graph.facebook.com/${args.apiVersion}/${args.adAccountId}/advideos`;
        const root = globalThis as typeof globalThis & {
          __fbAgentFetchAbort?: {
            controllers: Map<string, Set<AbortController>>;
            cancelled: Set<string>;
          };
        };
        const state = root.__fbAgentFetchAbort ??= {
          controllers: new Map<string, Set<AbortController>>(),
          cancelled: new Set<string>(),
        };
        const controller = new AbortController();
        const controllers = state.controllers.get(args.operationId) ?? new Set<AbortController>();
        controllers.add(controller);
        state.controllers.set(args.operationId, controllers);
        if (state.cancelled.has(args.operationId)) controller.abort('grpc_cancelled');
        const timeoutId = setTimeout(() => controller.abort('deadline_exceeded'), args.timeoutMs);
        try {
          const response = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            body: form,
            signal: controller.signal,
          });
          const text = await response.text();
          let parsed: any;
          try {
            parsed = JSON.parse(text);
          } catch {
            return { ok: false, videoId: '', error: `Невалидный JSON: ${text.slice(0, 300)}` };
          }
          if (parsed?.error) {
            return {
              ok: false,
              videoId: '',
              error: `GRAPH_ERROR_${parsed.error.code}: ${parsed.error.message}`,
            };
          }
          const vid = String(parsed.id ?? parsed.video_id ?? '');
          if (!vid) return { ok: false, videoId: '', error: `Ответ без id: ${text.slice(0, 200)}` };
          return { ok: true, videoId: vid, error: '' };
        } catch (err: any) {
          const msg = err?.name === 'AbortError' ? 'Timeout' : String(err?.message ?? err);
          return { ok: false, videoId: '', error: msg };
        } finally {
          clearTimeout(timeoutId);
          controllers.delete(controller);
          if (controllers.size === 0) state.controllers.delete(args.operationId);
        }
      },
      {
        adAccountId: params.adAccountId,
        filename: params.filename || 'upload.mp4',
        base64Data: base64,
        timeoutMs,
        apiVersion: META_API_VERSION,
        operationId,
      },
    ), options.signal);
    return { ok: result.ok, videoId: result.videoId, error: result.error, durationMs: Date.now() - t0 };
  } catch (err: any) {
    return { ok: false, videoId: '', error: String(err?.message ?? err), durationMs: Date.now() - t0 };
  } finally {
    abortBinding.dispose();
    void clearInPageFetchOperation(page, operationId);
  }
}
