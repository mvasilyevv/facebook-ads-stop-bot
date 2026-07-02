/**
 * API-хуки для страницы «Статистика залива».
 *
 * Эндпоинты:
 *   GET /api/stats/today?breakdown=offer|campaign  → StatsToday
 *   GET /api/stats/period?from_iso&to_iso           → StatsPeriod
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import type { StatsPeriod, StatsToday } from "@fb/shared";

// ─── Сегодня (сутки кабинета) ──────────────────────────────────────────────────

interface StatsTodayParams {
  /** Разрез: offer | campaign (опционально). */
  breakdown?: "offer" | "campaign";
}

export function useStatsToday(breakdown?: "offer" | "campaign") {
  const params: StatsTodayParams | undefined = breakdown ? { breakdown } : undefined;
  return useQuery<StatsToday>({
    queryKey: ["stats", "today", params],
    queryFn: ({ signal }) =>
      apiGet<StatsToday>(
        "/stats/today",
        params as Record<string, string | number | boolean | null | undefined>,
        signal,
      ),
    staleTime: 30_000,
  });
}

// ─── Период ─────────────────────────────────────────────────────────────────────

interface StatsPeriodParams {
  from_iso?: string;
  to_iso?: string;
}

export function useStatsPeriod(params: Required<StatsPeriodParams>) {
  return useQuery<StatsPeriod>({
    queryKey: ["stats", "period", params],
    queryFn: ({ signal }) =>
      apiGet<StatsPeriod>(
        "/stats/period",
        params as Record<string, string | number | boolean | null | undefined>,
        signal,
      ),
    staleTime: 30_000,
    // Смена периода держит прежние данные видимыми (без моргания скелетоном).
    placeholderData: keepPreviousData,
  });
}
