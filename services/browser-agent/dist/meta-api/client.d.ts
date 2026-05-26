import type { Page } from 'playwright';
export declare const META_API_VERSION = "v22.0";
export interface GraphApiCallParams {
    method: 'GET' | 'POST' | 'DELETE';
    endpoint: string;
    queryParams: Record<string, string>;
    bodyJson?: string;
    timeoutMs?: number;
}
export interface GraphApiCallResult {
    statusCode: number;
    responseJson: string;
    durationMs: number;
    error?: {
        code: number;
        subcode: number;
        type: string;
        message: string;
        fbtraceId: string;
    };
}
export interface MetaApiHealthResult {
    healthy: boolean;
    currentUrl: string;
    tokenPresent: boolean;
    tokenLength: number;
    detail: string;
}
/**
 * Исполняет запрос к Marketing API изнутри активной Playwright-страницы.
 *
 * Page должен быть на странице Ads Manager (adsmanager.facebook.com) для того,
 * чтобы access_token и cookies были в session-context. Иначе токен не найдётся
 * и запрос провалится с ошибкой TOKEN_NOT_FOUND.
 *
 * Возвращает структурированный результат. Никогда не выбрасывает исключение
 * на уровне network/timeout — все ошибки упаковываются в GraphApiCallResult.
 * Это даёт клиенту возможность анализировать error.code (например, code=190
 * означает инвалидацию токена → нужна перезагрузка Vision-сессии).
 */
export declare function executeGraphCall(page: Page, params: GraphApiCallParams): Promise<GraphApiCallResult>;
/**
 * Health-check: жива ли страница Ads Manager и доступен ли токен.
 * Не делает реальных запросов к Meta — только проверяет состояние страницы.
 */
export declare function checkMetaApiHealth(page: Page): Promise<MetaApiHealthResult>;
//# sourceMappingURL=client.d.ts.map