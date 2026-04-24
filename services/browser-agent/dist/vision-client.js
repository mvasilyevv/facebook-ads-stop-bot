"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.VisionClient = void 0;
/** HTTP-клиент для локального антидетект-браузера Vision на localhost:3030. */
class VisionClient {
    baseUrl;
    xToken;
    constructor(xToken, baseUrl = 'http://127.0.0.1:3030') {
        if (!xToken)
            throw new Error('Не задан VISION_X_TOKEN');
        this.xToken = xToken;
        this.baseUrl = baseUrl.replace(/\/$/, '');
    }
    async request(path) {
        const url = `${this.baseUrl}${path}`;
        const res = await fetch(url, {
            headers: { 'X-Token': this.xToken },
        });
        if (!res.ok) {
            throw new Error(`API Vision вернул ошибку ${res.status}: ${await res.text()}`);
        }
        return res.json();
    }
    async listProfiles() {
        const data = await this.request('/list');
        return (data.profiles || []).map((p) => ({
            folder_id: p.folder_id,
            profile_id: p.profile_id,
            port: p.port ?? null,
        }));
    }
    async getProfile(profileId) {
        const profiles = await this.listProfiles();
        return profiles.find((p) => p.profile_id === profileId) ?? null;
    }
    async waitUntilProfileStopped(profileId, timeoutSec = 15, pollIntervalSec = 1) {
        const deadline = Date.now() + timeoutSec * 1000;
        while (Date.now() < deadline) {
            const profile = await this.getProfile(profileId);
            if (!profile)
                return true;
            await sleep(pollIntervalSec * 1000);
        }
        return false;
    }
    async waitUntilProfileHasPort(profileId, timeoutSec = 15, pollIntervalSec = 1) {
        const deadline = Date.now() + timeoutSec * 1000;
        while (Date.now() < deadline) {
            const profile = await this.getProfile(profileId);
            if (profile?.port)
                return profile.port;
            await sleep(pollIntervalSec * 1000);
        }
        return null;
    }
    async waitUntilCdpReady(port, timeoutSec = 20, pollIntervalSec = 1) {
        const deadline = Date.now() + timeoutSec * 1000;
        const versionUrl = `${this.cdpUrl(port)}/json/version`;
        while (Date.now() < deadline) {
            try {
                const res = await fetch(versionUrl);
                if (res.ok) {
                    const data = await res.json();
                    if (typeof data.webSocketDebuggerUrl === 'string' && data.webSocketDebuggerUrl.length > 0) {
                        return true;
                    }
                }
            }
            catch {
                // Пока локальный CDP endpoint не поднялся, просто продолжаем ждать.
            }
            await sleep(pollIntervalSec * 1000);
        }
        return false;
    }
    async resolveFolderId(profileId) {
        const profile = await this.getProfile(profileId);
        if (!profile)
            throw new Error(`Профиль ${profileId} не найден в /list`);
        return profile.folder_id;
    }
    async startProfile(folderId, profileId, options) {
        const portWaitTimeoutSec = options?.portWaitTimeoutSec ?? 15;
        const path = `/start/${folderId}/${profileId}`;
        try {
            const data = await this.request(path);
            const port = data.port ?? data.cdp_port ?? null;
            if (port) {
                return { folder_id: folderId, profile_id: profileId, port };
            }
            // Порт не вернулся сразу — поллим /list
            const resolvedPort = await this.waitUntilProfileHasPort(profileId, portWaitTimeoutSec);
            if (resolvedPort) {
                return { folder_id: folderId, profile_id: profileId, port: resolvedPort };
            }
            throw new Error(`Профиль ${profileId} запущен, но CDP-порт не вернулся`);
        }
        catch (err) {
            // Запасной вариант: пробуем через GET /list после старта.
            const profile = await this.getProfile(profileId);
            if (profile?.port)
                return profile;
            throw err;
        }
    }
    async stopProfile(folderId, profileId) {
        await this.request(`/stop/${folderId}/${profileId}`);
    }
    async restartProfileToRecoverPort(folderId, profileId, options) {
        const stopTimeoutSec = options?.stopTimeoutSec ?? 20;
        const portWaitTimeoutSec = options?.portWaitTimeoutSec ?? 20;
        const settleAfterStopMs = options?.settleAfterStopMs ?? 1_000;
        // Если профиль застрял без CDP-порта, нужен полный stop/start через Vision API.
        await this.stopProfile(folderId, profileId);
        const stopped = await this.waitUntilProfileStopped(profileId, stopTimeoutSec);
        if (!stopped) {
            throw new Error(`Профиль ${profileId} не остановился перед перезапуском для восстановления CDP-порта`);
        }
        // Даем Vision короткую паузу очистить старый процесс перед новым стартом.
        await sleep(settleAfterStopMs);
        return this.startProfile(folderId, profileId, {
            portWaitTimeoutSec,
        });
    }
    cdpUrl(port) {
        return `http://127.0.0.1:${port}`;
    }
}
exports.VisionClient = VisionClient;
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
//# sourceMappingURL=vision-client.js.map