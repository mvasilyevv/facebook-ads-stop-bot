"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.createMetaApiServiceHandlers = createMetaApiServiceHandlers;
// gRPC-обработчики MetaApiService.
// Мост между gRPC-запросом и executeGraphCall/checkMetaApiHealth в client.ts.
const grpc = __importStar(require("@grpc/grpc-js"));
const session_manager_js_1 = require("../session-manager.js");
const client_js_1 = require("./client.js");
const upload_js_1 = require("./upload.js");
const page_lock_js_1 = require("../page-lock.js");
function grpcCodeForError(err) {
    const message = String(err?.message || '').toLowerCase();
    return message.includes('not found') || message.includes('не найден')
        ? grpc.status.NOT_FOUND
        : grpc.status.INTERNAL;
}
function getPage(session) {
    const preferredPage = (0, session_manager_js_1.findPreferredPrimaryPage)(session.browser);
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
function createMetaApiServiceHandlers(sessionManager) {
    function resolveSession(sessionId) {
        const normalizedSessionId = String(sessionId || '').trim();
        return normalizedSessionId
            ? sessionManager.getSession(normalizedSessionId)
            : sessionManager.getPreferredSession();
    }
    async function executeGraphCallHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            const page = getPage(session);
            // Конвертация proto map<string, string> в plain object для page.evaluate.
            const queryParams = {};
            if (req.query_params && typeof req.query_params === 'object') {
                for (const [key, value] of Object.entries(req.query_params)) {
                    queryParams[String(key)] = String(value);
                }
            }
            const params = {
                method: (req.method || 'GET').toUpperCase(),
                endpoint: req.endpoint || '/me',
                queryParams,
                bodyJson: req.body_json && req.body_json.length > 0 ? req.body_json : undefined,
                timeoutMs: req.timeout_ms && req.timeout_ms > 0 ? req.timeout_ms : undefined,
            };
            // H-7 (BA-4): per-session лок — mutation page.evaluate(fetch) не должен
            // пересекаться со scan page.reload (acquireGraphContext) на общей primaryPage,
            // иначе reload рвёт in-flight fetch → «Execution context was destroyed».
            const result = await (0, page_lock_js_1.withPageLock)(session.id, () => (0, client_js_1.executeGraphCall)(page, params));
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
        }
        catch (err) {
            callback({
                code: grpcCodeForError(err),
                message: String(err?.message ?? err),
            });
        }
    }
    async function checkMetaApiHealthHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            const page = getPage(session);
            const result = await (0, client_js_1.checkMetaApiHealth)(page);
            callback(null, {
                healthy: result.healthy,
                current_url: result.currentUrl,
                token_present: result.tokenPresent,
                token_length: result.tokenLength,
                detail: result.detail,
            });
        }
        catch (err) {
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
    async function uploadImageHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            const page = getPage(session);
            const fileBytes = req.file_bytes;
            // proto-loader отдаёт bytes как Buffer; нормализуем.
            const buf = Buffer.isBuffer(fileBytes)
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
            const result = await (0, upload_js_1.uploadImage)(page, {
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
        }
        catch (err) {
            callback({
                code: grpcCodeForError(err),
                message: String(err?.message ?? err),
            });
        }
    }
    // Client streaming: клиент шлёт несколько UploadVideoChunk, сервер отвечает одним UploadVideoResponse.
    // Поток: первый chunk с метаданными (filename + file_size + ad_account_id) → start;
    // далее transfer для каждого chunk с bytes; последний chunk с is_last_chunk=true → finish.
    function uploadVideoHandler(call, callback) {
        const t0 = Date.now();
        let videoSession = null;
        let chunkIndex = 0;
        let chunksProcessed = 0;
        let resolvedSession = null;
        let resolvedPage = null;
        let isFinishing = false;
        let respondedOnce = false;
        // Все на await-приёме данных: каждый chunk обрабатывается последовательно через очередь.
        const pendingQueue = [];
        let processing = false;
        let endReceived = false;
        let isLastChunkPending = false;
        function respondError(msg) {
            if (respondedOnce)
                return;
            respondedOnce = true;
            callback(null, {
                video_id: videoSession?.id || '',
                ok: false,
                error: msg,
                duration_ms: Date.now() - t0,
                chunks_processed: chunksProcessed,
            });
        }
        function respondSuccess(videoId) {
            if (respondedOnce)
                return;
            respondedOnce = true;
            callback(null, {
                video_id: videoId,
                ok: true,
                error: '',
                duration_ms: Date.now() - t0,
                chunks_processed: chunksProcessed,
            });
        }
        async function drainQueue() {
            if (processing)
                return;
            processing = true;
            try {
                while (pendingQueue.length > 0) {
                    const chunk = pendingQueue.shift();
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
                }
                else if (endReceived && !respondedOnce && videoSession && !isFinishing) {
                    // Клиент закрыл стрим без is_last_chunk — корректно делаем finish.
                    isFinishing = true;
                    const videoId = await videoSession.finish();
                    respondSuccess(videoId);
                }
            }
            catch (err) {
                respondError(`UploadVideo: ${String(err?.message ?? err)}`);
            }
            finally {
                processing = false;
            }
        }
        call.on('data', async (chunk) => {
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
                    videoSession = new upload_js_1.VideoUploadSession(resolvedPage, {
                        adAccountId,
                        filename,
                        fileSize,
                    });
                    await videoSession.start();
                    // Если в первом chunk есть и bytes (не init-only) — обрабатываем как transfer.
                    const initOnly = Boolean(chunk.is_init);
                    const bytes = Buffer.isBuffer(chunk.chunk_bytes)
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
                const bytes = Buffer.isBuffer(chunk.chunk_bytes)
                    ? chunk.chunk_bytes
                    : Buffer.from(chunk.chunk_bytes || []);
                if (bytes.length > 0) {
                    pendingQueue.push(bytes);
                }
                if (chunk.is_last_chunk) {
                    isLastChunkPending = true;
                }
                await drainQueue();
            }
            catch (err) {
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
        call.on('error', (err) => {
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
//# sourceMappingURL=service.js.map