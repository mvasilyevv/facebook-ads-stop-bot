/**
 * История заливов (issue #340) переходит на тот же курсорный паттерн
 * «Показать ещё», что уже работает в /actions. Ручка `/api/tools/campaigns/runs`
 * не отдаёт total в теле ответа — только заголовок `X-Total-Count` — и не умеет
 * cursor/before, но честно поддерживает `limit`/`offset`. Здесь — чистые
 * хелперы для накопления страниц через offset; сам fetch остаётся за каждым
 * фронтом (у web и TMA разные типизированные fetch-клиенты).
 */

export interface CampaignRunsPage<TRun> {
  runs: TRun[];
  /** null — total не подтверждён (заголовок отсутствует/не разобрался), а не 0. */
  total: number | null;
  offset: number;
  limit: number;
}

/** `X-Total-Count` может отсутствовать — это не то же самое, что подтверждённый 0. */
export function parseTotalCountHeader(value: string | null): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : null;
}

/**
 * offset следующей порции или `null`, если следующей порции нет.
 * При неизвестном total (заголовок не пришёл) решаем по заполненности
 * последней страницы: короткая страница не может прятать за собой ещё одну.
 */
export function nextCampaignRunsOffset<TRun>(page: CampaignRunsPage<TRun>): number | null {
  const seen = page.offset + page.runs.length;
  if (page.total != null) {
    return seen < page.total ? seen : null;
  }
  return page.runs.length >= page.limit && page.limit > 0 ? seen : null;
}
