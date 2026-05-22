import type { Page } from 'playwright';
export interface HardReloadResult {
    success: boolean;
    errorMessage: string;
    reloadMs: number;
}
export declare function hardReloadPage(page: Page, bypassCache: boolean): Promise<HardReloadResult>;
//# sourceMappingURL=hard-reload.d.ts.map