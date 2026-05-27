"use strict";
// Ad Library client — прямой GraphQL fetch из активной Vision-сессии.
//
// Архитектура: открываем facebook.com/ads/library один раз для извлечения
// токенов (lsd, fb_dtsg, __dyn, __csr и др.) из HTML, потом через page.evaluate
// делаем POST на https://www.facebook.com/api/graphql/ — тот же endpoint,
// который дёргает Meta UI и meta-ads-collector.
//
// Преимущества vs UI-driven (input.fill + scroll):
// - Точный контроль variables (searchType, country, pagination cursor)
// - Не зависим от Meta UI ranking (первая страница UI может содержать chicken-cages
//   для "Chicken Road 2", но прямой GraphQL дёргает search_results_connection
//   с нашими параметрами)
// - Pagination через cursor — собираем 100-200 ads, не первые 30
//
// Структура запроса заимствована из meta-ads-collector v1.3.0
// (.venv/lib/python3.14/site-packages/meta_ads_collector/client.py:1011-1019,
// constants.py:50-51).
Object.defineProperty(exports, "__esModule", { value: true });
exports.searchAds = searchAds;
exports.searchAdsBatch = searchAdsBatch;
exports.checkAdLibraryHealth = checkAdLibraryHealth;
const DEFAULT_TIMEOUT_MS = 60_000;
const AD_LIBRARY_URL = 'https://www.facebook.com/ads/library/';
// doc_id для AdLibrarySearchPaginationQuery актуальный на 2026-05.
// MAC v1.3.0 использовал 25464068859919530 — устарел.
// Захвачено из реального запроса Meta UI через page.on('request').
const DOC_ID_SEARCH = '27201872659451053';
const FRIENDLY_NAME = 'AdLibrarySearchPaginationQuery';
const ASBD_ID = '359341';
const ALLOWED_SEARCH_TYPES = new Set([
    'KEYWORD_UNORDERED',
    'KEYWORD_EXACT_PHRASE',
    'PAGE',
]);
/**
 * Один поиск ads по keyword + country. Открывает Ad Library страницу для
 * извлечения токенов, потом делает 1+ GraphQL запросов с pagination.
 */
async function searchAds(context, params) {
    // Debug-режим: query="__capture__" → открываем Ad Library и перехватываем
    // настоящий GraphQL запрос UI, возвращаем его body для анализа.
    if (params.query === '__capture__') {
        return captureUiGraphQLRequest(context, params);
    }
    const t0 = Date.now();
    let page = null;
    try {
        page = await context.newPage();
        const bootstrapResult = await bootstrapPage(page, params.country, params.timeoutMs);
        if (bootstrapResult.error) {
            return {
                adCount: 0,
                adsJson: '[]',
                durationMs: Date.now() - t0,
                pagesFetched: 0,
                error: bootstrapResult.error,
            };
        }
        const collected = await fetchAdsWithPagination(page, params);
        return {
            adCount: collected.ads.length,
            adsJson: JSON.stringify(collected.ads),
            durationMs: Date.now() - t0,
            pagesFetched: collected.pagesFetched,
            error: collected.error,
        };
    }
    catch (err) {
        return {
            adCount: 0,
            adsJson: '[]',
            durationMs: Date.now() - t0,
            pagesFetched: 0,
            error: {
                code: -3,
                type: 'PageError',
                message: String(err?.message ?? err),
            },
        };
    }
    finally {
        if (page) {
            page.close().catch(() => { });
        }
    }
}
/**
 * Batch: открывает Ad Library один раз для country, прогоняет все queries
 * через прямой GraphQL fetch (переиспользуя токены из bootstrap).
 *
 * Каждый query получает свой pagination loop, между queries токены не
 * перевычитываются (живут несколько минут).
 */
async function searchAdsBatch(context, params) {
    const t0 = Date.now();
    if (!params.queries || params.queries.length === 0) {
        return { results: [], totalDurationMs: Date.now() - t0 };
    }
    const results = [];
    let page = null;
    try {
        page = await context.newPage();
        const bootstrapResult = await bootstrapPage(page, params.country, params.perQueryTimeoutMs);
        if (bootstrapResult.error) {
            for (const q of params.queries) {
                results.push({
                    query: q,
                    adCount: 0,
                    adsJson: '[]',
                    durationMs: 0,
                    pagesFetched: 0,
                    error: bootstrapResult.error,
                });
            }
            return { results, totalDurationMs: Date.now() - t0 };
        }
        for (const query of params.queries) {
            const queryStart = Date.now();
            const collected = await fetchAdsWithPagination(page, {
                country: params.country,
                query,
                activeStatus: params.activeStatus,
                adType: params.adType,
                searchType: params.searchType,
                maxPages: params.maxPages,
                pageSize: params.pageSize,
                timeoutMs: params.perQueryTimeoutMs,
            });
            results.push({
                query,
                adCount: collected.ads.length,
                adsJson: JSON.stringify(collected.ads),
                durationMs: Date.now() - queryStart,
                pagesFetched: collected.pagesFetched,
                error: collected.error,
            });
        }
    }
    catch (err) {
        const fatalQueries = params.queries.slice(results.length);
        for (const q of fatalQueries) {
            results.push({
                query: q,
                adCount: 0,
                adsJson: '[]',
                durationMs: 0,
                pagesFetched: 0,
                error: {
                    code: -3,
                    type: 'PageError',
                    message: String(err?.message ?? err),
                },
            });
        }
    }
    finally {
        if (page) {
            page.close().catch(() => { });
        }
    }
    return { results, totalDurationMs: Date.now() - t0 };
}
/**
 * Открыть Ad Library страницу для получения cookies + чтения токенов из HTML.
 * Не возвращает ads — только готовит сессию.
 */
async function bootstrapPage(page, country, timeoutMs) {
    const url = new URL(AD_LIBRARY_URL);
    url.searchParams.set('active_status', 'active');
    url.searchParams.set('ad_type', 'all');
    url.searchParams.set('country', (country || 'US').toUpperCase());
    url.searchParams.set('media_type', 'all');
    try {
        await page.goto(url.toString(), {
            waitUntil: 'commit',
            timeout: timeoutMs ?? DEFAULT_TIMEOUT_MS,
        });
        // commit срабатывает ДО того как documentElement создан → ждём DOM.
        await page.waitForLoadState('domcontentloaded', { timeout: 20_000 }).catch(() => { });
        // Ждём LSD как минимум — это критично. Остальные токены опциональны
        // (MAC использует fallback значения если они отсутствуют).
        const waitStart = Date.now();
        let tokenStatus = { lsd: false, dyn: false, csr: false, spin: false, html_len: 0 };
        while (Date.now() - waitStart < 30_000) {
            tokenStatus = await page.evaluate(() => {
                if (!document || !document.documentElement) {
                    return { lsd: false, dyn: false, csr: false, spin: false, html_len: 0 };
                }
                const html = document.documentElement.innerHTML;
                return {
                    lsd: /"LSD",\[\],\{"token":"[^"]+/.test(html),
                    dyn: html.includes('__dyn'),
                    csr: html.includes('__csr'),
                    spin: html.includes('__spin_r'),
                    html_len: html.length,
                };
            });
            if (tokenStatus.lsd) {
                // Подождать ещё 3с для надёжности (остальные bundle-chunks)
                await new Promise((r) => setTimeout(r, 3000));
                return {};
            }
            await new Promise((r) => setTimeout(r, 500));
        }
        return {
            error: {
                code: -2,
                type: 'TokensNotReady',
                message: `LSD не появился за 30s. Status: ${JSON.stringify(tokenStatus)}`,
            },
        };
    }
    catch (err) {
        return {
            error: {
                code: -1,
                type: 'NavigationError',
                message: String(err?.message ?? err),
            },
        };
    }
}
/**
 * Цикл GraphQL запросов с pagination — пока has_next_page и не превышен maxPages.
 */
async function fetchAdsWithPagination(page, params) {
    const country = (params.country || 'US').toUpperCase();
    // Meta UI 2026: activeStatus/mediaType/searchType — lowercase, adType — UPPERCASE.
    const activeStatus = (params.activeStatus || 'active').toLowerCase();
    const adType = (params.adType || 'all').toUpperCase();
    const searchType = (params.searchType || 'keyword_unordered').toLowerCase();
    const maxPages = params.maxPages ?? 5;
    const pageSize = params.pageSize ?? 30;
    const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const ads = [];
    const seenIds = new Set();
    let cursor = null;
    let pagesFetched = 0;
    for (let pageIdx = 0; pageIdx < maxPages; pageIdx++) {
        const evalParams = {
            country,
            query: params.query,
            activeStatus,
            adType,
            searchType,
            cursor,
            first: pageSize,
            timeoutMs,
            docId: DOC_ID_SEARCH,
            friendlyName: FRIENDLY_NAME,
            asbdId: ASBD_ID,
        };
        let responseText;
        let statusCode;
        try {
            const result = await page.evaluate(executeAdLibraryGraphQL, evalParams);
            responseText = result.responseText;
            statusCode = result.statusCode;
        }
        catch (err) {
            return {
                ads,
                pagesFetched,
                error: {
                    code: -3,
                    type: 'PageEvaluateError',
                    message: String(err?.message ?? err),
                },
            };
        }
        if (statusCode < 200 || statusCode >= 400) {
            return {
                ads,
                pagesFetched,
                error: {
                    code: statusCode,
                    type: 'HttpError',
                    message: `GraphQL HTTP ${statusCode}: ${responseText.slice(0, 200)}`,
                },
            };
        }
        let parsed;
        try {
            parsed = JSON.parse(responseText);
        }
        catch {
            return {
                ads,
                pagesFetched,
                error: {
                    code: -4,
                    type: 'JsonParseError',
                    message: `Invalid JSON: ${responseText.slice(0, 200)}`,
                },
            };
        }
        if (parsed?.errors) {
            return {
                ads,
                pagesFetched,
                error: {
                    code: -5,
                    type: 'GraphQLError',
                    message: JSON.stringify(parsed.errors).slice(0, 300),
                },
            };
        }
        const connection = parsed?.data?.ad_library_main?.search_results_connection ||
            parsed?.data?.ad_library_main?.searchResultsConnection;
        if (!connection) {
            // На первой странице это ошибка. На последующих — конец pagination.
            if (pageIdx === 0) {
                // Debug: показать что Meta вернула
                const sample = JSON.stringify(parsed).slice(0, 600);
                return {
                    ads,
                    pagesFetched,
                    error: {
                        code: -6,
                        type: 'NoSearchResults',
                        message: `Нет search_results_connection. Response sample: ${sample}`,
                    },
                };
            }
            break;
        }
        const edges = Array.isArray(connection.edges) ? connection.edges : [];
        for (const edge of edges) {
            const collated = edge?.node?.collated_results || edge?.node?.collatedResults || [];
            for (const ad of collated) {
                const id = String(ad?.ad_archive_id || ad?.adArchiveID || ad?.id || '');
                if (id && !seenIds.has(id)) {
                    seenIds.add(id);
                    ads.push(ad);
                }
            }
        }
        pagesFetched++;
        const pageInfo = connection.page_info || connection.pageInfo || {};
        const hasNext = pageInfo.has_next_page ?? pageInfo.hasNextPage;
        const endCursor = pageInfo.end_cursor ?? pageInfo.endCursor;
        if (!hasNext || !endCursor) {
            break;
        }
        cursor = endCursor;
    }
    return { ads, pagesFetched };
}
/**
 * Исполняется внутри браузерной страницы (page.evaluate).
 * Извлекает токены из HTML и делает POST /api/graphql/ с form-encoded payload.
 */
async function executeAdLibraryGraphQL(args) {
    const html = document.documentElement.innerHTML;
    // ─── Извлечение токенов (regex как в MAC) ────────────────────────────────
    const matchFirst = (patterns) => {
        for (const pat of patterns) {
            const m = html.match(pat);
            if (m && m[1])
                return m[1];
        }
        return '';
    };
    const lsd = matchFirst([
        /"LSD",\[\],\{"token":"([^"]+)"\}/,
        /name="lsd"\s+value="([^"]+)"/,
    ]);
    if (!lsd) {
        return {
            statusCode: 0,
            responseText: JSON.stringify({ error: 'LSD не найден' }),
        };
    }
    const rev = matchFirst([/"__spin_r":(\d+)/, /"server_revision":(\d+)/]) || '1033837939';
    const spinT = matchFirst([/"__spin_t":(\d+)/]) || String(Math.floor(Date.now() / 1000));
    const spinB = matchFirst([/"__spin_b":"([^"]+)"/]) || 'trunk';
    const hsi = matchFirst([/"__hsi":"(\d+)"/, /"hsi":"(\d+)"/]) || String(Date.now());
    const hs = matchFirst([/"__hs":"([^"]+)"/]) || '';
    const dyn = matchFirst([/"__dyn":"([^"]+)"/]) || '';
    const csr = matchFirst([/"__csr":"([^"]+)"/]) || '';
    // fb_dtsg — CSRF токен. Без него Meta даёт error 1357004.
    const fbDtsg = matchFirst([
        /"DTSGInitialData",\[\],\{"token":"([^"]+)"/,
        /name="fb_dtsg"\s+value="([^"]+)"/,
    ]) || '';
    // Дополнительные dynamic токены (UI их передаёт).
    const hsdp = matchFirst([/"__hsdp":"([^"]+)"/]) || '';
    const hblp = matchFirst([/"__hblp":"([^"]+)"/]) || '';
    const sjsp = matchFirst([/"__sjsp":"([^"]+)"/]) || '';
    // jazoest — НЕ вычисляется из lsd (MAC v1.3.0 алгоритм устарел).
    // Извлекается из HTML напрямую.
    const jazoest = matchFirst([/"jazoest":"(\d+)"/, /name="jazoest"\s+value="(\d+)"/]) || '0';
    // User ID — из c_user cookie. Если "0" — Meta думает что юзер не залогинен
    // и возвращает error 1357004/1357032 (anti-bot).
    const cookies = document.cookie || '';
    const cUserMatch = cookies.match(/c_user=(\d+)/);
    const userId = cUserMatch ? cUserMatch[1] : '0';
    // ─── Variables ───────────────────────────────────────────────────────────
    const genUuid = () => {
        if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
            return crypto.randomUUID();
        }
        // fallback
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    };
    // Структура захвачена из реального запроса Meta UI на 2026-05.
    // Ключевые отличия от MAC v1.3.0:
    //  - activeStatus/mediaType/searchType — lowercase
    //  - excludedIDs/potentialReachInput/regions — null, не []
    //  - collationToken — null на первой странице
    //  - НЕТ полей v, viewAllPageID, source, startDate
    // Структура захвачена из реального cURL Meta UI 2026-05-26.
    // Все обязательные поля (хоть и null) присутствуют.
    const variables = {
        activeStatus: args.activeStatus,
        adType: args.adType,
        bylines: [],
        collationToken: null,
        contentLanguages: [],
        countries: [args.country],
        cursor: args.cursor || null,
        excludedIDs: null,
        first: args.first,
        isTargetedCountry: false,
        location: null,
        mediaType: 'all',
        multiCountryFilterMode: null,
        pageIDs: [],
        potentialReachInput: null,
        publisherPlatforms: [],
        queryString: args.query,
        regions: null,
        searchType: args.searchType,
        sessionID: genUuid(),
        sortData: null,
        source: null,
        startDate: null,
        v: '568602',
        viewAllPageID: '0',
    };
    // ─── Body (form-urlencoded) ──────────────────────────────────────────────
    // Структура и порядок полей захвачены из реального cURL Meta UI 2026-05-26.
    const randShortId = `${Math.random().toString(36).substring(2, 8)}:${Math.random().toString(36).substring(2, 8)}:${Math.random().toString(36).substring(2, 8)}`;
    const body = new URLSearchParams();
    body.set('av', userId); // user ID, не 0!
    body.set('__aaid', '0');
    body.set('__user', userId); // user ID, не 0!
    body.set('__a', '1');
    body.set('__req', '8');
    body.set('__hs', hs);
    body.set('dpr', '1');
    body.set('__ccg', 'EXCELLENT'); // не GOOD
    body.set('__rev', rev);
    body.set('__s', randShortId);
    body.set('__hsi', hsi);
    if (dyn)
        body.set('__dyn', dyn);
    if (csr)
        body.set('__csr', csr);
    if (hsdp)
        body.set('__hsdp', hsdp);
    if (hblp)
        body.set('__hblp', hblp);
    if (sjsp)
        body.set('__sjsp', sjsp);
    body.set('__comet_req', '94');
    body.set('fb_dtsg', fbDtsg); // CSRF — обязателен
    body.set('jazoest', jazoest);
    body.set('lsd', lsd);
    body.set('__spin_r', rev);
    body.set('__spin_b', spinB);
    body.set('__spin_t', spinT);
    body.set('__jssesw', '1');
    body.set('fb_api_caller_class', 'RelayModern');
    body.set('fb_api_req_friendly_name', args.friendlyName);
    body.set('server_timestamps', 'true');
    body.set('variables', JSON.stringify(variables));
    body.set('doc_id', args.docId);
    // ─── Fetch с timeout ─────────────────────────────────────────────────────
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), args.timeoutMs);
    try {
        const response = await fetch('https://www.facebook.com/api/graphql/', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'content-type': 'application/x-www-form-urlencoded',
                'x-fb-lsd': lsd,
                'x-fb-friendly-name': args.friendlyName,
                'x-asbd-id': args.asbdId,
            },
            body: body.toString(),
            signal: controller.signal,
        });
        clearTimeout(timeoutId);
        let text = await response.text();
        // Meta XSSI-prefix защита: ответ начинается с `for (;;);` — снимаем.
        if (text.startsWith('for (;;);')) {
            text = text.slice('for (;;);'.length);
        }
        return { statusCode: response.status, responseText: text };
    }
    catch (err) {
        clearTimeout(timeoutId);
        return {
            statusCode: 0,
            responseText: JSON.stringify({
                error: String(err?.message ?? err),
                type: err?.name === 'AbortError' ? 'timeout' : 'network',
            }),
        };
    }
}
/**
 * Debug: открывает Ad Library с country параметром, перехватывает первый
 * GraphQL запрос UI и возвращает его body+headers в виде "ads".
 *
 * Это сравнение с нашим build-from-scratch GraphQL: видим точные параметры
 * которые Meta UI использует (doc_id, friendly_name, токены, переменные).
 */
async function captureUiGraphQLRequest(context, params) {
    const t0 = Date.now();
    let page = null;
    const captures = [];
    try {
        page = await context.newPage();
        // Listener на каждый запрос: ловим POST к /api/graphql/
        page.on('request', (req) => {
            try {
                const url = req.url();
                if (!url.includes('/api/graphql/'))
                    return;
                if (req.method() !== 'POST')
                    return;
                const postData = req.postData() || '';
                const headers = req.headers();
                // Парсим form-urlencoded body
                const parsed = {};
                for (const pair of postData.split('&')) {
                    const [k, v] = pair.split('=', 2);
                    if (k)
                        parsed[decodeURIComponent(k)] = decodeURIComponent(v || '').slice(0, 600);
                }
                captures.push({
                    captured_at: Date.now(),
                    friendly_name: parsed.fb_api_req_friendly_name || 'unknown',
                    doc_id: parsed.doc_id || '',
                    variables_preview: parsed.variables ? parsed.variables.slice(0, 500) : '',
                    all_body_keys: Object.keys(parsed),
                    headers_used: {
                        'x-fb-friendly-name': headers['x-fb-friendly-name'] || '',
                        'x-fb-lsd': (headers['x-fb-lsd'] || '').slice(0, 10) + '...',
                        'x-asbd-id': headers['x-asbd-id'] || '',
                    },
                });
            }
            catch {
                // ignored
            }
        });
        const url = new URL(AD_LIBRARY_URL);
        url.searchParams.set('active_status', 'active');
        url.searchParams.set('ad_type', 'all');
        url.searchParams.set('country', (params.country || 'US').toUpperCase());
        url.searchParams.set('q', 'Chicken Road 2');
        await page.goto(url.toString(), { waitUntil: 'commit', timeout: 30_000 });
        // Подождать пока UI сделает первые GraphQL запросы
        await new Promise((r) => setTimeout(r, 8_000));
        // Скроллим вниз — это должно триггернуть search pagination запрос
        for (let i = 0; i < 3; i++) {
            await page.evaluate(() => window.scrollBy(0, window.innerHeight * 2));
            await new Promise((r) => setTimeout(r, 3_000));
        }
        return {
            adCount: captures.length,
            adsJson: JSON.stringify(captures),
            durationMs: Date.now() - t0,
            pagesFetched: 0,
        };
    }
    catch (err) {
        return {
            adCount: 0,
            adsJson: JSON.stringify(captures),
            durationMs: Date.now() - t0,
            pagesFetched: 0,
            error: {
                code: -99,
                type: 'CaptureError',
                message: String(err?.message ?? err),
            },
        };
    }
    finally {
        if (page)
            page.close().catch(() => { });
    }
}
/**
 * Health-check: context жив, browser подключён.
 */
async function checkAdLibraryHealth(context) {
    if (!context) {
        return { healthy: false, detail: 'context_missing' };
    }
    try {
        const browser = context.browser();
        if (!browser || !browser.isConnected()) {
            return { healthy: false, detail: 'browser_disconnected' };
        }
        return { healthy: true, detail: 'ok' };
    }
    catch (err) {
        return { healthy: false, detail: `error: ${String(err?.message ?? err)}` };
    }
}
//# sourceMappingURL=client.js.map