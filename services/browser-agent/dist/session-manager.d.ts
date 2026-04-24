import type { Browser, Page } from 'playwright';
import type { BrowserSession } from './types.js';
export declare function isAdsManagerUrl(url: string | null | undefined): boolean;
export declare function findPreferredPrimaryPage(browser: Browser | null): Page | null;
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
    listSessions(): Array<{
        id: string;
        status: string;
        connectedAt: string;
    }>;
    private connectOverReadyCdp;
    private restartProfileForMissingCdp;
}
//# sourceMappingURL=session-manager.d.ts.map