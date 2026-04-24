import type { Page } from 'playwright';
import type { ScannedAdRow } from './types.js';
type ParseRowsReader = (page: Page) => Promise<ScannedAdRow[]>;
type WaitForParsedAdsRowsOptions = {
    timeoutMs?: number;
    pollMs?: number;
    readRows?: ParseRowsReader;
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
export {};
//# sourceMappingURL=parser.d.ts.map