// gRPC-обработчики MetaApiService.
// Мост между gRPC-запросом и executeGraphCall/checkMetaApiHealth в client.ts.
import * as grpc from '@grpc/grpc-js';
import type { Page } from 'playwright';
import { SessionManager, findPreferredPrimaryPage } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { executeGraphCall, checkMetaApiHealth, type GraphApiCallParams } from './client.js';
import { uploadImage, uploadVideoSingle } from './upload.js';
import { withPageLock } from '../page-lock.js';
import { recordFetchOutcome, shouldHealNow } from '../session-health.js';

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

      // Авто-исцеление: сетевой сбой мутации (statusCode 0 / NetworkError code -2) при живой
      // странице — копим серию и эскалируем лечение, чтобы money-канал авто-стопа сам оживал
      // (а не требовал ручного рестарта). После callback — не задерживаем ответ воркеру.
      const netFail = result.statusCode === 0;
      recordFetchOutcome(session, !netFail);
      if (netFail && shouldHealNow(session, Date.now())) {
        try {
          const healed = await sessionManager.healSessionNetwork(session.id);
          console.warn(`[meta-api] авто-исцеление после мутации: ${healed.action} (ok=${healed.ok})`);
        } catch (e) {
          console.error('[meta-api] авто-исцеление после мутации упало:', e);
        }
      }
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
      const fullProbe = Boolean(req.full_probe);

      // full_probe делает реальный page.evaluate(fetch) — под per-session лок (как
      // executeGraphCall), чтобы probe не пересекался со scan reload и не рвал in-flight
      // fetch. Token-only режим — без лока (дешёвое чтение DOM).
      const result = fullProbe
        ? await withPageLock(session.id, () => checkMetaApiHealth(page, { fullProbe: true }))
        : await checkMetaApiHealth(page);

      callback(null, {
        healthy: result.healthy,
        current_url: result.currentUrl,
        token_present: result.tokenPresent,
        token_length: result.tokenLength,
        detail: result.detail,
        probe_performed: result.probePerformed,
        probe_ok: result.probeOk,
        probe_status_code: result.probeStatusCode,
        probe_duration_ms: result.probeDurationMs,
        probe_detail: result.probeDetail,
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
        probe_performed: false,
        probe_ok: false,
        probe_status_code: 0,
        probe_duration_ms: 0,
        probe_detail: 'not_performed',
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

  // Client streaming: клиент шлёт несколько UploadVideoChunk, сервер отвечает одним
  // UploadVideoResponse. Чанки нужны только для обхода gRPC-лимита сообщения — видео
  // собирается целиком и грузится ОДНИМ multipart-POST (source=File), как картинки.
  // Meta v22 отвергает chunked resumable (upload_phase=start/transfer/finish) как
  // 'Invalid parameter', а single-POST принимает (проверено живьём: 200 + video_id).
  function uploadVideoHandler(call: any, callback: any): void {
    const t0 = Date.now();
    let chunksProcessed = 0;
    let isFinishing = false;
    let respondedOnce = false;
    let videoId = '';

    // 'data'-обработчик ТОЛЬКО синхронно кладёт chunk в очередь, единственный воркер
    // processChunks разбирает строго последовательно (без гонок async-обработчиков) и
    // накапливает байты по порядку. Раньше async 'data' наезжали → перемешивание/гонка.
    let metadataSeen = false;
    let adAccountId = '';
    let filename = 'upload.mp4';
    let resolvedPage: Page | null = null;
    const videoBuffers: Buffer[] = [];

    const pendingChunks: any[] = [];
    let processing = false;
    let endReceived = false;
    let isLastChunkSeen = false;

    function respondError(msg: string): void {
      if (respondedOnce) return;
      respondedOnce = true;
      callback(null, {
        video_id: videoId,
        ok: false,
        error: msg,
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    function respondSuccess(vid: string): void {
      if (respondedOnce) return;
      respondedOnce = true;
      videoId = vid;
      callback(null, {
        video_id: vid,
        ok: true,
        error: '',
        duration_ms: Date.now() - t0,
        chunks_processed: chunksProcessed,
      });
    }

    function bytesOf(chunk: any): Buffer {
      return Buffer.isBuffer(chunk.chunk_bytes)
        ? chunk.chunk_bytes
        : Buffer.from(chunk.chunk_bytes || []);
    }

    async function processChunks(): Promise<void> {
      if (processing || respondedOnce) return;
      processing = true;
      try {
        while (pendingChunks.length > 0) {
          const chunk = pendingChunks.shift();
          if (!metadataSeen) {
            const sessionId = String(chunk.session_id || '');
            adAccountId = String(chunk.ad_account_id || '');
            filename = String(chunk.filename || 'upload.mp4');
            if (!adAccountId) {
              respondError('Первый chunk должен содержать ad_account_id');
              return;
            }
            const resolvedSession = sessionId
              ? sessionManager.getSession(sessionId)
              : sessionManager.getPreferredSession();
            resolvedPage = getPage(resolvedSession);
            metadataSeen = true;
          }
          const bytes = bytesOf(chunk);
          if (bytes.length > 0) {
            videoBuffers.push(bytes);
          }
          chunksProcessed += 1;
          if (chunk.is_last_chunk) {
            isLastChunkSeen = true;
          }
        }
        // Очередь пуста: всё видео собрано → грузим ОДНИМ POST.
        if (metadataSeen && resolvedPage && !isFinishing && (isLastChunkSeen || endReceived)) {
          isFinishing = true;
          const full = Buffer.concat(videoBuffers);
          if (full.length === 0) {
            respondError('UploadVideo: пустое видео (0 байт)');
            return;
          }
          const res = await uploadVideoSingle(resolvedPage, { adAccountId, filename, fileBytes: full });
          if (res.ok && res.videoId) {
            respondSuccess(res.videoId);
          } else {
            respondError(`UploadVideo: ${res.error || 'no video_id'}`);
          }
        }
      } catch (err: any) {
        respondError(`UploadVideo: ${String(err?.message ?? err)}`);
      } finally {
        processing = false;
      }
      // Догоняем chunks, приехавшие пока обрабатывали/финишили.
      if (!respondedOnce && pendingChunks.length > 0) {
        void processChunks();
      }
    }

    call.on('data', (chunk: any) => {
      pendingChunks.push(chunk);
      void processChunks();
    });

    call.on('end', () => {
      endReceived = true;
      if (!metadataSeen && pendingChunks.length === 0) {
        respondError('UploadVideo: стрим закрыт без единого chunk');
        return;
      }
      void processChunks();
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
