import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac, randomBytes } from 'node:crypto';

import {
  assertCanonicalGraphMethodSemantics,
  BROWSER_OPERATION_CONTRACT_VERSION,
  graphOperationBinding,
  mediaOperationBinding,
  verifyOperationCapability,
  type OperationCapabilityBinding,
} from './operation-capability.js';

const SECRET = 'o'.repeat(64);
const NOW = 1_800_000_000;
const BINDING: OperationCapabilityBinding = {
  browserContractVersion: BROWSER_OPERATION_CONTRACT_VERSION,
  rpc: 'execute_graph_call',
  operation: graphOperationBinding(
    'POST',
    '/987654321',
    { status: 'PAUSED' },
    '',
  ),
  sessionId: 'session-exact',
  visionProfileId: 'profile-exact',
  adAccountId: '123',
};

function signedRequest(
  requestOverrides: Record<string, unknown> = {},
  binding: OperationCapabilityBinding = BINDING,
): Record<string, unknown> {
  const request: Record<string, unknown> = {
    session_id: binding.sessionId,
    vision_profile_id: binding.visionProfileId,
    ad_account_id: binding.adAccountId,
    authorized_caller: 'autopause',
    task_id: 1842,
    lease_owner: '2c5114e4-d921-4fc5-9986-18831eb56d5d',
    lease_token: 7,
    capability_expires_at: NOW + 30,
    capability_nonce: randomBytes(16).toString('hex'),
    ...requestOverrides,
  };
  const payload = [
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
  request.capability_signature = createHmac('sha256', SECRET)
    .update(payload)
    .digest('hex');
  return request;
}

describe('fenced browser operation capability', () => {
  it('matches the Python canonical request binding vector', () => {
    assert.equal(
      graphOperationBinding(
        'post',
        '/987654321',
        { status: 'PAUSED', batch: '[]' },
        '{"value":1}',
      ),
      'POST:/987654321'
        + '|q=96ba85c6a27e6cfada78a34fd6937ad0132bfd46dfed0469c52a1012b0b601eb'
        + '|b=48208f9428d64634bd8e28ff345bf0eab60d53c18fa2fbdb0b9bc1e84df2b5f6',
    );
    assert.equal(
      mediaOperationBinding('upload_image', {
        filename: 'hero.jpg',
        content_type: 'image/jpeg',
        content_sha256: 'a'.repeat(64),
      }),
      'upload_image'
        + '|r=ef5196641216c0bb73b2dd598de83aeb5483bc079adcd952810923ad6815649f',
    );
  });

  it('rejects query, body, encoded, duplicate and case method overrides', () => {
    const variants: Array<{
      endpoint?: string;
      queryParams?: Record<string, string>;
      bodyJson?: string;
    }> = [
      { queryParams: { method: 'post' } },
      { queryParams: { MeThOd: 'POST' } },
      { queryParams: { '%256dethod': 'post' } },
      { queryParams: { '%25252525256dethod': 'post' } },
      { queryParams: { method: 'get', METHOD: 'post' } },
      { endpoint: '/me?method=post' },
      { endpoint: '/me?method=get&METHOD=post' },
      { endpoint: '/me%253Fmethod%253Dpost' },
      { endpoint: '/me%25252525253Fmethod%25252525253Dpost' },
      { bodyJson: '{"method":"post"}' },
      { bodyJson: '{"\\u006dethod":"post"}' },
      { bodyJson: '{"%256dethod":"post"}' },
      { bodyJson: '{"%25252525256dethod":"post"}' },
      { bodyJson: '{"method":"GET","method":"POST"}' },
      { bodyJson: 'method%3Dpost' },
    ];

    for (const variant of variants) {
      assert.throws(
        () => assertCanonicalGraphMethodSemantics(
          'GET',
          variant.endpoint ?? '/me',
          variant.queryParams ?? {},
          variant.bodyJson ?? '',
        ),
        /method|semantics|canonical|query\/fragment/i,
      );
    }
  });

  it('accepts the exact task/session/profile binding for durable consume', () => {
    const request = signedRequest();

    verifyOperationCapability(request, BINDING, {
      nowSeconds: NOW,
      secret: SECRET,
    });
  });

  it('rejects every task lease, caller and exact browser identity substitution', () => {
    for (const [field, replacement] of [
      ['session_id', 'session-other'],
      ['vision_profile_id', 'profile-other'],
      ['ad_account_id', '456'],
      ['authorized_caller', 'anonymous'],
      ['task_id', 1843],
      ['lease_owner', '335f9958-a213-4ca5-b6df-2a370dd4d80a'],
      ['lease_token', 8],
    ] as const) {
      const request = signedRequest();
      request[field] = replacement;
      assert.throws(
        () => verifyOperationCapability(request, BINDING, {
          nowSeconds: NOW,
          secret: SECRET,
        }),
        /identity|binding|authorized|signature/,
      );
    }
  });

  it('binds the RPC and exact operation', () => {
    const request = signedRequest();
    for (const binding of [
      { ...BINDING, rpc: 'upload_image' as const },
      { ...BINDING, operation: 'POST:/different-target' },
    ]) {
      assert.throws(
        () => verifyOperationCapability(request, binding, {
          nowSeconds: NOW,
          secret: SECRET,
        }),
        /signature is invalid/,
      );
    }
  });

  it('rejects every capability from an incompatible browser contract', () => {
    const request = signedRequest();
    assert.throws(
      () => verifyOperationCapability(request, {
        ...BINDING,
        browserContractVersion: 4 as typeof BROWSER_OPERATION_CONTRACT_VERSION,
      }, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /contract is incompatible/,
    );
  });

  it('rejects a legacy v1 capability signed with the same secret', () => {
    const request = signedRequest();
    const legacyPayload = [
      'browser_operation/v1',
      BINDING.rpc,
      BINDING.operation,
      BINDING.sessionId,
      BINDING.visionProfileId,
      BINDING.adAccountId,
      String(request.authorized_caller),
      String(request.task_id),
      String(request.lease_owner),
      String(request.lease_token),
      String(request.capability_expires_at),
      String(request.capability_nonce),
    ].join('\n');
    request.capability_signature = createHmac('sha256', SECRET)
      .update(legacyPayload)
      .digest('hex');

    assert.throws(
      () => verifyOperationCapability(request, BINDING, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /signature is invalid/,
    );
  });

  it('rejects a signed status request changed from PAUSED to ACTIVE', () => {
    const request = signedRequest();
    const tamperedBinding: OperationCapabilityBinding = {
      ...BINDING,
      operation: graphOperationBinding(
        'POST',
        '/987654321',
        { status: 'ACTIVE' },
        '',
      ),
    };

    assert.throws(
      () => verifyOperationCapability(request, tamperedBinding, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /signature is invalid/,
    );
  });

  it('rejects replacement of targets in a signed Graph batch', () => {
    const originalBatch = JSON.stringify([
      { method: 'POST', relative_url: '111?status=PAUSED' },
      { method: 'POST', relative_url: '222?status=PAUSED' },
    ]);
    const originalBinding: OperationCapabilityBinding = {
      ...BINDING,
      operation: graphOperationBinding(
        'POST',
        '/',
        { batch: originalBatch },
        '',
      ),
    };
    const request = signedRequest({}, originalBinding);
    const tamperedBatch = JSON.stringify([
      { method: 'POST', relative_url: '111?status=PAUSED' },
      { method: 'POST', relative_url: '999?status=ACTIVE' },
    ]);

    assert.throws(
      () => verifyOperationCapability(request, {
        ...originalBinding,
        operation: graphOperationBinding(
          'POST',
          '/',
          { batch: tamperedBatch },
          '',
        ),
      }, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /signature is invalid/,
    );
  });

  it('rejects retargeting signed media bytes or metadata', () => {
    const originalBinding: OperationCapabilityBinding = {
      ...BINDING,
      rpc: 'upload_image',
      operation: mediaOperationBinding('upload_image', {
        filename: 'hero.jpg',
        content_type: 'image/jpeg',
        content_sha256: 'a'.repeat(64),
      }),
    };
    const request = signedRequest({}, originalBinding);
    const tamperedBinding: OperationCapabilityBinding = {
      ...originalBinding,
      operation: mediaOperationBinding('upload_image', {
        filename: 'other.jpg',
        content_type: 'image/jpeg',
        content_sha256: 'b'.repeat(64),
      }),
    };

    assert.throws(
      () => verifyOperationCapability(request, tamperedBinding, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /signature is invalid/,
    );
  });

  it('rejects expired, unbounded and unsigned capabilities', () => {
    for (const expiresAt of [NOW, NOW + 41]) {
      assert.throws(
        () => verifyOperationCapability(
          signedRequest({ capability_expires_at: expiresAt }),
          BINDING,
          { nowSeconds: NOW, secret: SECRET },
        ),
        /expired or unbounded/,
      );
    }
    assert.throws(
      () => verifyOperationCapability(
        signedRequest(),
        BINDING,
        { nowSeconds: NOW, secret: '' },
      ),
      /secret is unavailable/,
    );
  });

  it('allows media TTL to cover the 180 second upload deadline only', () => {
    const uploadBinding: OperationCapabilityBinding = {
      ...BINDING,
      rpc: 'upload_video',
      operation: mediaOperationBinding('upload_video', {
        filename: 'creative.mp4',
        file_size: 5,
        content_sha256: 'c'.repeat(64),
      }),
    };
    verifyOperationCapability(
      signedRequest({ capability_expires_at: NOW + 185 }, uploadBinding),
      uploadBinding,
      { nowSeconds: NOW, secret: SECRET },
    );
    assert.throws(
      () => verifyOperationCapability(
        signedRequest({ capability_expires_at: NOW + 186 }, uploadBinding),
        uploadBinding,
        { nowSeconds: NOW, secret: SECRET },
      ),
      /expired or unbounded/,
    );
  });
});
