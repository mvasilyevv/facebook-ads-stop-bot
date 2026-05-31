import type { Browser, Page } from 'playwright';
import type { BrowserSession } from './types.js';
export declare function isAdsManagerUrl(url: string | null | undefined): boolean;
/** Достаёт numeric ad-account id из URL Ads Manager (?act=<num>). null, если не читается. */
export declare function extractAdAccountId(url: string | null | undefined): string | null;
export declare function findPreferredPrimaryPage(browser: Browser | null): Page | null;
/** Запоминает URL живой вкладки Ads Manager на сессии — чтобы переоткрыть её при self-heal. */
export declare function rememberAdsManagerUrl(session: BrowserSession, page: Page | null | undefined): void;
/** Менеджер браузерных сессий: запуск, подключение, отключение, переподключение. */
export declare class SessionManager {
    private sessions;
    startBrowser(options: {
        visionXToken: string;
        visionApiUrl: string;
        visionProfileId: string;
        visionFolderId?: string;
        viewportWidth?: number;
        viewportHeight?: number;
    }): Promise<BrowserSession>;
    disconnectBrowser(sessionId: string): Promise<void>;
    stopBrowser(sessionId: string): Promise<void>;
    reconnectBrowser(sessionId: string, options?: {
        visionXToken?: string;
        visionApiUrl?: string;
        visionProfileId?: string;
    }): Promise<BrowserSession>;
    getSession(sessionId: string): BrowserSession;
    getPreferredSession(): BrowserSession;
    /**
     * Гарантирует живую primary-вкладку Ads Manager для скан-цикла (self-heal Layer 1).
     *
     * Сценарии:
     *  - Живая вкладка Ads Manager НАШЕГО кабинета открыта → используем её (и запоминаем URL).
     *  - Открыта вкладка ДРУГОГО кабинета (act не совпал с ожидаемым) → не сканируем чужой act,
     *    переоткрываем свой кабинет ниже. Защита от тихой слепоты MV при нескольких кабинетах.
     *  - Вкладку закрыли, но CDP/браузер живы → переоткрываем НОВУЮ вкладку на последнем
     *    known-good URL кабинета (или реконструированном из act_id). Чужие вкладки не трогаем.
     *  - Браузер/CDP мертвы или URL кабинета неизвестен → бросаем
     *    'Основная страница браузера недоступна' (эскалация на observer: reconnect/StartBrowser).
     *
     * В общем кабинете НЕ угадываем дефолтный act — иначе можно открыть чужой кабинет.
     */
    ensureAdsManagerPage(session: BrowserSession, opts?: {
        fallbackUrl?: string;
    }): Promise<Page>;
    listSessions(): Array<{
        id: string;
        status: string;
        connectedAt: string;
    }>;
    private connectOverReadyCdp;
    private restartProfileForMissingCdp;
}
//# sourceMappingURL=session-manager.d.ts.map