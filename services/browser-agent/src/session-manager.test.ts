import test from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import {
  adsManagerUrlForAct,
  canonicalAdsManagerUrl,
  extractAdAccountId,
  findAdsManagerPageByAct,
  findLiveAdsManagerPage,
  findPreferredPrimaryPage,
  isAdsManagerUrl,
  rememberAdsManagerUrl,
  safeCabinetTabError,
  SessionManager,
} from "./session-manager.js";
import { VisionClient } from "./vision-client.js";

// Проверяем, что helper корректно распознаёт URL Ads Manager.
test("isAdsManagerUrl detects ads manager pages", () => {
  assert.equal(
    isAdsManagerUrl("https://www.facebook.com/adsmanager/manage/campaigns"),
    true,
  );
  assert.equal(
    isAdsManagerUrl(
      "https://adsmanager.facebook.com/adsmanager/manage/ads?act=123",
    ),
    true,
  );
  assert.equal(
    isAdsManagerUrl(
      "https://business.facebook.com/adsmanager/manage/campaigns",
    ),
    true,
  );
  assert.equal(isAdsManagerUrl("https://www.facebook.com/ads/library/"), false);
  assert.equal(isAdsManagerUrl("https://www.facebook.com/messages/"), false);
  assert.equal(
    isAdsManagerUrl("https://adsmanager.facebook.com/login/?act=123"),
    false,
  );
  assert.equal(
    isAdsManagerUrl("https://adsmanager.facebook.com/?act=123"),
    false,
  );
  assert.equal(
    isAdsManagerUrl("http://adsmanager.facebook.com/adsmanager/manage?act=123"),
    false,
  );
  assert.equal(
    isAdsManagerUrl(
      "https://user:password@adsmanager.facebook.com/adsmanager/manage?act=123",
    ),
    false,
  );
  assert.equal(
    isAdsManagerUrl(
      "https://adsmanager.facebook.com/business/adsmanager/manage?act=123",
    ),
    false,
  );
  assert.equal(
    isAdsManagerUrl(
      "https://business.facebook.com/business/loginpage/?next=https%3A%2F%2Fadsmanager.facebook.com%2Fadsmanager%2Fmanage%2Fads",
    ),
    false,
  );
});

test("extractAdAccountId requires one 1..32 digit top-level act query value", () => {
  const base = "https://adsmanager.facebook.com/adsmanager/manage/campaigns";
  assert.equal(extractAdAccountId(`${base}?act=1`), "1");
  assert.equal(
    extractAdAccountId(`${base}?act=${"9".repeat(32)}`),
    "9".repeat(32),
  );
  assert.equal(extractAdAccountId(`${base}?act=111&act=222`), null);
  assert.equal(extractAdAccountId(`${base}?act=${"9".repeat(33)}`), null);
  assert.equal(extractAdAccountId(`${base}?act=act_123`), null);
  assert.equal(extractAdAccountId(`${base}#?act=123`), null);
});

test("OpenCabinetTabs error formatter preserves only stable cabinet reasons", () => {
  assert.equal(
    safeCabinetTabError(
      new Error("cabinet_not_found: navigation failed for act=123"),
    ),
    "cabinet_not_found: navigation failed for act=123",
  );
  assert.equal(
    safeCabinetTabError(
      new Error(
        "cabinet_not_confirmed: final Ads Manager URL does not confirm act=123",
      ),
    ),
    "cabinet_not_confirmed: final Ads Manager URL does not confirm act=123",
  );
  assert.equal(
    safeCabinetTabError(new Error("net::ERR_FAILED ?access_token=secret")),
    "cabinet_not_confirmed: cabinet tab unavailable",
  );
});

test("findPreferredPrimaryPage ignores login redirect containing adsmanager in query", () => {
  const loginPage = {
    isClosed: () => false,
    url: () =>
      "https://business.facebook.com/business/loginpage/?next=https%3A%2F%2Fadsmanager.facebook.com%2Fadsmanager",
  };
  const adsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=123",
  };
  const browser = {
    contexts: () => [{ pages: () => [loginPage, adsPage] }],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), adsPage);
});

// Проверяем, что primary page выбирается по вкладке Ads Manager, а не по первой вкладке профиля.
test("findPreferredPrimaryPage prefers ads manager tab over first page", () => {
  const inboxPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const adsPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
  const browser = {
    contexts: () => [{ pages: () => [inboxPage, adsPage] }],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), adsPage);
});

// Проверяем, что при отсутствии Ads Manager helper возвращает первую доступную вкладку.
test("findPreferredPrimaryPage falls back to first available page", () => {
  const firstPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/",
  };
  const browser = {
    contexts: () => [{ pages: () => [firstPage] }],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), firstPage);
});

// Проверяем, что закрытая вкладка не возвращается как рабочая primaryPage.
test("findPreferredPrimaryPage игнорирует закрытые вкладки", () => {
  const closedAdsPage = {
    isClosed: () => true,
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
  const openAdsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
  };
  const browser = {
    contexts: () => [{ pages: () => [closedAdsPage, openAdsPage] }],
  };

  assert.equal(findPreferredPrimaryPage(browser as any), openAdsPage);
});

function makeSession(overrides: Record<string, unknown> = {}): any {
  return {
    id: "session-1",
    visionApiUrl: "http://127.0.0.1:3030",
    visionXToken: "token",
    visionProfileId: "profile-1",
    visionFolderId: "folder-1",
    cdpPort: 4555,
    playwright: chromium as any,
    browser: null,
    primaryPage: null,
    scanPages: new Map(),
    controlPages: new Map(),
    interactivePages: new Map(),
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
    connectedAt: new Date("2026-01-01T00:00:00.000Z"),
    status: "connected",
    ...overrides,
  };
}

// Проверяем, что startBrowser ждёт готовности CDP endpoint перед подключением.
test("startBrowser waits for cdp readiness before connecting", async () => {
  const manager = new SessionManager();
  const adsPage = {
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
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
    return "folder-1";
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: 7001 };
  };
  VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady(
    port: number,
  ) {
    readyCalls.push(port);
    return true;
  };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    return browser as any;
  };

  try {
    const session = await manager.startBrowser({
      visionXToken: "token",
      visionApiUrl: "http://127.0.0.1:3030",
      visionProfileId: "profile-1",
    });

    assert.deepEqual(readyCalls, [7001]);
    assert.deepEqual(connectCalls, ["http://127.0.0.1:7001"]);
    assert.equal(session.primaryPage, adsPage as any);
    assert.equal(session.cdpPort, 7001);
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

// H-6 (BA-2): reconnectBrowser отвязывает СТАРЫЙ CDP-клиент (removeAllListeners),
// но НЕ закрывает его — browser.close() для connectOverCDP убил бы удалённый
// Vision-профиль, к которому мы только что переподключились. Защита от утечки
// ws-соединений/listeners при recovery-штормах.
test("reconnectBrowser detaches old CDP client without closing it (H-6)", async () => {
  const manager = new SessionManager();
  const adsPage = {
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
  const mkBrowser = (tag: { removeAll: number; close: number }) => ({
    contexts: () => [{ addInitScript: async () => {}, pages: () => [adsPage] }],
    removeAllListeners: () => {
      tag.removeAll += 1;
    },
    close: async () => {
      tag.close += 1;
    },
  });
  const oldTag = { removeAll: 0, close: 0 };
  const newTag = { removeAll: 0, close: 0 };
  const oldBrowser = mkBrowser(oldTag);
  const newBrowser = mkBrowser(newTag);

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  let connectCalls = 0;
  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return "folder-1";
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: 7001 };
  };
  VisionClient.prototype.waitUntilCdpReady =
    async function waitUntilCdpReady() {
      return true;
    };
  (chromium as any).connectOverCDP = async () => {
    connectCalls += 1;
    return (connectCalls === 1 ? oldBrowser : newBrowser) as any;
  };

  try {
    const session = await manager.startBrowser({
      visionXToken: "token",
      visionApiUrl: "http://127.0.0.1:3030",
      visionProfileId: "profile-1",
    });
    assert.equal(
      session.browser as any,
      oldBrowser,
      "startBrowser подключил старый browser",
    );

    await manager.reconnectBrowser(session.id);

    assert.equal(
      session.browser as any,
      newBrowser,
      "после reconnect — новый browser",
    );
    assert.equal(
      oldTag.removeAll,
      1,
      "старый browser отвязан (removeAllListeners вызван)",
    );
    assert.equal(
      oldTag.close,
      0,
      "старый browser НЕ закрыт (close убил бы Vision-профиль)",
    );
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

test("ordinary start never restarts a profile without a CDP port", async () => {
  const manager = new SessionManager();
  let restartCalls = 0;

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilProfileHasPort =
    VisionClient.prototype.waitUntilProfileHasPort;
  const originalRestartProfileToRecoverPort =
    VisionClient.prototype.restartProfileToRecoverPort;

  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return "folder-1";
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: null };
  };
  VisionClient.prototype.waitUntilProfileHasPort =
    async function waitUntilProfileHasPort() {
      return null;
    };
  VisionClient.prototype.restartProfileToRecoverPort =
    async function restartProfileToRecoverPort() {
      restartCalls += 1;
      throw new Error("ordinary start must not restart Vision");
    };

  try {
    await assert.rejects(
      manager.startBrowser({
        visionXToken: "token",
        visionApiUrl: "http://127.0.0.1:3030",
        visionProfileId: "profile-1",
      }),
      /capability-authorized maintenance recovery/,
    );
    assert.equal(restartCalls, 0);
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilProfileHasPort =
      originalWaitUntilProfileHasPort;
    VisionClient.prototype.restartProfileToRecoverPort =
      originalRestartProfileToRecoverPort;
  }
});

test("maintenance recovery restarts a live profile without a local session", async () => {
  const manager = new SessionManager();
  const adsPage = {
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
  const browser = {
    contexts: () => [
      {
        addInitScript: async () => {},
        pages: () => [adsPage],
      },
    ],
  };
  let restartCalls = 0;
  const connectedPorts: string[] = [];

  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalRestartProfileToRecoverPort =
    VisionClient.prototype.restartProfileToRecoverPort;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return "folder-1";
  };
  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: 6001 };
  };
  VisionClient.prototype.restartProfileToRecoverPort =
    async function restartProfileToRecoverPort() {
      restartCalls += 1;
      return { folder_id: "folder-1", profile_id: "profile-1", port: 7101 };
    };
  VisionClient.prototype.waitUntilCdpReady =
    async function waitUntilCdpReady() {
      return true;
    };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectedPorts.push(url);
    return browser as any;
  };

  try {
    const session = await manager.recoverBrowserProfileUnderMaintenance({
      visionXToken: "token",
      visionApiUrl: "http://127.0.0.1:3030",
      visionProfileId: "profile-1",
      visionFolderId: "folder-1",
    });

    assert.equal(restartCalls, 1);
    assert.deepEqual(connectedPorts, ["http://127.0.0.1:7101"]);
    assert.equal(session.cdpPort, 7101);
    assert.equal(session.primaryPage, adsPage as any);
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.restartProfileToRecoverPort =
      originalRestartProfileToRecoverPort;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

test("cancel during start initialization never commits a hidden session", async () => {
  const manager = new SessionManager();
  const controller = new AbortController();
  let initStartedResolve: (() => void) | undefined;
  let releaseInitResolve: (() => void) | undefined;
  const initStarted = new Promise<void>((resolve) => {
    initStartedResolve = resolve;
  });
  const releaseInit = new Promise<void>((resolve) => {
    releaseInitResolve = resolve;
  });
  const adsPage = {
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
  const browser = {
    contexts: () => [
      {
        addInitScript: async () => {
          initStartedResolve?.();
          await releaseInit;
        },
        pages: () => [adsPage],
      },
    ],
  };
  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.resolveFolderId = async () => "folder-1";
  VisionClient.prototype.getProfile = async () => ({
    folder_id: "folder-1",
    profile_id: "profile-1",
    port: 6001,
  });
  VisionClient.prototype.waitUntilCdpReady = async () => true;
  (chromium as any).connectOverCDP = async () => browser as any;

  try {
    const pending = manager.startBrowser({
      visionXToken: "token",
      visionApiUrl: "http://127.0.0.1:3030",
      visionProfileId: "profile-1",
      signal: controller.signal,
    });
    await initStarted;
    controller.abort("test_cancelled");
    releaseInitResolve?.();

    await assert.rejects(pending, /lifecycle operation cancelled/);
    assert.deepEqual(manager.listSessions(), []);
  } finally {
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

test("cancel during reconnect page creation preserves incumbent session", async () => {
  const manager = new SessionManager();
  const controller = new AbortController();
  let newPageStartedResolve: (() => void) | undefined;
  let releaseNewPageResolve: ((page: any) => void) | undefined;
  const newPageStarted = new Promise<void>((resolve) => {
    newPageStartedResolve = resolve;
  });
  const releaseNewPage = new Promise<any>((resolve) => {
    releaseNewPageResolve = resolve;
  });
  const oldBrowser = {
    contexts: () => [],
    removeAllListeners: () => undefined,
  };
  const newBrowser = {
    contexts: () => [
      {
        addInitScript: async () => undefined,
        pages: () => [],
        newPage: async () => {
          newPageStartedResolve?.();
          return releaseNewPage;
        },
      },
    ],
    removeAllListeners: () => undefined,
  };
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.getProfile = async () => ({
    folder_id: "folder-1",
    profile_id: "profile-1",
    port: 6001,
  });
  VisionClient.prototype.waitUntilCdpReady = async () => true;
  (chromium as any).connectOverCDP = async () => newBrowser as any;

  try {
    const session = makeSession({
      browser: oldBrowser,
      primaryPage: null,
      status: "disconnected",
    });
    (manager as any).sessions.set(session.id, session);
    const pending = manager.reconnectBrowser(session.id, {
      signal: controller.signal,
    });
    await newPageStarted;
    controller.abort("test_cancelled");
    releaseNewPageResolve?.({ url: () => "about:blank" });

    await assert.rejects(pending, /lifecycle operation cancelled/);
    assert.equal(session.browser, oldBrowser);
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

// Проверяем, что reconnectBrowser переиспользует живой профиль с CDP-портом без аварийного restart.
test("reconnectBrowser reuses existing profile port without restart", async () => {
  const manager = new SessionManager();
  const adsPage = {
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns",
  };
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
  const originalRestartProfileToRecoverPort =
    VisionClient.prototype.restartProfileToRecoverPort;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: 6001 };
  };
  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return "folder-1";
  };
  VisionClient.prototype.restartProfileToRecoverPort =
    async function restartProfileToRecoverPort() {
      throw new Error("restartProfileToRecoverPort не должен вызываться");
    };
  VisionClient.prototype.waitUntilCdpReady =
    async function waitUntilCdpReady() {
      return true;
    };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    return browser as any;
  };

  try {
    const session = makeSession({
      status: "disconnected",
      browser: null,
      primaryPage: null,
      playwright: null,
      connectedAt: new Date("2025-01-01T00:00:00.000Z"),
    });
    (manager as any).sessions.set(session.id, session);

    const restored = await manager.reconnectBrowser(session.id);

    assert.equal(connectCalls.length, 1);
    assert.equal(connectCalls[0], "http://127.0.0.1:6001");
    assert.equal(restored.browser, browser as any);
    assert.equal(restored.primaryPage, adsPage as any);
    assert.equal(restored.cdpPort, 6001);
    assert.equal(restored.status, "connected");
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.resolveFolderId = originalResolveFolderId;
    VisionClient.prototype.restartProfileToRecoverPort =
      originalRestartProfileToRecoverPort;
    VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
    (chromium as any).connectOverCDP = originalConnectOverCDP;
  }
});

test("ordinary reconnect never restarts a profile without a CDP port", async () => {
  const manager = new SessionManager();
  let restartCalls = 0;
  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalWaitUntilProfileHasPort =
    VisionClient.prototype.waitUntilProfileHasPort;
  const originalRestartProfileToRecoverPort =
    VisionClient.prototype.restartProfileToRecoverPort;

  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: null };
  };
  VisionClient.prototype.waitUntilProfileHasPort =
    async function waitUntilProfileHasPort() {
      return null;
    };
  VisionClient.prototype.restartProfileToRecoverPort =
    async function restartProfileToRecoverPort() {
      restartCalls += 1;
      throw new Error("ordinary reconnect must not restart Vision");
    };

  try {
    const session = makeSession({
      status: "disconnected",
      browser: null,
      primaryPage: null,
      playwright: null,
    });
    (manager as any).sessions.set(session.id, session);

    await assert.rejects(
      manager.reconnectBrowser(session.id),
      /capability-authorized maintenance recovery/,
    );
    assert.equal(restartCalls, 0);
  } finally {
    VisionClient.prototype.getProfile = originalGetProfile;
    VisionClient.prototype.waitUntilProfileHasPort =
      originalWaitUntilProfileHasPort;
    VisionClient.prototype.restartProfileToRecoverPort =
      originalRestartProfileToRecoverPort;
  }
});

// Проверяем, что reconnectBrowser явно падает, если CDP endpoint так и не стал доступен.
test("reconnectBrowser fails when cdp endpoint never becomes ready", async () => {
  const manager = new SessionManager();
  const connectCalls: string[] = [];

  const originalGetProfile = VisionClient.prototype.getProfile;
  const originalResolveFolderId = VisionClient.prototype.resolveFolderId;
  const originalWaitUntilCdpReady = VisionClient.prototype.waitUntilCdpReady;
  const originalConnectOverCDP = chromium.connectOverCDP;

  VisionClient.prototype.getProfile = async function getProfile() {
    return { folder_id: "folder-1", profile_id: "profile-1", port: 6001 };
  };
  VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
    return "folder-1";
  };
  VisionClient.prototype.waitUntilCdpReady =
    async function waitUntilCdpReady() {
      return false;
    };
  (chromium as any).connectOverCDP = async (url: string) => {
    connectCalls.push(url);
    throw new Error("connectOverCDP не должен вызываться");
  };

  try {
    const session = makeSession({
      status: "disconnected",
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

// rememberAdsManagerUrl: пишет на сессию только URL Ads Manager, чужие/прочие URL игнорирует.
test("rememberAdsManagerUrl записывает только URL Ads Manager", () => {
  const session = makeSession({ lastAdsManagerUrl: null });
  rememberAdsManagerUrl(
    session as any,
    { url: () => "https://www.facebook.com/messages/" } as any,
  );
  assert.equal(session.lastAdsManagerUrl, null);
  rememberAdsManagerUrl(
    session as any,
    {
      url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
    } as any,
  );
  assert.equal(session.lastAdsManagerUrl, adsManagerUrlForAct("1"));
});

test("canonicalAdsManagerUrl удаляет query-секреты и навигационные идентификаторы", () => {
  const canonical = canonicalAdsManagerUrl(
    "https://adsmanager.facebook.com/adsmanager/manage/ads" +
      "?act=111&access_token=secret&business_id=42&nav_source=mcm",
  );
  assert.equal(canonical, adsManagerUrlForAct("111"));
  assert.equal(canonical?.includes("secret"), false);
  assert.equal(canonical?.includes("business_id"), false);
});

// ensureScanPage: первый цикл (act ещё неизвестен, нет fallbackUrl) → trust-on-first-use:
// принимаем открытую вкладку Ads Manager и запоминаем URL (далее act известен → строгая сверка).
test("ensureScanPage возвращает живую вкладку Ads Manager и запоминает URL (TOFU)", async () => {
  const manager = new SessionManager();
  const adsUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=123";
  const adsPage = { isClosed: () => false, url: () => adsUrl };
  const browser = {
    isConnected: () => true,
    contexts: () => [{ pages: () => [adsPage] }],
  };
  const session = makeSession({ browser, primaryPage: null });

  const page = await manager.ensureScanPage(session as any);

  assert.equal(page, adsPage as any);
  assert.equal(session.primaryPage, adsPage as any);
  assert.equal(session.lastAdsManagerUrl, adsManagerUrlForAct("123"));
});

// ensureScanPage: браузер/CDP мертвы → бросаем (восстановление эскалируется на observer).
test("ensureScanPage бросает, если браузер отключён", async () => {
  const manager = new SessionManager();
  const browser = { isConnected: () => false, contexts: () => [] };
  const session = makeSession({ browser, primaryPage: null });

  await assert.rejects(
    manager.ensureScanPage(session as any),
    /Основная страница браузера недоступна/,
  );
});

// ensureScanPage: вкладку закрыли, браузер жив → переоткрываем НОВУЮ вкладку на known-good
// URL кабинета; чужую вкладку не трогаем (её goto не зовём).
test("ensureScanPage переоткрывает вкладку на known-good URL, чужие не трогает", async () => {
  const manager = new SessionManager();
  let otherGotoCalls = 0;
  const otherPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
    goto: async () => {
      otherGotoCalls += 1;
    },
  };
  const knownUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=777";
  let gotoUrl: string | null = null;
  let newPageCalls = 0;
  const newPage = {
    isClosed: () => false,
    url: () => knownUrl,
    goto: async (u: string) => {
      gotoUrl = u;
    },
  };
  const context = {
    pages: () => [otherPage],
    newPage: async () => {
      newPageCalls += 1;
      return newPage;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: knownUrl,
  });

  const page = await manager.ensureScanPage(session as any);

  assert.equal(newPageCalls, 1);
  assert.equal(gotoUrl, adsManagerUrlForAct("777"));
  assert.equal(page, newPage as any);
  assert.equal(session.primaryPage, newPage as any);
  assert.equal(otherGotoCalls, 0); // чужую вкладку не трогали
});

// ensureScanPage: нет вкладки и нет known-good/fallback URL → бросаем
// (в общем кабинете НЕ угадываем дефолтный act, иначе можно попасть в чужой кабинет).
test("ensureScanPage бросает, если URL кабинета неизвестен", async () => {
  const manager = new SessionManager();
  const context = {
    pages: () => [],
    newPage: async () => {
      throw new Error("newPage не должен вызываться без известного URL");
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: null,
  });

  await assert.rejects(
    manager.ensureScanPage(session as any),
    /Основная страница браузера недоступна/,
  );
});

// ensureScanPage: known-good URL нет, но передан fallbackUrl (реконструированный из act_id) →
// открываем новую вкладку на нём.
test("ensureScanPage использует fallbackUrl, если known-good URL нет", async () => {
  const manager = new SessionManager();
  const fallbackUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=555";
  let gotoUrl: string | null = null;
  const newPage = {
    isClosed: () => false,
    url: () => fallbackUrl,
    goto: async (u: string) => {
      gotoUrl = u;
    },
  };
  const context = { pages: () => [], newPage: async () => newPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: null,
  });

  const page = await manager.ensureScanPage(session as any, { fallbackUrl });

  assert.equal(gotoUrl, adsManagerUrlForAct("555"));
  assert.equal(page, newPage as any);
});

test("cancel during role-page creation closes the eventual unowned tab", async () => {
  const manager = new SessionManager();
  const fallbackUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=555";
  let resolvePage!: (page: any) => void;
  const pendingPage = new Promise<any>((resolve) => {
    resolvePage = resolve;
  });
  let closed = 0;
  const newPage = {
    isClosed: () => closed > 0,
    url: () => "",
    goto: async () => undefined,
    close: async () => {
      closed += 1;
    },
  };
  const context = { pages: () => [], newPage: () => pendingPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: null,
  });
  const controller = new AbortController();

  const operation = manager.ensureScanPage(session as any, {
    fallbackUrl,
    signal: controller.signal,
  });
  controller.abort("grpc_cancelled");
  await assert.rejects(operation, /browser operation cancelled/i);

  resolvePage(newPage);
  await new Promise<void>((resolve) => {
    setImmediate(resolve);
  });

  assert.equal(closed, 1);
  assert.equal(session.scanPages?.size, 0);
});

test("cancel during role-page navigation closes and never assigns the page", async () => {
  const manager = new SessionManager();
  const fallbackUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=555";
  let navigationStartedResolve!: () => void;
  const navigationStarted = new Promise<void>((resolve) => {
    navigationStartedResolve = resolve;
  });
  let closed = 0;
  const newPage = {
    isClosed: () => closed > 0,
    url: () => "",
    goto: async () => {
      navigationStartedResolve();
      return new Promise<void>(() => undefined);
    },
    close: async () => {
      closed += 1;
    },
  };
  const context = { pages: () => [], newPage: async () => newPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: null,
  });
  const controller = new AbortController();

  const operation = manager.ensureScanPage(session as any, {
    fallbackUrl,
    signal: controller.signal,
  });
  await navigationStarted;
  controller.abort("grpc_cancelled");
  await assert.rejects(operation, /browser operation cancelled/i);

  assert.equal(closed, 1);
  assert.equal(session.scanPages?.size, 0);
});

// ensureScanPage: открыта вкладка ДРУГОГО кабинета (act≠ожидаемому) → не сканируем чужой,
// переоткрываем СВОЙ кабинет. Защита от тихой слепоты MV при нескольких кабинетах владельца.
test("ensureScanPage не подхватывает вкладку другого кабинета (act mismatch)", async () => {
  const manager = new SessionManager();
  const foreignAds = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=999",
  };
  const ownUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111";
  let gotoUrl: string | null = null;
  const ownPage = {
    isClosed: () => false,
    url: () => ownUrl,
    goto: async (u: string) => {
      gotoUrl = u;
    },
  };
  const context = { pages: () => [foreignAds], newPage: async () => ownPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl: ownUrl,
  });

  const page = await manager.ensureScanPage(session as any);

  assert.equal(gotoUrl, adsManagerUrlForAct("111"));
  assert.equal(page, ownPage as any);
});

// ensureScanPage: открыта вкладка ТОГО ЖE кабинета (act совпал) → используем её, не переоткрываем.
test("ensureScanPage принимает вкладку того же кабинета (act match)", async () => {
  const manager = new SessionManager();
  const liveUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111&business_id=5";
  const sameAds = { isClosed: () => false, url: () => liveUrl };
  const context = {
    pages: () => [sameAds],
    newPage: async () => {
      throw new Error("переоткрывать не нужно — кабинет тот же");
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: null,
    lastAdsManagerUrl:
      "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111",
  });

  const page = await manager.ensureScanPage(session as any);

  assert.equal(page, sameAds as any);
  assert.equal(session.lastAdsManagerUrl, adsManagerUrlForAct("111"));
});

test("ensureScanPage применяет изменённые presentation-колонки к открытой вкладке", async () => {
  const manager = new SessionManager();
  let currentUrl = adsManagerUrlForAct("111");
  let gotoUrl: string | null = null;
  const sameAds = {
    isClosed: () => false,
    url: () => currentUrl,
    goto: async (url: string) => {
      gotoUrl = url;
      currentUrl = url;
    },
  };
  const context = {
    pages: () => [sameAds],
    newPage: async () => assert.fail("открытая вкладка должна переиспользоваться"),
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({
    browser,
    primaryPage: sameAds,
    lastAdsManagerUrl: currentUrl,
    scanPages: new Map([["111", sameAds]]),
  });
  const amColumnsQs = "columns=name%2Cspend&column_preset=999";

  const page = await manager.ensureScanPage(session, {
    actId: "111",
    amColumnsQs,
  });

  assert.equal(page, sameAds);
  assert.equal(gotoUrl, adsManagerUrlForAct("111", amColumnsQs));
  assert.equal(currentUrl.includes("access_token"), false);
});

// ====================== Явная multi-cabinet identity ======================

// URL кабинета строится детерминированно из числового act.
test("adsManagerUrlForAct строит URL кабинета на уровне кампаний с колонками", () => {
  const url = adsManagerUrlForAct("123456");
  // Уровень кампаний + нужный кабинет + набор колонок (пользовательский пресет).
  assert.ok(
    url.startsWith(
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123456",
    ),
    `URL должен быть на уровне кампаний нужного кабинета, получено: ${url}`,
  );
  assert.ok(url.includes("columns="), "URL должен содержать набор колонок");
  assert.ok(
    url.includes("column_preset="),
    "URL должен содержать column_preset",
  );
  assert.throws(
    () => adsManagerUrlForAct("act_123"),
    /cabinet_not_found: ad account id must be 1\.\.32 digits/,
  );
  assert.throws(
    () => adsManagerUrlForAct("9".repeat(33)),
    /cabinet_not_found: ad account id must be 1\.\.32 digits/,
  );
});

test("ensureScanPage rejects an explicit act outside the 1..32 digit identity", async () => {
  const manager = new SessionManager();
  let newPageCalls = 0;
  const context = {
    pages: () => [],
    newPage: async () => {
      newPageCalls += 1;
      return assert.fail("invalid act must fail before allocation");
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: null });

  for (const actId of ["act_123", "9".repeat(33), "123.4", " 123 "]) {
    await assert.rejects(
      manager.ensureScanPage(session, { actId }),
      /cabinet_not_found: ad account id must be 1\.\.32 digits/,
    );
  }
  assert.equal(newPageCalls, 0);
});

// Поиск вкладки нужного кабинета среди нескольких открытых (включая чужой act).
test("findAdsManagerPageByAct находит вкладку своего кабинета", () => {
  const cab1 = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111",
  };
  const cab2 = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/ads?act=222&nav_source=mcm",
  };
  const browser = { contexts: () => [{ pages: () => [cab1, cab2] }] };

  assert.equal(findAdsManagerPageByAct(browser as any, "222"), cab2);
  assert.equal(findAdsManagerPageByAct(browser as any, "111"), cab1);
});

test("findAdsManagerPageByAct rejects duplicate-act, HTTP, and credential URLs", () => {
  const invalidPages = [
    {
      isClosed: () => false,
      url: () =>
        "https://adsmanager.facebook.com/adsmanager/manage/ads?act=111&act=111",
    },
    {
      isClosed: () => false,
      url: () => "http://adsmanager.facebook.com/adsmanager/manage/ads?act=111",
    },
    {
      isClosed: () => false,
      url: () =>
        "https://user:secret@adsmanager.facebook.com/adsmanager/manage/ads?act=111",
    },
  ];
  const browser = { contexts: () => [{ pages: () => invalidPages }] };

  assert.equal(findAdsManagerPageByAct(browser as any, "111"), null);
});

test("ensureScanPage finds exact act in a non-active page across browser contexts", async () => {
  const manager = new SessionManager();
  const primary = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const wrongCabinet = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=999",
  };
  const exactCabinet = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=222",
  };
  const browser = {
    isConnected: () => true,
    contexts: () => [
      {
        pages: () => [primary, wrongCabinet],
        newPage: async () => assert.fail("must not open"),
      },
      {
        pages: () => [exactCabinet],
        newPage: async () => assert.fail("must not open"),
      },
    ],
  };
  const session = makeSession({ browser, primaryPage: primary });

  const selected = await manager.ensureScanPage(session, { actId: "222" });

  assert.equal(selected, exactCabinet);
  assert.equal(session.scanPages.get("222"), exactCabinet);
  assert.equal(session.primaryPage, exactCabinet);
});

test("duplicate exact-act tabs are selected deterministically and never shared across roles", async () => {
  const manager = new SessionManager();
  const first = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=333",
  };
  const second = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/adsmanager/manage/campaigns?act=333",
  };
  const browser = {
    isConnected: () => true,
    contexts: () => [
      {
        pages: () => [first],
        newPage: async () => assert.fail("must not open"),
      },
      {
        pages: () => [second],
        newPage: async () => assert.fail("must not open"),
      },
    ],
  };
  const session = makeSession({ browser, primaryPage: first });

  const scan = await manager.ensureScanPage(session, { actId: "333" });
  const control = await manager.ensureControlPage(session, { actId: "333" });

  assert.equal(scan, first);
  assert.equal(control, second);
});

// Чужой/несуществующий кабинет и закрытые вкладки → null (вкладку откроет ensureScanPage).
test("findAdsManagerPageByAct: нет вкладки → null, закрытая не считается", () => {
  const closedCab = {
    isClosed: () => true,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=333",
  };
  const browser = { contexts: () => [{ pages: () => [closedCab] }] };

  assert.equal(findAdsManagerPageByAct(browser as any, "333"), null);
  assert.equal(findAdsManagerPageByAct(null, "333"), null);
});

// ensureScanPage(actId): пользовательские Facebook/чужие кабинетные вкладки сохраняются;
// кабинет открывается только в новой принадлежащей агенту вкладке.
test("ensureScanPage (actId): сохраняет ручные Facebook и foreign cabinet вкладки", async () => {
  const manager = new SessionManager();
  let fbGoto: string | null = null;
  const fbPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/",
    goto: async (u: string) => {
      fbGoto = u;
    },
  };
  let foreignGoto = 0;
  let foreignClose = 0;
  const foreignCab = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999",
    goto: async () => {
      foreignGoto += 1;
    },
    close: async () => {
      foreignClose += 1;
    },
  };
  let blankGoto = 0;
  let blankClose = 0;
  const blank = {
    isClosed: () => false,
    url: () => "about:blank",
    goto: async () => {
      blankGoto += 1;
    },
    close: async () => {
      blankClose += 1;
    },
  };
  let newPageCalls = 0;
  let newUrl = "about:blank";
  let newClosed = 0;
  const newPage = {
    isClosed: () => newClosed > 0,
    url: () => newUrl,
    goto: async (u: string) => {
      newUrl = u;
    },
    close: async () => {
      newClosed += 1;
    },
  };
  const context = {
    pages: () => [fbPage, foreignCab, blank],
    newPage: async () => {
      newPageCalls += 1;
      return newPage;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: null });

  const page = await manager.ensureScanPage(session as any, { actId: "111" });

  assert.equal(page, newPage as any);
  assert.equal(fbGoto, null); // пользовательскую Facebook-вкладку не навигировали
  assert.equal(newPageCalls, 1);
  assert.equal(foreignGoto, 0); // чужой кабинет не трогали
  assert.equal(foreignClose, 0); // ручную foreign-cabinet вкладку не закрывали
  assert.equal(blankGoto, 0); // даже about:blank не используется как scratch page
  assert.equal(blankClose, 0);
  assert.equal(newClosed, 0);
});

// ensureScanPage(actId): нейтральной вкладки нет (только чужой кабинет) → открывает НОВУЮ.
test("ensureScanPage (actId): нет нейтральной вкладки → открывает новую", async () => {
  const manager = new SessionManager();
  const foreignCab = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999",
  };
  let gotoUrl: string | null = null;
  let newPageCalls = 0;
  let currentUrl = "about:blank";
  const newPage = {
    isClosed: () => false,
    url: () => currentUrl,
    goto: async (u: string) => {
      gotoUrl = u;
      currentUrl = u;
    },
  };
  const context = {
    pages: () => [foreignCab],
    newPage: async () => {
      newPageCalls += 1;
      return newPage;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: null });

  const page = await manager.ensureScanPage(session as any, { actId: "111" });

  assert.equal(newPageCalls, 1);
  assert.ok(String(gotoUrl).includes("act=111"));
  assert.equal(page, newPage as any);
});

for (const [name, finalUrl] of [
  [
    "wrong act",
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999",
  ],
  [
    "login redirect",
    "https://business.facebook.com/business/loginpage/?next=https%3A%2F%2Fadsmanager.facebook.com",
  ],
] as const) {
  test(`auto-open fails closed and does not map ${name}`, async () => {
    const manager = new SessionManager();
    let currentUrl = "about:blank";
    let closeCalls = 0;
    const createdPage = {
      isClosed: () => closeCalls > 0,
      url: () => currentUrl,
      goto: async () => {
        currentUrl = finalUrl;
      },
      close: async () => {
        closeCalls += 1;
      },
    };
    const manualPage = {
      isClosed: () => false,
      url: () => "https://www.facebook.com/messages/",
    };
    const context = {
      pages: () => [manualPage],
      newPage: async () => createdPage,
    };
    const browser = { isConnected: () => true, contexts: () => [context] };
    const session = makeSession({ browser, primaryPage: manualPage });

    await assert.rejects(
      manager.ensureScanPage(session, { actId: "111" }),
      /cabinet_not_confirmed: final Ads Manager URL does not confirm act=111/,
    );

    assert.equal(session.scanPages.has("111"), false);
    assert.equal(
      closeCalls,
      1,
      "only the failed page created by the agent is closed",
    );
    assert.equal(session.primaryPage, manualPage);
  });
}

test("auto-open normalizes raw Playwright failures without leaking URL or token", async () => {
  const manager = new SessionManager();
  let closeCalls = 0;
  const createdPage = {
    isClosed: () => closeCalls > 0,
    url: () => "about:blank",
    goto: async () => {
      throw new Error(
        "net::ERR_FAILED https://example.test/?access_token=secret",
      );
    },
    close: async () => {
      closeCalls += 1;
    },
  };
  const context = { pages: () => [], newPage: async () => createdPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: null });

  await assert.rejects(
    manager.ensureScanPage(session, { actId: "111" }),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(
        error.message,
        "cabinet_not_found: navigation failed for act=111",
      );
      assert.equal(error.message.includes("secret"), false);
      return true;
    },
  );
  assert.equal(closeCalls, 1);
});

test("failed agent-owned navigation leaves an existing about:blank untouched", async () => {
  const manager = new SessionManager();
  let blankGoto = 0;
  let blankClose = 0;
  const blank = {
    isClosed: () => false,
    url: () => "about:blank",
    goto: async () => {
      blankGoto += 1;
    },
    close: async () => {
      blankClose += 1;
    },
  };
  let createdClose = 0;
  const createdPage = {
    isClosed: () => createdClose > 0,
    url: () => "https://www.facebook.com/login/",
    goto: async () => undefined,
    close: async () => {
      createdClose += 1;
    },
  };
  const context = { pages: () => [blank], newPage: async () => createdPage };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: blank });

  await assert.rejects(
    manager.ensureScanPage(session, { actId: "111" }),
    /cabinet_not_confirmed/,
  );

  assert.equal(blankGoto, 0);
  assert.equal(blankClose, 0);
  assert.equal(createdClose, 1);
  assert.equal(session.scanPages.has("111"), false);
});

test("mapped login/wrong-origin page is rejected and replaced with a confirmed canonical tab", async () => {
  const manager = new SessionManager();
  const staleMapped = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/login/?act=111",
  };
  let canonicalUrl = "about:blank";
  const replacement = {
    isClosed: () => false,
    url: () => canonicalUrl,
    goto: async (url: string) => {
      canonicalUrl = url;
    },
  };
  const context = {
    pages: () => [staleMapped],
    newPage: async () => replacement,
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: staleMapped });
  session.scanPages.set("111", staleMapped);

  const selected = await manager.ensureScanPage(session, { actId: "111" });

  assert.equal(selected, replacement);
  assert.equal(session.scanPages.get("111"), replacement);
  assert.equal(canonicalUrl, adsManagerUrlForAct("111"));
});

test("concurrent missing scan and control create distinct agent-owned pages", async () => {
  const manager = new SessionManager();
  let blankGoto = 0;
  let navigationStartedResolve!: () => void;
  const navigationStarted = new Promise<void>((resolve) => {
    navigationStartedResolve = resolve;
  });
  let releaseNavigationResolve!: () => void;
  const releaseNavigation = new Promise<void>((resolve) => {
    releaseNavigationResolve = resolve;
  });
  const blank = {
    isClosed: () => false,
    url: () => "about:blank",
    goto: async () => {
      blankGoto += 1;
    },
  };
  const pages: any[] = [blank];
  let created = 0;
  const createdPages: any[] = [];
  const context = {
    pages: () => pages,
    newPage: async () => {
      created += 1;
      const creationIndex = created;
      let url = "about:blank";
      const page = {
        isClosed: () => false,
        url: () => url,
        goto: async (target: string) => {
          if (creationIndex === 1) {
            navigationStartedResolve();
            await releaseNavigation;
          }
          url = target;
        },
      };
      pages.push(page);
      createdPages.push(page);
      return page;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: blank });

  const scanPromise = manager.ensureScanPage(session, { actId: "444" });
  await navigationStarted;
  const controlPromise = manager.ensureControlPage(session, { actId: "444" });
  const control = await controlPromise;
  releaseNavigationResolve();
  const scan = await scanPromise;

  assert.equal(scan, createdPages[0]);
  assert.equal(control, createdPages[1]);
  assert.equal(created, 2);
  assert.equal(blankGoto, 0);
  assert.equal(session.scanPages.get("444"), scan);
  assert.equal(session.controlPages.get("444"), control);
});

// ensureScanPage(actId): своя вкладка кабинета уже открыта → переиспользуется как есть (без navigate).
test("ensureScanPage (actId): своя вкладка уже открыта → используется без navigate", async () => {
  const manager = new SessionManager();
  const ownCab = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111",
  };
  const context = {
    pages: () => [ownCab],
    newPage: async () => {
      throw new Error("переоткрывать не нужно — вкладка кабинета уже есть");
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: null });

  const page = await manager.ensureScanPage(session as any, { actId: "111" });
  assert.equal(page, ownCab as any);
});

test("scan/control pages физически разделены per cabinet", async () => {
  const manager = new SessionManager();
  const targetUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111";
  const scanPage = {
    isClosed: () => false,
    url: () => targetUrl,
  };
  let created = 0;
  const pages: any[] = [scanPage];
  const context = {
    pages: () => pages,
    newPage: async () => {
      created += 1;
      let currentUrl = "about:blank";
      const page = {
        isClosed: () => false,
        url: () => currentUrl,
        goto: async (url: string) => {
          currentUrl = url;
        },
      };
      pages.push(page);
      return page;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: scanPage });

  const scan = await manager.ensureScanPage(session, { actId: "111" });
  const control = await manager.ensureControlPage(session, { actId: "111" });

  assert.equal(scan, scanPage);
  assert.notEqual(
    control,
    scan,
    "control не имеет права fallback на scan page",
  );
  assert.equal(created, 1);
  assert.equal(session.scanPages.get("111"), scan);
  assert.equal(session.controlPages.get("111"), control);
});

test("scan/control/interactive pages physically distinct per cabinet", async () => {
  const manager = new SessionManager();
  const targetUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=333";
  const initial = { isClosed: () => false, url: () => targetUrl };
  const pages: any[] = [initial];
  const context = {
    pages: () => pages,
    newPage: async () => {
      let currentUrl = "about:blank";
      const page = {
        isClosed: () => false,
        url: () => currentUrl,
        goto: async (url: string) => {
          currentUrl = url;
        },
      };
      pages.push(page);
      return page;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: initial });

  const scan = await manager.ensureScanPage(session, { actId: "333" });
  const control = await manager.ensureControlPage(session, { actId: "333" });
  const interactive = await manager.ensureInteractivePage(session, {
    actId: "333",
  });

  assert.equal(new Set([scan, control, interactive]).size, 3);
  assert.equal(session.scanPages.get("333"), scan);
  assert.equal(session.controlPages.get("333"), control);
  assert.equal(session.interactivePages.get("333"), interactive);
});

test("poisoned control page is never reused even when CDP close does not settle", async () => {
  const manager = new SessionManager();
  const targetUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=444";
  const poisoned = {
    isClosed: () => false,
    url: () => targetUrl,
    close: async () => new Promise<void>(() => undefined),
  };
  const pages: any[] = [poisoned];
  let replacement: any;
  const context = {
    pages: () => pages,
    newPage: async () => {
      let currentUrl = "about:blank";
      replacement = {
        isClosed: () => false,
        url: () => currentUrl,
        goto: async (url: string) => {
          currentUrl = url;
        },
      };
      pages.push(replacement);
      return replacement;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: poisoned });
  session.controlPages.set("444", poisoned as any);

  manager.poisonRolePage(session, "control", "444", poisoned as any);
  const selected = await manager.ensureControlPage(session, { actId: "444" });

  assert.equal(selected, replacement);
  assert.notEqual(selected, poisoned);
  assert.equal(session.controlPages.get("444"), replacement);
});

test("poison quarantine stays weak across many hung-close replacement cycles", async () => {
  const manager = new SessionManager();
  const targetUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=445";
  const makeHungPage = () => ({
    isClosed: () => false,
    url: () => targetUrl,
    close: async () => new Promise<void>(() => undefined),
  });
  const initial = makeHungPage();
  const pages: any[] = [initial];
  let created = 0;
  const context = {
    pages: () => pages,
    newPage: async () => {
      let currentUrl = "about:blank";
      const page = {
        isClosed: () => false,
        url: () => currentUrl,
        goto: async (url: string) => {
          currentUrl = url;
        },
        close: async () => new Promise<void>(() => undefined),
      };
      created += 1;
      pages.push(page);
      return page;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: initial });
  session.controlPages.set("445", initial as any);

  let current: any = initial;
  const quarantined = new Set<any>();
  for (let cycle = 0; cycle < 128; cycle += 1) {
    quarantined.add(current);
    manager.poisonRolePage(session, "control", "445", current);
    const replacement = await manager.ensureControlPage(session, {
      actId: "445",
    });
    assert.equal(
      quarantined.has(replacement),
      false,
      `cycle ${cycle} reused a page whose close never settled`,
    );
    current = replacement;
  }

  assert.equal(created, 128);
  assert.equal((manager as any).poisonedPages instanceof WeakSet, true);
  assert.equal(
    (manager as any).poisonedPages.size,
    undefined,
    "weak quarantine must not expose or retain an ever-growing strong Set",
  );
});

test("закрытие scan page не заменяет и не трогает control page", async () => {
  const manager = new SessionManager();
  const targetUrl =
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=222";
  let scanClosed = false;
  const oldScan = { isClosed: () => scanClosed, url: () => targetUrl };
  const control = { isClosed: () => false, url: () => targetUrl };
  const pages: any[] = [oldScan, control];
  let replacement: any;
  const context = {
    pages: () => pages,
    newPage: async () => {
      let currentUrl = "about:blank";
      replacement = {
        isClosed: () => false,
        url: () => currentUrl,
        goto: async (url: string) => {
          currentUrl = url;
        },
      };
      pages.push(replacement);
      return replacement;
    },
  };
  const browser = { isConnected: () => true, contexts: () => [context] };
  const session = makeSession({ browser, primaryPage: oldScan });
  session.scanPages.set("222", oldScan as any);
  session.controlPages.set("222", control as any);
  scanClosed = true;

  const healedScan = await manager.ensureScanPage(session, { actId: "222" });

  assert.equal(healedScan, replacement);
  assert.notEqual(healedScan, control);
  assert.equal(session.controlPages.get("222"), control);
});

// Проба готовности читает токен с уже открытой вкладки кабинета.
test("findLiveAdsManagerPage возвращает живую вкладку Ads Manager", () => {
  const inboxPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const adsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123",
  };
  const browser = {
    contexts: () => [{ pages: () => [inboxPage, adsPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), adsPage);
});

// В отличие от findPreferredPrimaryPage здесь НЕТ отката на первую попавшуюся
// вкладку: выдать чужую вкладку за Ads Manager значит соврать о готовности канала.
test("findLiveAdsManagerPage не подменяет Ads Manager чужой вкладкой", () => {
  const inboxPage = {
    isClosed: () => false,
    url: () => "https://www.facebook.com/messages/",
  };
  const browser = {
    contexts: () => [{ pages: () => [inboxPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), null);
});

// Закрытая вкладка не является доказательством живого канала.
test("findLiveAdsManagerPage игнорирует закрытые вкладки", () => {
  const closedAdsPage = {
    isClosed: () => true,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=123",
  };
  const openAdsPage = {
    isClosed: () => false,
    url: () => "https://adsmanager.facebook.com/adsmanager/manage/ads?act=456",
  };
  const browser = {
    contexts: () => [{ pages: () => [closedAdsPage, openAdsPage] }],
  };

  assert.equal(findLiveAdsManagerPage(browser as any), openAdsPage);
});

// Без браузера проба обязана честно ответить «нет страницы», а не упасть.
test("findLiveAdsManagerPage без браузера возвращает null", () => {
  assert.equal(findLiveAdsManagerPage(null), null);
});

// Вкладку кабинета мог открыть оператор руками. Проба готовности обязана её
// переиспользовать: перезагрузка сбросила бы его фильтры и выделение.
test("ensureInteractivePage переиспользует вкладку кабинета, не навигируя и не закрывая её", async () => {
  const manager = new SessionManager();
  let gotoCalls = 0;
  let closeCalls = 0;
  const adsPage = {
    isClosed: () => false,
    url: () =>
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=2108857220005012",
    goto: async () => {
      gotoCalls += 1;
    },
    close: async () => {
      closeCalls += 1;
    },
  };
  const browser = {
    isConnected: () => true,
    contexts: () => [{ pages: () => [adsPage] }],
  };
  const session = makeSession({ browser });

  const page = await manager.ensureInteractivePage(session, {
    actId: "2108857220005012",
  });

  assert.equal(page, adsPage as any);
  assert.equal(gotoCalls, 0);
  assert.equal(closeCalls, 0);
});
