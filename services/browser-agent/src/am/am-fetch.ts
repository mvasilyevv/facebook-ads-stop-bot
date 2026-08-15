// Active replication канала метрик Ads Manager: сами зовём am_tabular + light_* изнутри
// Vision-сессии через page.evaluate(fetch). Токен/куки сессии уже в странице — httpx НЕ используем
// (правило Meta-доступа). Не скроллим, не парсим DOM. См. docs/am_tabular_scanner_plan.md §2.

import type { Page } from 'playwright';
import { randomUUID } from 'crypto';
import {
  parseAmTabular,
  parseLightList,
  lightNextCursor,
  mergeAmRows,
  type AmRow,
  type LightMeta,
} from './am-parser.js';
import { buildScannedRows, type AmAdMeta } from './am-join.js';
import { campaignMatchesOwner, resolveOwnerCampaignIds } from './am-owner.js';
import {
  AM_COLUMN_FIELDS,
  AM_ACTION_TYPES,
  AM_AD_DELIVERY_STATUSES,
  AM_ATTRIBUTION_WINDOWS,
  AM_PAGE_LIMIT,
  type AmScanConfig,
} from './am-config.js';
import { adsManagerColumnsQs } from './am-columns-preset.js';
import type { ScannedAdRow } from '../types.js';
import {
  bindAbortSignalToPage,
  clearInPageFetchOperation,
  raceWithAbort,
} from '../in-page-abort.js';

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
export async function extractGraphContext(
  page: Page,
  timeoutMs = 15000,
  signal?: AbortSignal,
): Promise<GraphContext> {
  return await new Promise<GraphContext>((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      page.off('request', onReq as never);
      signal?.removeEventListener('abort', onAbort);
      if (timer) clearTimeout(timer);
    };
    const settleReject = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const onReq = (req: { url(): string }) => {
      try {
        const u = req.url();
        if (!u.includes('adsmanager-graph.facebook.com')) return;
        const m = u.match(/\/(v\d+\.\d+)\/(act_\d+)\//);
        if (!m) return;
        const token = new URL(u).searchParams.get('access_token');
        if (!token) return;
        if (settled) return;
        settled = true;
        cleanup();
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
    const onAbort = () => settleReject(new Error('am: graph context acquisition cancelled'));
    page.on('request', onReq as never);
    signal?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => {
      settleReject(new Error('am: не удалось извлечь access_token из сессии (нет запросов к adsmanager-graph)'));
    }, timeoutMs);
    if (signal?.aborted) onAbort();
  });
}

// Кэш GraphContext: токен валиден всю сессию → сниффим ОДИН раз на кабинет.
// am_tabular — живой REST, данные всегда актуальны; reload нужен только чтобы спровоцировать
// запрос для снятия токена. С кэшем стационарный скан = только наши fetch'и, без reload.
// Ключ всегда `${session_id}:act_<id>`: session-only context запрещён.
const _graphContextCache = new Map<string, GraphContext>();

function normalizedActId(actId: string): string {
  const normalized = String(actId).replace(/^act_/, '').trim();
  if (!/^\d+$/.test(normalized)) {
    throw new Error('am: explicit numeric ad account id is required');
  }
  return normalized;
}

function graphContextKey(sessionId: string, actId: string): string {
  return `${sessionId}:act_${normalizedActId(actId)}`;
}

export function invalidateGraphContext(sessionId: string, actId: string): void {
  _graphContextCache.delete(graphContextKey(sessionId, actId));
}

// Реконструировать URL кабинета Ads Manager.
// Explicit actId makes it deterministic; no cached/session-global fallback exists.
export function reconstructAdsManagerUrl(sessionId: string, actId: string): string {
  void sessionId;
  return cabinetCampaignsUrl(normalizedActId(actId));
}

// URL вкладки кабинета: уровень кампаний + колонки пользователя (единый формат со
// session-manager.adsManagerUrlForAct). Уровень вкладки на скан не влияет.
function cabinetCampaignsUrl(actId: string): string {
  return (
    `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${actId}` +
    `&${adsManagerColumnsQs()}`
  );
}

// Вернуть GraphContext из кэша; при cache-miss/forceRefresh — сниффить (reload триггерит запрос,
// т.к. уже загруженная страница пассивно ничего не шлёт). sniffed=true → был reload.
// expectedActId — sanity-check мульти-кабинета: act из сниффа обязан совпасть с запрошенным
// кабинетом, иначе сканировали бы чужой кабинет под видом своего (money-критично).
export async function acquireGraphContext(
  page: Page,
  sessionId: string,
  opts: { expectedActId: string; forceRefresh?: boolean; signal?: AbortSignal },
): Promise<{ ctx: GraphContext; sniffed: boolean }> {
  const expectedActId = normalizedActId(opts.expectedActId);
  const key = graphContextKey(sessionId, expectedActId);
  if (!opts.forceRefresh) {
    const cached = _graphContextCache.get(key);
    if (cached) return { ctx: cached, sniffed: false };
  }
  const ctxPromise = extractGraphContext(page, 20000, opts.signal);
  try {
    await raceWithAbort(page.reload({ waitUntil: 'domcontentloaded' }), opts.signal);
  } catch {
    /* ignore — listener всё равно может поймать запрос */
  }
  const ctx = await ctxPromise;
  if (ctx.actId !== `act_${expectedActId}`) {
    throw new Error(
      `am: вкладка открыта не на том кабинете (ожидался act_${expectedActId}, ` +
        `снифф дал ${ctx.actId}) — скан кабинета прерван`,
    );
  }
  _graphContextCache.set(key, ctx);
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
  extraParams?: Record<string, string>,
): string {
  const qs = new URLSearchParams();
  qs.set('access_token', ctx.accessToken);
  qs.set('fields', fields.join(','));
  if (filtering.length) qs.set('filtering', JSON.stringify(filtering));
  qs.set('limit', String(AM_PAGE_LIMIT));
  if (after) qs.set('after', after);
  // Top-level params (например thumbnail_width/height для крупного thumbnail_url —
  // модификатор на edge creative невалиден, размер задаётся параметром запроса).
  for (const [k, v] of Object.entries(extraParams ?? {})) qs.set(k, v);
  return `${origin}/${ctx.apiVersion}/${ctx.actId}/${edge}?${qs.toString()}`;
}

// GET изнутри страницы (origin facebook → куки сессии + наш access_token). Без httpx.
// Отдаём __amError с диагностикой redirect/HTML, чтобы отличить разлогин от сетевого блипа:
//   redirected/finalUrl — fetch увёл на login.php/checkpoint (сессия протухла);
//   contentType/body — HTML вместо JSON тоже признак login-редиректа/заглушки.
interface AmFetchExecution {
  signal?: AbortSignal;
  operationId?: string;
}

async function fetchJson(
  page: Page,
  url: string,
  execution: AmFetchExecution = {},
): Promise<Record<string, unknown>> {
  if (execution.signal?.aborted) {
    throw new Error('am: graph fetch cancelled');
  }
  const result = (await page.evaluate(async (args: { url: string; operationId?: string }) => {
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
    const controller = new AbortController();
    if (args.operationId) {
      const controllers = state.controllers.get(args.operationId) ?? new Set<AbortController>();
      controllers.add(controller);
      state.controllers.set(args.operationId, controllers);
      if (state.cancelled.has(args.operationId)) controller.abort('grpc_cancelled');
    }
    try {
      const r = await fetch(args.url, { credentials: 'include', signal: controller.signal });
      const text = await r.text();
      try {
        return JSON.parse(text);
      } catch {
        return {
          __amError: true,
          status: r.status,
          body: text.slice(0, 300),
          redirected: r.redirected,
          finalUrl: r.url,
          contentType: r.headers.get('content-type') || '',
        };
      }
    } catch (e) {
      return {
        __amError: true,
        __amCancelled: controller.signal.aborted,
        message: String(e),
      };
    } finally {
      if (args.operationId) {
        const controllers = state.controllers.get(args.operationId);
        controllers?.delete(controller);
        if (controllers?.size === 0) state.controllers.delete(args.operationId);
      }
    }
  }, { url, operationId: execution.operationId })) as Record<string, unknown>;
  if (execution.signal?.aborted || result.__amCancelled) {
    throw new Error('am: graph fetch cancelled');
  }
  return result;
}

// Facebook OAuth-subcodes, которые означают именно РАЗЛОГИН / чекпоинт (нужен ре-логин
// профиля), а не просто протухший короткоживущий токен. 458 App-not-installed,
// 459 checkpoint (user must log in), 460 password changed (сессия инвалидирована),
// 463 session expired, 464 unconfirmed user, 467 invalid access token (logged out).
const _LOGIN_REQUIRED_SUBCODES: ReadonlySet<number> = new Set([458, 459, 460, 463, 464, 467]);

// Чистый детектор разлогина/чекпоинта по результату fetchJson (экспорт для unit-теста).
// Возвращает true, если ответ Meta — признак протухшей сессии (нужен ре-логин Vision),
// а НЕ транзиентный сетевой блип и не обычная Graph-ошибка. Триггеры:
//   (а) fetch увёл на login.php|checkpoint (redirected/finalUrl);
//   (б) вместо JSON пришёл HTML (content-type text/html или тело похоже на HTML) —
//       Meta так отдаёт login-страницу;
//   (в) Graph error code 190 (OAuthException) с login/checkpoint-subcode ИЛИ явным
//       упоминанием re-login в тексте (session expired / log in / checkpoint).
export function isLoginRequiredResponse(body: Record<string, unknown> | null | undefined): boolean {
  if (!body || typeof body !== 'object') return false;

  // (а)/(б): __amError с redirect/HTML.
  if (body.__amError) {
    const finalUrl = String(body.finalUrl ?? '').toLowerCase();
    if (body.redirected === true || /login\.php|checkpoint|\/login\//.test(finalUrl)) {
      return true;
    }
    const contentType = String(body.contentType ?? '').toLowerCase();
    const rawBody = String(body.body ?? '');
    const looksHtml =
      contentType.includes('text/html') ||
      /^\s*<(?:!doctype|html)\b/i.test(rawBody) ||
      /login\.php|checkpoint|<title>[^<]*log ?in/i.test(rawBody);
    if (looksHtml) return true;
    return false;
  }

  // (в): Graph error 190 в теле JSON.
  const gErr = body.error as
    | { code?: number; type?: string; error_subcode?: number; message?: string }
    | undefined;
  if (!gErr) return false;
  const code = Number(gErr.code ?? 0);
  const isOAuth = code === 190 || gErr.type === 'OAuthException';
  if (!isOAuth) return false;
  const subcode = Number(gErr.error_subcode ?? 0);
  if (_LOGIN_REQUIRED_SUBCODES.has(subcode)) return true;
  const msg = String(gErr.message ?? '').toLowerCase();
  return /session.*expired|log ?in|checkpoint|re-?authenticate|not logged in|logged out/.test(msg);
}

const _sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

// Повтор при ТРАНЗИЕНТНОМ результате (generic, экспорт для unit-теста). delaysMs задаёт
// число и паузы повторов: первый вызов всегда, далее до delaysMs.length повторов, пока
// isTransient(result)=true. На success/нетранзиент — мгновенный возврат без ожидания.
// Исчерпали попытки → возвращаем последний результат (caller разбирается, как и раньше).
export async function retryTransient<T>(
  fn: () => Promise<T>,
  opts: { delaysMs: number[]; isTransient: (r: T) => boolean },
): Promise<T> {
  let result = await fn();
  for (let attempt = 0; attempt < opts.delaysMs.length; attempt++) {
    if (!opts.isTransient(result)) return result;
    await _sleep(opts.delaysMs[attempt]);
    result = await fn();
  }
  return result;
}

// am_tabular — money-путь (метрики для авто-стопа). Сетевой блип Vision-сессии (~0.6%
// сканов, одиночный) даёт пустой скан → авто-стоп откладывается на следующий цикл (~95с).
// 2 быстрых повтора при __amError убирают эту задержку прозрачно. НЕ ретраим Graph-error
// в теле (токен 190) — это не блип (идёт прежним путём → authExpired → re-sniff).
const AM_TABULAR_RETRY_DELAYS_MS = [300, 800];

// am_tabular с курсорной пагинацией (limit 5000 → обычно одна итерация; цикл-бэкстоп 20).
async function fetchAllAmTabular(
  page: Page,
  ctx: GraphContext,
  filtering: Filter[],
  datePreset: string,
  execution: AmFetchExecution = {},
): Promise<{ rows: AmRow[]; error?: string; authExpired?: boolean; loginRequired?: boolean }> {
  const rows: AmRow[] = [];
  let after: string | undefined;
  for (let i = 0; i < 20; i++) {
    const body = await retryTransient(
      () => fetchJson(page, amTabularUrl(ctx, filtering, datePreset, after), execution),
      {
        delaysMs: AM_TABULAR_RETRY_DELAYS_MS,
        isTransient: (b) => Boolean((b as Record<string, unknown>)?.__amError),
      },
    );
    // Разлогин/чекпоинт (money-критично): fetch увёл на login.php/checkpoint, пришёл HTML
    // вместо JSON, или Graph 190 с login-subcode. Это НЕ транзиент — re-sniff токена не
    // поможет (сессия протухла), нужен ре-логин Vision-профиля. Отдаём отдельным флагом,
    // чтобы observer поднял явный алерт вместо тихого «пустого скана».
    if (isLoginRequiredResponse(body)) {
      return {
        rows,
        error: `am_tabular: login_required (${body.status ?? body.error ?? ''})`,
        loginRequired: true,
      };
    }
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
  extraParams?: Record<string, string>,
  execution: AmFetchExecution = {},
): Promise<{ items: LightMeta[]; error?: string; loginRequired?: boolean }> {
  const out: LightMeta[] = [];
  let after: string | undefined;
  for (let i = 0; i < 20; i++) {
    const body = await fetchJson(
      page,
      edgeUrl(ctx, origin, edge, fields, filtering, after, extraParams),
      execution,
    );
    // Разлогин/чекпоинт на edge-запросе (имена/иерархия) — тот же money-критичный сигнал.
    if (isLoginRequiredResponse(body)) {
      return {
        items: out,
        error: `${edge}: login_required (${body.status ?? body.error ?? ''})`,
        loginRequired: true,
      };
    }
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

// Лучший постер из video.thumbnails: предпочитаем is_preferred, иначе самый широкий кадр.
// Чистая функция (экспорт для unit-теста) — вход = массив node.thumbnails.data.
export function pickPreferredThumb(data: unknown): string | null {
  if (!Array.isArray(data) || data.length === 0) return null;
  const items = data as Array<Record<string, unknown>>;
  const preferred = items.find((t) => t?.is_preferred && t?.uri);
  if (preferred?.uri) return String(preferred.uri);
  let best: Record<string, unknown> | null = null;
  for (const t of items) {
    if (!t?.uri) continue;
    if (!best || Number(t.width ?? 0) > Number(best.width ?? 0)) best = t;
  }
  return best?.uri ? String(best.uri) : null;
}

// Полноразмерные постеры видео по video_id: batch GET /?ids=...&fields=thumbnails.
// Best-effort: ошибки чанка проглатываем (оставляем thumbnail_url как было).
async function fetchVideoPosters(
  page: Page,
  ctx: GraphContext,
  videoIds: string[],
  execution: AmFetchExecution = {},
): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  const CHUNK = 50; // держим URL короче лимита
  for (let i = 0; i < videoIds.length; i += CHUNK) {
    const ids = videoIds.slice(i, i + CHUNK);
    const qs = new URLSearchParams();
    qs.set('access_token', ctx.accessToken);
    qs.set('ids', ids.join(','));
    qs.set('fields', 'thumbnails{uri,is_preferred,width,height}');
    const url = `${GRAPH_REST_ORIGIN}/${ctx.apiVersion}/?${qs.toString()}`;
    const body = await fetchJson(page, url, execution);
    if (body?.__amError || body?.error) continue; // best-effort: чанк пропускаем
    for (const [vid, node] of Object.entries(body as Record<string, any>)) {
      if (vid.startsWith('__')) continue; // служебные ключи (__fb_trace_id__ и т.п.)
      const uri = pickPreferredThumb(node?.thumbnails?.data);
      if (uri) out.set(vid, uri);
    }
  }
  return out;
}

// Для видео-ад без image_url дотягиваем полноразмерный кадр из video node (in-place).
// Возвращает число обогащённых ад'ов. Никогда не бросает — превью не money-критично.
async function enrichVideoPosters(
  page: Page,
  ctx: GraphContext,
  items: LightMeta[],
  execution: AmFetchExecution = {},
): Promise<number> {
  const need = items.filter((a) => !a.creativeImageUrl && a.videoId);
  if (!need.length) return 0;
  const videoIds = [...new Set(need.map((a) => a.videoId as string))];
  let posters: Map<string, string>;
  try {
    posters = await fetchVideoPosters(page, ctx, videoIds, execution);
  } catch (e) {
    console.warn(`[am] video poster fetch упал (best-effort, оставляю thumbnail): ${String(e)}`);
    return 0;
  }
  let n = 0;
  for (const a of need) {
    const uri = a.videoId ? posters.get(a.videoId) : undefined;
    if (uri) {
      a.creativeImageUrl = uri;
      n += 1;
    }
  }
  return n;
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
    // Разлогин/чекпоинт Facebook: сессия протухла (login.php/checkpoint/HTML/190-login).
    // Money-критично: re-sniff НЕ помогает — нужен ре-логин Vision-профиля. Caller
    // (index.ts) отдаёт это в scan-ответе как empty_reason='login_required'.
    loginRequired?: boolean;
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

// Live-список кампаний владельца (id+name) по owner_tag — ТОЛЬКО campaigns edge, без
// метрик/ад'ов и БЕЗ allowlist-фильтра. Для UI «Кампании для сканирования»: показывает
// все кампании владельца, включая новые (которые обычный скан с allowlist не подхватывал).
export async function listOwnerCampaigns(
  page: Page,
  ownerTag: string,
  sessionId: string,
  adAccountId: string,
  signal?: AbortSignal,
): Promise<Array<{ id: string; name: string }>> {
  const operationId = `list-campaigns:${sessionId}:${adAccountId}:${randomUUID()}`;
  const abortBinding = bindAbortSignalToPage(page, operationId, signal);
  try {
    const { ctx } = await acquireGraphContext(page, sessionId, {
      expectedActId: adAccountId,
      signal,
    });
    const campRes = await fetchAllEdge(
      page,
      ctx,
      GRAPH_REST_ORIGIN,
      'campaigns',
      ['id', 'name'],
      [],
      undefined,
      { signal, operationId },
    );
    const items = ownerTag
      ? campRes.items.filter((c) => campaignMatchesOwner(c.name ?? '', ownerTag))
      : campRes.items;
    return items.map((c) => ({ id: c.id, name: c.name ?? '' }));
  } finally {
    abortBinding.dispose();
    await clearInPageFetchOperation(page, operationId);
  }
}

// Полный am-скан с самостоятельным извлечением токена (для standalone-вызовов/тестов).
export interface AmScanExecutionOptions {
  signal?: AbortSignal;
  operationId?: string;
}

export async function runAmScan(
  page: Page,
  config: AmScanConfig,
  options: AmScanExecutionOptions = {},
): Promise<AmScanResult> {
  const ctx = await extractGraphContext(page, 15000, options.signal);
  return runAmScanWithContext(page, ctx, config, options);
}

// am-скан с уже извлечённым GraphContext: метрики (am_tabular) + имена/статус (light_*) → ScannedAdRow[].
// runScanCycle сниффит токен во время reload и передаёт ctx сюда.
export async function runAmScanWithContext(
  page: Page,
  ctx: GraphContext,
  config: AmScanConfig,
  options: AmScanExecutionOptions = {},
): Promise<AmScanResult> {
  const operationId = options.operationId ?? `am-scan:${randomUUID()}`;
  const execution: AmFetchExecution = { signal: options.signal, operationId };
  const abortBinding = bindAbortSignalToPage(page, operationId, options.signal);
  try {
    return await runAmScanWithContextInternal(page, ctx, config, execution);
  } finally {
    abortBinding.dispose();
    await clearInPageFetchOperation(page, operationId);
  }
}

async function runAmScanWithContextInternal(
  page: Page,
  ctx: GraphContext,
  config: AmScanConfig,
  execution: AmFetchExecution,
): Promise<AmScanResult> {
  // 0) Кампании (id+name) — ПЕРВЫМИ: нужны для резолва owner_tag → campaign.id (#3, вариант 3),
  //    чтобы am тянул сразу только свой скоуп, а не весь общий кабинет.
  const campRes = await fetchAllEdge(
    page,
    ctx,
    GRAPH_REST_ORIGIN,
    'campaigns',
    ['id', 'name'],
    [],
    undefined,
    execution,
  );

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
  const {
    rows: amRows,
    error: amError,
    authExpired,
    loginRequired: amLoginRequired,
  } = await fetchAllAmTabular(
    page,
    ctx,
    buildFiltering({ campaignIds }),
    config.datePreset,
    execution,
  );
  const merged = mergeAmRows(amRows);

  // 2) Имена/статус ад'ов + adsets — тоже в скоупе (тянем только своё, не весь кабинет).
  const adsRes = await fetchAllEdge(
    page,
    ctx,
    GRAPH_REST_ORIGIN,
    'ads',
    [
      'id',
      'name',
      'effective_status',
      'ad_review_feedback',
      'issues_info',
      'campaign_id',
      'adset_id',
      // Канонная форма: модификатор thumbnail_width на edge creative невалиден
      // (Graph молча опускал поле). Размер thumbnail_url задаём top-level param ниже.
      // object_story_spec.video_data.image_url — постер видео-крео, если был загружен
      // (часто пуст: тогда полноразмерный кадр тянем из video node по video_id, ниже).
      'creative{id,thumbnail_url,image_url,video_id,object_story_spec{video_data{image_url,video_id}}}',
    ],
    scopeFilter,
    // thumbnail_url по умолчанию ~64px → блюр в крупной карточке. 400px — чёткое
    // превью в drawer (для видео-крео, где image_url пуст). В таблице thumb мелкий.
    { thumbnail_width: '400', thumbnail_height: '400' },
    execution,
  );
  const adsetRes = await fetchAllEdge(
    page,
    ctx,
    GRAPH_REST_ORIGIN,
    'adsets',
    ['id', 'name', 'promoted_object{pixel_id}', 'daily_budget', 'lifetime_budget', 'budget_remaining', 'learning_stage_info'],
    scopeFilter,
    undefined,
    execution,
  );

  // Видео-ад без image_url: дотянуть полноразмерный постер из video node (best-effort,
  // in-place правит adsRes.items[].creativeImageUrl). video_data.image_url у видео-крео
  // обычно пуст — единственный полноразмерный кадр живёт в thumbnails видео-объекта.
  const postersResolved = await enrichVideoPosters(page, ctx, adsRes.items, execution);

  const campName = new Map(campRes.items.map((c) => [c.id, c.name ?? '']));
  // Полная карта адсетов с расширенными полями (пиксель/бюджеты/learning).
  const adsetMeta = new Map(adsetRes.items.map((a) => [a.id, a]));

  // Волна 1 диагностика: сколько новых полей реально пришло из Graph (после parseLightList).
  // Локализует разрыв: 0 здесь = Graph/парс, >0 здесь но NULL в БД = downstream (proto/writers).
  const wv1Creative = adsRes.items.filter((a) => a.creativeThumbUrl).length;
  const wv1Image = adsRes.items.filter((a) => a.creativeImageUrl).length;
  const wv1Pixel = adsetRes.items.filter((a) => a.pixelId).length;
  const wv1Budget = adsetRes.items.filter((a) => a.dailyBudget || a.lifetimeBudget).length;
  const wv1Learning = adsetRes.items.filter((a) => a.learningStage).length;
  console.log(
    `[scan][am][wave1] creative=${wv1Creative}/${adsRes.items.length} ` +
      `image=${wv1Image} (video_posters+${postersResolved}) ` +
      `pixel=${wv1Pixel} budget=${wv1Budget} learning=${wv1Learning}/${adsetRes.items.length}`,
  );

  let namesResolved = 0;
  let statusResolved = 0;
  const adMeta = new Map<string, AmAdMeta>();
  for (const ad of adsRes.items) {
    if (ad.name) namesResolved += 1;
    if (ad.effectiveStatus) statusResolved += 1;
    const asMeta = ad.adsetId ? adsetMeta.get(ad.adsetId) : undefined;
    adMeta.set(ad.id, {
      adName: ad.name,
      effectiveStatus: ad.effectiveStatus,
      moderationReason: ad.moderationReason,
      campaignId: ad.campaignId,
      adsetId: ad.adsetId,
      campaignName: ad.campaignId ? campName.get(ad.campaignId) : undefined,
      adsetName: asMeta?.name,
      // Поля из ad (крео, превью):
      creativeThumbUrl: ad.creativeThumbUrl,
      creativeImageUrl: ad.creativeImageUrl,
      // Поля из адсета (пиксель/бюджеты/learning):
      pixelId: asMeta?.pixelId,
      dailyBudget: asMeta?.dailyBudget,
      lifetimeBudget: asMeta?.lifetimeBudget,
      budgetRemaining: asMeta?.budgetRemaining,
      learningStage: asMeta?.learningStage,
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

  // Разлогин на любом из запросов цикла (метрики или edge имён/иерархии) → единый флаг.
  const loginRequired = Boolean(
    amLoginRequired || campRes.loginRequired || adsRes.loginRequired || adsetRes.loginRequired,
  );

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
      loginRequired,
      adsEdgeOnly: adsEdgeOnly.length,
      adsEdgeOnlySample: adsEdgeOnly.slice(0, 12),
      metricsOnly: metricsOnly.length,
      campaigns,
      scopeCampaignCount: campaignIds.length,
      ownerResolved,
    },
  };
}
