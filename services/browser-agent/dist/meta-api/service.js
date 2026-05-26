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
            const result = await (0, client_js_1.executeGraphCall)(page, params);
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
    return {
        executeGraphCall: executeGraphCallHandler,
        checkMetaApiHealth: checkMetaApiHealthHandler,
    };
}
//# sourceMappingURL=service.js.map