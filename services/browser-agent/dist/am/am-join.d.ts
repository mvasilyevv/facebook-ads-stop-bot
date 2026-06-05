import type { ScannedAdRow } from '../types.js';
import type { AmRow } from './am-parser.js';
export interface AmAdMeta {
    adName?: string;
    adsetName?: string;
    campaignName?: string;
    campaignId?: string;
    effectiveStatus?: string;
    budget?: string;
}
export declare function mapEffectiveStatus(status: string | undefined): string;
export declare function buildScannedRow(am: AmRow, meta?: AmAdMeta): ScannedAdRow;
export declare function buildScannedRows(merged: Map<string, AmRow>, adMeta: Map<string, AmAdMeta>): ScannedAdRow[];
//# sourceMappingURL=am-join.d.ts.map