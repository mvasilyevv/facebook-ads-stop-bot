// gRPC-обработчики MetaApiService.
// Мост между gRPC-запросом и executeGraphCall/checkMetaApiHealth в client.ts.
import * as grpc from '@grpc/grpc-js';
import type { Page } from 'playwright';
import { SessionManager, findPreferredPrimaryPage } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { executeGraphCall, checkMetaApiHealth, type GraphApiCallParams } from './client.js';
import { uploadImage, VideoUploadSession } from './upload.js';
import { withPageLock } from '../page-lock.js';

function grpcCodeForError(err: any): number {
  const message = String(err?.message || '').toLowerCase();
  return message.includes('not found') || message.includes('не найден')
    ? grpc.status.NOT_FOUND
    : grpc.status.INTERNAL;
}

function getPage(session: BrowserSession): Page {
  const preferredPage = findPreferredPrimaryPage(session.browser);
  if (preferredPage && preferredPage !== session.primaryPage) {
    session.primaryPage = preferredPage;
  }
  const page = session.primaryPage;
  const closed = typeof page?.isClosed === 'function' && page.isClosed();
  if (!page || closed) {
    throw new Error('Основная страница браузера недоступна');
  }
  return page;
}

export function createMetaApiServiceHandlers(sessionManager: SessionManager) {
  function resolveSession(sessionId: string): BrowserSession {
    const normalizedSessionId = String(sessionId || '').trim();
    return normalizedSessionId
      ? sessionManager.getSession(normalizedSessionId)
      : sessionManager.getPreferredSession();
  }

  async function executeGraphCallHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      // Мульти-кабинет: с ad_account_id fetch уходит из вкладки СВОЕГО кабинета
      // («человеческий» паттерн). Пусто → legacy primary-вкладка (токен общий).
      const actId: string = String(req.ad_account_id || '').replace(/^act_/, '').trim();

      // Конвертация proto map<string, string> в plain object для page.evaluate.
      const queryParams: Record<string, string> = {};
      if (req.query_params && typeof req.query_params === 'object') {
        for (const [key, value] of Object.entries(req.query_params)) {
          queryParams[String(key)] = String(value);
        }
      }

      const params: GraphApiCallParams = {
        method: (req.method || 'GET').toUpperCase() as 'GET' | 'POST' | 'DELETE',
        endpoint: req.endpoint || '/me',
        queryParams,
        bodyJson: req.body_json && req.body_json.length > 0 ? req.body_json : undefined,
        timeoutMs: req.timeout_ms && req.timeout_ms > 0 ? req.timeout_ms : undefined,
      };

      // H-7 (BA-4): per-session лок — mutation page.evaluate(fetch) не должен
      // пересекаться со scan page.reload (acquireGraphContext) на общей странице,
      // иначе reload рвёт in-flight fetch → «Execution context was destroyed».
      // Резолв вкладки кабинета (может открыть новую) — тоже под локом.
      const result = await withPageLock(session.id, async () => {
        const page = actId
          ? await sessionManager.ensureAdsManagerPage(session, { actId })
          : getPage(session);
        return executeGraphCall(page, params);
      });

      callback(null, {
        status_code: result.statusCode,
        response_json: result.responseJson,
        duration_ms: result.durationMs,
        error: result.error
          ? {
              code: result.error.code,
              subcode: result.error.subcode,
              type: result.error.type,
              message: result.error.message,
              fbtrace_id: result.error.fbtraceId,
            }
          : undefined,
      });
    } catch (err: any) {
      callback({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    }
  }

  async function checkMetaApiHealthHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      const page = getPage(session);

      const result = await checkMetaApiHealth(page);

      callback(null, {
        healthy: result.healthy,
        current_url: result.currentUrl,
        token_present: result.tokenPresent,
        token_length: result.tokenLength,
        detail: result.detail,
      });
    } catch (err: any) {
      // Если сессия не найдена — возвращаем healthy=false как штатный ответ
      // (а не gRPC ошибку), потому что вызывающий health_watchdog хочет видеть состояние.
      callback(null, {
        healthy: false,
        current_url: '',
        token_present: false,
        token_length: 0,
        detail: `error: ${String(err?.message ?? err)}`,
      });
    }
  }

  async function uploadImageHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      const page = getPage(session);

      const fileBytes = req.file_bytes;
      // proto-loader отдаёт bytes как Buffer; нормализуем.
      const buf: Buffer = Buffer.isBuffer(fileBytes)
        ? fileBytes
        : Buffer.from(fileBytes || []);

      const imageUrl = String(req.image_url || '');
      const name = String(req.name || '');

      // Если image_url пуст и file_bytes тоже — вернём ошибку без вызова uploadImage.
      if (!imageUrl && buf.length === 0) {
        callback(null, {
          image_hash: '',
          ok: false,
          error: 'INVALID_ARGUMENT: image_url и file_bytes оба пусты — нужен один из двух',
          url: '',
          duration_ms: 0,
        });
        return;
      }

      const result = await uploadImage(page, {
        adAccountId: String(req.ad_account_id || ''),
        filename: String(req.filename || 'upload.jpg'),
        contentType: String(req.content_type || 'image/jpeg'),
        fileBytes: buf,
        imageUrl: imageUrl || undefined,
        name: name || undefined,
      });

      callback(null, {
        image_hash: result.imageHash,
        ok: result.ok,
        error: result.error,
        url: result.url,
        duration_ms: result.durationMs,
      });
    } catch (err: any) {
      callback({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    }
  }

  // Client streaming: клиент шлёт несколько UploadVideoChunk, сервер отвечает одним UploadVideoResponse.
  // Поток: первый chunk с метаданными (filename + file_size + ad_account_id) → start;
  // далее transfer для каждого chunk с bytes; последний chunk с is_last_chunk=true → finish.
  function uploadVideoHandler(call: any, callback: any): void {
    const t0 = Date.now();
    let videoSession: VideoUploadSession | null = null;
    let chunkIndex = 0;
    let chunksProcessed = 0;
    let resolvedSession: BrowserSession | null = null;
    let resolvedPage: Page | null = null;
    let isFinishing = false;
    let respondedOnce = false;

    // Все на await-приёме данных: каждый chunk обрабатывается последовательно через очередь.
    const pendingQueue: Buffer[] = [];
    let processing = false;
    let endReceived = false;
    let isLastChunkPending = false;

    function respondError(msg: string): void {
      if (respondedOnce) return;
      respondedOnce = true;
      callback(null, {
        video_id: videoSession?.id || '',
        ok: false,
        error: msg,
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    function respondSuccess(videoId: string): void {
      if (respondedOnce) return;
      respondedOnce = true;
      callback(null, {
        video_id: videoId,
        ok: true,
        error: '',
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    async function drainQueue(): Promise<void> {
      if (processing) return;
      processing = true;
      try {
        while (pendingQueue.length > 0) {
          const chunk = pendingQueue.shift()!;
          if (!videoSession) {
            respondError('Внутренняя ошибка: videoSession не инициализирован к началу transfer');
            return;
          }
          await videoSession.transfer(chunk);
          chunksProcessed += 1;
        }
        if (isLastChunkPending && videoSession && !isFinishing) {
          isFinishing = true;
          const videoId = await videoSession.finish();
          respondSuccess(videoId);
        } else if (endReceived && !respondedOnce && videoSession && !isFinishing) {
          // Клиент закрыл стрим без is_last_chunk — корректно делаем finish.
          isFinishing = true;
          const videoId = await videoSession.finish();
          respondSuccess(videoId);
        }
      } catch (err: any) {
        respondError(`UploadVideo: ${String(err?.message ?? err)}`);
      } finally {
        processing = false;
      }
    }

    call.on('data', async (chunk: any) => {
      try {
        chunkIndex += 1;
        // Первый chunk должен принести метаданные (init).
        if (videoSession === null) {
          const sessionId = String(chunk.session_id || '');
          const adAccountId = String(chunk.ad_account_id || '');
          const filename = String(chunk.filename || 'upload.mp4');
          const fileSize = Number(chunk.file_size || 0);

          if (!adAccountId) {
            respondError('Первый chunk должен содержать ad_account_id');
            return;
          }
          if (fileSize <= 0) {
            respondError('Первый chunk должен содержать file_size > 0');
            return;
          }

          resolvedSession = sessionId
            ? sessionManager.getSession(sessionId)
            : sessionManager.getPreferredSession();
          resolvedPage = getPage(resolvedSession);

          videoSession = new VideoUploadSession(resolvedPage, {
            adAccountId,
            filename,
            fileSize,
          });
          await videoSession.start();

          // Если в первом chunk есть и bytes (не init-only) — обрабатываем как transfer.
          const initOnly = Boolean(chunk.is_init);
          const bytes: Buffer = Buffer.isBuffer(chunk.chunk_bytes)
            ? chunk.chunk_bytes
            : Buffer.from(chunk.chunk_bytes || []);
          if (!initOnly && bytes.length > 0) {
            pendingQueue.push(bytes);
          }
          if (chunk.is_last_chunk) {
            isLastChunkPending = true;
          }
          await drainQueue();
          return;
        }

        // Последующие chunks — это transfer.
        const bytes: Buffer = Buffer.isBuffer(chunk.chunk_bytes)
          ? chunk.chunk_bytes
          : Buffer.from(chunk.chunk_bytes || []);
        if (bytes.length > 0) {
          pendingQueue.push(bytes);
        }
        if (chunk.is_last_chunk) {
          isLastChunkPending = true;
        }
        await drainQueue();
      } catch (err: any) {
        respondError(`UploadVideo: ${String(err?.message ?? err)}`);
      }
    });

    call.on('end', async () => {
      endReceived = true;
      await drainQueue();
      if (!respondedOnce) {
        // Стрим закрыт, но finish не запущен — это значит что-то не так
        // (например, ни одного chunk не пришло). Сигналим клиенту.
        if (videoSession === null) {
          respondError('UploadVideo: стрим закрыт без единого chunk');
        }
      }
    });

    call.on('error', (err: any) => {
      respondError(`UploadVideo: stream error ${String(err?.message ?? err)}`);
    });

    call.on('cancelled', () => {
      respondError('UploadVideo: cancelled клиентом');
    });
  }

  return {
    executeGraphCall: executeGraphCallHandler,
    checkMetaApiHealth: checkMetaApiHealthHandler,
    uploadImage: uploadImageHandler,
    uploadVideo: uploadVideoHandler,
  };
}
