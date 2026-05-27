import type { BrowserContext } from 'playwright';
export interface SearchAdsParams {
    country: string;
    query: string;
    activeStatus?: 'active' | 'inactive' | 'all';
    adType?: 'all' | 'political_and_issue_ads';
    searchType?: 'keyword_unordered' | 'keyword_exact_phrase' | 'page';
    /** Max pagination iterations. По умолчанию 5 (≈150 ads при first=30). */
    maxPages?: number;
    /** Размер страницы. По умолчанию 30 (как Meta UI). */
    pageSize?: number;
    timeoutMs?: number;
}
export interface SearchAdsResult {
    adCount: number;
    adsJson: string;
    durationMs: number;
    /** Сколько страниц pagination реально получено. */
    pagesFetched: number;
    error?: {
        code: number;
        type: string;
        message: string;
    };
}
export interface AdLibraryHealthResult {
    healthy: boolean;
    detail: string;
}
export interface SearchAdsBatchParams {
    country: string;
    queries: string[];
    activeStatus?: 'active' | 'inactive' | 'all';
    adType?: 'all' | 'political_and_issue_ads';
    searchType?: 'keyword_unordered' | 'keyword_exact_phrase' | 'page';
    maxPages?: number;
    pageSize?: number;
    perQueryTimeoutMs?: number;
}
export interface QueryResult {
    query: string;
    adCount: number;
    adsJson: string;
    durationMs: number;
    pagesFetched: number;
    error?: {
        code: number;
        type: string;
        message: string;
    };
}
export interface SearchAdsBatchResult {
    results: QueryResult[];
    totalDurationMs: number;
}
/**
 * Один поиск ads по keyword + country. Открывает Ad Library страницу для
 * извлечения токенов, потом делает 1+ GraphQL запросов с pagination.
 */
export declare function searchAds(context: BrowserContext, params: SearchAdsParams): Promise<SearchAdsResult>;
/**
 * Batch: открывает Ad Library один раз для country, прогоняет все queries
 * через прямой GraphQL fetch (переиспользуя токены из bootstrap).
 *
 * Каждый query получает свой pagination loop, между queries токены не
 * перевычитываются (живут несколько минут).
 */
export declare function searchAdsBatch(context: BrowserContext, params: SearchAdsBatchParams): Promise<SearchAdsBatchResult>;
/**
 * Health-check: context жив, browser подключён.
 */
export declare function checkAdLibraryHealth(context: BrowserContext | null | undefined): Promise<AdLibraryHealthResult>;
//# sourceMappingURL=client.d.ts.map