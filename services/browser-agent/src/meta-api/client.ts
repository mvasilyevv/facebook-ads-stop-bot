// Marketing API client — исполняет запросы изнутри Playwright-страницы.
//
// Архитектурное обоснование: EAAB-токены Ads Manager привязаны к browser session
// (machine_id, datr cookie, fingerprint). Standalone HTTP-запросы из Python/curl
// отвергаются anti-fraud Meta. Поэтому всё идёт через page.evaluate(fetch) —
// запрос исходит из того же session-context, что и DOM-операции.

import type { Page } from 'playwright';

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
): Promise<GraphApiCallResult> {
  const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const apiVersion = META_API_VERSION;

  // Передаём minimal параметры внутрь page.evaluate (нельзя передавать функции/классы).
  const evalParams = {
    method: params.method.toUpperCase(),
    endpoint: params.endpoint.startsWith('/') ? params.endpoint : `/${params.endpoint}`,
    queryParams: params.queryParams || {},
    bodyJson: params.bodyJson ?? null,
    timeoutMs,
    apiVersion,
  };

  // H4: на свежеоткрытой вкладке кабинета (ensureAdsManagerPage) EAA-токен ещё не
  // в DOM — ждём его появления ПЕРЕД fetch, иначе page.evaluate вернёт code=-1
  // TokenNotFound и мутация (pause/activate) фейлится с первой попытки. Если не
  // дождались за 10с — продолжаем: евал вернёт -1 → SessionUnavailableError → requeue.
  try {
    await page.waitForFunction(
      () => /EAA[A-Za-z0-9_-]{100,}/.test(document.documentElement.innerHTML),
      { timeout: 10_000 },
    );
  } catch {
    // токен не появился — не блокируем, ниже евал отдаст -1 (Temporary → retry)
  }

  const t0 = Date.now();
  try {
    const result = await page.evaluate(async (args) => {
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

      // Построить URL: https://graph.facebook.com/v22.0/<endpoint>?<params>&access_token=<token>
      const url = new URL(`https://graph.facebook.com/${args.apiVersion}${args.endpoint}`);
      for (const [key, value] of Object.entries(args.queryParams)) {
        url.searchParams.set(key, value);
      }
      url.searchParams.set('access_token', token);

      // AbortController для таймаута.
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);

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
        clearTimeout(timeoutId);

        const text = await response.text();
        return {
          status_code: response.status,
          response_json: text,
        };
      } catch (err: any) {
        clearTimeout(timeoutId);
        const errMessage = err?.name === 'AbortError'
          ? `Timeout после ${args.timeoutMs}мс`
          : String(err?.message ?? err);
        return {
          status_code: 0,
          response_json: JSON.stringify({
            error: { code: -2, type: 'NetworkError', message: errMessage },
          }),
        };
      }
    }, evalParams);

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
  }
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

    const tokenInfo = await page.evaluate(() => {
      const match = document.documentElement.innerHTML.match(/EAA[A-Za-z0-9_-]{100,}/);
      return {
        present: !!match,
        length: match ? match[0].length : 0,
      };
    });

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
      verdict = await runNetworkProbe(page);
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
async function runNetworkProbe(page: Page): Promise<ProbeVerdict> {
  const result = await executeGraphCall(page, {
    method: 'GET',
    endpoint: '/me',
    queryParams: { fields: 'id' },
    timeoutMs: PROBE_TIMEOUT_MS,
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
    // 190 OAuth — токен протух, мутации невозможны → канал мёртв для money-операций.
    if (code === 190) {
      return { ...base, probeOk: false, probeDetail: 'probe_token_invalid', channelDown: true };
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
