// Жёсткая перезагрузка страницы Ads Manager с обходом кеша.
// Вызывается observer'ом при STALE_DATA, когда Ads Manager не отдаёт метрики.

import type { Page } from 'playwright';

export interface HardReloadResult {
  success: boolean;
  errorMessage: string;
  reloadMs: number;
}

export async function hardReloadPage(page: Page, bypassCache: boolean): Promise<HardReloadResult> {
  const startedAt = Date.now();

  if (bypassCache) {
    const session = await page.context().newCDPSession(page);
    try {
      await session.send('Network.clearBrowserCache');
    } finally {
      await session.detach().catch(() => undefined);
    }
  }

  try {
    await page.reload({ waitUntil: 'networkidle', timeout: 60_000 });
  } catch (err: any) {
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
