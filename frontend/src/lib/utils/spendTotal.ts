/**
 * cumulativeSpendTotal — корректный суммарный спенд из часовых бакетов chart-data.
 *
 * ПРОБЛЕМА (money-bug). `ad_metrics` хранит КУМУЛЯТИВНЫЕ snapshot'ы: спенд растёт за
 * сутки и СБРАСЫВАЕТСЯ ПОСУТОЧНО (cabinet day reset). Бакеты `/dashboard/chart-data`
 * уже несут накопленный итог к концу каждого часа. Наивная сумма всех бакетов
 * (`reduce((a,b)=>a+b.spend, 0)`) сложила бы кумулятив и завысила спенд в разы
 * (реальные $1.78 показывались как $5).
 *
 * ПРАВИЛЬНО: в пределах одних суток кумулятив монотонен → дневной итог = ПОСЛЕДНИЙ
 * бакет суток. Складываем дневные итоги через посуточные сбросы (по UTC-дню).
 *
 * Устойчиво к порядку входных бакетов: на каждый UTC-день держим бакет с максимальным
 * ts. Бакеты с непарсящимся ts игнорируются.
 */

/** Минимальный контракт бакета (подмножество ChartBucket). */
export interface SpendBucketLike {
  ts?: string | null;
  spend?: string | number | null;
}

export function cumulativeSpendTotal(buckets: readonly SpendBucketLike[]): number {
  // day (YYYY-MM-DD UTC) → { ts: epoch_ms последнего бакета дня, spend }
  const lastPerDay = new Map<string, { ts: number; spend: number }>();
  for (const b of buckets) {
    // Только непустая строка-ts: иначе new Date(null) === epoch 1970 (валиден) — мусор.
    if (typeof b.ts !== "string" || b.ts === "") continue;
    const t = new Date(b.ts).getTime();
    if (Number.isNaN(t)) continue;
    const day = new Date(t).toISOString().slice(0, 10);
    const prev = lastPerDay.get(day);
    if (!prev || t >= prev.ts) {
      lastPerDay.set(day, { ts: t, spend: Number(b.spend ?? 0) || 0 });
    }
  }
  let total = 0;
  for (const v of lastPerDay.values()) total += v.spend;
  return total;
}
