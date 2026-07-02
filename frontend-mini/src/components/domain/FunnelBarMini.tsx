/**
 * FunnelBarMini — компактная воронка ступеней клики→лиды→реги→депы с CR%.
 * Div/SVG-бары: ширина пропорциональна значению относительно первой ступени
 * (клики = 100%). CR% между соседними ступенями — из derived (cr_click_lead_pct
 * и т.п.), при null — «—».
 */
import type { FunnelDerived, FunnelTotals } from "@fb/shared";
import { formatInt, formatPercentValue } from "@fb/shared";
import { Skeleton } from "@/components/ui";

interface FunnelBarMiniProps {
  totals?: FunnelTotals;
  derived?: FunnelDerived;
  loading?: boolean;
}

interface Step {
  key: string;
  label: string;
  value: number;
  crFromPrev: string | null | undefined;
}

export function FunnelBarMini({ totals, derived, loading }: FunnelBarMiniProps) {
  if (loading || !totals) {
    return (
      <div className="flex flex-col gap-2.5">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }

  const steps: Step[] = [
    { key: "clicks", label: "Клики", value: totals.clicks ?? 0, crFromPrev: null },
    { key: "leads", label: "Лиды", value: totals.leads ?? 0, crFromPrev: derived?.cr_click_lead_pct },
    { key: "registrations", label: "Регистрации", value: totals.registrations ?? 0, crFromPrev: derived?.cr_lead_reg_pct },
    { key: "deposits", label: "Депозиты", value: totals.deposits ?? 0, crFromPrev: derived?.cr_reg_dep_pct },
  ];

  const base = steps[0]!.value || 1;

  return (
    <div className="flex flex-col gap-2.5" role="group" aria-label="Воронка ступеней">
      {steps.map((step, i) => {
        const pct = Math.max(4, Math.min(100, (step.value / base) * 100));
        return (
          <div key={step.key} className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] text-bg-10">{step.label}</span>
              <div className="flex items-center gap-2">
                {i > 0 && (
                  <span className="font-display text-[11px] tabular-nums text-bg-8">
                    CR {formatPercentValue(step.crFromPrev ?? null)}
                  </span>
                )}
                <span className="font-display tabular-nums text-[14px] text-bg-11">
                  {formatInt(step.value)}
                </span>
              </div>
            </div>
            <div className="h-2 w-full bg-bg-2 rounded-[var(--radius-1)] overflow-hidden">
              <div
                className="h-full bg-accent rounded-[var(--radius-1)]"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
