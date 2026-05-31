export interface AmColumn {
    name: string;
    type?: string;
    attribution_window?: string;
}
export interface AmActionValue {
    types?: string[];
    values?: string[];
    breakdown?: string;
}
export interface AmResultValue {
    indicator?: string;
    value?: string;
}
export interface AmRow {
    adId: string;
    objective: string | null;
    atomic: Record<string, string>;
    actions: Record<string, string>;
    costPerAction: Record<string, string>;
    outboundClicks: string | null;
    outboundCtr: string | null;
    results: string | null;
    costPerResult: string | null;
}
export interface LightMeta {
    id: string;
    name?: string;
    effectiveStatus?: string;
    campaignId?: string;
    adsetId?: string;
    dailyBudget?: string;
    lifetimeBudget?: string;
}
export declare function parseAmTabular(body: unknown): AmRow[];
export declare function mergeAmRows(rows: AmRow[]): Map<string, AmRow>;
export declare function parseLightList(body: unknown): LightMeta[];
export declare function lightNextCursor(body: unknown): string | null;
//# sourceMappingURL=am-parser.d.ts.map