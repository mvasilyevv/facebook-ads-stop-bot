import * as grpc from '@grpc/grpc-js';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createRecoverBrowserProfileHandler } from './index.js';
import {
  consumeMaintenanceCapability,
  MaintenanceCapabilityAuthorityUnavailableError,
  MaintenanceCapabilityConsumeDeniedError,
} from './maintenance-authority-client.js';

const ENDPOINT =
  'https://app.adpulse.su/api/v1/internal/browser-maintenance/consume';
const TOKEN = 'authority-token-' + 'x'.repeat(48);
const REQUEST = {
  vision_x_token: 'vision-secret-must-not-cross-authority-boundary',
  vision_api_url: 'http://127.0.0.1:3030',
  vision_profile_id: 'profile-1',
  vision_folder_id: 'folder-1',
  maintenance_owner: 'a'.repeat(32),
  capability_expires_at: 1_900_000_000,
  capability_nonce: 'b'.repeat(32),
  capability_signature: 'c'.repeat(64),
};

test('maintenance authority sends only durable consume binding and accepts committed 204', async () => {
  let receivedUrl = '';
  let receivedInit: RequestInit | undefined;
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    receivedUrl = String(url);
    receivedInit = init;
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  await consumeMaintenanceCapability(REQUEST, {
    endpoint: ENDPOINT,
    token: TOKEN,
    fetchImpl,
  });

  assert.equal(receivedUrl, ENDPOINT);
  assert.equal(receivedInit?.method, 'POST');
  assert.equal(receivedInit?.redirect, 'error');
  assert.equal(
    new Headers(receivedInit?.headers).get('X-Browser-Authority-Token'),
    TOKEN,
  );
  const body = JSON.parse(String(receivedInit?.body));
  assert.deepEqual(body, {
    rpc: 'recover_browser_profile',
    vision_profile_id: 'profile-1',
    maintenance_owner: 'a'.repeat(32),
    capability_expires_at: 1_900_000_000,
    capability_nonce: 'b'.repeat(32),
  });
  assert.equal(String(receivedInit?.body).includes('vision-secret'), false);
  assert.equal(String(receivedInit?.body).includes('vision_api_url'), false);
  assert.equal(String(receivedInit?.body).includes('capability_signature'), false);
});

test('maintenance authority fails closed on replay, outage and unsafe endpoint', async () => {
  const deniedFetch = (async () =>
    new Response(null, { status: 409 })) as typeof fetch;
  await assert.rejects(
    consumeMaintenanceCapability(REQUEST, {
      endpoint: ENDPOINT,
      token: TOKEN,
      fetchImpl: deniedFetch,
    }),
    MaintenanceCapabilityConsumeDeniedError,
  );

  const unavailableFetch = (async () =>
    new Response(null, { status: 503 })) as typeof fetch;
  await assert.rejects(
    consumeMaintenanceCapability(REQUEST, {
      endpoint: ENDPOINT,
      token: TOKEN,
      fetchImpl: unavailableFetch,
    }),
    MaintenanceCapabilityAuthorityUnavailableError,
  );

  let fetchCalls = 0;
  await assert.rejects(
    consumeMaintenanceCapability(REQUEST, {
      endpoint: `${ENDPOINT}?token=forbidden`,
      token: TOKEN,
      fetchImpl: (async () => {
        fetchCalls += 1;
        return new Response(null, { status: 204 });
      }) as typeof fetch,
    }),
    MaintenanceCapabilityAuthorityUnavailableError,
  );
  assert.equal(fetchCalls, 0);
});

test('authority outage returns gRPC unavailable before any Vision mutation', async () => {
  let recoverCalls = 0;
  const manager = {
    recoverBrowserProfileUnderMaintenance: async () => {
      recoverCalls += 1;
      throw new Error('Vision mutation must not start');
    },
  };
  const handler = createRecoverBrowserProfileHandler(manager as any, {
    verify: () => undefined,
    consume: async () => {
      throw new MaintenanceCapabilityAuthorityUnavailableError();
    },
  });
  const call = new EventEmitter() as any;
  call.request = {
    ...REQUEST,
    capability_expires_at: Math.floor(Date.now() / 1_000) + 30,
  };
  call.getDeadline = () => Infinity;

  const error = await new Promise<any>((resolve) => {
    void handler(call, (callbackError: any) => resolve(callbackError));
  });

  assert.equal(error.code, grpc.status.UNAVAILABLE);
  assert.equal(recoverCalls, 0);
});
