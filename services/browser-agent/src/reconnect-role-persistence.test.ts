import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';

import { findAdsManagerPageByAct, SessionManager } from './session-manager.js';
import { VisionClient } from './vision-client.js';

/**
 * Вкладка, помеченная ролью control до переподключения CDP, не должна
 * усыновляться ролью scan после него.
 *
 * Регрессия: reconnectBrowserWithConfig сбрасывал реестры ролей в пустые Map,
 * из-за чего новый Playwright-прокси той же физической вкладки не попадал в
 * _agentMoneyPages и не числился в controlPages. findAdsManagerPageByAct
 * находил его по URL и отдавал scan.
 */
test('вкладка control после переподключения CDP не усыновляется scan', async () => {
  const manager = new SessionManager();
  const actId = '999888777';
  const targetId = 'target-control-999888777';
  const adsUrl =
    `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${actId}`;

  function makePage(tid: string) {
    return {
      isClosed: () => false,
      url: () => adsUrl,
      context: () => ({
        newCDPSession: async (_page: unknown) => ({
          send: async (method: string) => {
            if (method === 'Target.getTargetInfo') {
              return { targetInfo: { targetId: tid } };
            }
          },
          detach: async () => {},
        }),
      }),
    };
  }

  const oldPage = makePage(targetId);
  // Новый Playwright-прокси той же физической вкладки (иной объект, тот же targetId)
  const newPage = makePage(targetId);

  const oldBrowser = {
    isConnected: () => true,
    contexts: () => [{ pages: () => [oldPage], addInitScript: async () => {} }],
    removeAllListeners: () => {},
  };

  const newBrowser = {
    isConnected: () => true,
    contexts: () => [{ pages: () => [newPage], addInitScript: async () => {} }],
    removeAllListeners: () => {},
  };

  const session = {
    id: 'test-session-role-persistence',
    visionApiUrl: 'http://127.0.0.1:3030',
    visionXToken: 'token',
    visionProfileId: 'profile-1',
    visionFolderId: 'folder-1',
    cdpPort: 4555,
    playwright: chromium,
    browser: oldBrowser as any,
    primaryPage: null,
    scanPages: new Map<string, any>(),
    controlPages: new Map<string, any>([[actId, oldPage]]),
    interactivePages: new Map<string, any>(),
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
  };
  (manager as any).sessions.set(session.id, session);

  // Имитируем состояние, которое складывается после ensureControlPage:
  // targetId вкладки записан в карту менеджера.
  (manager as any).pageRolesByTargetId.set(targetId, { role: 'control', actId });

  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = (chromium as any).connectOverCDP;

  VisionClient.prototype.getProfile = async () => ({
    folder_id: 'folder-1',
    profile_id: 'profile-1',
    port: 4555,
  });
  VisionClient.prototype.waitUntilCdpReady = async () => true;
  (chromium as any).connectOverCDP = async () => newBrowser;

  try {
    await manager.reconnectBrowser(session.id);

    // После переподключения controlPages должен содержать новый Playwright-прокси
    assert.equal(
      session.controlPages.get(actId),
      newPage,
      'controlPages должен быть восстановлен с новым Page-прокси той же вкладки',
    );

    // scan не должен усыновлять вкладку, которая была control-страницей
    assert.equal(
      findAdsManagerPageByAct(newBrowser as any, actId),
      null,
      'findAdsManagerPageByAct не должен возвращать восстановленную control-вкладку',
    );
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});
