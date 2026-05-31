"use strict";
// Active replication канала метрик Ads Manager: сами зовём am_tabular + light_* изнутри
// Vision-сессии через page.evaluate(fetch). Токен/куки сессии уже в странице — httpx НЕ используем
// (правило Meta-доступа). Не скроллим, не парсим DOM. См. docs/am_tabular_scanner_plan.md §2.
Object.defineProperty(exports, "__esModule", { value: true });
exports.extractGraphContext = extractGraphContext;
exports.invalidateGraphContext = invalidateGraphContext;
exports.reconstructAdsManagerUrl = reconstructAdsManagerUrl;
exports.acquireGraphContext = acquireGraphContext;
exports.runAmScan = runAmScan;
exports.runAmScanWithContext = runAmScanWithContext;
const am_parser_js_1 = require("./am-parser.js");
const am_join_js_1 = require("./am-join.js");
const am_owner_js_1 = require("./am-owner.js");
const am_config_js_1 = require("./am-config.js");
// Имена/статус берём из Graph REST edges (ads/campaigns/adsets) — статичная метадата,
// лага нет (метрики остаются на am_tabular). Решение пользователя: Marketing API только для имён.
const GRAPH_REST_ORIGIN = 'https://graph.facebook.com';
// Сниф access_token/act_id/версии из исходящих запросов страницы к adsmanager-graph.
// Страница и так шлёт light_*/am_tabular при загрузке/refresh → токен берём оттуда (без httpx).
async function extractGraphContext(page, timeoutMs = 15000) {
    return await new Promise((resolve, reject) => {
        let settled = false;
        const onReq = (req) => {
            try {
                const u = req.url();
                if (!u.includes('adsmanager-graph.facebook.com'))
                    return;
                const m = u.match(/\/(v\d+\.\d+)\/(act_\d+)\//);
                if (!m)
                    return;
                const token = new URL(u).searchParams.get('access_token');
                if (!token)
                    return;
                settled = true;
                page.off('request', onReq);
                resolve({
                    accessToken: token,
                    apiVersion: m[1],
                    actId: m[2],
                    graphOrigin: 'https://adsmanager-graph.facebook.com',
                });
            }
            catch {
                /* ignore */
            }
        };
        page.on('request', onReq);
        setTimeout(() => {
            if (settled)
                return;
            page.off('request', onReq);
            reject(new Error('am: не удалось извлечь access_token из сессии (нет запросов к adsmanager-graph)'));
        }, timeoutMs);
    });
}
// Кэш GraphContext по session_id: токен валиден всю сессию → сниффим ОДИН раз.
// am_tabular — живой REST, данные всегда актуальны; reload нужен только чтобы спровоцировать
// запрос для снятия токена. С кэшем стационарный скан = только наши fetch'и, без reload.
const _graphContextCache = new Map();
function invalidateGraphContext(sessionId) {
    _graphContextCache.delete(sessionId);
}
// Реконструировать URL кабинета Ads Manager из закэшированного GraphContext (act_id).
// Нужно для self-heal: переоткрыть закрытую вкладку, даже если последний URL не запомнен.
// null, если контекст ещё не сниффился (нет act_id) — тогда переоткрытие на этом уровне невозможно.
function reconstructAdsManagerUrl(sessionId) {
    const ctx = _graphContextCache.get(sessionId);
    if (!ctx)
        return null;
    const actNum = ctx.actId.replace(/^act_/, '');
    if (!actNum)
        return null;
    return `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${actNum}`;
}
// Вернуть GraphContext из кэша; при cache-miss/forceRefresh — сниффить (reload триггерит запрос,
// т.к. уже загруженная страница пассивно ничего не шлёт). sniffed=true → был reload.
async function acquireGraphContext(page, sessionId, opts = {}) {
    if (!opts.forceRefresh) {
        const cached = _graphContextCache.get(sessionId);
        if (cached)
            return { ctx: cached, sniffed: false };
    }
    const ctxPromise = extractGraphContext(page, 20000);
    try {
        await page.reload({ waitUntil: 'domcontentloaded' });
    }
    catch {
        /* ignore — listener всё равно может поймать запрос */
    }
    const ctx = await ctxPromise;
    _graphContextCache.set(sessionId, ctx);
    return { ctx, sniffed: true };
}
function buildFiltering(opts) {
    const f = [
        { field: 'ad.delivery_info', operator: 'IN', value: [...am_config_js_1.AM_AD_DELIVERY_STATUSES] },
    ];
    if (opts.campaignIds?.length)
        f.push({ field: 'campaign.id', operator: 'IN', value: opts.campaignIds });
    if (opts.adIds?.length)
        f.push({ field: 'ad.id', operator: 'IN', value: opts.adIds });
    f.push({ field: 'action_type', operator: 'IN', value: [...am_config_js_1.AM_ACTION_TYPES] });
    return f;
}
function amTabularUrl(ctx, filtering, datePreset, after) {
    const qs = new URLSearchParams();
    qs.set('access_token', ctx.accessToken);
    qs.set('level', 'ad');
    qs.set('column_fields', JSON.stringify(am_config_js_1.AM_COLUMN_FIELDS));
    qs.set('filtering', JSON.stringify(filtering));
    qs.set('date_preset', datePreset);
    qs.set('limit', String(am_config_js_1.AM_PAGE_LIMIT));
    qs.set('action_attribution_windows', JSON.stringify(am_config_js_1.AM_ATTRIBUTION_WINDOWS));
    qs.set('use_unified_attribution_setting', 'true');
    qs.set('locale', 'en_US');
    if (after)
        qs.set('after', after);
    return `${ctx.graphOrigin}/${ctx.apiVersion}/${ctx.actId}/am_tabular?${qs.toString()}`;
}
function edgeUrl(ctx, origin, edge, fields, filtering, after) {
    const qs = new URLSearchParams();
    qs.set('access_token', ctx.accessToken);
    qs.set('fields', fields.join(','));
    if (filtering.length)
        qs.set('filtering', JSON.stringify(filtering));
    qs.set('limit', String(am_config_js_1.AM_PAGE_LIMIT));
    if (after)
        qs.set('after', after);
    return `${origin}/${ctx.apiVersion}/${ctx.actId}/${edge}?${qs.toString()}`;
}
// GET изнутри страницы (origin facebook → куки сессии + наш access_token). Без httpx.
async function fetchJson(page, url) {
    return (await page.evaluate(async (u) => {
        try {
            const r = await fetch(u, { credentials: 'include' });
            const text = await r.text();
            try {
                return JSON.parse(text);
            }
            catch {
                return { __amError: true, status: r.status, body: text.slice(0, 300) };
            }
        }
        catch (e) {
            return { __amError: true, message: String(e) };
        }
    }, url));
}
// am_tabular с курсорной пагинацией (limit 5000 → обычно одна итерация; цикл-бэкстоп 20).
async function fetchAllAmTabular(page, ctx, filtering, datePreset) {
    const rows = [];
    let after;
    for (let i = 0; i < 20; i++) {
        const body = await fetchJson(page, amTabularUrl(ctx, filtering, datePreset, after));
        if (body?.__amError) {
            return { rows, error: `am_tabular: ${body.status ?? ''} ${body.body ?? body.message ?? ''}` };
        }
        // Graph error в теле (напр. протухший токен → code 190 / OAuthException).
        const gErr = body?.error;
        if (gErr) {
            const authExpired = gErr.code === 190 || gErr.type === 'OAuthException';
            return { rows, error: `am_tabular: ${gErr.code ?? ''} ${gErr.message ?? ''}`, authExpired };
        }
        const got = (0, am_parser_js_1.parseAmTabular)(body);
        rows.push(...got);
        const paging = body?.paging;
        const cursor = paging?.cursors?.after ?? null;
        if (!cursor || got.length === 0)
            break;
        after = cursor;
    }
    return { rows };
}
// Graph REST edge (ads/campaigns/adsets) с курсорной пагинацией → метадата (id/name/status/иерархия).
async function fetchAllEdge(page, ctx, origin, edge, fields, filtering) {
    const out = [];
    let after;
    for (let i = 0; i < 20; i++) {
        const body = await fetchJson(page, edgeUrl(ctx, origin, edge, fields, filtering, after));
        if (body?.__amError) {
            return { items: out, error: `${edge}: ${body.status ?? ''} ${body.body ?? body.message ?? ''}` };
        }
        const got = (0, am_parser_js_1.parseLightList)(body);
        out.push(...got);
        const cursor = (0, am_parser_js_1.lightNextCursor)(body);
        if (!cursor || got.length === 0)
            break;
        after = cursor;
    }
    return { items: out };
}
// Полный am-скан с самостоятельным извлечением токена (для standalone-вызовов/тестов).
async function runAmScan(page, config) {
    const ctx = await extractGraphContext(page);
    return runAmScanWithContext(page, ctx, config);
}
// am-скан с уже извлечённым GraphContext: метрики (am_tabular) + имена/статус (light_*) → ScannedAdRow[].
// runScanCycle сниффит токен во время reload и передаёт ctx сюда.
async function runAmScanWithContext(page, ctx, config) {
    // 0) Кампании (id+name) — ПЕРВЫМИ: нужны для резолва owner_tag → campaign.id (#3, вариант 3),
    //    чтобы am тянул сразу только свой скоуп, а не весь общий кабинет.
    const campRes = await fetchAllEdge(page, ctx, GRAPH_REST_ORIGIN, 'campaigns', ['id', 'name'], []);
    // Эффективный скоуп: явный campaignIds, иначе резолв по owner_tag (имена кампаний → id своих).
    let campaignIds = config.campaignIds ?? [];
    let ownerResolved = false;
    if (!campaignIds.length && config.ownerTag) {
        campaignIds = (0, am_owner_js_1.resolveOwnerCampaignIds)(campRes.items, config.ownerTag);
        ownerResolved = true;
        // Безопасность: owner_tag задан, но 0 кампаний матчнулось → НЕ сужаем до нуля (иначе
        // пропустим всё); оставляем без фильтра, Python-пайплайн отфильтрует. Логируем аномалию.
        if (!campaignIds.length) {
            console.warn(`[am] owner_tag="${config.ownerTag}" не дал ни одной кампании — скан без сужения`);
        }
    }
    const scopeFilter = campaignIds.length
        ? [{ field: 'campaign.id', operator: 'IN', value: campaignIds }]
        : [];
    // 1) Метрики per-ad (am_tabular) — уже в скоупе.
    const { rows: amRows, error: amError, authExpired } = await fetchAllAmTabular(page, ctx, buildFiltering({ campaignIds }), config.datePreset);
    const merged = (0, am_parser_js_1.mergeAmRows)(amRows);
    // 2) Имена/статус ад'ов + adsets — тоже в скоупе (тянем только своё, не весь кабинет).
    const adsRes = await fetchAllEdge(page, ctx, GRAPH_REST_ORIGIN, 'ads', ['id', 'name', 'effective_status', 'campaign_id', 'adset_id'], scopeFilter);
    const adsetRes = await fetchAllEdge(page, ctx, GRAPH_REST_ORIGIN, 'adsets', ['id', 'name'], scopeFilter);
    const campName = new Map(campRes.items.map((c) => [c.id, c.name ?? '']));
    const adsetName = new Map(adsetRes.items.map((a) => [a.id, a.name ?? '']));
    let namesResolved = 0;
    let statusResolved = 0;
    const adMeta = new Map();
    for (const ad of adsRes.items) {
        if (ad.name)
            namesResolved += 1;
        if (ad.effectiveStatus)
            statusResolved += 1;
        adMeta.set(ad.id, {
            adName: ad.name,
            effectiveStatus: ad.effectiveStatus,
            campaignName: ad.campaignId ? campName.get(ad.campaignId) : undefined,
            adsetName: ad.adsetId ? adsetName.get(ad.adsetId) : undefined,
        });
    }
    const rows = (0, am_join_js_1.buildScannedRows)(merged, adMeta);
    // Сверка полноты множеств ad_id: метрики (am_tabular) vs сущности (ads-edge).
    const metricIds = new Set(merged.keys());
    const edgeIds = new Set(adsRes.items.map((a) => a.id));
    const adsEdgeOnly = [...edgeIds].filter((id) => !metricIds.has(id));
    const metricsOnly = [...metricIds].filter((id) => !edgeIds.has(id));
    // Кампании с числом ад'ов — для выбора campaign_ids (#3).
    const adsPerCampaign = new Map();
    for (const ad of adsRes.items) {
        if (ad.campaignId)
            adsPerCampaign.set(ad.campaignId, (adsPerCampaign.get(ad.campaignId) ?? 0) + 1);
    }
    const campaigns = campRes.items
        .map((c) => ({ id: c.id, name: c.name ?? '', adCount: adsPerCampaign.get(c.id) ?? 0 }))
        .sort((a, b) => b.adCount - a.adCount);
    return {
        rows,
        diagnostics: {
            adCountMetrics: merged.size,
            adCountNames: adsRes.items.length,
            namesResolved,
            statusResolved,
            amError,
            nameError: adsRes.error ?? campRes.error ?? adsetRes.error,
            authExpired,
            adsEdgeOnly: adsEdgeOnly.length,
            adsEdgeOnlySample: adsEdgeOnly.slice(0, 12),
            metricsOnly: metricsOnly.length,
            campaigns,
            scopeCampaignCount: campaignIds.length,
            ownerResolved,
        },
    };
}
//# sourceMappingURL=am-fetch.js.map