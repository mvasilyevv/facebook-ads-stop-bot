import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { findPreferredPrimaryPage, isAdsManagerUrl, SessionManager } from './session-manager.js';
import { VisionClient } from './vision-client.js';

// Проверяем, что helper корректно распознаёт URL Ads Manager.
test('isAdsManagerUrl detects ads manager pages', () => {
  assert.equal(isAdsManagerUrl('https://www.facebook.com/adsmanager/manage/campaigns'), true);
  assert.equal(isAdsManagerUrl('https://www.facebook.com/ads/library/'), true);
  assert.equal(isAdsManagerUrl('https://www.facebook.com/messages/'), false);
});

// Проверяем, что primary page выбирается по вкладке Ads Manager, а не по первой вкладке профиля.
test('findPreferredPrimaryPage prefers ads manager tab over first page', () => {
  const inboxPage = { isClosed: () => false, url: () => 'https://www.facebook.com/messages/' };
  const adsPage = { isClosed: () => false, url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
  const browser = {
    contexts: () => [
      { pages: () => [inboxPage, adsPage] },
    ],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), adsPage);
});

// Проверяем, что при отсутствии Ads Manager helper возвращает первую доступную вкладку.
test('findPreferredPrimaryPage falls back to first available page', () => {
  const firstPage = { isClosed: () => false, url: () => 'https://www.facebook.com/' };
  const browser = {
    contexts: () => [
      { pages: () => [firstPage] },
    ],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), firstPage);
});

// Проверяем, что закрытая вкладка не возвращается как рабочая primaryPage.
test('findPreferredPrimaryPage игнорирует закрытые вкладки', () => {
  const closedAdsPage = { isClosed: () => true, url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
  const openAdsPage = { isClosed: () => false, url: () => 'https://adsmanager.facebook.com/adsmanager/manage/campaigns' };
  const browser = {
    contexts: () => [
      { pages: () => [closedAdsPage, openAdsPage] },
    ],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), openAdsPage);
});

function makeSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'session-1',
    visionApiUrl: 'http://127.0.0.1:3030',
    visionXToken: 'token',
    visionProfileId: 'profile-1',
    visionFolderId: 'folder-1',
    cdpPort: 4555,
    playwright: chromium as any,
    browser: null,
    primaryPage: null,
    humanProfile: {
      speedFactor: 1,
      jitterFactor: 1,
      pauseFactor: 1,
      overshootChance: 0,
      idleChance: 0,
      idleDurationMin: 0,
      idleDurationMax: 0,
      bezierStepsMin: 1,
      bezierStepsMax: 1,
    },
    connectedAt: new Date('2026-01-01T00:00:00.000Z'),
    status: 'connected',
    ...overrides,
  };
}

// Проверяем, что disconnectBrowser рвёт только локальную сессию и не закрывает удалённый профиль.
test('disconnectBrowser clears local references without closing browser', async () => {
  const manager = new SessionManager();
  let closeCalls = 0;
  const session = makeSession({
    browser: {
      close: async () => {
        closeCalls += 1;
      },
      contexts: () => [],
    },
    primaryPage: { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' },
  });

  (manager as any).sessions.set(session.id, session);
  await manager.disconnectBrowser(session.id);

  const stored = (manager as any).sessions.get(session.id);
  assert.equal(closeCalls, 0);
  assert.equal(stored.browser, null);
  assert.equal(stored.primaryPage, null);
  assert.equal(stored.playwright, null);
  assert.equal(stored.status, 'disconnected');
});

// Проверяем, что stopBrowser закрывает браузер и завершает профиль через Vision API.
test('stopBrowser closes browser and stops Vision profile', async () => {
  const manager = new SessionManager();
  let closeCalls = 0;
  const stopCalls: Array<[string, string]> = [];
  const originalStopProfile = VisionClient.prototype.stopProfile;

  VisionClient.prototype.stopProfile = async function stopProfile(folderId: string, profileId: string) {
    stopCalls.push([folderId, profileId]);
  };

  try {
    const session = makeSession({
      browser: {
        close: async () => {
          closeCalls += 1;
        },
        contexts: () => [],
      },
    });
    (manager as any).sessions.set(session.id, session);

    await manager.stopBrowser(session.id);

    assert.equal(closeCalls, 1);
    assert.deepEqual(stopCalls, [['folder-1', 'profile-1']]);
    assert.equal((manager as any).sessions.has(session.id), false);
  } finally {
    VisionClient.prototype.stopProfile = originalStopProfile;
  }
});

// Проверяем, что startBrowser ждёт готовности CDP endpoint перед подключением.
test('startBrowser waits for cdp readiness before connecting', async () => {
  const manager = new SessionManager();
  const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
  const browser = {
    contexts: () => [
      {
        addInitScript: async () => {},
        pages: () => [adsPage],
      },
    ],
  };
  const readyCalls: number[] = [];
  const connectCalls: string[] = [];

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return 'folder-1';
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: 7001 };
  };
  VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady(port: number) {
    readyCalls.push(port);
    return true;
  };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    return browser as any;
  };

  try {
    const session = await manager.startBrowser({
      visionXToken: 'token',
      visionApiUrl: 'http://127.0.0.1:3030',
      visionProfileId: 'profile-1',
    });

    assert.deepEqual(readyCalls, [7001]);
    assert.deepEqual(connectCalls, ['http://127.0.0.1:7001']);
    assert.equal(session.primaryPage, adsPage as any);
    assert.equal(session.cdpPort, 7001);
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

// Проверяем, что профиль без CDP-порта не перезапускается, если recovery явно выключен.
test('startBrowser does not restart missing cdp profile when auto recovery is disabled', async () => {
  const manager = new SessionManager();
  const previousFlag = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
  process.env.VISION_AUTO_RESTART_ON_MISSING_CDP = 'false';

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilProfileHasPort = VisionClient.prototype.waitUntilProfileHasPort;
  const originalRestartProfileToRecoverPort = VisionClient.prototype.restartProfileToRecoverPort;

  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return 'folder-1';
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: null };
  };
  VisionClient.prototype.waitUntilProfileHasPort = async function waitUntilProfileHasPort() {
    return null;
  };
  VisionClient.prototype.restartProfileToRecoverPort = async function restartProfileToRecoverPort() {
    throw new Error('restartProfileToRecoverPort не должен вызываться');
  };

  try {
    await assert.rejects(
      manager.startBrowser({
        visionXToken: 'token',
        visionApiUrl: 'http://127.0.0.1:3030',
        visionProfileId: 'profile-1',
      }),
      /Автоперезапуск профиля для восстановления CDP-порта отключён/,
    );
  } finally {
    if (previousFlag === undefined) {
      delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    } else {
      process.env.VISION_AUTO_RESTART_ON_MISSING_CDP = previousFlag;
    }
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilProfileHasPort = originalWaitUntilProfileHasPort;
    VisionClient.prototype.restartProfileToRecoverPort = originalRestartProfileToRecoverPort;
  }
});

// Проверяем, что профиль без CDP-порта перезапускается по умолчанию.
test('startBrowser restarts missing cdp profile by default', async () => {
  const manager = new SessionManager();
  const previousFlag = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
  delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;

  const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
  const browser = {
    contexts: () => [
      {
        addInitScript: async () => {},
        pages: () => [adsPage],
      },
    ],
  };
  let restartCalls = 0;

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilProfileHasPort = VisionClient.prototype.waitUntilProfileHasPort;
  const originalRestartProfileToRecoverPort = VisionClient.prototype.restartProfileToRecoverPort;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return 'folder-1';
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: null };
  };
  VisionClient.prototype.waitUntilProfileHasPort = async function waitUntilProfileHasPort() {
    return null;
  };
  VisionClient.prototype.restartProfileToRecoverPort = async function restartProfileToRecoverPort() {
    restartCalls += 1;
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: 7101 };
  };
  VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
    return true;
  };
  (chromium as any).connectOverCDP = async () => browser as any;

  try {
    const session = await manager.startBrowser({
      visionXToken: 'token',
      visionApiUrl: 'http://127.0.0.1:3030',
      visionProfileId: 'profile-1',
    });

    assert.equal(restartCalls, 1);
    assert.equal(session.cdpPort, 7101);
    assert.equal(session.primaryPage, adsPage as any);
  } finally {
    if (previousFlag === undefined) {
      delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    } else {
      process.env.VISION_AUTO_RESTART_ON_MISSING_CDP = previousFlag;
    }
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilProfileHasPort = originalWaitUntilProfileHasPort;
    VisionClient.prototype.restartProfileToRecoverPort = originalRestartProfileToRecoverPort;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

// Проверяем, что reconnectBrowser переиспользует живой профиль с CDP-портом без аварийного restart.
test('reconnectBrowser reuses existing profile port without restart', async () => {
  const manager = new SessionManager();
  const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
  const browser = {
    contexts: () => [
      {
        addInitScript: async () => {},
        pages: () => [adsPage],
      },
    ],
  };
  const connectCalls: string[] = [];

  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalRestartProfileToRecoverPort = VisionClient.prototype.restartProfileToRecoverPort;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: 6001 };
  };
  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return 'folder-1';
  };
  VisionClient.prototype.restartProfileToRecoverPort = async function restartProfileToRecoverPort() {
    throw new Error('restartProfileToRecoverPort не должен вызываться');
  };
  VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
    return true;
  };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    return browser as any;
  };

  try {
    const session = makeSession({
      status: 'disconnected',
      browser: null,
      primaryPage: null,
      playwright: null,
      connectedAt: new Date('2025-01-01T00:00:00.000Z'),
    });
    (manager as any).sessions.set(session.id, session);

    const restored = await manager.reconnectBrowser(session.id);

    assert.equal(connectCalls.length, 1);
    assert.equal(connectCalls[0], 'http://127.0.0.1:6001');
    assert.equal(restored.browser, browser as any);
    assert.equal(restored.primaryPage, adsPage as any);
    assert.equal(restored.cdpPort, 6001);
    assert.equal(restored.status, 'connected');
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.restartProfileToRecoverPort = originalRestartProfileToRecoverPort;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

// Проверяем, что reconnectBrowser явно падает, если CDP endpoint так и не стал доступен.
test('reconnectBrowser fails when cdp endpoint never becomes ready', async () => {
  const manager = new SessionManager();
  const connectCalls: string[] = [];

  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: 'folder-1', profile_id: 'profile-1', port: 6001 };
  };
  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return 'folder-1';
  };
  VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
    return false;
  };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    throw new Error('connectOverCDP не должен вызываться');
  };

  try {
    const session = makeSession({
      status: 'disconnected',
      browser: null,
      primaryPage: null,
      playwright: null,
    });
    (manager as any).sessions.set(session.id, session);

    await assert.rejects(
      manager.reconnectBrowser(session.id),
      /CDP endpoint профиля profile-1 на порту 6001 не стал доступен/,
    );
    assert.deepEqual(connectCalls, []);
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});
