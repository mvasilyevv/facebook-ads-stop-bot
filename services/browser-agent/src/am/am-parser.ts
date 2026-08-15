// Парсинг ответов канала метрик Ads Manager UI: `am_tabular` (метрики per-ad + конверсии)
// и `light_*` (имена/статус). Чистые функции, без сети. Money-критичный путь —
// структура и маппинг сверены на боевых данных (см. docs/am_tabular_scanner_plan.md §1, §3).

export interface AmColumn {
  name: string;
  type?: string;
  attribution_window?: string;
}

export interface AmActionValue {
  types?: string[];
  values?: string[];
  breakdown?: string;
}

export interface AmResultValue {
  indicator?: string;
  value?: string;
}

// Сырьё одной per-ad строки am_tabular до маппинга в ScannedAdRow.
export interface AmRow {
  adId: string;
  objective: string | null;
  // имя atomic-колонки -> сырое значение ("na"/"null" отфильтрованы)
  atomic: Record<string, string>;
  // action_type -> значение, окно default
  actions: Record<string, string>;
  costPerAction: Record<string, string>;
  outboundClicks: string | null;
  outboundCtr: string | null;
  // result_columns: name=results / cost_per_result, окно default
  results: string | null;
  costPerResult: string | null;
}

export interface LightMeta {
  id: string;
  name?: string;
  effectiveStatus?: string;
  moderationReason?: string;
  campaignId?: string;
  adsetId?: string;
  dailyBudget?: string;
  lifetimeBudget?: string;
  creativeThumbUrl?: string;
  creativeImageUrl?: string;
  videoId?: string; // для видео-крео — нода видео, откуда тянем полноразмерный постер
  pixelId?: string;
  budgetRemaining?: string;
  learningStage?: string;
}

const NULLISH = new Set(['na', 'null', '', '--', '-', '—']);
const MODERATION_REASON_LIMIT = 600;

// Сырое значение am_tabular -> string | null. Отбрасываем плейсхолдеры FB.
function clean(v: string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (NULLISH.has(s.toLowerCase())) return null;
  return s;
}

function cleanModerationText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.replace(/\s+/g, ' ').trim();
  return normalized || null;
}

function collectModerationFeedback(
  value: unknown,
  parts: string[],
  label?: string,
): void {
  const text = cleanModerationText(value);
  if (text) {
    parts.push(label ? `${label}: ${text}` : text);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectModerationFeedback(item, parts, label);
    return;
  }
  if (!value || typeof value !== 'object') return;
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    collectModerationFeedback(nested, parts, key.replace(/_/g, ' '));
  }
}

/** Причина модерации только из явных полей Meta; отсутствие остаётся unknown. */
export function extractModerationReason(value: Record<string, any>): string | undefined {
  const parts: string[] = [];
  collectModerationFeedback(value.ad_review_feedback, parts);

  const issues = Array.isArray(value.issues_info) ? value.issues_info : [];
  for (const issue of issues) {
    if (!issue || typeof issue !== 'object') continue;
    const summary = cleanModerationText(issue.error_summary);
    const message = cleanModerationText(issue.error_message);
    if (summary && message && summary !== message) parts.push(`${summary}: ${message}`);
    else if (message || summary) parts.push(message ?? summary!);
  }

  const unique = [...new Set(parts)];
  if (!unique.length) return undefined;
  return unique.join(' · ').slice(0, MODERATION_REASON_LIMIT);
}

// Индекс колонки по имени с приоритетом окна default; иначе первое совпадение по имени.
function pickDefaultIndex(cols: AmColumn[], name: string): number {
  let fallback = -1;
  for (let i = 0; i < cols.length; i++) {
    if (cols[i]?.name !== name) continue;
    if (cols[i]?.attribution_window === 'default') return i;
    if (fallback === -1) fallback = i;
  }
  return fallback;
}

// {types:[...], values:[...]} -> dict action_type -> значение (null-значения отброшены).
function actionMap(av: AmActionValue | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!av?.types || !av?.values) return out;
  const n = Math.min(av.types.length, av.values.length);
  for (let i = 0; i < n; i++) {
    const t = av.types[i];
    const v = clean(av.values[i]);
    if (t && v !== null) out[t] = v;
  }
  return out;
}

// Значение по ключу, иначе первое непустое в map (для одно-типовых колонок outbound_*).
function pickValue(map: Record<string, string>, key: string): string | null {
  if (map[key] !== undefined) return map[key];
  const vals = Object.values(map);
  return vals.length ? vals[0] : null;
}

// Парс одного ответа am_tabular -> массив per-ad строк (summary-строки ad_id="na" отброшены).
// За скан прилетает несколько ответов (sync/async, пагинация) — мёржить через mergeAmRows.
export function parseAmTabular(body: unknown): AmRow[] {
  const rows: AmRow[] = [];
  const blocks = (body as { data?: unknown[] })?.data;
  if (!Array.isArray(blocks)) return rows;

  for (const block of blocks as Array<Record<string, any>>) {
    const headers = block?.headers || {};
    const dims: string[] = headers.dimensions || [];
    const adIdx = dims.indexOf('ad_id');
    if (adIdx < 0) continue; // footer/summary без ad_id
    const objIdx = dims.indexOf('objective');

    const atomicCols: AmColumn[] = headers.atomic_columns || [];
    const actionCols: AmColumn[] = headers.action_columns || [];
    const resultCols: AmColumn[] = headers.result_columns || [];

    const actionsIdx = pickDefaultIndex(actionCols, 'actions');
    const cpaIdx = pickDefaultIndex(actionCols, 'cost_per_action_type');
    const outIdx = pickDefaultIndex(actionCols, 'outbound_clicks');
    const outCtrIdx = pickDefaultIndex(actionCols, 'outbound_clicks_ctr');
    const resultsIdx = pickDefaultIndex(resultCols, 'results');
    const cprIdx = pickDefaultIndex(resultCols, 'cost_per_result');

    for (const row of (block?.rows || []) as Array<Record<string, any>>) {
      const dv: string[] = row?.dimension_values || [];
      const adId = dv[adIdx];
      if (!adId || NULLISH.has(String(adId).toLowerCase())) continue;

      const atomicVals: string[] = row?.atomic_values || [];
      const atomic: Record<string, string> = {};
      for (let i = 0; i < atomicCols.length; i++) {
        const v = clean(atomicVals[i]);
        const name = atomicCols[i]?.name;
        if (name && v !== null) atomic[name] = v;
      }

      const av: AmActionValue[] = row?.action_values || [];
      const actions = actionsIdx >= 0 ? actionMap(av[actionsIdx]) : {};
      const costPerAction = cpaIdx >= 0 ? actionMap(av[cpaIdx]) : {};
      const outboundMap = outIdx >= 0 ? actionMap(av[outIdx]) : {};
      const outboundCtrMap = outCtrIdx >= 0 ? actionMap(av[outCtrIdx]) : {};

      const rv: AmResultValue[] = row?.result_values || [];
      const results = resultsIdx >= 0 ? clean(rv[resultsIdx]?.value) : null;
      const costPerResult = cprIdx >= 0 ? clean(rv[cprIdx]?.value) : null;

      rows.push({
        adId: String(adId),
        objective: objIdx >= 0 ? clean(dv[objIdx]) : null,
        atomic,
        actions,
        costPerAction,
        outboundClicks: pickValue(outboundMap, 'outbound_click'),
        outboundCtr: pickValue(outboundCtrMap, 'outbound_click'),
        results,
        costPerResult,
      });
    }
  }
  return rows;
}

// Мёрж per-ad строк из нескольких ответов (sync даёт atomic+result, async добивает actions).
// По ad_id: atomic объединяем (непустые перекрывают), actions/costPerAction берём populated,
// result/cost — первое непустое. Защита от прогрессивной загрузки UI (см. §1).
export function mergeAmRows(rows: AmRow[]): Map<string, AmRow> {
  const out = new Map<string, AmRow>();
  for (const r of rows) {
    const prev = out.get(r.adId);
    if (!prev) {
      out.set(r.adId, r);
      continue;
    }
    out.set(r.adId, {
      adId: r.adId,
      objective: r.objective ?? prev.objective,
      atomic: { ...prev.atomic, ...r.atomic },
      actions: Object.keys(r.actions).length ? r.actions : prev.actions,
      costPerAction: Object.keys(r.costPerAction).length ? r.costPerAction : prev.costPerAction,
      outboundClicks: r.outboundClicks ?? prev.outboundClicks,
      outboundCtr: r.outboundCtr ?? prev.outboundCtr,
      results: r.results ?? prev.results,
      costPerResult: r.costPerResult ?? prev.costPerResult,
    });
  }
  return out;
}

// light_campaigns / light_adsets / lightads -> список метаданных.
// С fields=id отдаёт только id; с fields=name,effective_status — имя/статус/бюджет.
export function parseLightList(body: unknown): LightMeta[] {
  const data = (body as { data?: unknown[] })?.data;
  if (!Array.isArray(data)) return [];
  const out: LightMeta[] = [];
  for (const d of data as Array<Record<string, any>>) {
    if (!d || d.id === undefined || d.id === null) continue;
    const meta: LightMeta = { id: String(d.id) };
    if (d.name !== undefined) meta.name = String(d.name);
    if (d.effective_status !== undefined) meta.effectiveStatus = String(d.effective_status);
    meta.moderationReason = extractModerationReason(d);
    if (d.campaign_id !== undefined) meta.campaignId = String(d.campaign_id);
    if (d.adset_id !== undefined) meta.adsetId = String(d.adset_id);
    if (d.daily_budget !== undefined) meta.dailyBudget = String(d.daily_budget);
    if (d.lifetime_budget !== undefined) meta.lifetimeBudget = String(d.lifetime_budget);
    if (d.creative?.thumbnail_url !== undefined) meta.creativeThumbUrl = String(d.creative.thumbnail_url);
    // image_url приоритетно; для видео-крео top-level image_url часто пуст →
    // берём полноразмерный кадр из object_story_spec.video_data.image_url (постер видео).
    const topImage = d.creative?.image_url;
    const videoFrame = d.creative?.object_story_spec?.video_data?.image_url;
    const image =
      topImage !== undefined && topImage !== null && String(topImage) !== '' ? topImage : videoFrame;
    if (image !== undefined && image !== null && String(image) !== '') {
      meta.creativeImageUrl = String(image);
    }
    // video_id (top-level creative или внутри object_story_spec.video_data) — нужен,
    // чтобы для видео-крео без image_url дотянуть полноразмерный кадр из video node.
    const videoId = d.creative?.video_id ?? d.creative?.object_story_spec?.video_data?.video_id;
    if (videoId !== undefined && videoId !== null && String(videoId) !== '') {
      meta.videoId = String(videoId);
    }
    if (d.promoted_object?.pixel_id !== undefined) meta.pixelId = String(d.promoted_object.pixel_id);
    if (d.budget_remaining !== undefined) meta.budgetRemaining = String(d.budget_remaining);
    if (d.learning_stage_info?.status !== undefined) meta.learningStage = String(d.learning_stage_info.status);
    out.push(meta);
  }
  return out;
}

// Курсор следующей страницы (paging.cursors.after). Идём по нему, не скроллом.
// Graph отдаёт курсор даже на последней странице → останавливаемся, когда страница пуста.
export function lightNextCursor(body: unknown): string | null {
  const paging = (body as { paging?: { cursors?: { after?: string } } })?.paging;
  return paging?.cursors?.after ?? null;
}
