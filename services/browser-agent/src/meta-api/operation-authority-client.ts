import type { OperationCapabilityBinding } from './operation-capability.js';

const AUTHORITY_TIMEOUT_MS = 2_000;

export interface OperationAuthorityClientOptions {
  endpoint?: string;
  token?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

/**
 * Atomically consume the PostgreSQL-issued grant through the narrow API edge.
 *
 * The endpoint receives no signer key. A 204 response is the durable external
 * boundary; every other result fails closed and Meta/browser work must not run.
 */
export async function consumeOperationCapability(
  request: Record<string, unknown>,
  binding: OperationCapabilityBinding,
  options: OperationAuthorityClientOptions = {},
): Promise<void> {
  const endpoint = (
    options.endpoint
    ?? process.env.BROWSER_AUTHORITY_CONSUME_URL
    ?? ''
  ).trim();
  const token = options.token
    ?? process.env.BROWSER_AUTHORITY_CONSUMER_TOKEN
    ?? '';
  if (!/^https?:\/\//.test(endpoint) || token.length < 48) {
    throw new Error('Browser operation capability authority is unavailable');
  }

  const controller = new AbortController();
  const abortFromCaller = (): void => controller.abort(options.signal?.reason);
  options.signal?.addEventListener('abort', abortFromCaller, { once: true });
  if (options.signal?.aborted) abortFromCaller();
  const timeout = setTimeout(
    () => controller.abort('authority_timeout'),
    AUTHORITY_TIMEOUT_MS,
  );
  try {
    const response = await (options.fetchImpl ?? fetch)(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Browser-Authority-Token': token,
      },
      body: JSON.stringify({
        browser_contract_version: binding.browserContractVersion,
        rpc: binding.rpc,
        operation: binding.operation,
        session_id: binding.sessionId,
        vision_profile_id: binding.visionProfileId,
        ad_account_id: binding.adAccountId,
        authorized_caller: String(request.authorized_caller || ''),
        task_id: Number(request.task_id),
        lease_owner: String(request.lease_owner || ''),
        lease_token: Number(request.lease_token),
        capability_expires_at: Number(request.capability_expires_at),
        capability_nonce: String(request.capability_nonce || ''),
      }),
      signal: controller.signal,
    });
    if (response.status === 204) return;
    if (response.status === 409 || response.status === 401) {
      throw new Error('Browser operation capability consume was denied');
    }
    throw new Error('Browser operation capability authority is unavailable');
  } catch (error: any) {
    if (String(error?.message || '').includes('Browser operation capability')) {
      throw error;
    }
    throw new Error('Browser operation capability authority is unavailable', {
      cause: error,
    });
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abortFromCaller);
  }
}
