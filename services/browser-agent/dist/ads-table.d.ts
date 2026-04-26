import type { Page, ElementHandle } from 'playwright';
import type { ScrollMetrics } from './types.js';
import { type ColumnWidthTarget } from './ads-columns.js';
export { REQUIRED_COLUMNS } from './ads-columns.js';
export interface ColumnValidationResult {
    valid: boolean;
    missingColumns: string[];
    foundColumns: string[];
    errorMessage: string;
}
export interface ColumnWidthApplyResult {
    applied: boolean;
    matchedColumns: string[];
    missingColumns: string[];
    errorMessage: string;
    adjustedCells: number;
    totalWidthPx: number;
}
export interface ColumnWidthCaptureResult {
    captured: boolean;
    columnWidths: ColumnWidthTarget[];
    matchedColumns: string[];
    errorMessage: string;
    totalWidthPx: number;
}
/** Проверить наличие всех необходимых колонок в таблице Ads Manager. */
export declare function validateAdsTableColumns(page: Page): Promise<ColumnValidationResult>;
/** Снять текущую ручную ширину видимых и горизонтально доступных колонок Ads Manager. */
export declare function captureAdsTableColumnWidths(page: Page): Promise<ColumnWidthCaptureResult>;
/** Применить сохранённый пресет ширины колонок Ads Manager без запуска сканирования. */
export declare function applyAdsTableColumnWidthPreset(page: Page, savedTargets?: ColumnWidthTarget[]): Promise<ColumnWidthApplyResult>;
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
}): Promise<ElementHandle | null>;
export declare function scrollAdsTableDown(page: Page, stepPx?: number): Promise<ScrollMetrics>;
//# sourceMappingURL=ads-table.d.ts.map