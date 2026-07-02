/**
 * StatCard — тап-карточка «Статистика дня» для Dashboard.
 * Заголовок + пара цифр (spend / лиды) + chevron → onClick навигация на /stats.
 * loading=true — скелетон вместо цифр (карточка остаётся кликабельной).
 */
import { ChevronRight, BarChart3 } from "lucide-react";
import { formatSpend, formatInt } from "@fb/shared";
import { Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";

interface StatCardProps {
  spend?: string | number | null;
  leads?: number | null;
  loading?: boolean;
  onClick: () => void;
  className?: string;
}

export function StatCard({ spend, leads, loading, onClick, className }: StatCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 min-h-[44px] px-4 py-3.5",
        "border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)]",
        "text-left active:bg-bg-2 transition-colors",
        className,
      )}
    >
      <span className="text-bg-9 shrink-0">
        <BarChart3 size={18} strokeWidth={1.6} />
      </span>
      <div className="flex-1 min-w-0">
        <p className="font-display text-[13px] text-bg-11 leading-tight">Статистика дня</p>
        {loading ? (
          <div className="flex items-center gap-3 mt-1.5">
            <Skeleton className="h-3.5 w-16" />
            <Skeleton className="h-3.5 w-14" />
          </div>
        ) : (
          <p className="font-display tabular-nums text-[12px] text-bg-9 mt-0.5">
            {formatSpend(spend ?? null)} · {formatInt(leads ?? null)} лидов
          </p>
        )}
      </div>
      <ChevronRight size={16} strokeWidth={1.5} className="text-bg-7 shrink-0" />
    </button>
  );
}
