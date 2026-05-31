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
    const inboxPage = { isClosed: () => false, url: () => 'https://www.facebook.com/messages/' };
    const adsPage = { isClosed: () => false, url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            { pages: () => [inboxPage, adsPage] },
        ],
    };
    strict_1.default.equal((0, session_manager_js_1.findPreferredPrimaryPage)(browser), adsPage);
});
// Проверяем, что при отсутствии Ads Manager helper возвращает первую доступную вкладку.
(0, node_test_1.default)('findPreferredPrimaryPage falls back to first available page', () => {
    const firstPage = { isClosed: () => false, url: () => 'https://www.facebook.com/' };
    const browser = {
        contexts: () => [
            { pages: () => [firstPage] },
        ],
    };
    strict_1.default.equal((0, session_manager_js_1.findPreferredPrimaryPage)(browser), firstPage);
});
// Проверяем, что закрытая вкладка не возвращается как рабочая primaryPage.
(0, node_test_1.default)('findPreferredPrimaryPage игнорирует закрытые вкладки', () => {
    const closedAdsPage = { isClosed: () => true, url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const openAdsPage = { isClosed: () => false, url: () => 'https://adsmanager.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            { pages: () => [closedAdsPage, openAdsPage] },
        ],
    };
    strict_1.default.equal((0, session_manager_js_1.findPreferredPrimaryPage)(browser), openAdsPage);
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
// Проверяем, что профиль без CDP-порта не перезапускается, если recovery явно выключен.
(0, node_test_1.default)('startBrowser does not restart missing cdp profile when auto recovery is disabled', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const previousFlag = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    process.env.VISION_AUTO_RESTART_ON_MISSING_CDP = 'false';
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
// Проверяем, что профиль без CDP-порта перезапускается по умолчанию.
(0, node_test_1.default)('startBrowser restarts missing cdp profile by default', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const previousFlag = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    delete process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    const adsPage = { url: () => 'https://www.facebook.com/adsmanager/manage/campaigns' };
    const browser = {
        contexts: () => [
            {
                addInitScript: async () => { },
                pages: () => [adsPage],
            },
        ],
    };
    let restartCalls = 0;
    const originalResolveFolderId = vision_client_js_1.VisionClient.prototype.resolveFolderId;
    const originalGetProfile = vision_client_js_1.VisionClient.prototype.getProfile;
    const originalWaitUntilProfileHasPort = vision_client_js_1.VisionClient.prototype.waitUntilProfileHasPort;
    const originalRestartProfileToRecoverPort = vision_client_js_1.VisionClient.prototype.restartProfileToRecoverPort;
    const originalWaitUntilCdpReady = vision_client_js_1.VisionClient.prototype.waitUntilCdpReady;
    const originalConnectOverCDP = playwright_1.chromium.connectOverCDP;
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
        restartCalls += 1;
        return { folder_id: 'folder-1', profile_id: 'profile-1', port: 7101 };
    };
    vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = async function waitUntilCdpReady() {
        return true;
    };
    playwright_1.chromium.connectOverCDP = async () => browser;
    try {
        const session = await manager.startBrowser({
            visionXToken: 'token',
            visionApiUrl: 'http://127.0.0.1:3030',
            visionProfileId: 'profile-1',
        });
        strict_1.default.equal(restartCalls, 1);
        strict_1.default.equal(session.cdpPort, 7101);
        strict_1.default.equal(session.primaryPage, adsPage);
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
        vision_client_js_1.VisionClient.prototype.waitUntilCdpReady = originalWaitUntilCdpReady;
        playwright_1.chromium.connectOverCDP = originalConnectOverCDP;
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
// rememberAdsManagerUrl: пишет на сессию только URL Ads Manager, чужие/прочие URL игнорирует.
(0, node_test_1.default)('rememberAdsManagerUrl записывает только URL Ads Manager', () => {
    const session = makeSession({ lastAdsManagerUrl: null });
    (0, session_manager_js_1.rememberAdsManagerUrl)(session, { url: () => 'https://www.facebook.com/messages/' });
    strict_1.default.equal(session.lastAdsManagerUrl, null);
    (0, session_manager_js_1.rememberAdsManagerUrl)(session, { url: () => 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=1' });
    strict_1.default.equal(session.lastAdsManagerUrl, 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=1');
});
// ensureAdsManagerPage: первый цикл (act ещё неизвестен, нет fallbackUrl) → trust-on-first-use:
// принимаем открытую вкладку Ads Manager и запоминаем URL (далее act известен → строгая сверка).
(0, node_test_1.default)('ensureAdsManagerPage возвращает живую вкладку Ads Manager и запоминает URL (TOFU)', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const adsUrl = 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=123';
    const adsPage = { isClosed: () => false, url: () => adsUrl };
    const browser = {
        isConnected: () => true,
        contexts: () => [{ pages: () => [adsPage] }],
    };
    const session = makeSession({ browser, primaryPage: null });
    const page = await manager.ensureAdsManagerPage(session);
    strict_1.default.equal(page, adsPage);
    strict_1.default.equal(session.primaryPage, adsPage);
    strict_1.default.equal(session.lastAdsManagerUrl, adsUrl);
});
// ensureAdsManagerPage: браузер/CDP мертвы → бросаем (восстановление эскалируется на observer).
(0, node_test_1.default)('ensureAdsManagerPage бросает, если браузер отключён', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const browser = { isConnected: () => false, contexts: () => [] };
    const session = makeSession({ browser, primaryPage: null });
    await strict_1.default.rejects(manager.ensureAdsManagerPage(session), /Основная страница браузера недоступна/);
});
// ensureAdsManagerPage: вкладку закрыли, браузер жив → переоткрываем НОВУЮ вкладку на known-good
// URL кабинета; чужую вкладку не трогаем (её goto не зовём).
(0, node_test_1.default)('ensureAdsManagerPage переоткрывает вкладку на known-good URL, чужие не трогает', async () => {
    const manager = new session_manager_js_1.SessionManager();
    let otherGotoCalls = 0;
    const otherPage = {
        isClosed: () => false,
        url: () => 'https://www.facebook.com/messages/',
        goto: async () => {
            otherGotoCalls += 1;
        },
    };
    const knownUrl = 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=777';
    let gotoUrl = null;
    let newPageCalls = 0;
    const newPage = {
        isClosed: () => false,
        url: () => knownUrl,
        goto: async (u) => {
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
    const session = makeSession({ browser, primaryPage: null, lastAdsManagerUrl: knownUrl });
    const page = await manager.ensureAdsManagerPage(session);
    strict_1.default.equal(newPageCalls, 1);
    strict_1.default.equal(gotoUrl, knownUrl);
    strict_1.default.equal(page, newPage);
    strict_1.default.equal(session.primaryPage, newPage);
    strict_1.default.equal(otherGotoCalls, 0); // чужую вкладку не трогали
});
// ensureAdsManagerPage: нет вкладки и нет known-good/fallback URL → бросаем
// (в общем кабинете НЕ угадываем дефолтный act, иначе можно попасть в чужой кабинет).
(0, node_test_1.default)('ensureAdsManagerPage бросает, если URL кабинета неизвестен', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const context = {
        pages: () => [],
        newPage: async () => {
            throw new Error('newPage не должен вызываться без известного URL');
        },
    };
    const browser = { isConnected: () => true, contexts: () => [context] };
    const session = makeSession({ browser, primaryPage: null, lastAdsManagerUrl: null });
    await strict_1.default.rejects(manager.ensureAdsManagerPage(session), /Основная страница браузера недоступна/);
});
// ensureAdsManagerPage: known-good URL нет, но передан fallbackUrl (реконструированный из act_id) →
// открываем новую вкладку на нём.
(0, node_test_1.default)('ensureAdsManagerPage использует fallbackUrl, если known-good URL нет', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const fallbackUrl = 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=555';
    let gotoUrl = null;
    const newPage = {
        isClosed: () => false,
        url: () => fallbackUrl,
        goto: async (u) => {
            gotoUrl = u;
        },
    };
    const context = { pages: () => [], newPage: async () => newPage };
    const browser = { isConnected: () => true, contexts: () => [context] };
    const session = makeSession({ browser, primaryPage: null, lastAdsManagerUrl: null });
    const page = await manager.ensureAdsManagerPage(session, { fallbackUrl });
    strict_1.default.equal(gotoUrl, fallbackUrl);
    strict_1.default.equal(page, newPage);
});
// ensureAdsManagerPage: открыта вкладка ДРУГОГО кабинета (act≠ожидаемому) → не сканируем чужой,
// переоткрываем СВОЙ кабинет. Защита от тихой слепоты MV при нескольких кабинетах владельца.
(0, node_test_1.default)('ensureAdsManagerPage не подхватывает вкладку другого кабинета (act mismatch)', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const foreignAds = {
        isClosed: () => false,
        url: () => 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=999',
    };
    const ownUrl = 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=111';
    let gotoUrl = null;
    const ownPage = {
        isClosed: () => false,
        url: () => ownUrl,
        goto: async (u) => {
            gotoUrl = u;
        },
    };
    const context = { pages: () => [foreignAds], newPage: async () => ownPage };
    const browser = { isConnected: () => true, contexts: () => [context] };
    const session = makeSession({ browser, primaryPage: null, lastAdsManagerUrl: ownUrl });
    const page = await manager.ensureAdsManagerPage(session);
    strict_1.default.equal(gotoUrl, ownUrl); // переоткрыли СВОЙ кабинет, а не сканируем чужой act=999
    strict_1.default.equal(page, ownPage);
});
// ensureAdsManagerPage: открыта вкладка ТОГО ЖE кабинета (act совпал) → используем её, не переоткрываем.
(0, node_test_1.default)('ensureAdsManagerPage принимает вкладку того же кабинета (act match)', async () => {
    const manager = new session_manager_js_1.SessionManager();
    const liveUrl = 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=111&business_id=5';
    const sameAds = { isClosed: () => false, url: () => liveUrl };
    const context = {
        pages: () => [sameAds],
        newPage: async () => {
            throw new Error('переоткрывать не нужно — кабинет тот же');
        },
    };
    const browser = { isConnected: () => true, contexts: () => [context] };
    const session = makeSession({
        browser,
        primaryPage: null,
        lastAdsManagerUrl: 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=111',
    });
    const page = await manager.ensureAdsManagerPage(session);
    strict_1.default.equal(page, sameAds);
    strict_1.default.equal(session.lastAdsManagerUrl, liveUrl); // обновили на текущий URL той же вкладки
});
//# sourceMappingURL=session-manager.test.js.map