export declare const AM_COLUMN_FIELDS: readonly string[];
export declare const AM_ACTION_TYPES: readonly string[];
export declare const AM_AD_DELIVERY_STATUSES: readonly string[];
export declare const AM_ATTRIBUTION_WINDOWS: readonly string[];
export declare const AM_PAGE_LIMIT = 5000;
export interface AmScanConfig {
    campaignIds: string[];
    ownerTag?: string;
    datePreset: string;
}
export declare function defaultAmConfig(campaignIds?: string[], ownerTag?: string): AmScanConfig;
//# sourceMappingURL=am-config.d.ts.map