"use strict";
// Marketing API client — исполняет запросы изнутри Playwright-страницы.
//
// Архитектурное обоснование: EAAB-токены Ads Manager привязаны к browser session
// (machine_id, datr cookie, fingerprint). Standalone HTTP-запросы из Python/curl
// отвергаются anti-fraud Meta. Поэтому всё идёт через page.evaluate(fetch) —
// запрос исходит из того же session-context, что и DOM-операции.
Object.defineProperty(exports, "__esModule", { value: true });
exports.META_API_VERSION = void 0;
exports.executeGraphCall = executeGraphCall;
exports.checkMetaApiHealth = checkMetaApiHealth;
// Версия Marketing API. Не использовать "latest", фиксируем явно.
// При обновлении (раз в год) — синхронизировать с настройками в core/config.py.
exports.META_API_VERSION = 'v22.0';
const DEFAULT_TIMEOUT_MS = 30_000;
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
async function executeGraphCall(page, params) {
    const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const apiVersion = exports.META_API_VERSION;
    // Передаём minimal параметры внутрь page.evaluate (нельзя передавать функции/классы).
    const evalParams = {
        method: params.method.toUpperCase(),
        endpoint: params.endpoint.startsWith('/') ? params.endpoint : `/${params.endpoint}`,
        queryParams: params.queryParams || {},
        bodyJson: params.bodyJson ?? null,
        timeoutMs,
        apiVersion,
    };
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
                const fetchOptions = {
                    method: args.method,
                    credentials: 'include', // КРИТИЧНО: использует cookies сессии (datr, c_user, xs, ...)
                    headers: { Accept: 'application/json' },
                    signal: controller.signal,
                };
                if (args.bodyJson) {
                    fetchOptions.headers['Content-Type'] = 'application/json';
                    fetchOptions.body = args.bodyJson;
                }
                const response = await fetch(url.toString(), fetchOptions);
                clearTimeout(timeoutId);
                const text = await response.text();
                return {
                    status_code: response.status,
                    response_json: text,
                };
            }
            catch (err) {
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
    }
    catch (err) {
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
 * Health-check: жива ли страница Ads Manager и доступен ли токен.
 * Не делает реальных запросов к Meta — только проверяет состояние страницы.
 */
async function checkMetaApiHealth(page) {
    try {
        if (page.isClosed()) {
            return {
                healthy: false,
                currentUrl: '',
                tokenPresent: false,
                tokenLength: 0,
                detail: 'page_closed',
            };
        }
        const currentUrl = page.url();
        const isAdsManagerUrl = currentUrl.includes('adsmanager.facebook.com') ||
            currentUrl.includes('business.facebook.com') ||
            currentUrl.includes('facebook.com/adsmanager');
        if (!isAdsManagerUrl) {
            return {
                healthy: false,
                currentUrl,
                tokenPresent: false,
                tokenLength: 0,
                detail: 'wrong_url',
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
            };
        }
        return {
            healthy: true,
            currentUrl,
            tokenPresent: true,
            tokenLength: tokenInfo.length,
            detail: 'ok',
        };
    }
    catch (err) {
        return {
            healthy: false,
            currentUrl: '',
            tokenPresent: false,
            tokenLength: 0,
            detail: `error: ${String(err?.message ?? err)}`,
        };
    }
}
/**
 * Распарсить error блок из JSON-ответа Meta.
 * Возвращает undefined если ошибки нет (success response).
 */
function extractGraphError(responseJson) {
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
    }
    catch {
        return undefined;
    }
}
//# sourceMappingURL=client.js.map