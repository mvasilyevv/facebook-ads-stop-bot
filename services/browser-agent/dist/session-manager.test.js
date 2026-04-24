"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = __importDefault(require("node:test"));
const strict_1 = __importDefault(require("node:assert/strict"));
const playwright_1 = require("playwright");
const session_manager_js_1 = require("./session-manager.js");
const vision_client_js_1 = require("./vision-client.js");
// Проверяем, что helper корректно распознаёт URL Ads Manager.
(0, node_test_1.default)('isAdsManagerUrl detects ads manager pages', () => {
    strict_1.default.equal((0, session_manager_js_1.isAdsManagerUrl)('https://www.facebook.com/adsmanager/manage/campaigns'), true);
    strict_1.default.equal((0, session_manager_js_1.isAdsManagerUrl)('https://www.facebook.com/ads/library/'), true);
    strict_1.default.equal((0, session_manager_js_1.isAdsManagerUrl)('https://www.facebook.com/messages/'), false);
});
// Проверяем, что primary page выбирается по вкладке Ads Manager, а не по первой вкладке профиля.
(0, node_test_1.default)('findPreferredPrimaryPage prefers ads manager tab over first page', () => {
    const inboxPage = { url: () => 'https://www.facebook.com/messages/' };
    const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            { pages: () => [inboxPage, adsPage] },
        ],
    };
    strict_1.default.equal((0, session_manager_js_1.findPreferredPrimaryPage)(browser), adsPage);
});
// Проверяем, что при отсутствии Ads Manager helper возвращает первую доступную вкладку.
(0, node_test_1.default)('findPreferredPrimaryPage falls back to first available page', () => {
    const firstPage = { url: () => 'https://www.facebook.com/' };
    const browser = {
        contexts: () => [
            { pages: () => [firstPage] },
        ],
    };
    strict_1.default.equal((0, session_manager_js_1.findPreferredPrimaryPage)(browser), firstPage);
});
function makeSession(overrides = {}) {
    return {
        id: 'session-1',
        visionApiUrl: 'http://127.0.0.1:3030',
        visionXToken: 'token',
        visionProfileId: 'profile-1',
        visionFolderId: 'folder-1',
        cdpPort: 4555,
        playwright: playwright_1.chromium,
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
(0, node_test_1.default)('disconnectBrowser clears local references without closing browser', async () => {
    const manager = new session_manager_js_1.SessionManager();
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
    manager.sessions.set(session.id, session);
    await manager.disconnectBrowser(session.id);
    const stored = manager.sessions.get(session.id);
    strict_1.default.equal(closeCalls, 0);
    strict_1.default.equal(stored.browser, null);
    strict_1.default.equal(stored.primaryPage, null);
    strict_1.default.equal(stored.playwright, null);
    strict_1.default.equal(stored.status, 'disconnected');
});
// Проверяем, что stopBrowser закрывает браузер и завершает профиль через Vision API.
(0, node_test_1.default)('stopBrowser closes browser and stops Vision profile', async () => {
    const manager = new session_manager_js_1.SessionManager();
    let closeCalls = 0;
    const stopCalls = [];
    const originalStopProfile = vision_client_js_1.VisionClient.prototype.stopProfile;
    vision_client_js_1.VisionClient.prototype.stopProfile = async function stopProfile(folderId, profileId) {
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
        manager.sessions.set(session.id, session);
        await manager.stopBrowser(session.id);
        strict_1.default.equal(closeCalls, 1);
        strict_1.default.deepEqual(stopCalls, [['folder-1', 'profile-1']]);
        strict_1.default.equal(manager.sessions.has(session.id), false);
    }
    finally {
        vision_client_js_1.VisionClient.prototype.stopProfile = originalStopProfile;
    }
});
// Проверяем, что startBrowser ждёт готовности CDP endpoint перед подключением.
(0, node_test_1.default)('startBrowser waits for cdp readiness before connecting', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            {
                addInitScript: async () => { },
                pages: () => [adsPage],
            },
        ],
    };
    const readyCalls = [];
    const connectCalls = [];
    const originalResolveFolderId = vision_client_js_1.VisionClient.prototype.resolveFolderId;
    const originalGetProfile = vision_client_js_1.VisionClient.prototype.getProfile;
    const originalWaitUntilCdpReady = vision_client_js_1.VisionClient.prototype.waitUntilCdpReady;
    const originalConnectOverCDP = playwright_1.chromium.connectOverCDP;
    vision_client_js_1.VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
        return 'folder-1';
    };
    vision_client_js_1.VisionClient.prototype.getProfile = async function getProfile() {
        return { folder_id: 'folder-1', profile_id: 'profile-1', port: 7001 };
    };
    vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady(port) {
        readyCalls.push(port);
        return true;
    };
    playwright_1.chromium.connectOverCDP = async (url) => {
        connectCalls.push(url);
        return browser;
    };
    try {
        const session = await manager.startBrowser({
            visionXToken: 'token',
            visionApiUrl: 'http://127.0.0.1:3030',
            visionProfileId: 'profile-1',
        });
        strict_1.default.deepEqual(readyCalls, [7001]);
        strict_1.default.deepEqual(connectCalls, ['http://127.0.0.1:7001']);
        strict_1.default.equal(session.primaryPage, adsPage);
        strict_1.default.equal(session.cdpPort, 7001);
    }
    finally {
        vision_client_js_1.VisionClient.prototype.resolveFolderId = originalResolveFolderId;
        vision_client_js_1.VisionClient.prototype.getProfile = originalGetProfile;
        vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
        playwright_1.chromium.connectOverCDP = originalConnectOverCDP;
    }
});
// Проверяем, что профиль без CDP-порта не перезапускается без явного feature flag.
(0, node_test_1.default)('startBrowser does not restart missing cdp profile by default', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const previousFlag = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    const originalResolveFolderId = vision_client_js_1.VisionClient.prototype.resolveFolderId;
    const originalGetProfile = vision_client_js_1.VisionClient.prototype.getProfile;
    const originalWaitUntilProfileHasPort = vision_client_js_1.VisionClient.prototype.waitUntilProfileHasPort;
    const originalRestartProfileToRecoverPort = vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort;
    vision_client_js_1.VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
        return 'folder-1';
    };
    vision_client_js_1.VisionClient.prototype.getProfile = async function getProfile() {
        return { folder_id: 'folder-1', profile_id: 'profile-1', port: null };
    };
    vision_client_js_1.VisionClient.prototype.waitUntilProfileHasPort = async function waitUntilProfileHasPort() {
        return null;
    };
    vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort = async function restartProfileToRecoverPort() {
        throw new Error('restartProfileToRecoverPort не должен вызываться');
    };
    try {
        await strict_1.default.rejects(manager.startBrowser({
            visionXToken: 'token',
            visionApiUrl: 'http://127.0.0.1:3030',
            visionProfileId: 'profile-1',
        }), /Автоперезапуск профиля для восстановления CDP-порта отключён/);
    }
    finally {
        if (previousFlag === undefined) {
            delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
        }
        else {
            process.env.VISION_AUTO_RESTART_ON_MISSING_CDP = previousFlag;
        }
        vision_client_js_1.VisionClient.prototype.resolveFolderId = originalResolveFolderId;
        vision_client_js_1.VisionClient.prototype.getProfile = originalGetProfile;
        vision_client_js_1.VisionClient.prototype.waitUntilProfileHasPort = originalWaitUntilProfileHasPort;
        vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort = originalRestartProfileToRecoverPort;
    }
});
// Проверяем, что reconnectBrowser переиспользует живой профиль с CDP-портом без аварийного restart.
(0, node_test_1.default)('reconnectBrowser reuses existing profile port without restart', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            {
                addInitScript: async () => { },
                pages: () => [adsPage],
            },
        ],
    };
    const connectCalls = [];
    const originalGetProfile = vision_client_js_1.VisionClient.prototype.getProfile;
    const originalResolveFolderId = vision_client_js_1.VisionClient.prototype.resolveFolderId;
    const originalRestartProfileToRecoverPort = vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort;
    const originalWaitUntilCdpReady = vision_client_js_1.VisionClient.prototype.waitUntilCdpReady;
    const originalConnectOverCDP = playwright_1.chromium.connectOverCDP;
    vision_client_js_1.VisionClient.prototype.getProfile = async function getProfile() {
        return { folder_id: 'folder-1', profile_id: 'profile-1', port: 6001 };
    };
    vision_client_js_1.VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
        return 'folder-1';
    };
    vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort = async function restartProfileToRecoverPort() {
        throw new Error('restartProfileToRecoverPort не должен вызываться');
    };
    vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
        return true;
    };
    playwright_1.chromium.connectOverCDP = async (url) => {
        connectCalls.push(url);
        return browser;
    };
    try {
        const session = makeSession({
            status: 'disconnected',
            browser: null,
            primaryPage: null,
            playwright: null,
            connectedAt: new Date('2025-01-01T00:00:00.000Z'),
        });
        manager.sessions.set(session.id, session);
        const restored = await manager.reconnectBrowser(session.id);
        strict_1.default.equal(connectCalls.length, 1);
        strict_1.default.equal(connectCalls[0], 'http://127.0.0.1:6001');
        strict_1.default.equal(restored.browser, browser);
        strict_1.default.equal(restored.primaryPage, adsPage);
        strict_1.default.equal(restored.cdpPort, 6001);
        strict_1.default.equal(restored.status, 'connected');
    }
    finally {
        vision_client_js_1.VisionClient.prototype.getProfile = originalGetProfile;
        vision_client_js_1.VisionClient.prototype.resolveFolderId = originalResolveFolderId;
        vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort = originalRestartProfileToRecoverPort;
        vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
        playwright_1.chromium.connectOverCDP = originalConnectOverCDP;
    }
});
// Проверяем, что reconnectBrowser явно падает, если CDP endpoint так и не стал доступен.
(0, node_test_1.default)('reconnectBrowser fails when cdp endpoint never becomes ready', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const connectCalls = [];
    const originalGetProfile = vision_client_js_1.VisionClient.prototype.getProfile;
    const originalResolveFolderId = vision_client_js_1.VisionClient.prototype.resolveFolderId;
    const originalWaitUntilCdpReady = vision_client_js_1.VisionClient.prototype.waitUntilCdpReady;
    const originalConnectOverCDP = playwright_1.chromium.connectOverCDP;
    vision_client_js_1.VisionClient.prototype.getProfile = async function getProfile() {
        return { folder_id: 'folder-1', profile_id: 'profile-1', port: 6001 };
    };
    vision_client_js_1.VisionClient.prototype.resolveFolderId = async function resolveFolderId() {
        return 'folder-1';
    };
    vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
        return false;
    };
    playwright_1.chromium.connectOverCDP = async (url) => {
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
        manager.sessions.set(session.id, session);
        await strict_1.default.rejects(manager.reconnectBrowser(session.id), /CDP endpoint профиля profile-1 на порту 6001 не стал доступен/);
        strict_1.default.deepEqual(connectCalls, []);
    }
    finally {
        vision_client_js_1.VisionClient.prototype.getProfile = originalGetProfile;
        vision_client_js_1.VisionClient.prototype.resolveFolderId = originalResolveFolderId;
        vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
        playwright_1.chromium.connectOverCDP = originalConnectOverCDP;
    }
});
//# sourceMappingURL=session-manager.test.js.map