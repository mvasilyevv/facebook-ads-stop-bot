import type { Page } from 'playwright';
import { type AmScanConfig } from './am-config.js';
import type { ScannedAdRow } from '../types.js';
export interface GraphContext {
    accessToken: string;
    actId: string;
    apiVersion: string;
    graphOrigin: string;
}
export declare function extractGraphContext(page: Page, timeoutMs?: number): Promise<GraphContext>;
export declare function invalidateGraphContext(sessionId: string): void;
export declare function reconstructAdsManagerUrl(sessionId: string): string | null;
export declare function acquireGraphContext(page: Page, sessionId: string, opts?: {
    forceRefresh?: boolean;
}): Promise<{
    ctx: GraphContext;
    sniffed: boolean;
}>;
export interface AmScanResult {
    rows: ScannedAdRow[];
    diagnostics: {
        adCountMetrics: number;
        adCountNames: number;
        namesResolved: number;
        statusResolved: number;
        amError?: string;
        nameError?: string;
        authExpired?: boolean;
        adsEdgeOnly: number;
        adsEdgeOnlySample: string[];
        metricsOnly: number;
        campaigns: Array<{
            id: string;
            name: string;
            adCount: number;
        }>;
        scopeCampaignCount: number;
        ownerResolved: boolean;
    };
}
export declare function listOwnerCampaigns(page: Page, ownerTag: string, sessionId: string): Promise<Array<{
    id: string;
    name: string;
}>>;
export declare function runAmScan(page: Page, config: AmScanConfig): Promise<AmScanResult>;
export declare function runAmScanWithContext(page: Page, ctx: GraphContext, config: AmScanConfig): Promise<AmScanResult>;
//# sourceMappingURL=am-fetch.d.ts.map