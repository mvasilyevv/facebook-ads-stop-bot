import type { Page } from 'playwright';

import {
  executeGraphCall,
  type GraphApiCallOptions,
  type GraphApiCallParams,
  type GraphApiCallResult,
} from './client.js';

const MAX_BATCH_TARGETS = 50;

type ExecuteGraph = (
  page: Page,
  params: GraphApiCallParams,
  options?: GraphApiCallOptions,
) => Promise<GraphApiCallResult>;

export interface GraphOwnershipPreflightOptions extends GraphApiCallOptions {
  executeGraph?: ExecuteGraph;
}

function normalizeAccountId(value: unknown): string {
  const normalized = String(value ?? '').replace(/^act_/, '').trim();
  return /^\d+$/.test(normalized) ? normalized : '';
}

function endpointPath(endpoint: string): string {
  const raw = String(endpoint || '');
  if (
    raw !== raw.trim()
    || !/^\/(?:$|(?:act_\d+|\d+)(?:\/[A-Za-z][A-Za-z0-9_]*)?)$/.test(raw)
  ) {
    throw new Error('Browser operation ownership preflight rejected the Graph target');
  }
  return raw.slice(1);
}

function firstEndpointSegment(endpoint: string): string {
  return endpointPath(endpoint).split('/', 1)[0] || '';
}

function requireOwnedAccount(value: unknown, expectedAccountId: string): void {
  const actual = normalizeAccountId(value);
  if (!actual || actual !== expectedAccountId) {
    throw new Error('Browser operation ownership preflight rejected the Meta target');
  }
}

// executeGraphCall никогда не бросает: локальные сбои он пакует в statusCode=0
// с внутренними кодами, а отказ Meta приходит обычным HTTP-статусом. Без
// разделения этих миров причина оборванного preflight неустановима (инцидент
// 19.08.2026: два залива подряд встали посередине с одинаковым сообщением).
const LOCAL_READ_FAILURE_REASONS: ReadonlyMap<number, string> = new Map([
  [-1, 'session_token_absent'],
  [-2, 'channel_unreachable'],
  [-3, 'page_context_lost'],
]);

function graphReadFailed(result: GraphApiCallResult): boolean {
  return result.statusCode < 200 || result.statusCode >= 300 || Boolean(result.error);
}

function readFailureReason(result: GraphApiCallResult): string {
  if (result.statusCode !== 0) {
    return 'meta_refused';
  }
  return LOCAL_READ_FAILURE_REASONS.get(Number(result.error?.code ?? 0))
    ?? 'channel_result_unknown';
}

/**
 * Отказать в preflight с машиночитаемой причиной. Причина едет в gRPC-сообщении
 * и доезжает до last_error задачи; классификация отказа не меняется — это
 * по-прежнему pre-dispatch FAILED_PRECONDITION без побочных эффектов в Meta.
 */
function failGraphRead(result: GraphApiCallResult, subject: string): never {
  const reason = readFailureReason(result);
  const code = Number(result.error?.code ?? 0);
  const type = String(result.error?.type ?? '');
  // Текст сообщения логируется только когда он пришёл от Meta. Сообщения кодов
  // -1/-2/-3 — это локальные тексты исключений страницы, наружу они не идут.
  const metaMessage = reason === 'meta_refused'
    ? ` meta_message=${JSON.stringify(String(result.error?.message ?? ''))}`
    : '';
  console.warn(
    `[meta-api] ownership preflight read failed subject=${subject} reason=${reason}`
    + ` status=${result.statusCode} code=${code} type=${type}${metaMessage}`,
  );
  throw new Error(
    `Browser operation ownership preflight could not read ${subject}`
    + ` (reason=${reason}, status=${result.statusCode}, code=${code})`,
  );
}

function parseObjectOwnership(result: GraphApiCallResult): unknown {
  if (graphReadFailed(result)) {
    failGraphRead(result, 'the Meta target');
  }
  let body: unknown;
  try {
    body = JSON.parse(result.responseJson);
  } catch {
    throw new Error('Browser operation ownership preflight returned invalid JSON');
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new Error('Browser operation ownership preflight returned an invalid object');
  }
  return (body as Record<string, unknown>).account_id;
}

function mutationBatchTargets(batchJson: string): string[] {
  let entries: unknown;
  try {
    entries = JSON.parse(batchJson);
  } catch {
    throw new Error('Browser operation ownership preflight rejected an invalid Graph batch');
  }
  if (
    !Array.isArray(entries)
    || entries.length === 0
    || entries.length > MAX_BATCH_TARGETS
  ) {
    throw new Error('Browser operation ownership preflight rejected an invalid Graph batch');
  }

  const targets: string[] = [];
  for (const rawEntry of entries) {
    if (!rawEntry || typeof rawEntry !== 'object' || Array.isArray(rawEntry)) {
      throw new Error('Browser operation ownership preflight rejected an invalid Graph batch');
    }
    const entry = rawEntry as Record<string, unknown>;
    const keys = Object.keys(entry).sort();
    if (
      keys.length !== 2
      || keys[0] !== 'method'
      || keys[1] !== 'relative_url'
      || !['GET', 'POST'].includes(String(entry.method || '').toUpperCase())
      || typeof entry.relative_url !== 'string'
      || /^(?:https?:)?\/\//i.test(entry.relative_url)
      || entry.relative_url.startsWith('/')
      || /[{}$%\\#]/.test(entry.relative_url)
    ) {
      throw new Error('Browser operation ownership preflight rejected an unknown Graph batch shape');
    }
    if (!/^\d+\?[^?#]+$/.test(entry.relative_url)) {
      throw new Error('Browser operation ownership preflight rejected a non-numeric batch target');
    }
    const target = entry.relative_url.slice(0, entry.relative_url.indexOf('?'));
    targets.push(target);
  }
  return targets;
}

async function assertNumericTargetsOwned(
  page: Page,
  targetIds: string[],
  expectedAccountId: string,
  options: GraphOwnershipPreflightOptions,
): Promise<void> {
  const executeGraph = options.executeGraph ?? executeGraphCall;
  const uniqueTargets = [...new Set(targetIds)];
  const batch = uniqueTargets.map((targetId) => ({
    method: 'GET',
    relative_url: `${targetId}?fields=account_id`,
  }));
  const result = await executeGraph(page, {
    method: 'POST',
    endpoint: '/',
    queryParams: { batch: JSON.stringify(batch) },
    timeoutMs: 10_000,
  }, {
    signal: options.signal,
    operationId: options.operationId
      ? `${options.operationId}:ownership`
      : undefined,
  });
  if (graphReadFailed(result)) {
    failGraphRead(result, 'batch targets');
  }

  let rows: unknown;
  try {
    rows = JSON.parse(result.responseJson);
  } catch {
    throw new Error('Browser operation ownership preflight returned invalid batch JSON');
  }
  if (!Array.isArray(rows) || rows.length !== uniqueTargets.length) {
    throw new Error('Browser operation ownership preflight returned an incomplete batch');
  }
  for (const row of rows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      throw new Error('Browser operation ownership preflight returned an invalid batch row');
    }
    const record = row as Record<string, unknown>;
    if (Number(record.code) < 200 || Number(record.code) >= 300 || typeof record.body !== 'string') {
      throw new Error('Browser operation ownership preflight could not read a batch target');
    }
    let body: unknown;
    try {
      body = JSON.parse(record.body);
    } catch {
      throw new Error('Browser operation ownership preflight returned an invalid batch target');
    }
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      throw new Error('Browser operation ownership preflight returned an invalid batch target');
    }
    requireOwnedAccount((body as Record<string, unknown>).account_id, expectedAccountId);
  }
}

/**
 * Prove that every Graph write/status-read target belongs to the explicitly
 * signed cabinet. No URL/session inference and no cached ownership is accepted.
 */
export async function assertGraphOperationOwnership(
  page: Page,
  params: GraphApiCallParams,
  expectedAccountId: string,
  options: GraphOwnershipPreflightOptions = {},
): Promise<void> {
  const accountId = normalizeAccountId(expectedAccountId);
  if (!accountId) {
    throw new Error('Browser operation ownership preflight requires an exact cabinet');
  }
  const firstSegment = firstEndpointSegment(params.endpoint);
  if (firstSegment.startsWith('act_')) {
    requireOwnedAccount(firstSegment, accountId);
    return;
  }

  if (firstSegment) {
    if (!/^\d+$/.test(firstSegment)) {
      throw new Error('Browser operation ownership preflight rejected the Graph target');
    }
    const executeGraph = options.executeGraph ?? executeGraphCall;
    const result = await executeGraph(page, {
      method: 'GET',
      endpoint: `/${firstSegment}`,
      queryParams: { fields: 'account_id' },
      timeoutMs: 10_000,
    }, {
      signal: options.signal,
      operationId: options.operationId
        ? `${options.operationId}:ownership`
        : undefined,
    });
    requireOwnedAccount(parseObjectOwnership(result), accountId);
    return;
  }

  if (params.method !== 'POST' || Object.keys(params.queryParams).length !== 1) {
    throw new Error('Browser operation ownership preflight rejected the Graph root request');
  }
  const batchJson = params.queryParams.batch;
  if (typeof batchJson !== 'string') {
    throw new Error('Browser operation ownership preflight requires an exact Graph batch');
  }
  await assertNumericTargetsOwned(
    page,
    mutationBatchTargets(batchJson),
    accountId,
    options,
  );
}
