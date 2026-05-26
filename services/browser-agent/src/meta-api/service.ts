// gRPC-обработчики MetaApiService.
// Мост между gRPC-запросом и executeGraphCall/checkMetaApiHealth в client.ts.
import * as grpc from '@grpc/grpc-js';
import type { Page } from 'playwright';
import { SessionManager, findPreferredPrimaryPage } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import { executeGraphCall, checkMetaApiHealth, type GraphApiCallParams } from './client.js';

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
      const page = getPage(session);

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

      const result = await executeGraphCall(page, params);

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

  return {
    executeGraphCall: executeGraphCallHandler,
    checkMetaApiHealth: checkMetaApiHealthHandler,
  };
}
