import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes } from 'node:crypto';
import {
  validateBrowserCapabilitySecrets,
  verifyMaintenanceCapabilitySignature,
} from './index.js';

const SECRET = 's'.repeat(64);
const NOW = 1_800_000_000;

function signedRequest(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const request: Record<string, unknown> = {
    vision_x_token: 'vision-token',
    vision_api_url: 'http://127.0.0.1:3030',
    vision_profile_id: 'profile-1',
    vision_folder_id: 'folder-1',
    maintenance_owner: 'a'.repeat(32),
    capability_expires_at: NOW + 30,
    capability_nonce: randomBytes(16).toString('hex'),
    ...overrides,
  };
  const payload = [
    'recover_browser_profile/v1',
    String(request.vision_profile_id),
    String(request.maintenance_owner),
    String(request.capability_expires_at),
    String(request.capability_nonce),
    String(request.vision_api_url),
    String(request.vision_folder_id),
    createHash('sha256').update(String(request.vision_x_token)).digest('hex'),
  ].join('\n');
  request.capability_signature = createHmac('sha256', SECRET)
    .update(payload)
    .digest('hex');
  return request;
}

test('maintenance signature verification is stateless; durable authority owns replay', () => {
  const request = signedRequest();

  verifyMaintenanceCapabilitySignature(request, {
    nowSeconds: NOW,
    secret: SECRET,
  });
  assert.doesNotThrow(() => verifyMaintenanceCapabilitySignature(request, {
    nowSeconds: NOW,
    secret: SECRET,
  }));
});

test('maintenance capability rejects expired and unbounded TTLs', () => {
  for (const expiresAt of [NOW, NOW + 36]) {
    const request = signedRequest({ capability_expires_at: expiresAt });
    assert.throws(
      () => verifyMaintenanceCapabilitySignature(request, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /expired or unbounded/,
    );
  }
});

test('maintenance capability binds every recovery credential and endpoint', () => {
  for (const [field, replacement] of [
    ['vision_profile_id', 'profile-attacker'],
    ['vision_folder_id', 'folder-attacker'],
    ['vision_api_url', 'https://attacker.invalid/collect'],
    ['vision_x_token', 'stolen-token-target'],
    ['maintenance_owner', 'b'.repeat(32)],
  ] as const) {
    const request = signedRequest();
    request[field] = replacement;
    assert.throws(
      () => verifyMaintenanceCapabilitySignature(request, {
        nowSeconds: NOW,
        secret: SECRET,
      }),
      /signature is invalid/,
    );
  }
});

test('maintenance capability requires a dedicated server secret', () => {
  assert.throws(
    () => verifyMaintenanceCapabilitySignature(signedRequest(), {
      nowSeconds: NOW,
      secret: '',
    }),
    /secret is unavailable/,
  );
});

test('browser startup requires caller-scoped secrets and durable authority', () => {
  const validEnvironment = {
    BROWSER_MAINTENANCE_CAPABILITY_SECRET: 'm'.repeat(64),
    BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE: 'a'.repeat(64),
    BROWSER_OPERATION_CAPABILITY_SECRET_META_API: 'o'.repeat(64),
    BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR: 'c'.repeat(64),
    BROWSER_AUTHORITY_CONSUMER_TOKEN: 't'.repeat(64),
    BROWSER_AUTHORITY_CONSUME_URL:
      'http://api:8100/api/v1/internal/browser-operations/consume',
    BROWSER_MAINTENANCE_CONSUME_URL:
      'http://api:8100/api/v1/internal/browser-maintenance/consume',
  };
  assert.doesNotThrow(() => validateBrowserCapabilitySecrets(validEnvironment));
  for (const environment of [
    {
      ...validEnvironment,
      BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE: '',
    },
    {
      ...validEnvironment,
      BROWSER_AUTHORITY_CONSUMER_TOKEN: 'short',
    },
    {
      ...validEnvironment,
      BROWSER_OPERATION_CAPABILITY_SECRET_META_API: 'a'.repeat(64),
    },
    {
      ...validEnvironment,
      BROWSER_AUTHORITY_CONSUME_URL: '',
    },
    {
      ...validEnvironment,
      BROWSER_MAINTENANCE_CONSUME_URL: '',
    },
    {
      ...validEnvironment,
      BROWSER_MAINTENANCE_CONSUME_URL:
        validEnvironment.BROWSER_AUTHORITY_CONSUME_URL,
    },
    {
      ...validEnvironment,
      BROWSER_MAINTENANCE_CONSUME_URL:
        'https://api.invalid/consume?credential=must-not-be-in-url',
    },
  ]) {
    assert.throws(
      () => validateBrowserCapabilitySecrets(environment),
      /unavailable|independently scoped/,
    );
  }
});
