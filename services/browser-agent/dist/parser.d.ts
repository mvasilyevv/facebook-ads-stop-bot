import type { Page } from 'playwright';
import type { ScannedAdRow } from './types.js';
type ParseRowsReader = (page: Page) => Promise<ScannedAdRow[]>;
type WaitForParsedAdsRowsOptions = {
    timeoutMs?: number;
    pollMs?: number;
    readRows?: ParseRowsReader;
    isCancelled?: () => boolean;
};
/** Нажать кнопку «Refresh» в Ads Manager. */
export declare function refreshTable(page: Page): Promise<boolean>;
/** Распарсить все видимые строки из текущей страницы. */
export declare function parseAdsFromPage(page: Page): Promise<ScannedAdRow[]>;
/** Дождаться, пока Meta вернет строки после краткого пустого состояния таблицы. */
export declare function waitForParsedAdsRows(page: Page, options?: WaitForParsedAdsRowsOptions): Promise<ScannedAdRow[]>;
export declare function detectLogicalDeliveryStatus(text?: string, toggleAriaChecked?: string): string;
export declare function parseIntValue(text?: string): number;
export declare function parseMoney(text?: string): string;
export declare function parseMoneyOrNull(text?: string): string | null;
export declare function parseDecimalOrNull(text?: string): string | null;
export declare function normalizeNumericText(text?: string): string | null;
/**
 * Строки, у которых все критические метрики (impressions/spend/cpm/cpc/ctr) пустые.
 * Используется для детекции STALE_DATA в observer.
 */
export declare function countEmptyMetricsRows(rows: ScannedAdRow[]): number;
/**
 * fb_ad_id строк, у которых пустые обязательные текстовые поля
 * (ad_name / campaign_name) — индикатор, что парсер не дочитал ячейки.
 */
export declare function findPartialRows(rows: ScannedAdRow[]): string[];
export {};
//# sourceMappingURL=parser.d.ts.map