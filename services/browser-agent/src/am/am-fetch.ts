// Active replication канала метрик Ads Manager: сами зовём am_tabular + light_* изнутри
// Vision-сессии через page.evaluate(fetch). Токен/куки сессии уже в странице — httpx НЕ используем
// (правило Meta-доступа). Не скроллим, не парсим DOM. См. docs/am_tabular_scanner_plan.md §2.

import type { Page } from 'playwright';
import {
  parseAmTabular,
  parseLightList,
  lightNextCursor,
  mergeAmRows,
  type AmRow,
  type LightMeta,
} from './am-parser.js';
import { buildScannedRows, type AmAdMeta } from './am-join.js';
import { resolveOwnerCampaignIds } from './am-owner.js';
import {
  AM_COLUMN_FIELDS,
  AM_ACTION_TYPES,
  AM_AD_DELIVERY_STATUSES,
  AM_ATTRIBUTION_WINDOWS,
  AM_PAGE_LIMIT,
  type AmScanConfig,
} from './am-config.js';
import type { ScannedAdRow } from '../types.js';

export interface GraphContext {
  accessToken: string;
  actId: string; // "act_<id>"
  apiVersion: string; // "v22.0"
  graphOrigin: string; // https://adsmanager-graph.facebook.com (метрики am_tabular)
}

// Имена/статус берём из Graph REST edges (ads/campaigns/adsets) — статичная метадата,
// лага нет (метрики остаются на am_tabular). Решение пользователя: Marketing API только для имён.
const GRAPH_REST_ORIGIN = 'https://graph.facebook.com';

interface Filter {
  field: string;
  operator: string;
  value: string[];
}

// Сниф access_token/act_id/версии из исходящих запросов страницы к adsmanager-graph.
// Страница и так шлёт light_*/am_tabular при загрузке/refresh → токен берём оттуда (без httpx).
export async function extractGraphContext(page: Page, timeoutMs = 15000): Promise<GraphContext> {
  return await new Promise<GraphContext>((resolve, reject) => {
    let settled = false;
    const onReq = (req: { url(): string }) => {
      try {
        const u = req.url();
        if (!u.includes('adsmanager-graph.facebook.com')) return;
        const m = u.match(/\/(v\d+\.\d+)\/(act_\d+)\//);
        if (!m) return;
        const token = new URL(u).searchParams.get('access_token');
        if (!token) return;
        settled = true;
        page.off('request', onReq as never);
        resolve({
          accessToken: token,
          apiVersion: m[1],
          actId: m[2],
          graphOrigin: 'https://adsmanager-graph.facebook.com',
        });
      } catch {
        /* ignore */
      }
    };
    page.on('request', onReq as never);
    setTimeout(() => {
      if (settled) return;
      page.off('request', onReq as never);
      reject(new Error('am: не удалось извлечь access_token из сессии (нет запросов к adsmanager-graph)'));
    }, timeoutMs);
  });
}

// Кэш GraphContext по session_id: токен валиден всю сессию → сниффим ОДИН раз.
// am_tabular — живой REST, данные всегда актуальны; reload нужен только чтобы спровоцировать
// запрос для снятия токена. С кэшем стационарный скан = только наши fetch'и, без reload.
const _graphContextCache = new Map<string, GraphContext>();

export function invalidateGraphContext(sessionId: string): void {
  _graphContextCache.delete(sessionId);
}

// Реконструировать URL кабинета Ads Manager из закэшированного GraphContext (act_id).
// Нужно для self-heal: переоткрыть закрытую вкладку, даже если последний URL не запомнен.
// null, если контекст ещё не сниффился (нет act_id) — тогда переоткрытие на этом уровне невозможно.
export function reconstructAdsManagerUrl(sessionId: string): string | null {
  const ctx = _graphContextCache.get(sessionId);
  if (!ctx) return null;
  const actNum = ctx.actId.replace(/^act_/, '');
  if (!actNum) return null;
  return `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${actNum}`;
}

// Вернуть GraphContext из кэша; при cache-miss/forceRefresh — сниффить (reload триггерит запрос,
// т.к. уже загруженная страница пассивно ничего не шлёт). sniffed=true → был reload.
export async function acquireGraphContext(
  page: Page,
  sessionId: string,
  opts: { forceRefresh?: boolean } = {},
): Promise<{ ctx: GraphContext; sniffed: boolean }> {
  if (!opts.forceRefresh) {
    const cached = _graphContextCache.get(sessionId);
    if (cached) return { ctx: cached, sniffed: false };
  }
  const ctxPromise = extractGraphContext(page, 20000);
  try {
    await page.reload({ waitUntil: 'domcontentloaded' });
  } catch {
    /* ignore — listener всё равно может поймать запрос */
  }
  const ctx = await ctxPromise;
  _graphContextCache.set(sessionId, ctx);
  return { ctx, sniffed: true };
}

function buildFiltering(opts: { campaignIds?: string[]; adIds?: string[] }): Filter[] {
  const f: Filter[] = [
    { field: 'ad.delivery_info', operator: 'IN', value: [...AM_AD_DELIVERY_STATUSES] },
  ];
  if (opts.campaignIds?.length) f.push({ field: 'campaign.id', operator: 'IN', value: opts.campaignIds });
  if (opts.adIds?.length) f.push({ field: 'ad.id', operator: 'IN', value: opts.adIds });
  f.push({ field: 'action_type', operator: 'IN', value: [...AM_ACTION_TYPES] });
  return f;
}

function amTabularUrl(ctx: GraphContext, filtering: Filter[], datePreset: string, after?: string): string {
  const qs = new URLSearchParams();
  qs.set('access_token', ctx.accessToken);
  qs.set('level', 'ad');
  qs.set('column_fields', JSON.stringify(AM_COLUMN_FIELDS));
  qs.set('filtering', JSON.stringify(filtering));
  qs.set('date_preset', datePreset);
  qs.set('limit', String(AM_PAGE_LIMIT));
  qs.set('action_attribution_windows', JSON.stringify(AM_ATTRIBUTION_WINDOWS));
  qs.set('use_unified_attribution_setting', 'true');
  qs.set('locale', 'en_US');
  if (after) qs.set('after', after);
  return `${ctx.graphOrigin}/${ctx.apiVersion}/${ctx.actId}/am_tabular?${qs.toString()}`;
}

function edgeUrl(
  ctx: GraphContext,
  origin: string,
  edge: string,
  fields: string[],
  filtering: Filter[],
  after?: string,
): string {
  const qs = new URLSearchParams();
  qs.set('access_token', ctx.accessToken);
  qs.set('fields', fields.join(','));
  if (filtering.length) qs.set('filtering', JSON.stringify(filtering));
  qs.set('limit', String(AM_PAGE_LIMIT));
  if (after) qs.set('after', after);
  return `${origin}/${ctx.apiVersion}/${ctx.actId}/${edge}?${qs.toString()}`;
}

// GET изнутри страницы (origin facebook → куки сессии + наш access_token). Без httpx.
async function fetchJson(page: Page, url: string): Promise<Record<string, unknown>> {
  return (await page.evaluate(async (u: string) => {
    try {
      const r = await fetch(u, { credentials: 'include' });
      const text = await r.text();
      try {
        return JSON.parse(text);
      } catch {
        return { __amError: true, status: r.status, body: text.slice(0, 300) };
      }
    } catch (e) {
      return { __amError: true, message: String(e) };
    }
  }, url)) as Record<string, unknown>;
}

// am_tabular с курсорной пагинацией (limit 5000 → обычно одна итерация; цикл-бэкстоп 20).
async function fetchAllAmTabular(
  page: Page,
  ctx: GraphContext,
  filtering: Filter[],
  datePreset: string,
): Promise<{ rows: AmRow[]; error?: string; authExpired?: boolean }> {
  const rows: AmRow[] = [];
  let after: string | undefined;
  for (let i = 0; i < 20; i++) {
    const body = await fetchJson(page, amTabularUrl(ctx, filtering, datePreset, after));
    if (body?.__amError) {
      return { rows, error: `am_tabular: ${body.status ?? ''} ${body.body ?? body.message ?? ''}` };
    }
    // Graph error в теле (напр. протухший токен → code 190 / OAuthException).
    const gErr = body?.error as { code?: number; type?: string; message?: string } | undefined;
    if (gErr) {
      const authExpired = gErr.code === 190 || gErr.type === 'OAuthException';
      return { rows, error: `am_tabular: ${gErr.code ?? ''} ${gErr.message ?? ''}`, authExpired };
    }
    const got = parseAmTabular(body);
    rows.push(...got);
    const paging = body?.paging as { cursors?: { after?: string } } | undefined;
    const cursor = paging?.cursors?.after ?? null;
    if (!cursor || got.length === 0) break;
    after = cursor;
  }
  return { rows };
}

// Graph REST edge (ads/campaigns/adsets) с курсорной пагинацией → метадата (id/name/status/иерархия).
async function fetchAllEdge(
  page: Page,
  ctx: GraphContext,
  origin: string,
  edge: string,
  fields: string[],
  filtering: Filter[],
): Promise<{ items: LightMeta[]; error?: string }> {
  const out: LightMeta[] = [];
  let after: string | undefined;
  for (let i = 0; i < 20; i++) {
    const body = await fetchJson(page, edgeUrl(ctx, origin, edge, fields, filtering, after));
    if (body?.__amError) {
      return { items: out, error: `${edge}: ${body.status ?? ''} ${body.body ?? body.message ?? ''}` };
    }
    const got = parseLightList(body);
    out.push(...got);
    const cursor = lightNextCursor(body);
    if (!cursor || got.length === 0) break;
    after = cursor;
  }
  return { items: out };
}

export interface AmScanResult {
  rows: ScannedAdRow[];
  diagnostics: {
    adCountMetrics: number; // ад'ов с метриками из am_tabular
    adCountNames: number; // ад'ов из Graph REST ads edge
    namesResolved: number;
    statusResolved: number;
    amError?: string;
    nameError?: string;
    authExpired?: boolean; // токен протух (code 190) → caller делает re-sniff + retry
    // Сверка полноты: ад'ы из Graph ads-edge (сущности), которых НЕТ в am_tabular (метриках).
    adsEdgeOnly: number;
    adsEdgeOnlySample: string[];
    // Обратное: ад'ы в am_tabular, которых нет в ads-edge (обычно archived вне дефолтного скоупа edge).
    metricsOnly: number;
    // Кампании (id, name, ad_count) — для выбора campaign_ids (#3). adCount считается по
    // ЗАСКОУПЛЕННЫМ ад'ам (для полного списка adCount гоняй без owner_tag/campaign_ids).
    campaigns: Array<{ id: string; name: string; adCount: number }>;
    // Скоуп фетча: сколько campaign.id в эффективном фильтре (0 = весь кабинет) + был ли резолв owner_tag.
    scopeCampaignCount: number;
    ownerResolved: boolean;
  };
}

// Полный am-скан с самостоятельным извлечением токена (для standalone-вызовов/тестов).
export async function runAmScan(page: Page, config: AmScanConfig): Promise<AmScanResult> {
  const ctx = await extractGraphContext(page);
  return runAmScanWithContext(page, ctx, config);
}

// am-скан с уже извлечённым GraphContext: метрики (am_tabular) + имена/статус (light_*) → ScannedAdRow[].
// runScanCycle сниффит токен во время reload и передаёт ctx сюда.
export async function runAmScanWithContext(
  page: Page,
  ctx: GraphContext,
  config: AmScanConfig,
): Promise<AmScanResult> {
  // 0) Кампании (id+name) — ПЕРВЫМИ: нужны для резолва owner_tag → campaign.id (#3, вариант 3),
  //    чтобы am тянул сразу только свой скоуп, а не весь общий кабинет.
  const campRes = await fetchAllEdge(page, ctx, GRAPH_REST_ORIGIN, 'campaigns', ['id', 'name'], []);

  // Эффективный скоуп: явный campaignIds, иначе резолв по owner_tag (имена кампаний → id своих).
  let campaignIds = config.campaignIds ?? [];
  let ownerResolved = false;
  if (!campaignIds.length && config.ownerTag) {
    campaignIds = resolveOwnerCampaignIds(campRes.items, config.ownerTag);
    ownerResolved = true;
    // Безопасность: owner_tag задан, но 0 кампаний матчнулось → НЕ сужаем до нуля (иначе
    // пропустим всё); оставляем без фильтра, Python-пайплайн отфильтрует. Логируем аномалию.
    if (!campaignIds.length) {
      console.warn(`[am] owner_tag="${config.ownerTag}" не дал ни одной кампании — скан без сужения`);
    }
  }
  const scopeFilter: Filter[] = campaignIds.length
    ? [{ field: 'campaign.id', operator: 'IN', value: campaignIds }]
    : [];

  // 1) Метрики per-ad (am_tabular) — уже в скоупе.
  const { rows: amRows, error: amError, authExpired } = await fetchAllAmTabular(
    page,
    ctx,
    buildFiltering({ campaignIds }),
    config.datePreset,
  );
  const merged = mergeAmRows(amRows);

  // 2) Имена/статус ад'ов + adsets — тоже в скоупе (тянем только своё, не весь кабинет).
  const adsRes = await fetchAllEdge(
    page,
    ctx,
    GRAPH_REST_ORIGIN,
    'ads',
    ['id', 'name', 'effective_status', 'campaign_id', 'adset_id'],
    scopeFilter,
  );
  const adsetRes = await fetchAllEdge(
    page,
    ctx,
    GRAPH_REST_ORIGIN,
    'adsets',
    ['id', 'name'],
    scopeFilter,
  );

  const campName = new Map(campRes.items.map((c) => [c.id, c.name ?? '']));
  const adsetName = new Map(adsetRes.items.map((a) => [a.id, a.name ?? '']));

  let namesResolved = 0;
  let statusResolved = 0;
  const adMeta = new Map<string, AmAdMeta>();
  for (const ad of adsRes.items) {
    if (ad.name) namesResolved += 1;
    if (ad.effectiveStatus) statusResolved += 1;
    adMeta.set(ad.id, {
      adName: ad.name,
      effectiveStatus: ad.effectiveStatus,
      campaignId: ad.campaignId,
      campaignName: ad.campaignId ? campName.get(ad.campaignId) : undefined,
      adsetName: ad.adsetId ? adsetName.get(ad.adsetId) : undefined,
    });
  }

  const rows = buildScannedRows(merged, adMeta);

  // Сверка полноты множеств ad_id: метрики (am_tabular) vs сущности (ads-edge).
  const metricIds = new Set(merged.keys());
  const edgeIds = new Set(adsRes.items.map((a) => a.id));
  const adsEdgeOnly = [...edgeIds].filter((id) => !metricIds.has(id));
  const metricsOnly = [...metricIds].filter((id) => !edgeIds.has(id));

  // Кампании с числом ад'ов — для выбора campaign_ids (#3).
  const adsPerCampaign = new Map<string, number>();
  for (const ad of adsRes.items) {
    if (ad.campaignId) adsPerCampaign.set(ad.campaignId, (adsPerCampaign.get(ad.campaignId) ?? 0) + 1);
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
