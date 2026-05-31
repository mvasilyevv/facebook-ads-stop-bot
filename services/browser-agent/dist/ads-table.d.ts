import type { Page, ElementHandle } from 'playwright';
import type { ScrollMetrics } from './types.js';
export declare function getAdsTableScrollAnchor(page: Page): Promise<[number, number] | null>;
export declare function resetAdsTableScroll(page: Page): Promise<number>;
export declare function getAdsTableScrollMetrics(page: Page): Promise<ScrollMetrics>;
export declare function getVisibleAdsTableRowIds(page: Page): Promise<string[]>;
export declare function toggleCellSelector(fbAdId: string): string;
export declare function findToggleCellInDom(page: Page, fbAdId: string): Promise<ElementHandle | null>;
export declare function readToggleAriaChecked(page: Page, fbAdId: string): Promise<string>;
export declare function findToggleCellWithTableScan(page: Page, fbAdId: string, options?: {
    resetToTop?: boolean;
    maxScrollPasses?: number;
    stepPx?: number;
    fallbackMaxSteps?: number;
    isCancelled?: () => boolean;
}): Promise<ElementHandle | null>;
export declare function scrollAdsTableDown(page: Page, stepPx?: number, isCancelled?: () => boolean): Promise<ScrollMetrics>;
//# sourceMappingURL=ads-table.d.ts.map