// Marketing API client — исполняет запросы изнутри Playwright-страницы.
//
// Архитектурное обоснование: EAAB-токены Ads Manager привязаны к browser session
// (machine_id, datr cookie, fingerprint). Standalone HTTP-запросы из Python/curl
// отвергаются anti-fraud Meta. Поэтому всё идёт через page.evaluate(fetch) —
// запрос исходит из того же session-context, что и DOM-операции.

import type { Page } from 'playwright';
import { randomUUID } from 'crypto';
import {
  bindAbortSignalToPage,
  clearInPageFetchOperation,
  raceWithAbort,
} from '../in-page-abort.js';
import { assertCanonicalGraphMethodSemantics } from './operation-capability.js';

// Версия Marketing API. Не использовать "latest", фиксируем явно.
// При обновлении (раз в год) — синхронизировать с настройками в core/config.py.
export const META_API_VERSION = 'v22.0';

const DEFAULT_TIMEOUT_MS = 30_000;

export interface GraphApiCallParams {
  method: 'GET' | 'POST' | 'DELETE';
  endpoint: string; // "/me", "/act_X/insights", без host и без версии
  queryParams: Record<string, string>;
  bodyJson?: string; // JSON-строка для POST/PUT
  timeoutMs?: number;
}

export interface GraphApiCallResult {
  statusCode: number;
  responseJson: string; // raw JSON ответ Meta (как строка для сохранения структуры)
  durationMs: number;
  error?: {
    code: number;
    subcode: number;
    type: string;
    message: string;
    fbtraceId: string;
  };
}

export interface GraphApiCallOptions {
  signal?: AbortSignal;
  operationId?: string;
}

export interface MetaApiHealthResult {
  healthy: boolean;
  currentUrl: string;
  tokenPresent: boolean;
  tokenLength: number;
  detail: string;
  // Поля реального сетевого probe (full_probe). При token-only probePerformed=false.
  probePerformed: boolean;
  probeOk: boolean;
  probeStatusCode: number;
  probeDurationMs: number;
  probeDetail: string;
}

export interface CheckHealthOptions {
  // true → выполнить реальный GET /me к graph.facebook.com (дороже, для watchdog).
  fullProbe?: boolean;
  // TTL кеша probe-результата на эту страницу (мс). Защита от частых запросов к Meta.
  cacheTtlMs?: number;
  signal?: AbortSignal;
}

// Таймаут реального probe-fetch (короче дефолтного 30с — health должен быть быстрым).
const PROBE_TIMEOUT_MS = 8_000;
// Дефолтный TTL кеша probe: даже при частых вызовах Meta видит максимум 1 запрос / TTL.
const DEFAULT_PROBE_CACHE_TTL_MS = 60_000;

interface ProbeVerdict {
  probePerformed: boolean;
  probeOk: boolean;
  probeStatusCode: number;
  probeDurationMs: number;
  probeDetail: string;
  // true → канал реально мёртв для мутаций (network-down / протухший токен).
  // Meta-side ошибки (rate-limit) сюда НЕ попадают — канал жив.
  channelDown: boolean;
}

// Кеш probe-вердикта на страницу. WeakMap — запись уходит с GC страницы.
const _probeCache = new WeakMap<Page, ProbeVerdict & { expiresAt: number }>();

const _PROBE_NOT_PERFORMED = {
  probePerformed: false,
  probeOk: false,
  probeStatusCode: 0,
  probeDurationMs: 0,
  probeDetail: 'not_performed',
} as const;

/**
 * Исполняет запрос к Marketing API изнутри активной Playwright-страницы.
 *
 * Page должен быть на странице Ads Manager (adsmanager.facebook.com) для того,
 * чтобы access_token и cookies были в session-context. Иначе токен не найдётся
 * и запрос провалится с ошибкой TOKEN_NOT_FOUND.
 *
 * Возвращает структурированный результат. Никогда не выбрасывает исключение
 * на уровне network/timeout — все ошибки упаковываются в GraphApiCallResult.
 * Это даёт клиенту возможность анализировать error.code (например, code=190
 * означает инвалидацию токена → нужна перезагрузка Vision-сессии).
 */
export async function executeGraphCall(
  page: Page,
  params: GraphApiCallParams,
  options: GraphApiCallOptions = {},
): Promise<GraphApiCallResult> {
  assertCanonicalGraphMethodSemantics(
    params.method,
    params.endpoint,
    params.queryParams || {},
    params.bodyJson ?? '',
  );
  const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const apiVersion = META_API_VERSION;
  const operationId = options.operationId ?? `meta:${randomUUID()}`;
  const t0 = Date.now();
  const abortBinding = bindAbortSignalToPage(page, operationId, options.signal);

  // Передаём minimal параметры внутрь page.evaluate (нельзя передавать функции/классы).
  const evalParams = {
    method: params.method.toUpperCase(),
    endpoint: params.endpoint.startsWith('/') ? params.endpoint : `/${params.endpoint}`,
    queryParams: params.queryParams || {},
    bodyJson: params.bodyJson ?? null,
    timeoutMs,
    apiVersion,
    operationId,
  };

  try {
    // On a freshly-created control page the token may appear asynchronously.
    // Cancellation must not be swallowed by the token warm-up timeout.
    try {
      await raceWithAbort(
        page.waitForFunction(
          () => /EAA[A-Za-z0-9_-]{100,}/.test(document.documentElement.innerHTML),
          { timeout: 10_000 },
        ),
        options.signal,
      );
    } catch {
      if (options.signal?.aborted) {
        return cancelledGraphResult(t0, 'gRPC request cancelled before Graph fetch completed');
      }
      // Token-not-found is returned below as -1 and remains safely retryable.
    }

    const result = await raceWithAbort(page.evaluate(async (args) => {
      // Извлечь access_token из page source.
      // Используем расширенный regex (EAA*, не только EAAbs*) — на случай если Meta
      // поменяет префикс. Минимум 100 символов — отсекает шум.
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      if (!match) {
        return {
          status_code: 0,
          response_json: JSON.stringify({
            error: { code: -1, type: 'TokenNotFound', message: 'EAA-токен не найден в page source' },
          }),
        };
      }
      const token = match[0];

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

      // Построить URL: https://graph.facebook.com/v22.0/<endpoint>?<params>&access_token=<token>
      const url = new URL(`https://graph.facebook.com/${args.apiVersion}${args.endpoint}`);
      for (const [key, value] of Object.entries(args.queryParams)) {
        url.searchParams.set(key, value);
      }
      url.searchParams.set('access_token', token);

      // AbortController для таймаута.
      const controller = new AbortController();
      const controllers = state.controllers.get(args.operationId) ?? new Set<AbortController>();
      controllers.add(controller);
      state.controllers.set(args.operationId, controllers);
      if (state.cancelled.has(args.operationId)) controller.abort('grpc_cancelled');
      const timeoutId = setTimeout(() => controller.abort('deadline_exceeded'), args.timeoutMs);

      try {
        const fetchOptions: RequestInit = {
          method: args.method,
          credentials: 'include', // КРИТИЧНО: использует cookies сессии (datr, c_user, xs, ...)
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        };

        if (args.bodyJson) {
          (fetchOptions.headers as Record<string, string>)['Content-Type'] = 'application/json';
          fetchOptions.body = args.bodyJson;
        }

        const response = await fetch(url.toString(), fetchOptions);
        const text = await response.text();
        return {
          status_code: response.status,
          response_json: text,
        };
      } catch (err: any) {
        const cancelled = state.cancelled.has(args.operationId);
        const errMessage = cancelled
          ? 'gRPC request cancelled; external result is unknown'
          : err?.name === 'AbortError'
            ? `Timeout после ${args.timeoutMs}мс`
          : String(err?.message ?? err);
        return {
          status_code: 0,
          response_json: JSON.stringify({
            error: { code: -2, type: 'NetworkError', message: errMessage },
          }),
        };
      } finally {
        clearTimeout(timeoutId);
        controllers.delete(controller);
        if (controllers.size === 0) state.controllers.delete(args.operationId);
      }
    }, evalParams), options.signal);

    const durationMs = Date.now() - t0;

    // Распарсить error блок из ответа Meta, если есть.
    const parsedError = extractGraphError(result.response_json);

    return {
      statusCode: result.status_code,
      responseJson: result.response_json,
      durationMs,
      error: parsedError,
    };
  } catch (err: any) {
    if (options.signal?.aborted) {
      return cancelledGraphResult(t0, 'gRPC request cancelled during Graph fetch');
    }
    // Это ошибка на уровне page.evaluate (например, page закрылся, browser упал).
    return {
      statusCode: 0,
      responseJson: JSON.stringify({
        error: {
          code: -3,
          type: 'PageEvaluateError',
          message: String(err?.message ?? err),
        },
      }),
      durationMs: Date.now() - t0,
      error: {
        code: -3,
        subcode: 0,
        type: 'PageEvaluateError',
        message: String(err?.message ?? err),
        fbtraceId: '',
      },
    };
  } finally {
    abortBinding.dispose();
    // Never let best-effort browser cleanup keep the role lock forever after
    // the local AbortSignal has already made the external outcome UNKNOWN.
    void clearInPageFetchOperation(page, operationId);
  }
}

function cancelledGraphResult(startedAt: number, message: string): GraphApiCallResult {
  const responseJson = JSON.stringify({
    error: { code: -2, type: 'NetworkError', message },
  });
  return {
    statusCode: 0,
    responseJson,
    durationMs: Date.now() - startedAt,
    error: {
      code: -2,
      subcode: 0,
      type: 'NetworkError',
      message,
      fbtraceId: '',
    },
  };
}

/**
 * Health-check канала Marketing API.
 *
 * Token-only режим (по умолчанию) — дёшево: проверяет, что страница Ads Manager жива
 * и EAA-токен есть в DOM. НЕ делает сетевых запросов.
 *
 * full_probe режим (opts.fullProbe) — выполняет РЕАЛЬНЫЙ лёгкий GET /me?fields=id тем же
 * page.evaluate(fetch), что и auto-stop pause_ad. Ловит инцидент 2026-06-19: token-only
 * возвращал healthy=true при мёртвом сетевом канале (Failed to fetch, code=-2). Результат
 * probe кешируется на страницу (cacheTtlMs) — Meta не дёргается чаще, чем раз в TTL.
 */
export async function checkMetaApiHealth(
  page: Page,
  opts?: CheckHealthOptions,
): Promise<MetaApiHealthResult> {
  try {
    if (page.isClosed()) {
      return {
        healthy: false,
        currentUrl: '',
        tokenPresent: false,
        tokenLength: 0,
        detail: 'page_closed',
        ..._PROBE_NOT_PERFORMED,
      };
    }

    const currentUrl = page.url();
    const isAdsManagerUrl =
      currentUrl.includes('adsmanager.facebook.com') ||
      currentUrl.includes('business.facebook.com') ||
      currentUrl.includes('facebook.com/adsmanager');

    if (!isAdsManagerUrl) {
      return {
        healthy: false,
        currentUrl,
        tokenPresent: false,
        tokenLength: 0,
        detail: 'wrong_url',
        ..._PROBE_NOT_PERFORMED,
      };
    }

    const tokenInfo = await raceWithAbort(
      page.evaluate(() => {
        const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
        return {
          present: !!match,
          length: match ? match[0].length : 0,
        };
      }),
      opts?.signal,
    );

    if (!tokenInfo.present) {
      return {
        healthy: false,
        currentUrl,
        tokenPresent: false,
        tokenLength: 0,
        detail: 'token_not_found',
        ..._PROBE_NOT_PERFORMED,
      };
    }

    // Token-only: токен есть, страница верная — для частых проверок этого достаточно.
    if (!opts?.fullProbe) {
      return {
        healthy: true,
        currentUrl,
        tokenPresent: true,
        tokenLength: tokenInfo.length,
        detail: 'ok',
        ..._PROBE_NOT_PERFORMED,
      };
    }

    // Full probe: реальный сетевой запрос (с кешем на страницу).
    const ttl = opts.cacheTtlMs ?? DEFAULT_PROBE_CACHE_TTL_MS;
    const now = Date.now();
    const cached = _probeCache.get(page);
    let verdict: ProbeVerdict;
    if (cached && cached.expiresAt > now) {
      verdict = cached;
    } else {
      verdict = await runNetworkProbe(page, opts.signal);
      if (opts.signal?.aborted) {
        throw new Error('health probe cancelled');
      }
      _probeCache.set(page, { ...verdict, expiresAt: now + ttl });
    }

    return {
      healthy: !verdict.channelDown,
      currentUrl,
      tokenPresent: true,
      tokenLength: tokenInfo.length,
      detail: verdict.channelDown ? verdict.probeDetail : 'ok',
      probePerformed: verdict.probePerformed,
      probeOk: verdict.probeOk,
      probeStatusCode: verdict.probeStatusCode,
      probeDurationMs: verdict.probeDurationMs,
      probeDetail: verdict.probeDetail,
    };
  } catch (err: any) {
    return {
      healthy: false,
      currentUrl: '',
      tokenPresent: false,
      tokenLength: 0,
      detail: `error: ${String(err?.message ?? err)}`,
      ..._PROBE_NOT_PERFORMED,
    };
  }
}

/**
 * Реальный лёгкий probe канала: GET /me?fields=id через executeGraphCall.
 * Классифицирует результат как «канал мёртв» (network/token) или «канал жив»
 * (200 / Meta-side ошибка вроде rate-limit). Никогда не бросает.
 */
async function runNetworkProbe(
  page: Page,
  signal?: AbortSignal,
): Promise<ProbeVerdict> {
  const result = await executeGraphCall(page, {
    method: 'GET',
    endpoint: '/me',
    queryParams: { fields: 'id' },
    timeoutMs: PROBE_TIMEOUT_MS,
  }, {
    signal,
    operationId: `health:${randomUUID()}`,
  });

  const base = {
    probePerformed: true,
    probeStatusCode: result.statusCode,
    probeDurationMs: result.durationMs,
  };

  if (!result.error && result.statusCode === 200) {
    return { ...base, probeOk: true, probeDetail: 'ok', channelDown: false };
  }

  if (result.error) {
    const code = result.error.code;
    // -1 token-not-found, -2 Failed to fetch, -3 page-evaluate — канал/сеть мертвы.
    if (code === -1 || code === -2 || code === -3) {
      return { ...base, probeOk: false, probeDetail: 'probe_network_down', channelDown: true };
    }
    // 190 OAuth — токен протух ИЛИ профиль разлогинен. Разлогин/чекпоинт (нужен ре-логин
    // Vision-профиля) отличаем от обычного протухания токена по subcode/тексту — health_watchdog
    // шлёт разный алерт (login_required = «зайди в Vision и залогинься», а не «обнови токен»).
    if (code === 190) {
      const detail = isLoginRequiredError(result.error) ? 'login_required' : 'probe_token_invalid';
      return { ...base, probeOk: false, probeDetail: detail, channelDown: true };
    }
    // Прочие Meta-ошибки (rate-limit 17/4/32 и т.п.) — fetch ДОШЁЛ до Meta → канал жив.
    return { ...base, probeOk: false, probeDetail: `meta_error:${code}`, channelDown: false };
  }

  // Не-200 без error-блока: Meta всё же ответила → канал жив, но probe не «ok».
  return {
    ...base,
    probeOk: false,
    probeDetail: `http_${result.statusCode}`,
    channelDown: false,
  };
}

// OAuth-subcodes, означающие РАЗЛОГИН/чекпоинт (нужен ре-логин профиля), а не просто
// протухший короткоживущий токен: 458/459 checkpoint, 460 password changed, 463 session
// expired, 464 unconfirmed, 467 invalid/logged-out. Зеркалит am-fetch._LOGIN_REQUIRED_SUBCODES.
const _LOGIN_REQUIRED_SUBCODES: ReadonlySet<number> = new Set([458, 459, 460, 463, 464, 467]);

/**
 * True, если Graph error (code 190 / OAuthException) — это именно разлогин/чекпоинт
 * профиля (нужен ре-логин Vision), а не рядовое протухание access_token. Экспорт для
 * unit-теста. Признаки: login-subcode ИЛИ явное упоминание re-login/checkpoint в тексте.
 */
export function isLoginRequiredError(
  err: { code?: number; subcode?: number; type?: string; message?: string } | undefined | null,
): boolean {
  if (!err) return false;
  const code = Number(err.code ?? 0);
  if (code !== 190 && err.type !== 'OAuthException') return false;
  const subcode = Number(err.subcode ?? 0);
  if (_LOGIN_REQUIRED_SUBCODES.has(subcode)) return true;
  const msg = String(err.message ?? '').toLowerCase();
  return /session.*expired|log ?in|checkpoint|re-?authenticate|not logged in|logged out/.test(msg);
}

/**
 * Распарсить error блок из JSON-ответа Meta.
 * Возвращает undefined если ошибки нет (success response).
 */
function extractGraphError(responseJson: string): GraphApiCallResult['error'] | undefined {
  try {
    const parsed = JSON.parse(responseJson);
    if (parsed?.error && typeof parsed.error === 'object') {
      return {
        code: Number(parsed.error.code ?? 0),
        subcode: Number(parsed.error.error_subcode ?? parsed.error.subcode ?? 0),
        type: String(parsed.error.type ?? ''),
        message: String(parsed.error.message ?? ''),
        fbtraceId: String(parsed.error.fbtrace_id ?? ''),
      };
    }
    return undefined;
  } catch {
    return undefined;
  }
}
