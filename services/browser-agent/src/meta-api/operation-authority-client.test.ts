import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { consumeOperationCapability } from './operation-authority-client.js';
import {
  BROWSER_OPERATION_CONTRACT_VERSION,
  type OperationCapabilityBinding,
} from './operation-capability.js';

const TOKEN = 'browser-authority-consumer-' + 't'.repeat(48);
const BINDING: OperationCapabilityBinding = {
  browserContractVersion: BROWSER_OPERATION_CONTRACT_VERSION,
  rpc: 'execute_graph_call',
  operation: `POST:/123|q=${'a'.repeat(64)}|b=${'b'.repeat(64)}`,
  sessionId: 'session-1',
  visionProfileId: 'profile-1',
  adAccountId: '456',
};
const REQUEST = {
  authorized_caller: 'autopause',
  task_id: 1842,
  lease_owner: '2c5114e4-d921-4fc5-9986-18831eb56d5d',
  lease_token: 7,
  capability_expires_at: 1_800_000_030,
  capability_nonce: 'c'.repeat(32),
};

describe('durable browser operation authority client', () => {
  it('sends the exact binding with a header-only credential and accepts 204', async () => {
    let observedUrl = '';
    let observedInit: RequestInit | undefined;
    await consumeOperationCapability(REQUEST, BINDING, {
      endpoint: 'http://api:8100/api/v1/internal/browser-operations/consume',
      token: TOKEN,
      fetchImpl: (async (url: string | URL | Request, init?: RequestInit) => {
        observedUrl = String(url);
        observedInit = init;
        return new Response(null, { status: 204 });
      }) as typeof fetch,
    });

    assert.equal(
      observedUrl,
      'http://api:8100/api/v1/internal/browser-operations/consume',
    );
    assert.ok(!observedUrl.includes(TOKEN));
    assert.equal(
      (observedInit?.headers as Record<string, string>)['X-Browser-Authority-Token'],
      TOKEN,
    );
    assert.deepEqual(JSON.parse(String(observedInit?.body)), {
      browser_contract_version: BROWSER_OPERATION_CONTRACT_VERSION,
      rpc: BINDING.rpc,
      operation: BINDING.operation,
      session_id: BINDING.sessionId,
      vision_profile_id: BINDING.visionProfileId,
      ad_account_id: BINDING.adAccountId,
      authorized_caller: 'autopause',
      task_id: 1842,
      lease_owner: '2c5114e4-d921-4fc5-9986-18831eb56d5d',
      lease_token: 7,
      capability_expires_at: 1_800_000_030,
      capability_nonce: 'c'.repeat(32),
    });
  });

  it('fails closed on replay denial and never exposes response bodies', async () => {
    await assert.rejects(
      consumeOperationCapability(REQUEST, BINDING, {
        endpoint: 'http://api:8100/api/v1/internal/browser-operations/consume',
        token: TOKEN,
        fetchImpl: (async () => new Response('sensitive', {
          status: 409,
        })) as typeof fetch,
      }),
      /consume was denied/,
    );
  });

  it('fails before network when the narrow endpoint credential is absent', async () => {
    let called = false;
    await assert.rejects(
      consumeOperationCapability(REQUEST, BINDING, {
        endpoint: 'http://api:8100/api/v1/internal/browser-operations/consume',
        token: '',
        fetchImpl: (async () => {
          called = true;
          return new Response(null, { status: 204 });
        }) as typeof fetch,
      }),
      /authority is unavailable/,
    );
    assert.equal(called, false);
  });
});
