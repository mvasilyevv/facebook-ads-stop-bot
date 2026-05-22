import type { Page } from 'playwright';
import type { ScannedAdRow } from './types.js';
export type ParseAdsResult = {
    rows: ScannedAdRow[];
    /** fb_ad_id строк, у которых какие-то метрики не прочитались (loading-spinner или missing cell). */
    partialRowIds: string[];
};
type ParseRowsReader = (page: Page) => Promise<ParseAdsResult>;
type WaitForParsedAdsRowsOptions = {
    timeoutMs?: number;
    pollMs?: number;
    readRows?: ParseRowsReader;
    isCancelled?: () => boolean;
};
/** Нажать кнопку «Refresh» в Ads Manager. */
export declare function refreshTable(page: Page): Promise<boolean>;
/** Распарсить все видимые строки из текущей страницы.
 *
 * Возвращает rows + partialRowIds. Партиал — это строки, у которых:
 *  - часть метрик пока в spinner-загрузке (Facebook ещё не отдал данные конкретно для этого объявления);
 *  - или часть ячеек не нашлась по координате/индексу (горизонтальная виртуализация).
 *
 * Эти случаи НЕ катастрофические — мы возвращаем строку с тем что прочиталось, observer
 * пишет snapshot, оценивает правила по доступным колонкам и помечает что часть данных
 * будет дочитана в следующем цикле. Throw остаётся ТОЛЬКО для одной катастрофы: в хедере
 * таблицы нет обязательных колонок (пользователь сам сломал layout Ads Manager).
 */
export declare function parseAdsFromPage(page: Page): Promise<ParseAdsResult>;
/** Дождаться, пока Meta вернет строки после краткого пустого состояния таблицы.
 *
 * Возвращает {rows, partialRowIds}. partialRowIds — это fb_ad_id строк, у которых
 * не дочитались часть колонок (skeleton-loader или missing-cell) — observer
 * пометит их как partial и дочитает в следующем цикле.
 */
export declare function waitForParsedAdsRows(page: Page, options?: WaitForParsedAdsRowsOptions): Promise<ParseAdsResult>;
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