"use strict";
// Жёсткая перезагрузка страницы Ads Manager с обходом кеша.
// Вызывается observer'ом при STALE_DATA, когда Ads Manager не отдаёт метрики.
Object.defineProperty(exports, "__esModule", { value: true });
exports.hardReloadPage = hardReloadPage;
async function hardReloadPage(page, bypassCache) {
    const startedAt = Date.now();
    if (bypassCache) {
        const session = await page.context().newCDPSession(page);
        try {
            await session.send('Network.clearBrowserCache');
        }
        finally {
            await session.detach().catch(() => undefined);
        }
    }
    try {
        await page.reload({ waitUntil: 'networkidle', timeout: 60_000 });
    }
    catch (err) {
        return {
            success: false,
            errorMessage: String(err?.message ?? err),
            reloadMs: Date.now() - startedAt,
        };
    }
    return {
        success: true,
        errorMessage: '',
        reloadMs: Date.now() - startedAt,
    };
}
//# sourceMappingURL=hard-reload.js.map