import type { VisionProfile } from './types.js';

// Таймаут по умолчанию на любой HTTP-вызов к Vision API. Без него зависший (но
// живой) Vision-процесс держал бы сокет открытым → StartBrowser/Reconnect/Stop
// висели бы ВЕЧНО, а поллящие циклы (waitUntilProfileHasPort/...) никогда не
// проверили бы собственный deadline (поток стоит на await fetch). См. аудит BA-1.
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
// Проба CDP /json/version — короче основного таймаута, т.к. вызывается в поллящем
// цикле: один зависший probe не должен съедать весь deadline ожидания CDP.
const CDP_PROBE_TIMEOUT_MS = 5_000;

/** HTTP-клиент для локального антидетект-браузера Vision на localhost:3030. */
export class VisionClient {
  private readonly baseUrl: string;
  private readonly xToken: string;
  private readonly requestTimeoutMs: number;

  constructor(
    xToken: string,
    baseUrl = 'http://127.0.0.1:3030',
    options?: { requestTimeoutMs?: number },
  ) {
    if (!xToken) throw new Error('Не задан VISION_X_TOKEN');
    this.xToken = xToken;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.requestTimeoutMs = options?.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  }

  /** fetch с жёстким таймаутом через AbortController. Аборт → понятная ошибка. */
  private async fetchWithTimeout(
    url: string,
    init: RequestInit,
    timeoutMs: number,
  ): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: controller.signal });
    } catch (err) {
      if (controller.signal.aborted) {
        throw new Error(`Vision API ${url} не ответил за ${timeoutMs}ms (timeout)`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  private async request<T>(path: string): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await this.fetchWithTimeout(
      url,
      { headers: { 'X-Token': this.xToken } },
      this.requestTimeoutMs,
    );
    if (!res.ok) {
      throw new Error(`API Vision вернул ошибку ${res.status}: ${await res.text()}`);
    }
    return res.json() as Promise<T>;
  }

  async listProfiles(): Promise<VisionProfile[]> {
    const data = await this.request<{ profiles: Array<{ folder_id: string; profile_id: string; port: number | null }> }>(
      '/list',
    );
    return (data.profiles || []).map((p) => ({
      folder_id: p.folder_id,
      profile_id: p.profile_id,
      port: p.port ?? null,
    }));
  }

  async getProfile(profileId: string): Promise<VisionProfile | null> {
    const profiles = await this.listProfiles();
    return profiles.find((p) => p.profile_id === profileId) ?? null;
  }

  async waitUntilProfileStopped(profileId: string, timeoutSec = 15, pollIntervalSec = 1): Promise<boolean> {
    const deadline = Date.now() + timeoutSec * 1000;
    while (Date.now() < deadline) {
      const profile = await this.getProfile(profileId);
      if (!profile) return true;
      await sleep(pollIntervalSec * 1000);
    }
    return false;
  }

  async waitUntilProfileHasPort(profileId: string, timeoutSec = 15, pollIntervalSec = 1): Promise<number | null> {
    const deadline = Date.now() + timeoutSec * 1000;
    while (Date.now() < deadline) {
      const profile = await this.getProfile(profileId);
      if (profile?.port) return profile.port;
      await sleep(pollIntervalSec * 1000);
    }
    return null;
  }

  async waitUntilCdpReady(port: number, timeoutSec = 20, pollIntervalSec = 1): Promise<boolean> {
    const deadline = Date.now() + timeoutSec * 1000;
    const versionUrl = `${this.cdpUrl(port)}/json/version`;
    while (Date.now() < deadline) {
      try {
        const res = await this.fetchWithTimeout(versionUrl, {}, CDP_PROBE_TIMEOUT_MS);
        if (res.ok) {
          const data = await res.json() as { webSocketDebuggerUrl?: string | null };
          if (typeof data.webSocketDebuggerUrl === 'string' && data.webSocketDebuggerUrl.length > 0) {
            return true;
          }
        }
      } catch {
        // Пока локальный CDP endpoint не поднялся, просто продолжаем ждать.
      }
      await sleep(pollIntervalSec * 1000);
    }
    return false;
  }

  async resolveFolderId(profileId: string): Promise<string> {
    const profile = await this.getProfile(profileId);
    if (!profile) throw new Error(`Профиль ${profileId} не найден в /list`);
    return profile.folder_id;
  }

  async startProfile(
    folderId: string,
    profileId: string,
    options?: {
      portWaitTimeoutSec?: number;
    },
  ): Promise<VisionProfile> {
    const portWaitTimeoutSec = options?.portWaitTimeoutSec ?? 15;
    const path = `/start/${folderId}/${profileId}`;
    try {
      const data = await this.request<{ port?: number | null; cdp_port?: number | null }>(path);
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
    } catch (err) {
      // Запасной вариант: пробуем через GET /list после старта.
      const profile = await this.getProfile(profileId);
      if (profile?.port) return profile;
      throw err;
    }
  }

  async stopProfile(folderId: string, profileId: string): Promise<void> {
    await this.request<void>(`/stop/${folderId}/${profileId}`);
  }

  async restartProfileToRecoverPort(
    folderId: string,
    profileId: string,
    options?: {
      stopTimeoutSec?: number;
      portWaitTimeoutSec?: number;
      settleAfterStopMs?: number;
    },
  ): Promise<VisionProfile> {
    const stopTimeoutSec = options?.stopTimeoutSec ?? 20;
    const portWaitTimeoutSec = options?.portWaitTimeoutSec ?? 20;
    const settleAfterStopMs = options?.settleAfterStopMs ?? 1_000;

    // Если профиль застрял без CDP-порта, нужен полный stop/start через Vision API.
    await this.stopProfile(folderId, profileId);

    const stopped = await this.waitUntilProfileStopped(profileId, stopTimeoutSec);
    if (!stopped) {
      throw new Error(
        `Профиль ${profileId} не остановился перед перезапуском для восстановления CDP-порта`,
      );
    }

    // Даем Vision короткую паузу очистить старый процесс перед новым стартом.
    await sleep(settleAfterStopMs);

    return this.startProfile(folderId, profileId, {
      portWaitTimeoutSec,
    });
  }

  cdpUrl(port: number): string {
    return `http://127.0.0.1:${port}`;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
