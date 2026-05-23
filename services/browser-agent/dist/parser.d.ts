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
    /**
     * Максимально допустимая доля partial-строк, при которой результат считается «хорошим»
     * и возвращается сразу. Если фактическая доля выше — функция продолжает поллить, пока
     * partial не упадёт или не истечёт timeoutMs. Защищает от снепшота, в котором половина
     * метрик ещё в spinner-загрузке. По умолчанию 0.1 (10%).
     */
    maxPartialRatio?: number;
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
 * Adaptive wait: возвращает результат СРАЗУ если доля partial-строк низкая (< maxPartialRatio).
 * Если partial много (Facebook ещё подгружает метрики для большинства строк), продолжает
 * поллить страницу до тех пор пока:
 *   а) доля partial не упадёт ниже порога — возвращаем,
 *   б) не истечёт timeoutMs — возвращаем best-so-far результат (с наименьшим partial).
 *
 * Это защищает от ситуации, когда мы успели прочитать таблицу в плохой момент: spinner'ы
 * в большинстве ячеек дают snapshot с почти-пустыми метриками, по которому правила
 * не сработают. Ждём 1-5 секунд — Facebook успевает дозаполнить, snapshot становится
 * репрезентативным.
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
/**
 * Возвращает реальное количество объявлений в таблице Ads Manager из footer-строки
 * «Результаты, число объявлений: N». Это источник истины: позволяет понять, что
 * наш скан недосканил (allRows.length < total). Возвращает null, если строка
 * не нашлась/не распарсилась.
 */
export declare function getAdsTableTotalCount(page: Page): Promise<number | null>;
export {};
//# sourceMappingURL=parser.d.ts.map