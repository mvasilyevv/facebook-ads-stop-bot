/**
 * SparklineKpiRow — 4-колоночный bordered strip KPI на Dashboard.
 *
 * Канон design_handoff/web-dashboard.jsx:
 *   ячейка = eyebrow (ACTIVE/WARNING/STOP/DISABLED) + trend-chip + count-up число
 *   (34px, toned по state) + filled sparkline + «label · note».
 *
 * Реальные данные: counts из DashboardStats. Тренд-проценты и поштучная история
 * по каждому state в API отсутствуют → trend не показываем (без фейка), sparkline
 * строим только там, где есть реальный ряд (ACTIVE — из общего spend-ряда как
 * прокси активности). Для остальных ячеек sparkline скрыт (плоский).
 */

import { Eyebrow } from "@/components/data/Eyebrow";
import { TrendChip, type TrendTone } from "@/components/data/TrendChip";
import { Sparkline } from "@/components/data/charts/Sparkline";
import { useCountUp } from "@/lib/hooks/useCountUp";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import type { DashboardStats } from "@fb/shared";

// Цвета числа по тону (канон TONE из dashboard-shared.jsx).
const TONE_COLOR: Record<TrendTone, string> = {
  normal: "var(--bg-11)",
  warning: "var(--warning)",
  stop: "var(--danger)",
  disabled: "var(--bg-9)",
};

interface KpiCellData {
  key: string;
  eyebrow: string;
  value: number;
  label: string;
  note: string;
  tone: TrendTone;
  spark: number[];
}

function KpiCell({ d, last }: { d: KpiCellData; last: boolean }) {
  const value = useCountUp(d.value);
  const color = TONE_COLOR[d.tone];

  return (
    <div
      className={cn(
        "flex flex-col gap-3 p-5",
        !last && "border-r border-bg-5",
      )}
    >
      <div className="flex items-center justify-between">
        <Eyebrow>{d.eyebrow}</Eyebrow>
        {/* Тренд-данных нет в API — рисуем «—» (TrendChip с value=null). */}
        <TrendChip value={null} pct="" tone={d.tone} />
      </div>
      <div className="flex items-end justify-between gap-2">
        <span
          className="font-display font-medium tabular-nums"
          style={{ fontSize: 34, lineHeight: 0.9, color }}
        >
          {value}
        </span>
        <Sparkline data={d.spark} color={color} w={72} h={26} fill />
      </div>
      <div className="text-[12px] text-bg-9">
        <span className="text-bg-10">{d.label}</span> · {d.note}
      </div>
    </div>
  );
}

interface SparklineKpiRowProps {
  stats: DashboardStats;
  /** Реальный ряд spend по часам — прокси-история для ячейки ACTIVE. */
  spendSpark?: number[];
}

export function SparklineKpiRow({ stats, spendSpark = [] }: SparklineKpiRowProps) {
  const cells: KpiCellData[] = [
    {
      key: "active",
      eyebrow: "ACTIVE",
      value: stats.ads_in_normal ?? 0,
      label: "Норма",
      note: "активны сейчас",
      tone: "normal",
      spark: spendSpark,
    },
    {
      key: "warning",
      eyebrow: "WARNING",
      value: stats.ads_in_warning ?? 0,
      label: "Предупреждение",
      note: "сейчас",
      tone: "warning",
      spark: [],
    },
    {
      key: "stop",
      eyebrow: "STOP",
      value: stats.ads_in_stop ?? 0,
      label: "Стоп",
      note: "требуют решения",
      tone: "stop",
      spark: [],
    },
    {
      key: "disabled",
      eyebrow: "DISABLED",
      value: stats.ads_in_disabled ?? 0,
      label: "Отключено",
      note: "всего",
      tone: "disabled",
      spark: [],
    },
  ];

  return (
    <div
      className="grid grid-cols-4 border border-bg-5"
      role="list"
      aria-label="Ключевые показатели"
    >
      {cells.map((d, i) => (
        <KpiCell key={d.key} d={d} last={i === cells.length - 1} />
      ))}
    </div>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

export function SparklineKpiRowSkeleton() {
  return (
    <div
      className="grid grid-cols-4 border border-bg-5"
      role="status"
      aria-label="Загрузка KPI"
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className={cn("flex flex-col gap-3 p-5", i < 3 && "border-r border-bg-5")}>
          <Skeleton height={10} width="55%" />
          <div className="flex items-end justify-between gap-2">
            <Skeleton height={34} width="40%" />
            <Skeleton height={26} width={72} />
          </div>
          <Skeleton height={12} width="70%" />
        </div>
      ))}
    </div>
  );
}
