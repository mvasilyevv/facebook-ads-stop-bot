import {
  createHash,
  createHmac,
  timingSafeEqual,
} from 'crypto';

const MAX_TTL_SECONDS_BY_RPC: Record<OperationCapabilityBinding['rpc'], number> = {
  execute_graph_call: 40,
  upload_image: 185,
  upload_video: 185,
};
const ALLOWED_CALLERS = new Set(['autopause', 'meta_api', 'campaign_creator']);
const SECRET_ENV_BY_CALLER: Record<string, string> = {
  autopause: 'BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE',
  meta_api: 'BROWSER_OPERATION_CAPABILITY_SECRET_META_API',
  campaign_creator: 'BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR',
};

function canonicalStringMapDigest(values: Record<string, string | number>): string {
  const entries = Object.entries(values)
    .map(([key, value]) => [String(key), String(value)] as [string, string])
    .sort(([leftKey, leftValue], [rightKey, rightValue]) => {
      if (leftKey < rightKey) return -1;
      if (leftKey > rightKey) return 1;
      if (leftValue < rightValue) return -1;
      if (leftValue > rightValue) return 1;
      return 0;
    });
  return createHash('sha256')
    .update(JSON.stringify(entries), 'utf8')
    .digest('hex');
}

function decodeGraphComponent(value: string, plusAsSpace: boolean): string {
  let decoded = String(value);
  for (let index = 0; index < 5; index += 1) {
    let candidate: string;
    try {
      candidate = decodeURIComponent(
        plusAsSpace ? decoded.replace(/\+/g, ' ') : decoded,
      );
    } catch {
      throw new Error('Graph request method semantics are ambiguous');
    }
    if (candidate === decoded) return decoded;
    decoded = candidate;
  }
  let candidate: string;
  try {
    candidate = decodeURIComponent(
      plusAsSpace ? decoded.replace(/\+/g, ' ') : decoded,
    );
  } catch {
    throw new Error('Graph request method semantics are ambiguous');
  }
  if (candidate !== decoded) {
    throw new Error('Graph request method semantics are ambiguous');
  }
  return decoded;
}

function normalizedGraphParameterName(value: string): string {
  return decodeGraphComponent(value, true).trim().toLowerCase();
}

function rejectBodyMethodKeys(value: unknown): void {
  if (Array.isArray(value)) {
    for (const item of value) rejectBodyMethodKeys(item);
    return;
  }
  if (!value || typeof value !== 'object') return;

  const seen = new Set<string>();
  for (const [rawKey, nestedValue] of Object.entries(
    value as Record<string, unknown>,
  )) {
    const normalizedKey = normalizedGraphParameterName(rawKey);
    if (normalizedKey === 'method') {
      throw new Error('Graph method override is not authorized');
    }
    if (seen.has(normalizedKey)) {
      throw new Error('Graph request method semantics are ambiguous');
    }
    seen.add(normalizedKey);
    rejectBodyMethodKeys(nestedValue);
  }
}

/**
 * Reject Graph transport aliases before classification, capability verification,
 * page selection, or fetch. Query strings belong only in queryParams, and an
 * effective HTTP method may never be supplied as data.
 */
export function assertCanonicalGraphMethodSemantics(
  method: string,
  endpoint: string,
  queryParams: Record<string, string>,
  bodyJson: string,
): void {
  const rawEndpoint = String(endpoint);
  if (rawEndpoint.includes('?') || rawEndpoint.includes('#')) {
    throw new Error('Graph endpoint query/fragment semantics are not authorized');
  }
  const decodedEndpoint = decodeGraphComponent(rawEndpoint, false);
  if (decodedEndpoint.includes('?') || decodedEndpoint.includes('#')) {
    throw new Error('Graph endpoint query/fragment semantics are not authorized');
  }

  const seenQueryKeys = new Set<string>();
  for (const rawKey of Object.keys(queryParams)) {
    const normalizedKey = normalizedGraphParameterName(rawKey);
    if (normalizedKey === 'method') {
      throw new Error('Graph method override is not authorized');
    }
    if (seenQueryKeys.has(normalizedKey)) {
      throw new Error('Graph request method semantics are ambiguous');
    }
    seenQueryKeys.add(normalizedKey);
  }

  if (bodyJson) {
    let parsedBody: unknown;
    try {
      parsedBody = JSON.parse(bodyJson);
    } catch {
      throw new Error('Graph request method semantics are ambiguous');
    }
    rejectBodyMethodKeys(parsedBody);
    if (method.trim().toUpperCase() === 'GET') {
      throw new Error('Graph GET body semantics are not authorized');
    }
  }
}

export function graphOperationBinding(
  method: string,
  endpoint: string,
  queryParams: Record<string, string>,
  bodyJson: string,
): string {
  assertCanonicalGraphMethodSemantics(
    method,
    endpoint,
    queryParams,
    bodyJson,
  );
  const queryDigest = canonicalStringMapDigest(queryParams);
  const bodyDigest = createHash('sha256').update(bodyJson, 'utf8').digest('hex');
  return `${method.trim().toUpperCase()}:${endpoint}|q=${queryDigest}|b=${bodyDigest}`;
}

export function mediaOperationBinding(
  rpc: 'upload_image' | 'upload_video',
  attributes: Record<string, string | number>,
): string {
  return `${rpc}|r=${canonicalStringMapDigest(attributes)}`;
}

export const BROWSER_OPERATION_CONTRACT_VERSION = 5;

export interface OperationCapabilityBinding {
  browserContractVersion: typeof BROWSER_OPERATION_CONTRACT_VERSION;
  rpc: 'execute_graph_call' | 'upload_image' | 'upload_video';
  operation: string;
  sessionId: string;
  visionProfileId: string;
  adAccountId: string;
}

function operationPayload(
  request: Record<string, unknown>,
  binding: OperationCapabilityBinding,
): string {
  return [
    'browser_operation/v2',
    String(binding.browserContractVersion),
    binding.rpc,
    binding.operation,
    binding.sessionId,
    binding.visionProfileId,
    binding.adAccountId,
    String(request.authorized_caller || ''),
    String(request.task_id || ''),
    String(request.lease_owner || ''),
    String(request.lease_token || ''),
    String(request.capability_expires_at || ''),
    String(request.capability_nonce || ''),
  ].join('\n');
}

/**
 * Verify an authorization issued by the caller-specific fenced queue worker.
 *
 * The signature is bound to the exact live session/profile, cabinet, RPC,
 * operation, task and lease. Browser-agent intentionally has no PostgreSQL
 * credentials. Single-consume is enforced by the API/PostgreSQL authority
 * after this local signature check and before any browser operation.
 */
export function verifyOperationCapability(
  request: Record<string, unknown>,
  binding: OperationCapabilityBinding,
  options: { nowSeconds?: number; secret?: string } = {},
): void {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1_000);
  const requestSessionId = String(request.session_id || '').trim();
  const requestProfileId = String(request.vision_profile_id || '').trim();
  const requestActId = String(request.ad_account_id || '').replace(/^act_/, '').trim();
  const caller = String(request.authorized_caller || '').trim();
  const taskId = Number(request.task_id);
  const leaseOwner = String(request.lease_owner || '').trim();
  const leaseToken = Number(request.lease_token);
  const expiresAt = Number(request.capability_expires_at);
  const nonce = String(request.capability_nonce || '').trim();
  const signature = String(request.capability_signature || '').trim();
  const secret = options.secret
    ?? process.env[SECRET_ENV_BY_CALLER[caller] || '']
    ?? '';

  if (binding.browserContractVersion !== BROWSER_OPERATION_CONTRACT_VERSION) {
    throw new Error('Browser operation capability contract is incompatible');
  }
  if (secret.length < 48) {
    throw new Error('Browser operation capability secret is unavailable');
  }
  if (
    !requestSessionId
    || !requestProfileId
    || requestSessionId !== binding.sessionId
    || requestProfileId !== binding.visionProfileId
  ) {
    throw new Error('Browser operation requires exact session/profile identity');
  }
  if (
    !/^\d+$/.test(requestActId)
    || requestActId !== binding.adAccountId
  ) {
    throw new Error('Browser operation capability cabinet binding is invalid');
  }
  if (!ALLOWED_CALLERS.has(caller)) {
    throw new Error('Browser operation caller is not authorized');
  }
  if (!Number.isSafeInteger(taskId) || taskId <= 0) {
    throw new Error('Browser operation task binding is invalid');
  }
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      leaseOwner,
    )
    || !Number.isSafeInteger(leaseToken)
    || leaseToken <= 0
  ) {
    throw new Error('Browser operation lease binding is invalid');
  }
  if (
    !Number.isSafeInteger(expiresAt)
    || expiresAt <= nowSeconds
    || expiresAt > nowSeconds + MAX_TTL_SECONDS_BY_RPC[binding.rpc]
  ) {
    throw new Error('Browser operation capability is expired or unbounded');
  }
  if (!/^[0-9a-f]{32}$/.test(nonce) || !/^[0-9a-f]{64}$/.test(signature)) {
    throw new Error('Browser operation capability is malformed');
  }

  const expected = Buffer.from(
    createHmac('sha256', secret)
      .update(operationPayload(request, binding))
      .digest('hex'),
    'hex',
  );
  const provided = Buffer.from(signature, 'hex');
  if (
    expected.length !== provided.length
    || !timingSafeEqual(expected, provided)
  ) {
    throw new Error('Browser operation capability signature is invalid');
  }
}

/**
 * Re-check only the temporal window of an already-verified grant.
 *
 * The signature is bound to the moment it was issued and does not change with
 * time; only freshness does. This is meant to run a second time, separately
 * from {@link verifyOperationCapability}, at the moment an operation actually
 * starts working — i.e. once it has won the control-page FIFO lock (see
 * page-lock.ts). A short-TTL grant (execute_graph_call: 40s) can be entirely
 * consumed by that queue wait alone; without this second check the grant's
 * clock effectively runs from the moment it was signed, not from the moment
 * work began, and dies silently mid-queue instead of failing with a named,
 * classifiable reason.
 */
export function assertCapabilityStillFresh(
  request: Record<string, unknown>,
  rpc: OperationCapabilityBinding['rpc'],
  options: { nowSeconds?: number } = {},
): void {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1_000);
  const expiresAt = Number(request.capability_expires_at);
  if (
    !Number.isSafeInteger(expiresAt)
    || expiresAt <= nowSeconds
    || expiresAt > nowSeconds + MAX_TTL_SECONDS_BY_RPC[rpc]
  ) {
    throw new Error('Browser operation capability is expired or unbounded');
  }
}
