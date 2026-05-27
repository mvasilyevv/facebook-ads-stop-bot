"use strict";
// gRPC-обработчики AdLibraryService.
// Мост между gRPC-запросом и searchAds/checkAdLibraryHealth в client.ts.
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
exports.createAdLibraryServiceHandlers = createAdLibraryServiceHandlers;
const grpc = __importStar(require("@grpc/grpc-js"));
const client_js_1 = require("./client.js");
function grpcCodeForError(err) {
    const message = String(err?.message || '').toLowerCase();
    return message.includes('not found') || message.includes('не найден')
        ? grpc.status.NOT_FOUND
        : grpc.status.INTERNAL;
}
function getContext(session) {
    const browser = session.browser;
    if (!browser || !browser.isConnected()) {
        throw new Error('Browser сессии недоступен');
    }
    const contexts = browser.contexts();
    if (!contexts || contexts.length === 0) {
        throw new Error('У browser нет ни одного context');
    }
    // Single-context anti-detect setup: всегда берём первый и единственный.
    return contexts[0];
}
function createAdLibraryServiceHandlers(sessionManager) {
    function resolveSession(sessionId) {
        const normalizedSessionId = String(sessionId || '').trim();
        return normalizedSessionId
            ? sessionManager.getSession(normalizedSessionId)
            : sessionManager.getPreferredSession();
    }
    async function searchAdsHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            const context = getContext(session);
            const params = {
                country: req.country || 'US',
                query: req.query || '',
                activeStatus: (req.active_status || 'active').toLowerCase(),
                adType: (req.ad_type || 'all'),
                searchType: (req.search_type || 'keyword_unordered').toLowerCase(),
                maxPages: req.max_pages && req.max_pages > 0 ? req.max_pages : undefined,
                pageSize: req.page_size && req.page_size > 0 ? req.page_size : undefined,
                timeoutMs: req.timeout_ms && req.timeout_ms > 0 ? req.timeout_ms : undefined,
            };
            const result = await (0, client_js_1.searchAds)(context, params);
            callback(null, {
                ad_count: result.adCount,
                ads_json: result.adsJson,
                duration_ms: result.durationMs,
                pages_fetched: result.pagesFetched,
                error: result.error
                    ? {
                        code: result.error.code,
                        type: result.error.type,
                        message: result.error.message,
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
    async function searchAdsBatchHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            const context = getContext(session);
            const queries = Array.isArray(req.queries) ? req.queries.map(String) : [];
            const params = {
                country: req.country || 'US',
                queries,
                activeStatus: (req.active_status || 'active').toLowerCase(),
                adType: (req.ad_type || 'all'),
                searchType: (req.search_type || 'keyword_unordered').toLowerCase(),
                maxPages: req.max_pages && req.max_pages > 0 ? req.max_pages : undefined,
                pageSize: req.page_size && req.page_size > 0 ? req.page_size : undefined,
                perQueryTimeoutMs: req.per_query_timeout_ms && req.per_query_timeout_ms > 0
                    ? req.per_query_timeout_ms
                    : undefined,
            };
            const result = await (0, client_js_1.searchAdsBatch)(context, params);
            callback(null, {
                results: result.results.map((r) => ({
                    query: r.query,
                    ad_count: r.adCount,
                    ads_json: r.adsJson,
                    duration_ms: r.durationMs,
                    pages_fetched: r.pagesFetched,
                    error: r.error
                        ? { code: r.error.code, type: r.error.type, message: r.error.message }
                        : undefined,
                })),
                total_duration_ms: result.totalDurationMs,
            });
        }
        catch (err) {
            callback({
                code: grpcCodeForError(err),
                message: String(err?.message ?? err),
            });
        }
    }
    async function checkAdLibraryHealthHandler(call, callback) {
        try {
            const req = call.request;
            const session = resolveSession(req.session_id);
            let context = null;
            try {
                context = getContext(session);
            }
            catch {
                context = null;
            }
            const result = await (0, client_js_1.checkAdLibraryHealth)(context);
            callback(null, {
                healthy: result.healthy,
                detail: result.detail,
            });
        }
        catch (err) {
            // Если сессия не найдена — возвращаем healthy=false как штатный ответ
            // (а не gRPC ошибку), потому что вызывающий health_watchdog хочет видеть состояние.
            callback(null, {
                healthy: false,
                detail: `error: ${String(err?.message ?? err)}`,
            });
        }
    }
    return {
        searchAds: searchAdsHandler,
        searchAdsBatch: searchAdsBatchHandler,
        checkAdLibraryHealth: checkAdLibraryHealthHandler,
    };
}
//# sourceMappingURL=service.js.map