const AUTHORITY_TIMEOUT_MS = 2_000;

export class MaintenanceCapabilityConsumeDeniedError extends Error {
  constructor() {
    super('Browser maintenance capability consume was denied');
    this.name = 'MaintenanceCapabilityConsumeDeniedError';
  }
}

export class MaintenanceCapabilityAuthorityUnavailableError extends Error {
  constructor(options?: ErrorOptions) {
    super('Browser maintenance capability authority is unavailable', options);
    this.name = 'MaintenanceCapabilityAuthorityUnavailableError';
  }
}

export interface MaintenanceAuthorityClientOptions {
  endpoint?: string;
  token?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

function isSafeAuthorityEndpoint(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
    );
  } catch {
    return false;
  }
}

/**
 * Atomically consume one recovery grant against the active PostgreSQL-backed
 * maintenance owner. A local signature is necessary but never sufficient:
 * only a committed 204 response permits Vision lifecycle mutation.
 */
export async function consumeMaintenanceCapability(
  request: Record<string, unknown>,
  options: MaintenanceAuthorityClientOptions = {},
): Promise<void> {
  const endpoint = (
    options.endpoint
    ?? process.env.BROWSER_MAINTENANCE_CONSUME_URL
    ?? ''
  ).trim();
  const token = options.token
    ?? process.env.BROWSER_AUTHORITY_CONSUMER_TOKEN
    ?? '';
  if (!isSafeAuthorityEndpoint(endpoint) || token.length < 48) {
    throw new MaintenanceCapabilityAuthorityUnavailableError();
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
      redirect: 'error',
      headers: {
        'Content-Type': 'application/json',
        'X-Browser-Authority-Token': token,
      },
      body: JSON.stringify({
        rpc: 'recover_browser_profile',
        vision_profile_id: String(request.vision_profile_id || ''),
        maintenance_owner: String(request.maintenance_owner || ''),
        capability_expires_at: Number(request.capability_expires_at),
        capability_nonce: String(request.capability_nonce || ''),
      }),
      signal: controller.signal,
    });
    if (response.status === 204) return;
    if (response.status === 401 || response.status === 409 || response.status === 422) {
      throw new MaintenanceCapabilityConsumeDeniedError();
    }
    throw new MaintenanceCapabilityAuthorityUnavailableError();
  } catch (error) {
    if (
      error instanceof MaintenanceCapabilityConsumeDeniedError
      || error instanceof MaintenanceCapabilityAuthorityUnavailableError
    ) {
      throw error;
    }
    throw new MaintenanceCapabilityAuthorityUnavailableError({
      cause: error,
    });
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abortFromCaller);
  }
}
