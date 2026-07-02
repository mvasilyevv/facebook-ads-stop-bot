/**
 * StatsPage — «Статистика залива»: сегодня / 7д / 30д.
 * MiniHeader → период-pills → FunnelKpiPlate(full) → StatsHourlyChart
 * (today: по часам из series_hourly; период: по дням из series_daily) →
 * FunnelBarMini → TrackerBlockMini.
 * Компонент экспортирован именованно (StatsPage) — тест импортирует его
 * напрямую поверх мокнутого @tanstack/react-router, без дублирования логики.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { EmptyState } from "@/components/ui";
import { FunnelKpiPlate } from "@/components/domain/FunnelKpiPlate";
import { StatsHourlyChart, type StatsChartPoint } from "@/components/domain/StatsHourlyChart";
import { FunnelBarMini } from "@/components/domain/FunnelBarMini";
import { TrackerBlockMini } from "@/components/domain/TrackerBlockMini";
import { useStatsToday, useStatsPeriod, type StatsPeriodDays } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/stats/")({
  component: StatsPage,
});

// ─── Периоды ────────────────────────────────────────────────────────────────

type PeriodId = "today" | 7 | 30;

const PERIODS: { id: PeriodId; label: string }[] = [
  { id: "today", label: "Сегодня" },
  { id: 7, label: "7д" },
  { id: 30, label: "30д" },
];

// ─── Pill-переключатель периода ───────────────────────────────────────────────

function PeriodPills({ period, onChange }: { period: PeriodId; onChange: (p: PeriodId) => void }) {
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--hairline)]">
      {PERIODS.map((p) => {
        const active = p.id === period;
        return (
          <button
            key={p.id}
            type="button"
            onClick={() => {
              haptic.selection();
              onChange(p.id);
            }}
            className={cn(
              "min-h-[36px] px-4 text-[12px] font-display font-semibold uppercase tracking-[0.08em] border rounded-[var(--radius-2)] transition-colors",
              active
                ? "bg-accent text-bg-0 border-accent"
                : "bg-bg-1 text-bg-9 border-[var(--hairline)] hover:text-bg-11",
            )}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

// ─── Форматтеры меток оси X ───────────────────────────────────────────────────

function hourLabel(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getUTCHours()).padStart(2, "0")}:00`;
}

function dayLabel(day: string): string {
  const d = new Date(day);
  if (Number.isNaN(d.getTime())) return day;
  return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

// ─── Секция-обёртка ────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <p className="font-display text-[10px] font-semibold uppercase tracking-[0.12em] text-bg-9 mb-2.5">
        {title}
      </p>
      {children}
    </section>
  );
}

// ─── Главный компонент ────────────────────────────────────────────────────────

export function StatsPage() {
  const [period, setPeriod] = useState<PeriodId>("today");

  const today = useStatsToday();
  const periodQuery = useStatsPeriod(period === "today" ? 7 : (period as StatsPeriodDays));

  const isToday = period === "today";
  const activeQuery = isToday ? today : periodQuery;
  const { isLoading, isError, error } = activeQuery;

  const funnelData = isToday
    ? today.data
      ? { totals: today.data.meta.totals, derived: today.data.meta.derived }
      : undefined
    : periodQuery.data
      ? { totals: periodQuery.data.meta.totals, derived: periodQuery.data.meta.derived }
      : undefined;

  const chartPoints: StatsChartPoint[] = isToday
    ? (today.data?.meta.series_hourly ?? []).map((p) => ({
        label: hourLabel(p.ts),
        spend: p.spend != null ? Number.parseFloat(p.spend) || 0 : 0,
        leads: p.leads ?? 0,
        deposits: p.deposits ?? 0,
      }))
    : (periodQuery.data?.meta.series_daily ?? []).map((p) => ({
        label: dayLabel(p.day),
        spend: p.spend != null ? Number.parseFloat(p.spend) || 0 : 0,
        leads: p.leads ?? 0,
        deposits: p.deposits ?? 0,
      }));

  const tracker = isToday ? today.data?.tracker : periodQuery.data?.tracker;

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* ── шапка ── */}
      <MiniHeader eyebrowNum="10" eyebrow="СТАТИСТИКА" title="Статистика" />

      {/* ── переключатель периода ── */}
      <PeriodPills period={period} onChange={setPeriod} />

      <div className="flex flex-col gap-5 p-4">
        {isError ? (
          <EmptyState
            title="Ошибка загрузки"
            description={(error as Error | null)?.message ?? "Повторите позже"}
          />
        ) : (
          <>
            {/* ── воронка ── */}
            <Section title="ВОРОНКА">
              <FunnelKpiPlate data={funnelData} loading={isLoading} />
            </Section>

            {/* ── график ── */}
            <Section title={isToday ? "ДИНАМИКА ПО ЧАСАМ" : "ДИНАМИКА ПО ДНЯМ"}>
              <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] bg-bg-1 p-4">
                <StatsHourlyChart data={chartPoints} live={isToday} />
              </div>
            </Section>

            {/* ── воронка ступеней ── */}
            <Section title="СТУПЕНИ ВОРОНКИ">
              <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] bg-bg-1 p-4">
                <FunnelBarMini
                  totals={funnelData?.totals}
                  derived={funnelData?.derived}
                  loading={isLoading}
                />
              </div>
            </Section>

            {/* ── трекер ── */}
            <TrackerBlockMini tracker={tracker} loading={isLoading} />
          </>
        )}
      </div>
    </div>
  );
}
