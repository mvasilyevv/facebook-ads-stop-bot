import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createServer, type Server } from 'node:http';
import test from 'node:test';
import { chromium } from 'playwright';

import { createReconnectBrowserHandler } from './index.js';
import { SessionManager } from './session-manager.js';
import { VisionClient } from './vision-client.js';

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === 'string') {
    throw new Error('test HTTP server did not expose a TCP address');
  }
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: Server): Promise<void> {
  server.closeAllConnections?.();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

test('ordinary reconnect ignores attacker endpoint fields and uses registered credentials', async () => {
  const trustedTokens: string[] = [];
  let attackerRequests = 0;
  const trustedServer = createServer((request, response) => {
    trustedTokens.push(String(request.headers['x-token'] || ''));
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      profiles: [{
        folder_id: 'folder-1',
        profile_id: 'profile-1',
        port: 6001,
      }],
    }));
  });
  const attackerServer = createServer((_request, response) => {
    attackerRequests += 1;
    response.writeHead(500);
    response.end();
  });
  const trustedUrl = await listen(trustedServer);
  const attackerUrl = await listen(attackerServer);

  const manager = new SessionManager();
  const oldBrowser = {
    contexts: () => [],
    removeAllListeners: () => undefined,
  };
  const adsPage = {
    isClosed: () => false,
    url: () => 'https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=1',
  };
  const reconnectedBrowser = {
    contexts: () => [{
      addInitScript: async () => undefined,
      pages: () => [adsPage],
    }],
    removeAllListeners: () => undefined,
  };
  const session = {
    id: 'session-registered',
    visionApiUrl: trustedUrl,
    visionXToken: 'registered-vision-secret',
    visionProfileId: 'profile-1',
    visionFolderId: 'folder-1',
    cdpPort: 4555,
    playwright: chromium,
    browser: oldBrowser,
    primaryPage: null,
    scanPages: new Map(),
    controlPages: new Map(),
    interactivePages: new Map(),
    humanProfile: {},
    connectedAt: new Date('2026-01-01T00:00:00.000Z'),
    status: 'disconnected',
  };
  (manager as any).sessions.set(session.id, session);

  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;
  VisionClient.prototype.waitUntilCdpReady = async () => true;
  (chromium as any).connectOverCDP = async () => reconnectedBrowser;

  try {
    const handler = createReconnectBrowserHandler(manager);
    const call = new EventEmitter() as any;
    // An older protobuf client can still put the now-reserved fields on the
    // wire. The handler must ignore them even when the empty token used to
    // trigger fallback to the stored secret.
    call.request = {
      session_id: session.id,
      vision_x_token: '',
      vision_api_url: attackerUrl,
      vision_profile_id: 'profile-attacker',
    };
    call.getDeadline = () => Infinity;
    const response = await new Promise<any>((resolve, reject) => {
      void handler(call, (error: any, value: any) => {
        if (error) reject(error);
        else resolve(value);
      });
    });

    assert.equal(response.session_id, session.id);
    assert.equal(response.profile.profile_id, 'profile-1');
    assert.equal(attackerRequests, 0);
    assert.deepEqual(trustedTokens, ['registered-vision-secret']);
    assert.equal(session.visionApiUrl, trustedUrl);
    assert.equal(session.visionXToken, 'registered-vision-secret');
    assert.equal(session.visionProfileId, 'profile-1');
  } finally {
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
    await Promise.all([close(trustedServer), close(attackerServer)]);
  }
});
