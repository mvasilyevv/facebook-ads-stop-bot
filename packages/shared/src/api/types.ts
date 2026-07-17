/**
 * Ergonomic-алиасы поверх generated.ts.
 * Импортируй из этого файла, а не из generated напрямую —
 * generated меняется автоматически, здесь — стабильный публичный контракт.
 */

import type { components } from "./generated";

// ─── Объявления ─────────────────────────────────────────────────────────────

/** Снимок одного объявления для /dashboard/ads, /dashboard/incidents */
export type AdSnapshot = components["schemas"]["AdSnapshotOut"];

/** Блок метрик внутри AdSnapshot */
export type MetricsBlock = components["schemas"]["MetricsBlock"];

/** Инцидент = AdSnapshot + incident-поля */
export type Incident = components["schemas"]["IncidentOut"];

/** Полный timeline объявления (метрики + алерты + задачи) */
export type AdTimeline = components["schemas"]["AdTimelineResponse"];

// ─── Алерты ──────────────────────────────────────────────────────────────────

/** Одна запись alert_events c JOIN по fb_ads/offers */
export type AlertEvent = components["schemas"]["AlertEventOut"];

// ─── Задачи ──────────────────────────────────────────────────────────────────

/** Строка task_queue в формате для фронта (uppercase status) */
export type TaskQueueRow = components["schemas"]["TaskQueueRowOut"];

/** Enable-recommendation строка */
export type EnableRecommendationRow = components["schemas"]["EnableRecommendationRowOut"];

// ─── Офферы ──────────────────────────────────────────────────────────────────

/** Оффер с правилами */
export type Offer = components["schemas"]["OfferOut"];

/** Правила оффера */
export type OfferRules = components["schemas"]["OfferRuleOut"];

// ─── Dashboard ────────────────────────────────────────────────────────────────

/** 14 scalar-полей для /dashboard/stats */
export type DashboardStats = components["schemas"]["DashboardStatsOut"];

/** Батч-ответ: stats + incidents + alerts + disable + enable_recommendations */
export type DashboardBatch = components["schemas"]["DashboardBatchOut"];

/** Топ-кампании, offer-leaderboard, топ-нарушения правил */
export type DashboardPerformance = components["schemas"]["DashboardPerformanceOut"];

/** Точка метрик для /dashboard/spend-history (не бакетированная) */
export type SpendPoint = components["schemas"]["SpendPointOut"];

/** Бакетированная точка для /dashboard/chart-data (hour|day) */
export type ChartBucket = components["schemas"]["ChartBucketOut"];

// ─── История ─────────────────────────────────────────────────────────────────

/** Сводка за период: spend/impressions/alerts/tasks */
export type HistorySummary = components["schemas"]["HistorySummaryOut"];

/** Одна строка объединённого timeline (alert + task) */
export type HistoryTimelineItem = components["schemas"]["HistoryTimelineItem"];

/** История по кампаниям */
export type HistoryCampaign = components["schemas"]["HistoryCampaignOut"];

/** История по офферам */
export type HistoryOffer = components["schemas"]["HistoryOfferOut"];

/** История по объявлениям */
export type HistoryAd = components["schemas"]["HistoryAdOut"];

/** Одно событие алерта в истории (drill-down) */
export type HistoryEvent = components["schemas"]["HistoryEventOut"];

// ─── Observer / Health ────────────────────────────────────────────────────────

/** Статус observer-воркера из Redis observer:runtime */
export type ObserverStatus = components["schemas"]["ObserverStatusResponse"];

/** Детальный health (workers online/offline) */
export type HealthDetails = components["schemas"]["HealthDetailsResponse"];

// ─── Settings ─────────────────────────────────────────────────────────────────

export type ObserverConfig = components["schemas"]["ObserverSettingsResponse"];
export type TelegramSettings = components["schemas"]["TelegramSettingsResponse"];

// ─── TMA-специфичные ─────────────────────────────────────────────────────────

/** Детальная страница объявления в TMA */
export type TmaAdDetail = components["schemas"]["TmaAdDetailResponse"];

// ─── Статистика залива ───────────────────────────────────────────────────────

/** Воронка текущих суток кабинета: тоталы + производные + почасовые дельты + трекер */
export type StatsToday = components["schemas"]["StatsTodayOut"];

/** Воронка за период: тоталы + производные + подневные серии Meta и трекера */
export type StatsPeriod = components["schemas"]["StatsPeriodOut"];

/** Тоталы воронки Meta (money-поля — Decimal-строки) */
export type FunnelTotals = components["schemas"]["FunnelTotalsOut"];

/** Производные метрики воронки (None = деление на ноль → «—») */
export type FunnelDerived = components["schemas"]["FunnelDerivedOut"];

// ─── Единая аналитика ──────────────────────────────────────────────────────

export type AnalyticsPerformance = components["schemas"]["AnalyticsPerformanceOut"];
export type AnalyticsPerformanceRow = components["schemas"]["AnalyticsPerformanceRowOut"];
export type AnalyticsLiveBudget = components["schemas"]["AnalyticsLiveBudgetOut"];
export type AnalyticsLiveBudgetSeries = components["schemas"]["AnalyticsLiveBudgetSeriesOut"];
export type AnalyticsDaypart = components["schemas"]["AnalyticsDaypartOut"];
