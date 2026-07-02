/**
 * SparklineKpiRow — 4-колоночный bordered strip KPI на Dashboard.
 *
 * Канон design_handoff/web-dashboard.jsx:
 *   ячейка = eyebrow (ACTIVE/WARNING/STOP/DISABLED) + trend-chip + count-up число
 *   (34px, toned по state) + «label · note». Sparkline убран (см. ниже).
 *
 * Реальные данные: counts из DashboardStats. Тренд-проценты в API отсутствуют →
 * trend не показываем (без фейка).
 *
 * Sparkline в ячейках УБРАН (решение владельца, 03.07): сначала он по ошибке
 * рисовал spend-ряд как «прокси активности» (рос при падении активных), потом —
 * честный active_ads по часам, но семантика мини-графика без подписи всё равно
 * считывалась неверно. Динамика активных видна на /stats (почасовая серия);
 * здесь — только чистые counts. По WARNING/STOP/DISABLED почасовой истории в
 * API и не было.
 */

import { Eyebrow } from "@/components/data/Eyebrow";
import { TrendChip, type TrendTone } from "@/components/data/TrendChip";
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
}

function KpiCell({
  d,
  last,
  onClick,
}: {
  d: KpiCellData;
  last: boolean;
  /** Клик по ячейке → переход в Ads с фильтром по состоянию (если задан). */
  onClick?: () => void;
}) {
  const value = useCountUp(d.value);
  const color = TONE_COLOR[d.tone];

  return (
    <div
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `${d.label}: открыть в списке объявлений` : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      className={cn(
        "flex flex-col gap-3 p-5",
        !last && "border-r border-[var(--hairline)]",
        onClick &&
          "cursor-pointer transition-colors duration-[120ms] hover:bg-bg-1 " +
            "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
      )}
    >
      <div className="flex items-center justify-between">
        <Eyebrow>{d.eyebrow}</Eyebrow>
        {/* Тренд-данных нет в API — рисуем «—» (TrendChip с value=null). */}
        <TrendChip value={null} pct="" tone={d.tone} />
      </div>
      <span
        className="font-display font-medium tabular-nums"
        style={{ fontSize: 34, lineHeight: 0.9, color }}
      >
        {value}
      </span>
      <div className="text-[12px] text-bg-9">
        <span className="text-bg-10">{d.label}</span> · {d.note}
      </div>
    </div>
  );
}

/** key ячейки → alert_state для deep-link /ads?state=… */
export const KPI_CELL_STATE: Record<string, string> = {
  active: "normal",
  warning: "warning_sent",
  stop: "stop_sent",
  disabled: "disabled",
};

interface SparklineKpiRowProps {
  stats: DashboardStats;
  /** Клик по ячейке (key: active|warning|stop|disabled) → фильтр в Ads. */
  onCellClick?: (key: string) => void;
}

export function SparklineKpiRow({
  stats,
  onCellClick,
}: SparklineKpiRowProps) {
  const cells: KpiCellData[] = [
    {
      key: "active",
      eyebrow: "ACTIVE",
      value: stats.ads_in_normal ?? 0,
      label: "Норма",
      note: "активны сейчас",
      tone: "normal",
    },
    {
      key: "warning",
      eyebrow: "WARNING",
      value: stats.ads_in_warning ?? 0,
      label: "Предупреждение",
      note: "сейчас",
      tone: "warning",
    },
    {
      key: "stop",
      eyebrow: "STOP",
      value: stats.ads_in_stop ?? 0,
      label: "Стоп",
      note: "требуют решения",
      tone: "stop",
    },
    {
      key: "disabled",
      eyebrow: "DISABLED",
      value: stats.ads_in_disabled ?? 0,
      label: "Отключено",
      note: "всего",
      tone: "disabled",
    },
  ];

  return (
    <div
      className="grid grid-cols-4 border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden"
      role="list"
      aria-label="Ключевые показатели"
    >
      {cells.map((d, i) => (
        <KpiCell
          key={d.key}
          d={d}
          last={i === cells.length - 1}
          onClick={onCellClick ? () => onCellClick(d.key) : undefined}
        />
      ))}
    </div>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

export function SparklineKpiRowSkeleton() {
  return (
    <div
      className="grid grid-cols-4 border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden"
      role="status"
      aria-label="Загрузка KPI"
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className={cn("flex flex-col gap-3 p-5", i < 3 && "border-r border-[var(--hairline)]")}>
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
