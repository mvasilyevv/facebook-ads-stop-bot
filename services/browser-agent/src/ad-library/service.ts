// gRPC-обработчики AdLibraryService.
// Мост между gRPC-запросом и searchAds/checkAdLibraryHealth в client.ts.

import * as grpc from '@grpc/grpc-js';
import type { BrowserContext } from 'playwright';
import { SessionManager } from '../session-manager.js';
import type { BrowserSession } from '../types.js';
import {
  checkAdLibraryHealth,
  searchAds,
  searchAdsBatch,
  type SearchAdsBatchParams,
  type SearchAdsParams,
} from './client.js';

function grpcCodeForError(err: any): number {
  const message = String(err?.message || '').toLowerCase();
  return message.includes('not found') || message.includes('не найден')
    ? grpc.status.NOT_FOUND
    : grpc.status.INTERNAL;
}

function getContext(session: BrowserSession): BrowserContext {
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

export function createAdLibraryServiceHandlers(sessionManager: SessionManager) {
  function resolveSession(sessionId: string): BrowserSession {
    const normalizedSessionId = String(sessionId || '').trim();
    return normalizedSessionId
      ? sessionManager.getSession(normalizedSessionId)
      : sessionManager.getPreferredSession();
  }

  async function searchAdsHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      const context = getContext(session);

      const params: SearchAdsParams = {
        country: req.country || 'US',
        query: req.query || '',
        activeStatus: ((req.active_status || 'active') as string).toLowerCase() as
          | 'active'
          | 'inactive'
          | 'all',
        adType: (req.ad_type || 'all') as 'all' | 'political_and_issue_ads',
        searchType: ((req.search_type || 'keyword_unordered') as string).toLowerCase() as
          | 'keyword_unordered'
          | 'keyword_exact_phrase'
          | 'page',
        maxPages: req.max_pages && req.max_pages > 0 ? req.max_pages : undefined,
        pageSize: req.page_size && req.page_size > 0 ? req.page_size : undefined,
        timeoutMs: req.timeout_ms && req.timeout_ms > 0 ? req.timeout_ms : undefined,
      };

      const result = await searchAds(context, params);

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
    } catch (err: any) {
      callback({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    }
  }

  async function searchAdsBatchHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      const context = getContext(session);

      const queries: string[] = Array.isArray(req.queries) ? req.queries.map(String) : [];

      const params: SearchAdsBatchParams = {
        country: req.country || 'US',
        queries,
        activeStatus: ((req.active_status || 'active') as string).toLowerCase() as
          | 'active'
          | 'inactive'
          | 'all',
        adType: (req.ad_type || 'all') as 'all' | 'political_and_issue_ads',
        searchType: ((req.search_type || 'keyword_unordered') as string).toLowerCase() as
          | 'keyword_unordered'
          | 'keyword_exact_phrase'
          | 'page',
        maxPages: req.max_pages && req.max_pages > 0 ? req.max_pages : undefined,
        pageSize: req.page_size && req.page_size > 0 ? req.page_size : undefined,
        perQueryTimeoutMs:
          req.per_query_timeout_ms && req.per_query_timeout_ms > 0
            ? req.per_query_timeout_ms
            : undefined,
      };

      const result = await searchAdsBatch(context, params);

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
    } catch (err: any) {
      callback({
        code: grpcCodeForError(err),
        message: String(err?.message ?? err),
      });
    }
  }

  async function checkAdLibraryHealthHandler(call: any, callback: any): Promise<void> {
    try {
      const req = call.request;
      const session = resolveSession(req.session_id);
      let context: BrowserContext | null = null;
      try {
        context = getContext(session);
      } catch {
        context = null;
      }

      const result = await checkAdLibraryHealth(context);

      callback(null, {
        healthy: result.healthy,
        detail: result.detail,
      });
    } catch (err: any) {
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
