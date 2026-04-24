"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SessionManager = void 0;
exports.isAdsManagerUrl = isAdsManagerUrl;
exports.findPreferredPrimaryPage = findPreferredPrimaryPage;
const playwright_1 = require("playwright");
const uuid_1 = require("uuid");
const vision_client_js_1 = require("./vision-client.js");
const stealth_js_1 = require("./stealth.js");
const humanizer_js_1 = require("./humanizer.js");
const EXISTING_PROFILE_PORT_GRACE_SECONDS = 8;
const START_PROFILE_PORT_WAIT_SECONDS = 20;
const CDP_READY_WAIT_SECONDS = 20;
const RECOVERY_STOP_TIMEOUT_SECONDS = 20;
const RECOVERY_SETTLE_DELAY_MS = 1_000;
const ADS_MANAGER_URL_MARKERS = ['adsmanager', 'facebook.com/ads'];
const DISABLED_FLAG_VALUES = new Set(['0', 'false', 'no', 'off']);
function isAutoRestartOnMissingCdpEnabled() {
    const rawValue = process.env.VISION_AUTO_RESTART_ON_MISSING_CDP;
    if (rawValue == null || String(rawValue).trim() === '') {
        return true;
    }
    const normalized = String(rawValue).trim().toLowerCase();
    if (DISABLED_FLAG_VALUES.has(normalized)) {
        return false;
    }
    return true;
}
function isAdsManagerUrl(url) {
    const normalized = String(url || '').toLowerCase();
    return ADS_MANAGER_URL_MARKERS.some((marker) => normalized.includes(marker));
}
function findPreferredPrimaryPage(browser) {
    if (!browser) {
        return null;
    }
    let fallbackPage = null;
    for (const context of browser.contexts()) {
        for (const page of context.pages()) {
            fallbackPage = fallbackPage || page;
            if (isAdsManagerUrl(page.url())) {
                return page;
            }
        }
    }
    return fallbackPage;
}
/** Менеджер браузерных сессий: запуск, подключение, отключение, переподключение. */
class SessionManager {
    sessions = new Map();
    async startBrowser(options) {
        const { visionXToken, visionApiUrl, visionProfileId, visionFolderId, } = options;
        const visionClient = new vision_client_js_1.VisionClient(visionXToken, visionApiUrl);
        // Vision API иногда требует folder_id отдельно, поэтому восстанавливаем его по profile_id.
        let folderId = visionFolderId;
        if (!folderId) {
            folderId = await visionClient.resolveFolderId(visionProfileId);
        }
        const existingProfile = await visionClient.getProfile(visionProfileId);
        let profile;
        if (existingProfile?.port) {
            // Не стартуем второй экземпляр профиля, иначе можно потерять открытую вкладку.
            profile = { port: existingProfile.port };
        }
        else if (existingProfile) {
            // У Vision порт иногда появляется с задержкой, поэтому сначала даем ему короткий grace period.
            const delayedPort = await visionClient.waitUntilProfileHasPort(visionProfileId, EXISTING_PROFILE_PORT_GRACE_SECONDS);
            if (delayedPort) {
                profile = { port: delayedPort };
            }
            else if (isAutoRestartOnMissingCdpEnabled()) {
                // Перезапуск уже открытого профиля потенциально разрушителен, поэтому он только по feature flag.
                profile = await this.restartProfileForMissingCdp(visionClient, folderId, visionProfileId);
            }
            else {
                throw buildMissingCdpRestartDisabledError(visionProfileId);
            }
        }
        else {
            try {
                // Если Vision не поднял CDP-порт, рестарт разрешён только явным feature flag.
                profile = await visionClient.startProfile(folderId, visionProfileId, {
                    portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
                });
            }
            catch (error) {
                if (!isMissingCdpPortError(error)) {
                    throw error;
                }
                if (!isAutoRestartOnMissingCdpEnabled()) {
                    throw buildMissingCdpRestartDisabledError(visionProfileId);
                }
                profile = await this.restartProfileForMissingCdp(visionClient, folderId, visionProfileId);
            }
        }
        if (!profile.port) {
            throw new Error(`У профиля ${visionProfileId} нет CDP-порта`);
        }
        // Подключаемся через CDP как внешний клиент, не владеющий жизненным циклом браузера.
        const playwright = playwright_1.chromium;
        const browser = await this.connectOverReadyCdp(visionClient, visionProfileId, profile.port);
        // Stealth добавляем в существующий контекст, не пересоздавая профиль и вкладки.
        const contexts = browser.contexts();
        if (contexts.length > 0) {
            await contexts[0].addInitScript(stealth_js_1.STEALTH_INIT_SCRIPT);
        }
        // Берем уже открытую страницу и никуда ее не перенаправляем.
        let primaryPage = findPreferredPrimaryPage(browser);
        if (!primaryPage && contexts[0]) {
            primaryPage = await contexts[0].newPage();
        }
        // Для CDP-вкладки Vision нельзя насильно ставить setViewportSize:
        // Playwright включает эмуляцию viewport и справа появляется белая полоса,
        // если реальное окно профиля шире 1280px. Сохраняем нативную геометрию окна.
        // Профиль "человечности" фиксируем на сессию, чтобы движения не прыгали между вызовами.
        const humanProfile = (0, humanizer_js_1.generateHumanProfile)();
        const session = {
            id: (0, uuid_1.v4)(),
            visionApiUrl,
            visionXToken,
            visionProfileId,
            visionFolderId: folderId,
            cdpPort: profile.port,
            playwright,
            browser,
            primaryPage,
            humanProfile,
            connectedAt: new Date(),
            status: 'connected',
        };
        this.sessions.set(session.id, session);
        return session;
    }
    async disconnectBrowser(sessionId) {
        const session = this.getSession(sessionId);
        // Для CDP-подключения browser.close() закрывает сам удалённый Vision-профиль.
        // Здесь нужен только логический разрыв сессии на стороне browser-agent.
        session.browser = null;
        session.primaryPage = null;
        session.playwright = null;
        session.status = 'disconnected';
    }
    async stopBrowser(sessionId) {
        const session = this.getSession(sessionId);
        // Здесь stopBrowser уже осознанно завершает удаленный профиль Vision.
        if (session.browser) {
            try {
                await session.browser.close();
            }
            catch {
                // Ошибка закрытия не должна мешать остановке профиля через Vision API.
            }
        }
        // Завершаем профиль через штатный API Vision после закрытия CDP-клиента.
        try {
            const visionClient = new vision_client_js_1.VisionClient(session.visionXToken, session.visionApiUrl);
            await visionClient.stopProfile(session.visionFolderId, session.visionProfileId);
        }
        catch {
            // Повторная остановка может упасть, если профиль уже закрыт пользователем.
        }
        this.sessions.delete(sessionId);
    }
    async reconnectBrowser(sessionId, options) {
        const session = this.getSession(sessionId);
        const visionXToken = options?.visionXToken ?? session.visionXToken;
        const visionApiUrl = options?.visionApiUrl ?? session.visionApiUrl;
        const visionProfileId = options?.visionProfileId ?? session.visionProfileId;
        const visionClient = new vision_client_js_1.VisionClient(visionXToken, visionApiUrl);
        // Переподключение сначала пытается забрать уже существующий CDP-порт и не трогать окно профиля.
        const existingProfile = await visionClient.getProfile(visionProfileId);
        const resolvedFolderId = session.visionProfileId === visionProfileId
            ? session.visionFolderId
            : await visionClient.resolveFolderId(visionProfileId);
        let resolvedPort = existingProfile?.port ?? null;
        if (!resolvedPort && existingProfile) {
            resolvedPort = await visionClient.waitUntilProfileHasPort(visionProfileId, EXISTING_PROFILE_PORT_GRACE_SECONDS);
        }
        if (!resolvedPort && existingProfile && isAutoRestartOnMissingCdpEnabled()) {
            const recoveredProfile = await this.restartProfileForMissingCdp(visionClient, resolvedFolderId, visionProfileId);
            resolvedPort = recoveredProfile.port;
        }
        if (!resolvedPort) {
            if (existingProfile) {
                throw buildMissingCdpRestartDisabledError(visionProfileId);
            }
            throw new Error(`Профиль ${visionProfileId} не запущен или не имеет CDP-порта`);
        }
        const browser = await this.connectOverReadyCdp(visionClient, visionProfileId, resolvedPort);
        // Повторно добавляем stealth в существующий контекст после нового CDP-подключения.
        const contexts = browser.contexts();
        if (contexts.length > 0) {
            await contexts[0].addInitScript(stealth_js_1.STEALTH_INIT_SCRIPT);
        }
        // Сохраняем текущую вкладку как primaryPage, чтобы восстановить работу без навигации.
        let primaryPage = findPreferredPrimaryPage(browser);
        if (!primaryPage && contexts[0]) {
            primaryPage = await contexts[0].newPage();
        }
        session.browser = browser;
        session.primaryPage = primaryPage;
        session.playwright = playwright_1.chromium;
        session.cdpPort = resolvedPort;
        session.status = 'connected';
        session.connectedAt = new Date();
        session.visionXToken = visionXToken;
        session.visionApiUrl = visionApiUrl;
        session.visionProfileId = visionProfileId;
        session.visionFolderId = resolvedFolderId;
        return session;
    }
    getSession(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) {
            throw new Error(`Сессия ${sessionId} не найдена`);
        }
        return session;
    }
    listSessions() {
        const result = [];
        for (const [id, session] of this.sessions) {
            result.push({
                id,
                status: session.status,
                connectedAt: session.connectedAt.toISOString(),
            });
        }
        return result;
    }
    async connectOverReadyCdp(visionClient, profileId, port) {
        const ready = await visionClient.waitUntilCdpReady(port, CDP_READY_WAIT_SECONDS);
        if (!ready) {
            throw new Error(`CDP endpoint профиля ${profileId} на порту ${port} не стал доступен`);
        }
        const cdpUrl = `http://127.0.0.1:${port}`;
        return playwright_1.chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
    }
    async restartProfileForMissingCdp(visionClient, folderId, profileId) {
        try {
            return await visionClient.restartProfileToRecoverPort(folderId, profileId, {
                stopTimeoutSec: RECOVERY_STOP_TIMEOUT_SECONDS,
                portWaitTimeoutSec: START_PROFILE_PORT_WAIT_SECONDS,
                settleAfterStopMs: RECOVERY_SETTLE_DELAY_MS,
            });
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`Не удалось восстановить CDP-порт автоперезапуском профиля ${profileId}: ${message}`);
        }
    }
}
exports.SessionManager = SessionManager;
function buildMissingCdpRestartDisabledError(profileId) {
    return new Error(`Профиль ${profileId} запущен без CDP-порта. `
        + 'Автоперезапуск профиля для восстановления CDP-порта отключён. '
        + 'Уберите VISION_AUTO_RESTART_ON_MISSING_CDP=false или перезапустите профиль вручную.');
}
function isMissingCdpPortError(error) {
    if (!(error instanceof Error)) {
        return false;
    }
    return error.message.toLowerCase().includes('cdp-порт');
}
//# sourceMappingURL=session-manager.js.map